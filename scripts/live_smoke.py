"""A quick check of the deployed site: login, health, topics, and three questions.

Steps, in order (docs/TESTING_AND_SCORES.md, "Live smoke check"):
1. GET /api/health
2. POST /api/login with STUDENT_PASSCODE (from the environment, else from the repo's .env)
3. GET /api/topics
4. POST /api/ask with an on-topic question (expects covered, with segments)
5. POST /api/ask with an off-topic question (expects not covered, no kind)
6. POST /api/ask with an FAQ question (expects kind "faq")

It prints each step's HTTP status, time, and what came back. It never prints
the passcode. It asks three questions, so it uses three of today's 30
questions for this visitor, one or two embeddings, and up to two model calls
(the logistics check and narration for the on-topic question). Audio is not
fetched, so no voice characters are spent.

Run from ~/Code/faculty-twin:
    uv run --no-project --with-requirements requirements.txt python -m scripts.live_smoke
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_BASE_URL = "https://faculty-twin.vercel.app"

# Invented questions. The FAQ one matches the "meeting" entry in app/faq_entries.json.
COVERED_QUESTION = "How does k-means decide which cluster a point belongs to?"
OFF_TOPIC_QUESTION = "Who won the Stanley Cup last year?"
FAQ_QUESTION = "When are your office hours?"


@dataclass
class Step:
    name: str
    status: Optional[int]
    ms: int
    ok: bool
    note: str = ""


@dataclass
class Report:
    steps: list[Step] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.steps) and all(s.ok for s in self.steps)


def read_passcode(env_file: Optional[Path] = None) -> Optional[str]:
    """STUDENT_PASSCODE from the environment, else from the repo's .env (or FT_ENV_FILE). Never logged."""
    from indexer.common import load_env

    load_env(env_file)
    return (os.environ.get("STUDENT_PASSCODE") or "").strip() or None


def _detail(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:120]
    return str(data.get("detail", ""))[:160] if isinstance(data, dict) else ""


def _ask_note(data: dict[str, Any]) -> str:
    kind = data.get("kind") or ("course_content" if data.get("covered") else "not_covered")
    return f"covered={data.get('covered')} kind={kind} segments={len(data.get('segments') or [])}"


def run(client: httpx.Client, passcode: Optional[str], questions: dict[str, str]) -> Report:
    report = Report()

    def step(name: str, call: Callable[[], httpx.Response], check: Callable[[httpx.Response], tuple[bool, str]]):
        started = time.monotonic()
        try:
            resp = call()
        except httpx.HTTPError as exc:
            report.steps.append(Step(name, None, int((time.monotonic() - started) * 1000), False,
                                     f"request failed: {type(exc).__name__}"))
            return None
        ms = int((time.monotonic() - started) * 1000)
        try:
            ok, note = check(resp)
        except (ValueError, KeyError, TypeError) as exc:
            ok, note = False, f"unexpected reply: {type(exc).__name__}"
        report.steps.append(Step(name, resp.status_code, ms, ok, note))
        return resp

    step("health", lambda: client.get("/api/health"),
         lambda r: (r.status_code == 200 and r.json().get("ok") is True, ""))
    if not passcode:
        report.steps.append(Step("login", None, 0, False, "STUDENT_PASSCODE is not set in the environment or .env"))
        return report
    login = step("login", lambda: client.post("/api/login", json={"passcode": passcode}),
                 lambda r: (r.status_code == 204, "" if r.status_code == 204 else _detail(r)))
    if login is None or login.status_code != 204:
        return report

    def topics_check(r: httpx.Response) -> tuple[bool, str]:
        if r.status_code != 200:
            return False, _detail(r)
        return isinstance(r.json(), list), f"{len(r.json())} suggested questions"

    step("topics", lambda: client.get("/api/topics"), topics_check)

    def ask(question: str) -> Callable[[], httpx.Response]:
        return lambda: client.post("/api/ask", json={"question": question}, timeout=90.0)

    def expect(want: Callable[[dict[str, Any]], bool]) -> Callable[[httpx.Response], tuple[bool, str]]:
        def check(r: httpx.Response) -> tuple[bool, str]:
            if r.status_code != 200:
                return False, _detail(r)
            data = r.json()
            return want(data), _ask_note(data)
        return check

    step("ask covered", ask(questions["covered"]),
         expect(lambda d: d.get("covered") is True and bool(d.get("segments"))))
    step("ask off-topic", ask(questions["off_topic"]),
         expect(lambda d: d.get("covered") is False and not d.get("kind")))
    step("ask FAQ", ask(questions["faq"]),
         expect(lambda d: d.get("kind") == "faq"))
    return report


def render(report: Report, base_url: str) -> str:
    lines = [f"Live smoke check: {base_url}", ""]
    for s in report.steps:
        status = "---" if s.status is None else str(s.status)
        mark = "ok  " if s.ok else "FAIL"
        lines.append(f"{mark} {s.name:<14} {status:>4} {s.ms:>6} ms  {s.note}".rstrip())
    lines += ["", "All steps passed." if report.ok else "Some steps failed."]
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None, transport: Optional[httpx.BaseTransport] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base-url", default=os.environ.get("SMOKE_BASE_URL", DEFAULT_BASE_URL))
    p.add_argument("--env-file", help="where to read STUDENT_PASSCODE if it is not in the environment (default ./.env)")
    p.add_argument("--covered-question", default=COVERED_QUESTION)
    p.add_argument("--off-topic-question", default=OFF_TOPIC_QUESTION)
    p.add_argument("--faq-question", default=FAQ_QUESTION)
    args = p.parse_args(argv)
    base_url = args.base_url.rstrip("/")
    questions = {"covered": args.covered_question, "off_topic": args.off_topic_question, "faq": args.faq_question}
    passcode = read_passcode(Path(args.env_file) if args.env_file else None)
    # X-FT-Source tags these rows as test traffic in the question log (Settings > Analytics hides them).
    with httpx.Client(base_url=base_url, timeout=httpx.Timeout(30.0, connect=10.0), transport=transport,
                      follow_redirects=False, headers={"X-FT-Source": "smoke"}) as client:
        report = run(client, passcode, questions)
    print(render(report, base_url))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())

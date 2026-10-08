"""A quick check of the deployed site: login, health, topics, five questions, media links, and Settings.

Steps, in order (docs/TESTING_AND_SCORES.md, "Live smoke check"):
1. GET /api/health
2. POST /api/login with STUDENT_PASSCODE (from the environment, else from the repo's .env)
3. GET /api/topics
4. POST /api/ask with an on-topic question (expects covered, with segments)
5. GET the first segment's signed slide image (first 1 KB only; expects an image)
6. POST /api/ask with an off-topic question (expects not covered, no kind)
7. POST /api/ask with an FAQ question (expects kind "faq")
8. POST /api/ask with a course-info question (expects kind "course_info", answered from Canvas)
9. POST /api/ask with the first suggested question (expects the stored walkthrough)
10. GET the stored walkthrough's first audio file (first 1 KB only; expects audio). Live voice
    links (/api/audio) are never fetched, because they would spend voice characters.
11. Only when ADMIN_PASSCODE is set: POST /api/admin/login, then GET /api/admin/status

It prints each step's HTTP status, time, and what came back (`--json` prints the same as JSON).
It never prints a passcode, a cookie, or a signed link. It asks five questions, so it uses five
of today's 30 questions for this visitor (and exactly the per-minute cap of 5), one or two
embeddings, and up to three model calls (the logistics check and narration for the on-topic
question, and the course-info answer). The stored walkthrough and the FAQ need no model call.

Run from ~/Code/faculty-twin:
    uv run --no-project --with-requirements requirements.txt python -m scripts.live_smoke
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_BASE_URL = "https://faculty-twin.vercel.app"

# Invented questions. The FAQ one matches the "meeting" entry in app/faq_entries.json.
# The course-info one is about the syllabus and matches no FAQ entry, so it reaches the Canvas index.
COVERED_QUESTION = "How does k-means decide which cluster a point belongs to?"
OFF_TOPIC_QUESTION = "Who won the Stanley Cup last year?"
FAQ_QUESTION = "When are your office hours?"
COURSE_INFO_QUESTION = "How is the final grade calculated in this course?"

MEDIA_PEEK_BYTES = 1024  # media checks read the first kilobyte, never the whole file


@dataclass
class Step:
    """One check's outcome: HTTP status (None when no request was made), time, pass or fail, a short note."""

    name: str
    status: int | None
    ms: int
    ok: bool
    note: str = ""


@dataclass
class Report:
    """Every step in the order it ran. Passes only when every step passed."""

    steps: list[Step] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.steps) and all(s.ok for s in self.steps)


def _read_env_secret(name: str, env_file: Path | None = None) -> str | None:
    """A secret from the environment, else from the repo's .env (or FT_ENV_FILE). Never logged."""
    from indexer.common import load_env

    load_env(env_file)
    return (os.environ.get(name) or "").strip() or None


def read_passcode(env_file: Path | None = None) -> str | None:
    """STUDENT_PASSCODE from the environment, else from the repo's .env (or FT_ENV_FILE). Never logged."""
    return _read_env_secret("STUDENT_PASSCODE", env_file)


def read_admin_passcode(env_file: Path | None = None) -> str | None:
    """ADMIN_PASSCODE, the same way. The admin step is skipped when it is not set."""
    return _read_env_secret("ADMIN_PASSCODE", env_file)


def _detail(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:120]
    return str(data.get("detail", ""))[:160] if isinstance(data, dict) else ""


def _ask_note(data: dict[str, Any]) -> str:
    kind = data.get("kind") or ("course_content" if data.get("covered") else "not_covered")
    return f"covered={data.get('covered')} kind={kind} segments={len(data.get('segments') or [])}"


def _status_note(data: dict[str, Any]) -> str:
    """Counts only: how many keys are set, whether content loaded, today's questions. No key names or values."""
    keys = data.get("keys") or {}
    content = data.get("content") or {}
    today = data.get("today") or {}
    return (f"keys {sum(1 for v in keys.values() if v)}/{len(keys)} set, "
            f"content loaded={content.get('loaded')}, questions today={today.get('questions')}")


def _ask_json(resp: httpx.Response | None) -> dict[str, Any]:
    """The JSON body of a successful /api/ask reply, else {}."""
    if resp is None or resp.status_code != 200:
        return {}
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


Check = Callable[[httpx.Response], tuple[bool, str]]


def _media_check(prefix: str) -> Check:
    """Passes when a media link answers 200 or 206 with a content type starting with `prefix`."""
    def check(r: httpx.Response) -> tuple[bool, str]:
        ctype = r.headers.get("content-type", "").split(";")[0]
        ok = r.status_code in (200, 206) and ctype.startswith(prefix)
        return ok, f"{ctype or 'no content type'}" + ("" if ok else f" (expected {prefix}*)")
    return check


def _expect(want: Callable[[dict[str, Any]], bool]) -> Check:
    """Passes when /api/ask answers 200 and `want` accepts the JSON."""
    def check(r: httpx.Response) -> tuple[bool, str]:
        if r.status_code != 200:
            return False, _detail(r)
        data = r.json()
        return want(data), _ask_note(data)
    return check


def _no_content(r: httpx.Response) -> tuple[bool, str]:
    """Passes on 204 (the login routes)."""
    return r.status_code == 204, "" if r.status_code == 204 else _detail(r)


def _topics_check(r: httpx.Response) -> tuple[bool, str]:
    if r.status_code != 200:
        return False, _detail(r)
    return isinstance(r.json(), list), f"{len(r.json())} suggested questions"


class _SmokeRun:
    """Runs the steps in order against one client, recording each one in the report."""

    def __init__(self, client: httpx.Client) -> None:
        self.client = client
        self.report = Report()

    def step(self, name: str, call: Callable[[], httpx.Response], check: Check) -> httpx.Response | None:
        """Time one request and record whether `check` passed. A network error or odd reply fails the step."""
        started = time.monotonic()
        try:
            resp = call()
        except httpx.HTTPError as exc:
            self.report.steps.append(Step(name, None, int((time.monotonic() - started) * 1000), False,
                                          f"request failed: {type(exc).__name__}"))
            return None
        ms = int((time.monotonic() - started) * 1000)
        try:
            ok, note = check(resp)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            ok, note = False, f"unexpected reply: {type(exc).__name__}"
        self.report.steps.append(Step(name, resp.status_code, ms, ok, note))
        return resp

    def skip(self, name: str, note: str, ok: bool = True) -> None:
        self.report.steps.append(Step(name, None, 0, ok, note))

    def peek(self, url: str) -> Callable[[], httpx.Response]:
        """GET the first kilobyte of a media link. Signed storage links are absolute; local ones are relative."""
        def call() -> httpx.Response:
            with self.client.stream("GET", url, headers={"Range": f"bytes=0-{MEDIA_PEEK_BYTES - 1}"}) as resp:
                for _ in resp.iter_bytes(MEDIA_PEEK_BYTES):
                    break  # one chunk is enough to know the file is there; never download the rest
                return resp
        return call

    def ask(self, question: str) -> Callable[[], httpx.Response]:
        return lambda: self.client.post("/api/ask", json={"question": question}, timeout=90.0)

    def check_covered_answer(self, question: str) -> None:
        """An on-topic question must be covered, and its first slide image must load."""
        covered = self.step("ask covered", self.ask(question),
                            _expect(lambda d: d.get("covered") is True and bool(d.get("segments"))))
        segments = _ask_json(covered).get("segments") or []
        image = next((s.get("image") for s in segments if isinstance(s, dict) and s.get("image")), None)
        if image:
            self.step("slide image", self.peek(str(image)), _media_check("image/"))
        else:
            self.skip("slide image", "no slide image to check (the covered answer failed)", ok=False)

    def check_stored_topic(self, topics_resp: httpx.Response | None) -> None:
        """The first suggested question replays its stored walkthrough; its stored mp3 must load.

        Live voice links (/api/audio) are never fetched: they would spend voice characters.
        """
        topics = topics_resp.json() if topics_resp is not None and topics_resp.status_code == 200 else []
        chip = next((t.get("question") for t in topics if isinstance(t, dict) and t.get("question")), None)
        if not chip:
            self.skip("ask chip", "no suggested questions to ask", ok=False)
            return
        stored = self.step("ask chip", self.ask(str(chip)),
                           _expect(lambda d: d.get("covered") is True and bool(d.get("segments"))))
        audio = [s.get("audio") for s in _ask_json(stored).get("segments") or [] if isinstance(s, dict)]
        stored_audio = next((a for a in audio if a and not str(a).startswith("/api/audio")), None)
        if stored_audio:
            self.step("chip audio", self.peek(str(stored_audio)), _media_check("audio/"))
        elif any(audio):
            self.skip("chip audio", "live voice links only (no stored mp3 for this voice); not fetched")
        else:
            self.skip("chip audio", "no audio in the stored answer (captions only)")

    def check_admin(self, admin_passcode: str | None) -> None:
        """Settings: log in with the admin passcode and read the status (counts only)."""
        if not admin_passcode:
            self.skip("admin status", "skipped: ADMIN_PASSCODE is not set")
            return
        admin = self.step("admin login",
                          lambda: self.client.post("/api/admin/login", json={"passcode": admin_passcode}), _no_content)
        if admin is not None and admin.status_code == 204:
            self.step("admin status", lambda: self.client.get("/api/admin/status"),
                      lambda r: (r.status_code == 200 and isinstance(r.json().get("keys"), dict),
                                 _status_note(r.json()) if r.status_code == 200 else _detail(r)))


def run(
    client: httpx.Client,
    passcode: str | None,
    questions: dict[str, str],
    admin_passcode: str | None = None,
) -> Report:
    """Run every step in order (see the module docstring). Stops early only when login fails."""
    smoke = _SmokeRun(client)
    smoke.step("health", lambda: client.get("/api/health"),
               lambda r: (r.status_code == 200 and r.json().get("ok") is True, ""))
    if not passcode:
        smoke.skip("login", "STUDENT_PASSCODE is not set in the environment or .env", ok=False)
        return smoke.report
    login = smoke.step("login", lambda: client.post("/api/login", json={"passcode": passcode}), _no_content)
    if login is None or login.status_code != 204:
        return smoke.report
    topics_resp = smoke.step("topics", lambda: client.get("/api/topics"), _topics_check)
    smoke.check_covered_answer(questions["covered"])
    smoke.step("ask off-topic", smoke.ask(questions["off_topic"]),
               _expect(lambda d: d.get("covered") is False and not d.get("kind")))
    smoke.step("ask FAQ", smoke.ask(questions["faq"]),
               _expect(lambda d: d.get("kind") == "faq"))
    smoke.step("ask course info", smoke.ask(questions["course_info"]),
               _expect(lambda d: d.get("kind") == "course_info" and bool(d.get("message"))))
    smoke.check_stored_topic(topics_resp)
    smoke.check_admin(admin_passcode)
    return smoke.report


def render(report: Report, base_url: str) -> str:
    """The report as aligned text, one line per step."""
    lines = [f"Live smoke check: {base_url}", ""]
    for s in report.steps:
        status = "---" if s.status is None else str(s.status)
        mark = "ok  " if s.ok else "FAIL"
        lines.append(f"{mark} {s.name:<15} {status:>4} {s.ms:>6} ms  {s.note}".rstrip())
    lines += ["", "All steps passed." if report.ok else "Some steps failed."]
    return "\n".join(lines)


def render_json(report: Report, base_url: str) -> str:
    """The report as JSON (same content as `render`)."""
    return json.dumps({"base_url": base_url, "ok": report.ok, "steps": [asdict(s) for s in report.steps]}, indent=2)


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base-url", default=os.environ.get("SMOKE_BASE_URL", DEFAULT_BASE_URL))
    p.add_argument("--env-file",
                   help="where to read STUDENT_PASSCODE / ADMIN_PASSCODE if not in the environment (default ./.env)")
    p.add_argument("--covered-question", default=COVERED_QUESTION)
    p.add_argument("--off-topic-question", default=OFF_TOPIC_QUESTION)
    p.add_argument("--faq-question", default=FAQ_QUESTION)
    p.add_argument("--course-info-question", default=COURSE_INFO_QUESTION)
    p.add_argument("--no-admin", action="store_true",
                   help="skip the Settings status step even if ADMIN_PASSCODE is set")
    p.add_argument("--json", action="store_true", help="print the report as JSON")
    args = p.parse_args(argv)
    base_url = args.base_url.rstrip("/")
    questions = {"covered": args.covered_question, "off_topic": args.off_topic_question, "faq": args.faq_question,
                 "course_info": args.course_info_question}
    env_file = Path(args.env_file) if args.env_file else None
    passcode = read_passcode(env_file)
    admin_passcode = None if args.no_admin else read_admin_passcode(env_file)
    # X-FT-Source tags these rows as test traffic in the question log (Settings > Analytics hides them).
    with httpx.Client(base_url=base_url, timeout=httpx.Timeout(30.0, connect=10.0), transport=transport,
                      follow_redirects=False, headers={"X-FT-Source": "smoke"}) as client:
        report = run(client, passcode, questions, admin_passcode)
    print(render_json(report, base_url) if args.json else render(report, base_url))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())

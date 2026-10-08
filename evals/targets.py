"""Where answers come from: the twin in-process, or a running site over HTTP.

Both return the same shape:

    {"status": "ok" | "not_covered" | "retrieval_not_ready" | "error",
     "message": str | None,
     "segments": [{"n", "slide_id", "narration", "evidence"}],
     "follow_ups": [...], "narration_source": "llm" | "fallback" | "stored" | None,
     "top_score": float | None, "latency_ms": int}

`evidence` is the slide material the narration was written from (the same
payload `app/narration.py` sends the model). Only the in-process target can
fill it, because it reads the index; over HTTP it is None and judges score
groundedness as null.

In-process is the main mode: run it on the local build machine, which holds the
built index, with keys from the git-ignored `.env`. It calls `app.main.answer`, the same function `/api/ask` calls, with the
real retriever, so it reports `retrieval_not_ready` until Ben's hand-written
retrieval lands.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Protocol

import httpx

from app import prompts


class Target(Protocol):
    name: str

    def ask(self, question: str) -> dict[str, Any]: ...


def _result(status: str, message: str | None = None, **extra: Any) -> dict[str, Any]:
    out = {
        "status": status,
        "message": message,
        "segments": [],
        "follow_ups": [],
        "narration_source": None,
        "top_score": None,
        "latency_ms": 0,
    }
    out.update(extra)
    return out


def _segments(playlist: dict[str, Any], evidence_for: Callable[[dict[str, Any]], Any]) -> list[dict[str, Any]]:
    return [
        {
            "n": seg.get("n"),
            "slide_id": seg.get("slide_id"),
            "narration": seg.get("narration") or "",
            "evidence": evidence_for(seg),
        }
        for seg in playlist.get("segments") or []
    ]


class InProcessTarget:
    """Runs `app.main.answer` with the real content, retriever, embedder, and model.

    With `provider` and `model` it answers with that model instead of the active one (the same per-call
    override Settings > Evals uses), so one process can compare several models. Every answer records
    what answered (`outcome`, answer()'s kind), its tokens and estimated cost (`usage`), and, like a
    Settings run, a FAQ answer or a referral is shown to the judges in words.
    """

    def __init__(self, retriever=None, embedder=None, completer=None, content=None,
                 provider: str | None = None, model: str | None = None, searcher=None):
        from app import main

        self._main = main
        self.retriever = retriever or main.get_retriever()
        self.embedder = embedder or main.get_embedder()
        self.completer = completer or main.get_completer()
        self._content = content
        self.provider, self.model = provider, model
        self.searcher = searcher  # the web search call for "beyond the slides" answers (None: the app's own)
        self.name = f"in-process {provider}:{model}" if provider and model else "in-process"

    def content(self):
        if self._content is None:
            from app import storage

            self._content = storage.store.get_or_503()
        return self._content

    def ask(self, question: str) -> dict[str, Any]:
        import contextlib

        from fastapi import HTTPException

        from app import llm, usage
        from app.admin_evals import answer_usage, response_from

        started = time.monotonic()
        override = (llm.model_override(self.provider, self.model) if self.provider and self.model
                    else contextlib.nullcontext())
        try:
            content = self.content()
            # Counted as eval spend, not student narration (sticky: inner tags keep it).
            with override, usage.purpose("eval_generate", sticky=True), usage.tally() as spent:
                extra = {"searcher": self.searcher} if self.searcher is not None else {}
                playlist, info = self._main.answer(
                    question, None, content, self.retriever, self.embedder, self.completer, self.provider, self.model,
                    **extra,
                )
        except self._main.RetrievalNotReady:
            return _result("retrieval_not_ready", "retrieval not implemented yet")
        except HTTPException as exc:
            return _result("error", str(exc.detail), latency_ms=int((time.monotonic() - started) * 1000))
        latency = int((time.monotonic() - started) * 1000)
        out = response_from(playlist, info, content, latency)
        out = {**_result(out["status"]), **out}
        if self.provider and self.model:
            out["usage"] = answer_usage({"provider": self.provider, "model": self.model}, spent)
        elif info.get("provider") and info.get("model"):
            out["usage"] = answer_usage({"provider": info["provider"], "model": info["model"]}, spent)
        if info.get("errors"):
            out["narration_errors"] = [str(e)[:200] for e in info["errors"][:3]]
        if self.provider and self.completer is not None and getattr(self.completer, "__name__", "") == "direct_complete":
            from .judges import route_of

            via = route_of(self.provider, self.model or "")[0]
            if via != self.provider:
                out["via"] = via
        return out


class HttpTarget:
    """Asks a running site (local `vercel dev` or the deployed URL) through its public API.

    Logs in with the student passcode from FT_EVAL_PASSCODE (or STUDENT_PASSCODE).
    The site allows 5 questions a minute per visitor, so asks are spaced out.
    """

    name = "http"

    def __init__(self, base_url: str, passcode: str | None = None, spacing: float = 13.0,
                 client: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep):
        self.base_url = base_url.rstrip("/")
        self.passcode = passcode or os.environ.get("FT_EVAL_PASSCODE") or os.environ.get("STUDENT_PASSCODE")
        self.spacing = spacing
        self.client = client or httpx.Client(timeout=httpx.Timeout(90.0, connect=10.0))
        self.sleep = sleep
        self._logged_in = False
        self._last = 0.0

    def _login(self) -> None:
        if not self.passcode:
            raise RuntimeError("Set FT_EVAL_PASSCODE to the student passcode to evaluate over HTTP.")
        r = self.client.post(f"{self.base_url}/api/login", json={"passcode": self.passcode})
        if r.status_code != 204:
            raise RuntimeError(f"login failed with {r.status_code}")
        self._logged_in = True

    def ask(self, question: str) -> dict[str, Any]:
        if not self._logged_in:
            self._login()
        wait = self.spacing - (time.monotonic() - self._last)
        if self._last and wait > 0:
            self.sleep(wait)
        self._last = time.monotonic()
        started = time.monotonic()
        try:
            # Tagged so Settings > Analytics keeps eval questions out of student numbers.
            r = self.client.post(f"{self.base_url}/api/ask", json={"question": question},
                                 headers={"X-FT-Source": "eval"})
        except httpx.HTTPError as exc:
            return _result("error", type(exc).__name__)
        latency = int((time.monotonic() - started) * 1000)
        if r.status_code == 503 and "retrieval not implemented" in r.text:
            return _result("retrieval_not_ready", "retrieval not implemented yet", latency_ms=latency)
        if r.status_code != 200:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = r.text[:200]
            return _result("error", f"{r.status_code}: {detail}", latency_ms=latency)
        playlist = r.json()
        if not playlist.get("covered"):
            return _result("not_covered", latency_ms=latency)
        return _result(
            "ok",
            segments=_segments(playlist, lambda seg: None),
            follow_ups=playlist.get("follow_ups") or [],
            latency_ms=latency,
        )


BASELINE_PROMPT = "eval_baseline"
BASELINE_SYSTEM = prompts.default(BASELINE_PROMPT)  # the built-in default; Settings can edit it (app/prompts.py)


class BaselineTarget:
    """A generic chatbot with no course material: the bar the twin has to clear.

    One model call per question, no retrieval, no grounding check, no decline
    rule. Its answer comes back as a single segment with no evidence, so judges
    score groundedness as null and everything else as usual. Comparing this run
    with an in-process run on the same questions shows what the twin's slides,
    grounding, and refusals add (or cost).
    """

    def __init__(self, spec: str, judge_cls=None):
        from .judges import Judge

        self._llm = (judge_cls or Judge).parse_spec(spec)
        self.name = f"baseline {self._llm.name}"

    def ready(self) -> str | None:
        return self._llm.ready()

    def ask(self, question: str) -> dict[str, Any]:
        import json as _json

        from .judges import JudgeError
        from . import rubric

        started = time.monotonic()
        try:
            raw = self._llm._send(prompts.get(BASELINE_PROMPT), f"Student question: {question}")
            answer = str(rubric._extract_json(raw).get("answer") or "").strip()
        except (JudgeError, rubric.JudgementError, AttributeError, _json.JSONDecodeError) as exc:
            return _result("error", f"baseline failed: {str(exc)[:120]}")
        latency = int((time.monotonic() - started) * 1000)
        if not answer:
            return _result("error", "baseline returned an empty answer", latency_ms=latency)
        return _result(
            "ok",
            segments=[{"n": 1, "slide_id": "baseline", "narration": answer, "evidence": None}],
            narration_source="baseline",
            latency_ms=latency,
        )

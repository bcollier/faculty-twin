"""LLM judges: several models from different providers score the same answers.

A judge is named `provider:model`, for example `anthropic:claude-opus-5-5`,
`openai:gpt-6.1-sol`, or `openrouter:google/gemini-3.8-flash`. Calls reuse the
request builders and reply parsers in `app/llm.py` (same keys, same env vars),
but go straight to the provider: they do not count against the app's daily
model-call cap, which protects the live site, not offline evals.

Using judges from more than one provider matters: a model tends to rate its
own family's writing higher, and disagreement between judges is itself a
finding worth reading.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from app import eval_core, llm, usage

from . import rubric

MAX_TOKENS = 1600  # eleven scored dimensions plus a rationale since Oct 8
RETRIES = 3


def route_of(provider: str, model: str) -> tuple[str, str]:
    """Where a call really goes. `FT_EVAL_ROUTE_ANTHROPIC=openrouter` sends Claude calls through OpenRouter
    (the same model, e.g. claude-opus-5-5 -> anthropic/claude-opus-5.5), for when the direct Anthropic
    key cannot be used. The model keeps its name in results; each judgement and answer records `via`."""
    if provider == "anthropic" and os.environ.get("FT_EVAL_ROUTE_ANTHROPIC") == "openrouter":
        return "openrouter", "anthropic/" + re.sub(r"-(\d+)-(\d+)$", r"-\1.\2", model)
    return provider, model


class JudgeError(RuntimeError):
    """A judge or answering call failed. `retryable` False means asking again cannot help."""

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class ProviderOutages:
    """Providers that refused a call for billing reasons in this process. Once one is down, no more calls go
    to it for the rest of the run: its answers and judgements are marked `provider_error` instead."""

    def __init__(self) -> None:
        self._down: dict[str, str] = {}
        self._lock = threading.Lock()

    def mark(self, provider: str, reason: str) -> None:
        with self._lock:
            self._down.setdefault(provider, reason[:300])

    def reason(self, provider: str) -> str | None:
        with self._lock:
            return self._down.get(provider)

    def down(self) -> dict[str, str]:
        with self._lock:
            return dict(self._down)

    def clear(self) -> None:
        with self._lock:
            self._down.clear()


OUTAGES = ProviderOutages()


@dataclass
class Judge:
    """One judge model. Its calls go straight to the provider (not through the site's daily cap)."""

    provider: str
    model: str
    # Injectable for tests: (system, user) -> raw reply text.
    call: Callable[[str, str], str] | None = None
    client: httpx.Client | None = field(default=None, repr=False)

    @property
    def name(self) -> str:
        return f"{self.provider}:{self.model}"

    @classmethod
    def parse_spec(cls, spec: str) -> Judge:
        """A judge from "provider:model". Raises JudgeError for an unknown provider or a missing model."""
        provider, sep, model = spec.partition(":")
        if not sep or provider not in llm.PROVIDERS or not model:
            raise JudgeError(
                f"judge {spec!r} must look like provider:model, provider one of {', '.join(llm.PROVIDERS)}")
        return cls(provider, model)

    def ready(self) -> str | None:
        """Why this judge cannot run, or None if it can."""
        if self.call is not None:
            return None
        if not llm.key_configured(self.provider):
            return f"{llm.KEY_VARS[self.provider]} is not set"
        return None

    def _send(self, system: str, user: str, max_tokens: int = MAX_TOKENS,
              purpose: str | None = "eval_judge") -> str:
        """One provider call, metered for Settings > Analytics. Billing refusals mark the provider down."""
        if self.call is not None:
            return self.call(system, user)
        provider, model = route_of(self.provider, self.model)
        if OUTAGES.reason(provider):
            raise JudgeError(f"{eval_core.PROVIDER_ERROR}: {provider} is refusing calls ({OUTAGES.reason(provider)})",
                             retryable=False)
        req = llm.BUILDERS[provider](model, system, user, max_tokens)
        if provider != self.provider and self.model.startswith(llm._EFFORT_PREFIXES):
            req.body["reasoning"] = {"effort": "low"}  # what the direct Anthropic request sets (output_config.effort)
        client = self.client or httpx.Client(timeout=llm.TIMEOUT)
        try:
            resp = client.post(req.url, headers=req.headers, json=req.body)
        except httpx.HTTPError as exc:
            raise JudgeError(f"{self.name} request failed: {type(exc).__name__}") from exc
        finally:
            if self.client is None:
                client.close()
        if resp.status_code >= 400 and (resp.status_code == 402 or eval_core.is_billing_error(resp.text)):
            OUTAGES.mark(provider, f"returned {resp.status_code}: {resp.text[:160]}")
            raise JudgeError(f"{eval_core.PROVIDER_ERROR}: {provider} returned {resp.status_code}: {resp.text[:160]}",
                             retryable=False)
        if resp.status_code == 429 or resp.status_code >= 500:
            raise JudgeError(f"{self.name} returned {resp.status_code} (retryable)")
        if resp.status_code >= 400:
            # 400s other than 429 (bad key, unknown model, bad request) will not fix themselves.
            raise JudgeError(f"{self.name} returned {resp.status_code}: {resp.text[:200]}", retryable=False)
        data = resp.json()
        usage.record_llm(provider, model, data, purpose)  # Settings > Analytics spend; never raises
        return llm.PARSERS[provider](data)

    def judge(self, item: dict[str, Any], sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
        """Score one item. Retries transport errors and unparseable replies; never raises."""
        user = rubric.build_user_prompt(item)
        system = rubric.system_prompt()  # read once per item
        started = time.monotonic()

        def attempt() -> dict[str, Any]:
            """One judge call: the parsed judgement with the route taken and the tokens and time it used."""
            with usage.tally() as spent:
                raw = self._send(system, user)
            out = rubric.parse(raw)
            out["judge"] = self.name
            if route_of(self.provider, self.model)[0] != self.provider:
                out["via"] = route_of(self.provider, self.model)[0]
            out["usage"] = {"tokens_in": spent.tokens_in, "tokens_out": spent.tokens_out,
                            "seconds": round(time.monotonic() - started, 1)}
            return out

        result, last = _with_retries(attempt, (JudgeError, llm.LLMError, rubric.JudgementError, KeyError, ValueError),
                                     sleep, backoff=2.0)
        if result is not None:
            return result
        out = {"judge": self.name, "error": last}
        if last.startswith(eval_core.PROVIDER_ERROR):
            out[eval_core.PROVIDER_ERROR] = True  # says nothing about the answer: left out of scores, asked again later
        return out


def _with_retries(call: Callable[[], Any], errors: tuple[type[BaseException], ...], sleep: Callable[[float], None],
                  backoff: float) -> tuple[Any, str]:
    """(result, "") from the first attempt that succeeds, else (None, the last error message).

    Up to RETRIES attempts with a growing wait (backoff, 2 x backoff, ...). An error marked
    `retryable = False` (a billing refusal, a bad request) stops at once: asking again cannot help.
    """
    last = ""
    for attempt in range(RETRIES):
        try:
            return call(), ""
        except errors as exc:
            last = str(exc)
            if not getattr(exc, "retryable", True):
                break
            if attempt + 1 < RETRIES:
                sleep(backoff * (attempt + 1))
    return None, last


def direct_complete(system: str, user: str, max_tokens: int, provider: str | None = None,
                    model: str | None = None, client: httpx.Client | None = None, **_: Any) -> str:
    """A completer for `app.main.answer` in offline comparisons: straight to the provider, like the judges.

    It never takes a call from the live site's DAILY_LLM_CALL_CAP (an eval must not starve students), but
    every call is still metered under the current purpose (eval_generate). Retries rate limits and server
    errors; raises `llm.LLMError` otherwise, so narration falls back exactly as it does on the site.
    """
    if provider is None or model is None:
        provider, model = llm.current_override() or (provider, model)
    if provider is None or model is None:
        from app import settings_store

        provider, model = settings_store.llm_choice()
    judge = Judge(provider, model, client=client)
    result, last = _with_retries(lambda: judge._send(system, user, max_tokens, purpose=None),
                                 (JudgeError, llm.LLMError, KeyError, ValueError), time.sleep, backoff=3.0)
    if result is None:
        raise llm.LLMError(last or f"{provider} call failed")
    return result


def make_judge(spec: str) -> Any:
    """A judge from a CLI spec: `provider:model` for an LLM, or `jev` / `jev:<model>` for Jev."""
    if spec == "jev" or spec.startswith("jev:"):
        from .jev_judge import JevJudge

        return JevJudge(spec.partition(":")[2] or None)
    return Judge.parse_spec(spec)

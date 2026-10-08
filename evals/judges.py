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
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from app import llm, usage

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
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class Judge:
    provider: str
    model: str
    # Injectable for tests: (system, user) -> raw reply text.
    call: Callable[[str, str], str] | None = None
    client: httpx.Client | None = field(default=None, repr=False)

    @property
    def name(self) -> str:
        return f"{self.provider}:{self.model}"

    @classmethod
    def parse_spec(cls, spec: str) -> "Judge":
        provider, sep, model = spec.partition(":")
        if not sep or provider not in llm.PROVIDERS or not model:
            raise JudgeError(f"judge {spec!r} must look like provider:model, provider one of {', '.join(llm.PROVIDERS)}")
        return cls(provider, model)

    def ready(self) -> str | None:
        """Why this judge cannot run, or None if it can."""
        if self.call is not None:
            return None
        if not llm.key_configured(self.provider):
            return f"{llm.KEY_VARS[self.provider]} is not set"
        return None

    def _send(self, system: str, user: str, max_tokens: int = MAX_TOKENS, purpose: str | None = "eval_judge") -> str:
        if self.call is not None:
            return self.call(system, user)
        provider, model = route_of(self.provider, self.model)
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
        last = ""
        started = time.monotonic()
        for attempt in range(RETRIES):
            try:
                with usage.tally() as spent:
                    raw = self._send(system, user)
                out = rubric.parse(raw)
                out["judge"] = self.name
                if route_of(self.provider, self.model)[0] != self.provider:
                    out["via"] = route_of(self.provider, self.model)[0]
                out["usage"] = {"tokens_in": spent.tokens_in, "tokens_out": spent.tokens_out,
                                "seconds": round(time.monotonic() - started, 1)}
                return out
            except (JudgeError, llm.LLMError, rubric.JudgementError, KeyError, ValueError) as exc:
                last = str(exc)
                if not getattr(exc, "retryable", True):
                    break
                if attempt + 1 < RETRIES:
                    sleep(2.0 * (attempt + 1))
        return {"judge": self.name, "error": last}


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
    last = ""
    for attempt in range(RETRIES):
        try:
            return judge._send(system, user, max_tokens, purpose=None)
        except (JudgeError, llm.LLMError, KeyError, ValueError) as exc:
            last = str(exc)
            if not getattr(exc, "retryable", True):
                break
            if attempt + 1 < RETRIES:
                time.sleep(3.0 * (attempt + 1))
    raise llm.LLMError(last or f"{provider} call failed")


def make_judge(spec: str):
    """A judge from a CLI spec: `provider:model` for an LLM, or `jev` / `jev:<model>` for Jev."""
    if spec == "jev" or spec.startswith("jev:"):
        from .jev_judge import JevJudge

        return JevJudge(spec.partition(":")[2] or None)
    return Judge.parse_spec(spec)

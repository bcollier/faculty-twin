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

import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from app import llm

from . import rubric

MAX_TOKENS = 1200
RETRIES = 3


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

    def _send(self, system: str, user: str) -> str:
        if self.call is not None:
            return self.call(system, user)
        req = llm.BUILDERS[self.provider](self.model, system, user, MAX_TOKENS)
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
        return llm.PARSERS[self.provider](resp.json())

    def judge(self, item: dict[str, Any], sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
        """Score one item. Retries transport errors and unparseable replies; never raises."""
        user = rubric.build_user_prompt(item)
        last = ""
        for attempt in range(RETRIES):
            try:
                out = rubric.parse(self._send(rubric.SYSTEM_PROMPT, user))
                out["judge"] = self.name
                return out
            except (JudgeError, llm.LLMError, rubric.JudgementError, KeyError, ValueError) as exc:
                last = str(exc)
                if not getattr(exc, "retryable", True):
                    break
                if attempt + 1 < RETRIES:
                    sleep(2.0 * (attempt + 1))
        return {"judge": self.name, "error": last}


def make_judge(spec: str):
    """A judge from a CLI spec: `provider:model` for an LLM, or `jev` / `jev:<model>` for Jev."""
    if spec == "jev" or spec.startswith("jev:"):
        from .jev_judge import JevJudge

        return JevJudge(spec.partition(":")[2] or None)
    return Judge.parse_spec(spec)

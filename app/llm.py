"""Admin-switchable LLM providers behind one call: `complete_json(system, user, max_tokens) -> str`.

Three providers, plain httpx, no SDKs (keeps the Vercel bundle small):

- anthropic: Messages API, POST https://api.anthropic.com/v1/messages
  headers x-api-key, anthropic-version: 2023-06-01. The reply's `content` is a
  list of blocks; we join the `text` blocks (thinking blocks are skipped).
  `stop_reason == "refusal"` is treated as an error. Effort is set to "low"
  on models that accept it: narration is short and grounded, so speed wins.
  On models that support it we opt into server-side refusal fallbacks
  (`fallbacks: "default"`, beta header server-side-fallback-2026-07-01).
- openai: Chat Completions, POST https://api.openai.com/v1/chat/completions,
  `response_format: {"type": "json_object"}`, `max_completion_tokens`.
- openrouter: OpenAI-compatible, POST https://openrouter.ai/api/v1/chat/completions,
  `response_format: {"type": "json_object"}`, `max_tokens`, plus the optional
  HTTP-Referer / X-Title attribution headers. Model list from the public
  GET https://openrouter.ai/api/v1/models.

Which provider and model are active comes from the Supabase `settings` table
(30 s cache, see settings_store), falling back to LLM_PROVIDER / LLM_MODEL.
Keys stay in env vars; nothing here returns or logs them.

Per-request override: Settings > Evals runs the real `answer()` path with a
chosen generator model. `with model_override(provider, model):` sets a
context variable that `settings_store.llm_choice()` (and so every model call in
that request) reads first. It lives only in the request's own context, so the
saved setting does not change and no student request ever sees it.
"""

from __future__ import annotations

import json
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from . import config, usage

TIMEOUT = httpx.Timeout(45.0, connect=5.0)
PROVIDERS = ("anthropic", "openai", "openrouter")
KEY_VARS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "openrouter": "OPENROUTER_API_KEY"}

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_MODELS_URL = "https://api.openai.com/v1/models"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

# Short curated lists for the Settings model picker (free-text ids are allowed too).
CURATED = {
    "anthropic": [
        {"id": "claude-sonnet-5-5", "name": "Claude Sonnet 5.5 (default: fast, strong at grounded JSON)"},
        {"id": "claude-opus-5-5", "name": "Claude Opus 5.5"},
        {"id": "claude-fable-5-1", "name": "Claude Fable 5.1 (strong, slower: about 20 s per typed answer in a live test)"},
        {"id": "claude-haiku-4-5", "name": "Claude Haiku 4.5 (cheapest)"},
        {"id": "claude-sonnet-5", "name": "Claude Sonnet 5"},
    ],
    "openai": [
        {"id": "gpt-6.1-sol", "name": "GPT-6.1 Sol"},
        {"id": "gpt-6-luna", "name": "GPT-6 Luna (efficient)"},
        {"id": "gpt-6-astra", "name": "GPT-6 Astra (most capable)"},
    ],
    "openrouter": [],  # always fetched live
}

# Anthropic models that take output_config.effort, and those that take the
# "default" server-side refusal fallback.
_EFFORT_PREFIXES = ("claude-sonnet-5", "claude-opus-5", "claude-fable-5", "claude-opus-4-6", "claude-opus-4-7",
                    "claude-opus-4-8", "claude-sonnet-4-6")
_FALLBACK_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"}


class LLMError(RuntimeError):
    pass


_PROVIDER_ERROR = re.compile(r"^(\w+) returned (\d{3}): (.*)$", re.S)
_SECRETISH = re.compile(r"\b(?:(?:sk|pk|rk)[-_]|gh[pos]_|AKIA|AIza|eyJ)[A-Za-z0-9_\-]{8,}|\b[A-Za-z0-9_\-]{36,}\b")


def describe_error(error: Any, limit: int = 200) -> str:
    """A short, readable reason for a failed model call, safe for a log line.

    "anthropic returned 400: {json}" becomes "anthropic 400: Your credit balance is too low ...".
    Provider error bodies carry no keys, but anything key-shaped is masked anyway.
    """
    text = str(error or "").strip()
    m = _PROVIDER_ERROR.match(text)
    if m:
        provider, status, body = m.groups()
        message = body
        try:
            data = json.loads(body)
            err = data.get("error") if isinstance(data, dict) else None
            if isinstance(err, dict):
                message = str(err.get("message") or err.get("type") or body)
            elif isinstance(err, str):
                message = err
        except ValueError:  # often cut short upstream: take the message field if it is there
            found = re.search(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)', body)
            if found:
                message = found.group(1)
        text = f"{provider} {status}: {message}"
    elif isinstance(error, BaseException) and not isinstance(error, LLMError):
        text = f"{type(error).__name__}: {text}"
    text = _SECRETISH.sub("[masked]", re.sub(r"\s+", " ", text))
    return text[:limit]


# ---------------------------------------------------------------- per-request override

_override: ContextVar[tuple[str, str] | None] = ContextVar("ft_llm_override", default=None)


def current_override() -> tuple[str, str] | None:
    """(provider, model) set by `model_override` in this context, or None."""
    return _override.get()


@contextmanager
def model_override(provider: str, model: str):
    """Use this provider and model for every model call inside the block, in this context only."""
    if provider not in PROVIDERS:
        raise LLMError(f"Unknown provider {provider!r}")
    if not model:
        raise LLMError("A model id is needed")
    token = _override.set((provider, model))
    try:
        yield
    finally:
        _override.reset(token)


def key_configured(provider: str) -> bool:
    return bool(config.env(KEY_VARS[provider]))


def _key(provider: str) -> str:
    key = config.env(KEY_VARS.get(provider, ""))
    if not key:
        raise LLMError(f"{KEY_VARS.get(provider, provider)} is not set")
    return key


@dataclass
class Request:
    url: str
    headers: dict[str, str]
    body: dict[str, Any]


# ---------------------------------------------------------------- request builders

def build_anthropic(model: str, system: str, user: str, max_tokens: int) -> Request:
    headers = {
        "x-api-key": _key("anthropic"),
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if model.startswith(_EFFORT_PREFIXES):
        body["output_config"] = {"effort": "low"}
    if model in _FALLBACK_MODELS:
        headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
        body["fallbacks"] = "default"
    return Request(ANTHROPIC_URL, headers, body)


def build_openai(model: str, system: str, user: str, max_tokens: int) -> Request:
    headers = {"Authorization": f"Bearer {_key('openai')}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
        "max_completion_tokens": max_tokens,
    }
    return Request(OPENAI_URL, headers, body)


def build_openrouter(model: str, system: str, user: str, max_tokens: int) -> Request:
    headers = {
        "Authorization": f"Bearer {_key('openrouter')}",
        "Content-Type": "application/json",
        "X-Title": "Faculty Twin",
    }
    referer = config.env("PUBLIC_SITE_URL")
    if referer:
        headers["HTTP-Referer"] = referer
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
    }
    return Request(OPENROUTER_URL, headers, body)


BUILDERS: dict[str, Callable[[str, str, str, int], Request]] = {
    "anthropic": build_anthropic,
    "openai": build_openai,
    "openrouter": build_openrouter,
}


# ---------------------------------------------------------------- response parsers

def parse_anthropic(data: dict[str, Any]) -> str:
    if data.get("stop_reason") == "refusal":
        raise LLMError("The model declined this request")
    texts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    text = "".join(texts).strip()
    if not text:
        raise LLMError("The model returned no text")
    return text


def parse_chat(data: dict[str, Any]) -> str:
    try:
        choice = data["choices"][0]
        text = choice["message"].get("content") or ""
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise LLMError("The model response had no message") from exc
    if choice.get("finish_reason") == "content_filter":
        raise LLMError("The model declined this request")
    if not text.strip():
        raise LLMError("The model returned no text")
    return text.strip()


PARSERS = {"anthropic": parse_anthropic, "openai": parse_chat, "openrouter": parse_chat}


# ---------------------------------------------------------------- call

def complete_json(
    system: str,
    user: str,
    max_tokens: int,
    provider: str | None = None,
    model: str | None = None,
    client: httpx.Client | None = None,
) -> str:
    """Send one prompt to the active (or given) provider and return the raw text reply.

    The caller parses and validates the JSON; this only moves text.
    """
    if provider is None or model is None:
        from . import settings_store

        active_provider, active_model = settings_store.llm_choice()
        provider = provider or active_provider
        model = model or active_model
    if provider not in BUILDERS:
        raise LLMError(f"Unknown provider {provider!r}")
    req = BUILDERS[provider](model, system, user, max_tokens)
    from . import limits

    if not limits.take_llm_call():  # global daily cap, fails closed; narration falls back to notes
        raise LLMError("The daily model-call cap is reached (DAILY_LLM_CALL_CAP)")
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT)
    started = time.monotonic()
    try:
        resp = client.post(req.url, headers=req.headers, json=req.body)
    except httpx.HTTPError as exc:
        raise LLMError(f"{provider} request failed: {type(exc).__name__}") from exc
    finally:
        if own:
            client.close()
    if resp.status_code >= 400:
        raise LLMError(f"{provider} returned {resp.status_code}: {resp.text[:300]}")
    config.log.info("llm %s/%s answered in %.1fs", provider, model, time.monotonic() - started)
    try:
        data = resp.json()
    except ValueError as exc:  # a gateway's HTML or empty 200: model trouble, so callers fall back
        raise LLMError(f"{provider} returned a reply that is not JSON") from exc
    if not isinstance(data, dict):
        raise LLMError(f"{provider} returned an unexpected reply")
    usage.record_llm(provider, model, data)  # tokens in/out for Settings > Analytics; never raises
    return PARSERS[provider](data)


# ---------------------------------------------------------------- model lists

_openrouter_cache: tuple[float, list[dict[str, Any]]] | None = None


def list_models(provider: str, client: httpx.Client | None = None) -> dict[str, Any]:
    """Models for the Settings picker: `{provider, models: [{id, name, ...}], source}`.

    OpenRouter is always live (public endpoint, cached 10 minutes). Claude and
    OpenAI return the curated list, extended with the live list when a key is
    configured. Any live failure falls back to the curated list.
    """
    global _openrouter_cache
    if provider not in PROVIDERS:
        raise LLMError(f"Unknown provider {provider!r}")
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0))
    try:
        if provider == "openrouter":
            now = time.monotonic()
            if _openrouter_cache and now - _openrouter_cache[0] < 600:
                return {"provider": provider, "models": _openrouter_cache[1], "source": "live"}
            resp = client.get(OPENROUTER_MODELS_URL)
            if resp.status_code >= 400:
                raise LLMError(f"OpenRouter model list returned {resp.status_code}")
            models = sorted(
                (
                    {
                        "id": m["id"],
                        "name": m.get("name") or m["id"],
                        "context_length": m.get("context_length"),
                        "pricing": m.get("pricing"),
                    }
                    for m in resp.json().get("data", [])
                    if m.get("id")
                ),
                key=lambda m: m["id"],
            )
            _openrouter_cache = (now, models)
            return {"provider": provider, "models": models, "source": "live"}

        curated = list(CURATED[provider])
        if not key_configured(provider):
            return {"provider": provider, "models": curated, "source": "curated"}
        try:
            if provider == "anthropic":
                resp = client.get(
                    ANTHROPIC_MODELS_URL,
                    params={"limit": 100},
                    headers={"x-api-key": _key("anthropic"), "anthropic-version": "2023-06-01"},
                )
                live = [{"id": m["id"], "name": m.get("display_name") or m["id"]} for m in resp.json().get("data", [])]
            else:
                resp = client.get(OPENAI_MODELS_URL, headers={"Authorization": f"Bearer {_key('openai')}"})
                live = [
                    {"id": m["id"], "name": m["id"]}
                    for m in resp.json().get("data", [])
                    if str(m.get("id", "")).startswith(("gpt-", "o"))
                ]
            if resp.status_code >= 400:
                raise LLMError(str(resp.status_code))
        except (httpx.HTTPError, LLMError, ValueError, KeyError) as exc:
            config.log.warning("live %s model list failed: %s", provider, exc)
            return {"provider": provider, "models": curated, "source": "curated"}
        seen = {m["id"] for m in curated}
        merged = curated + sorted((m for m in live if m["id"] not in seen), key=lambda m: m["id"])
        return {"provider": provider, "models": merged, "source": "curated+live"}
    except httpx.HTTPError as exc:
        raise LLMError(f"Model list request failed: {type(exc).__name__}") from exc
    finally:
        if own:
            client.close()

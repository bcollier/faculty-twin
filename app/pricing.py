"""Price table for the spend estimate in Settings > Analytics.

Stored in the `settings` table under key `pricing` (editable on the Settings
page); these defaults apply until it is saved. Every default carries the page
it came from and the day it was read, and is marked `verify`: providers change
prices, so check before trusting a total. The estimate is tokens (or
characters) times this table. It is not a bill.

Units: models in USD per million input / output tokens; Voyage in USD per
million tokens; ElevenLabs in USD per thousand characters for the chosen plan;
edge-tts (the free Microsoft voices) costs nothing; Twilio texts (instructor alerts) in USD per
SMS segment, base price plus an average carrier fee. OpenRouter models missing
from the table use OpenRouter's own live price list when it has been loaded.
Web searches (beyond-the-slides answers, app/web_answer.py) cost USD per
1,000 searches per provider, on top of the tokens.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Optional

CHECKED = "2026-10-07"
SRC_ANTHROPIC = "https://platform.claude.com/docs/en/about-claude/pricing"
SRC_OPENAI = "https://platform.openai.com/docs/pricing"
SRC_OPENROUTER = "https://openrouter.ai/api/v1/models"
SRC_VOYAGE = "https://docs.voyageai.com/docs/pricing"
SRC_ELEVENLABS = "https://elevenlabs.io/pricing/api"
SRC_EDGE = "https://github.com/rany2/edge-tts"
SRC_TWILIO = "https://www.twilio.com/en-us/sms/pricing/us"
SRC_ANTHROPIC_SEARCH = "https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool"
SRC_OPENAI_SEARCH = "https://developers.openai.com/api/docs/pricing"
SRC_OPENROUTER_SEARCH = "https://openrouter.ai/docs/guides/features/server-tools/web-search"
SEARCH_CHECKED = "2026-10-08"


def _llm(provider: str, model: str, p_in: float, p_out: float, source: str) -> dict[str, Any]:
    return {"provider": provider, "model": model, "in": p_in, "out": p_out, "source": source,
            "checked": CHECKED, "verify": True}


DEFAULT_PRICING: dict[str, Any] = {
    "llm": [
        _llm("anthropic", "claude-sonnet-5-5", 2.0, 10.0, SRC_ANTHROPIC),
        _llm("anthropic", "claude-opus-5-5", 4.0, 20.0, SRC_ANTHROPIC),
        _llm("anthropic", "claude-fable-5-1", 10.0, 50.0, SRC_ANTHROPIC),
        _llm("anthropic", "claude-haiku-4-5", 1.0, 5.0, SRC_ANTHROPIC),
        _llm("anthropic", "claude-sonnet-5", 2.0, 10.0, SRC_ANTHROPIC),
        _llm("openai", "gpt-6.1-sol", 2.0, 10.0, SRC_OPENAI),
        _llm("openai", "gpt-6-luna", 0.10, 0.50, SRC_OPENAI),
        _llm("openai", "gpt-6-astra", 10.0, 50.0, SRC_OPENAI),
        _llm("openrouter", "anthropic/claude-sonnet-5.5", 2.0, 10.0, SRC_OPENROUTER),
        _llm("openrouter", "anthropic/claude-haiku-4.5", 1.0, 5.0, SRC_OPENROUTER),
        _llm("openrouter", "openai/gpt-6.1-sol", 2.0, 10.0, SRC_OPENROUTER),
        _llm("openrouter", "openai/gpt-6-luna", 0.10, 0.50, SRC_OPENROUTER),
    ],
    "embed": [
        {"provider": "voyage", "model": "voyage-3.5", "per_mtok": 0.06, "source": SRC_VOYAGE, "checked": CHECKED,
         "verify": True, "note": "Older model on Voyage's page: no free-token allowance."},
        {"provider": "voyage", "model": "voyage-4", "per_mtok": 0.06, "source": SRC_VOYAGE, "checked": CHECKED,
         "verify": True, "note": "First 200M tokens free per account."},
    ],
    # USD per 1,000 web searches, on top of tokens (search results are billed as input tokens).
    "web_search": [
        {"provider": "anthropic", "per_1k": 10.0, "source": SRC_ANTHROPIC_SEARCH, "checked": SEARCH_CHECKED,
         "verify": True, "note": "$10 per 1,000 searches; failed searches are not billed."},
        {"provider": "openai", "per_1k": 10.0, "source": SRC_OPENAI_SEARCH, "checked": SEARCH_CHECKED,
         "verify": True, "note": "Reasoning models (GPT-6.1 Sol); non-reasoning models are $25 per 1,000."},
        {"provider": "openrouter", "per_1k": 7.0, "source": SRC_OPENROUTER_SEARCH, "checked": SEARCH_CHECKED,
         "verify": True, "note": "Exa engine (what web answers use): $7 per 1,000 requests; native engines are "
                                 "passed through from the model's provider."},
    ],
    "tts": {
        "elevenlabs_plan": "creator",
        # eleven_multilingual_v2: the same flat price per 1K characters on every plan (overage included).
        "elevenlabs_per_1k_chars": {"free": 0.08, "starter": 0.08, "creator": 0.08, "pro": 0.08, "scale": 0.08,
                                    "business": 0.08},
        "edge_per_1k_chars": 0.0,
        "source": SRC_ELEVENLABS,
        "edge_source": SRC_EDGE,
        "checked": CHECKED,
        "verify": True,
        "note": "Multilingual v2. Plans include monthly characters (Creator: 275,000), so the real bill can be "
                "lower than this estimate until the allowance is used up.",
    },
    # Added Oct 8 (instructor alerts). Twilio bills per outbound segment (a 300-character alert is 2).
    "sms": {
        "provider": "twilio",
        "per_segment": 0.0083,
        "carrier_fee_per_segment": 0.005,
        "source": SRC_TWILIO,
        "checked": "2026-10-08",
        "verify": True,
        "note": "US base price per outbound segment (local 10DLC or toll-free). Carrier fees vary by network "
                "(AT&T $0.0035, T-Mobile $0.0045, Verizon $0.005); the highest is used. The phone number's "
                "monthly fee ($1.15 local, $2.15 toll-free) is not included.",
    },
}

PROVIDERS = ("anthropic", "openai", "openrouter")
PLANS = ("free", "starter", "creator", "pro", "scale", "business")
MODEL_RE = re.compile(r"^[A-Za-z0-9._:/~\-]{1,200}$")
MAX_PRICE = 1000.0
MAX_ROWS = 200


class BadPricing(ValueError):
    """A price table that does not validate; the message is safe to show in Settings."""


def defaults() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_PRICING)


def _price(value: Any, what: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise BadPricing(f"{what} must be a number.") from exc
    if not 0 <= out <= MAX_PRICE:
        raise BadPricing(f"{what} must be between 0 and {MAX_PRICE:.0f}.")
    return out


def _text(value: Any, limit: int = 300) -> Optional[str]:
    return None if value in (None, "") else str(value)[:limit]


def validate(raw: Any) -> dict[str, Any]:
    """Check a price table sent by the Settings page and return the clean copy to store."""
    if not isinstance(raw, dict):
        raise BadPricing("The price table must be an object.")
    out: dict[str, Any] = {"llm": [], "embed": [], "tts": {}, "sms": {}, "web_search": []}
    rows = raw.get("llm") or []
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise BadPricing(f"llm must be a list of at most {MAX_ROWS} rows.")
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise BadPricing("Each model price must be an object.")
        provider, model = str(row.get("provider") or ""), str(row.get("model") or "").strip()
        if provider not in PROVIDERS:
            raise BadPricing("provider must be anthropic, openai, or openrouter.")
        if not MODEL_RE.match(model):
            raise BadPricing(f"The model id {model[:40]!r} does not look right.")
        if (provider, model) in seen:
            continue
        seen.add((provider, model))
        out["llm"].append({
            "provider": provider, "model": model,
            "in": _price(row.get("in"), f"{model} input price"),
            "out": _price(row.get("out"), f"{model} output price"),
            "source": _text(row.get("source")), "checked": _text(row.get("checked"), 20),
            "verify": bool(row.get("verify", False)),
        })
    embeds = raw.get("embed") or []
    if not isinstance(embeds, list) or len(embeds) > 20:
        raise BadPricing("embed must be a list of at most 20 rows.")
    for row in embeds:
        if not isinstance(row, dict):
            raise BadPricing("Each embedding price must be an object.")
        model = str(row.get("model") or "").strip()
        if not MODEL_RE.match(model):
            raise BadPricing("An embedding model id does not look right.")
        out["embed"].append({
            "provider": "voyage", "model": model, "per_mtok": _price(row.get("per_mtok"), f"{model} price"),
            "source": _text(row.get("source")), "checked": _text(row.get("checked"), 20),
            "verify": bool(row.get("verify", False)), "note": _text(row.get("note")),
        })
    searches = raw.get("web_search") or []
    if not isinstance(searches, list) or len(searches) > 10:
        raise BadPricing("web_search must be a list of at most 10 rows.")
    for row in searches:
        if not isinstance(row, dict):
            raise BadPricing("Each web search price must be an object.")
        provider = str(row.get("provider") or "")
        if provider not in PROVIDERS:
            raise BadPricing("A web search price needs provider anthropic, openai, or openrouter.")
        if any(r["provider"] == provider for r in out["web_search"]):
            continue
        out["web_search"].append({
            "provider": provider, "per_1k": _price(row.get("per_1k"), f"{provider} web search price"),
            "source": _text(row.get("source")), "checked": _text(row.get("checked"), 20),
            "verify": bool(row.get("verify", False)), "note": _text(row.get("note")),
        })
    tts = raw.get("tts") or {}
    if not isinstance(tts, dict):
        raise BadPricing("tts must be an object.")
    plan = str(tts.get("elevenlabs_plan") or "creator")
    if plan not in PLANS:
        raise BadPricing(f"The ElevenLabs plan must be one of {', '.join(PLANS)}.")
    per = tts.get("elevenlabs_per_1k_chars") or {}
    if not isinstance(per, dict):
        raise BadPricing("elevenlabs_per_1k_chars must be an object.")
    default_per = DEFAULT_PRICING["tts"]["elevenlabs_per_1k_chars"]
    out["tts"] = {
        "elevenlabs_plan": plan,
        "elevenlabs_per_1k_chars": {p: _price(per.get(p, default_per[p]), f"ElevenLabs {p} price") for p in PLANS},
        "edge_per_1k_chars": _price(tts.get("edge_per_1k_chars", 0.0), "edge-tts price"),
        "source": _text(tts.get("source")), "edge_source": _text(tts.get("edge_source")),
        "checked": _text(tts.get("checked"), 20), "verify": bool(tts.get("verify", False)),
        "note": _text(tts.get("note"), 500),
    }
    sms = raw.get("sms") or {}
    if not isinstance(sms, dict):
        raise BadPricing("sms must be an object.")
    default_sms = DEFAULT_PRICING["sms"]
    out["sms"] = {
        "provider": "twilio",
        "per_segment": _price(sms.get("per_segment", default_sms["per_segment"]), "Twilio price per segment"),
        "carrier_fee_per_segment": _price(sms.get("carrier_fee_per_segment", default_sms["carrier_fee_per_segment"]),
                                          "Twilio carrier fee per segment"),
        "source": _text(sms.get("source", default_sms["source"])), "checked": _text(sms.get("checked"), 20),
        "verify": bool(sms.get("verify", False)), "note": _text(sms.get("note"), 500),
    }
    return out


def current(stored: Any = None) -> dict[str, Any]:
    """The saved table (if it validates) over the defaults: a default row the table lacks is kept."""
    if stored is None:
        from . import settings_store

        stored = settings_store.get("pricing")
    table = defaults()
    if not stored:
        table["saved"] = False
        return table
    try:
        clean = validate(stored)
    except BadPricing:
        table["saved"] = False
        table["invalid_saved"] = True
        return table
    saved_llm = {(r["provider"], r["model"]) for r in clean["llm"]}
    clean["llm"] += [r for r in table["llm"] if (r["provider"], r["model"]) not in saved_llm]
    saved_embed = {r["model"] for r in clean["embed"]}
    clean["embed"] += [r for r in table["embed"] if r["model"] not in saved_embed]
    if not (isinstance(stored, dict) and stored.get("sms")):  # a table saved before texts were priced
        clean["sms"] = table["sms"]
    saved_search = {r["provider"] for r in clean["web_search"]}
    clean["web_search"] += [r for r in table["web_search"] if r["provider"] not in saved_search]
    clean["saved"] = True
    return clean


# ---------------------------------------------------------------- math

def llm_price(table: dict[str, Any], provider: str, model: str,
              live: Optional[dict[str, dict[str, Any]]] = None) -> Optional[tuple[float, float, str]]:
    """(USD per 1M input, per 1M output, source) for one model, or None when unknown.

    `live` is OpenRouter's model list by id (prices per token), used only for
    OpenRouter models not in the table.
    """
    for row in table.get("llm", []):
        if row["provider"] == provider and row["model"] == model:
            return float(row["in"]), float(row["out"]), row.get("source") or "table"
    if provider == "openrouter" and live and model in live:
        p = live[model].get("pricing") or {}
        try:
            p_in, p_out = float(p.get("prompt")) * 1_000_000, float(p.get("completion")) * 1_000_000
        except (TypeError, ValueError):
            return None
        if p_in >= 0 and p_out >= 0:
            return p_in, p_out, "OpenRouter live price"
    return None


def llm_cost(table: dict[str, Any], provider: str, model: str, tokens_in: int, tokens_out: int,
             live: Optional[dict[str, dict[str, Any]]] = None) -> Optional[float]:
    price = llm_price(table, provider, model, live)
    if price is None:
        return None
    return tokens_in / 1_000_000 * price[0] + tokens_out / 1_000_000 * price[1]


def search_cost(table: dict[str, Any], provider: str, searches: int) -> Optional[float]:
    """USD for `searches` web searches on this provider, or None when the table has no price for it."""
    for row in table.get("web_search", []):
        if row["provider"] == provider:
            return searches / 1000 * float(row["per_1k"])
    return None


def embed_cost(table: dict[str, Any], model: str, tokens: int) -> Optional[float]:
    for row in table.get("embed", []):
        if row["model"] == model:
            return tokens / 1_000_000 * float(row["per_mtok"])
    return None


def sms_cost(table: dict[str, Any], segments: int) -> float:
    """Twilio texts: segments times (base price + carrier fee) per segment."""
    sms = table.get("sms") or DEFAULT_PRICING["sms"]
    return segments * (float(sms.get("per_segment") or 0.0) + float(sms.get("carrier_fee_per_segment") or 0.0))


def tts_cost(table: dict[str, Any], tier: str, chars: int) -> float:
    tts = table.get("tts") or {}
    if tier == "free":
        per = float(tts.get("edge_per_1k_chars") or 0.0)
    else:
        plan = tts.get("elevenlabs_plan") or "creator"
        per = float((tts.get("elevenlabs_per_1k_chars") or {}).get(plan, 0.0))
    return chars / 1000 * per

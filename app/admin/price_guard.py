"""The model price guard: refuse OpenRouter models priced over the ceiling, warn on unlisted Claude or OpenAI ids.

Used by Settings > Model (saving and testing a model, in `app/admin/settings.py`) and by
Settings > Evals (`app/admin_evals.py`, before a run starts). It registers no routes.
"""

from __future__ import annotations

from fastapi import HTTPException

from .. import config, llm


def max_price_per_mtok() -> dict[str, float]:
    """The OpenRouter price ceiling per million tokens (prompt, completion): env overrides, else the defaults."""
    out = dict(config.DEFAULT_MAX_PRICE_PER_MTOK)
    for key, var in (("prompt", "LLM_MAX_PROMPT_PRICE_PER_MTOK"), ("completion", "LLM_MAX_COMPLETION_PRICE_PER_MTOK")):
        raw = config.env(var)
        if raw:
            try:
                out[key] = float(raw)
            except ValueError:
                config.log.warning("%s is not a number; using %s", var, out[key])
    return out


def check_model_price(provider: str, model: str) -> None:
    """Refuse OpenRouter models priced above the ceiling (o1-pro class models cost 10-60x more).

    OpenRouter publishes prices per token; the check fails closed when the
    price cannot be read. Claude and OpenAI have no price API, so ids outside
    the curated list get a warning in the settings view instead.
    """
    if provider != "openrouter":
        return
    try:
        listing = llm.list_models("openrouter")
    except llm.LLMError as exc:
        raise HTTPException(
            400, "Could not check this model's price on OpenRouter right now. Try again shortly.") from exc
    found = next((m for m in listing.get("models", []) if m.get("id") == model), None)
    if found is None:
        raise HTTPException(400, "OpenRouter does not list that model id.")
    pricing = found.get("pricing") or {}
    try:
        prompt = float(pricing.get("prompt")) * 1_000_000
        completion = float(pricing.get("completion")) * 1_000_000
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            400, "OpenRouter does not publish a fixed price for that model, so it cannot be used.") from exc
    if prompt < 0 or completion < 0:
        raise HTTPException(400, "That model has a variable price (a router), so it cannot be used.")
    ceiling = max_price_per_mtok()
    if prompt > ceiling["prompt"] or completion > ceiling["completion"]:
        raise HTTPException(
            400,
            f"{model} costs ${prompt:.2f} in / ${completion:.2f} out per million tokens, over the limit of "
            f"${ceiling['prompt']:.2f} / ${ceiling['completion']:.2f}. Pick a cheaper model, or raise "
            "LLM_MAX_PROMPT_PRICE_PER_MTOK / LLM_MAX_COMPLETION_PRICE_PER_MTOK in Vercel.",
        )


def model_warning(provider: str, model: str) -> str | None:
    """A warning for a Claude or OpenAI model id that is not on the curated list, else None."""
    if provider in ("anthropic", "openai") and model not in {m["id"] for m in llm.CURATED[provider]}:
        return "This model id is not on the curated list. Check its price before students use it."
    return None

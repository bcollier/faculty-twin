"""The voice picker: every voice Settings can choose, in groups, and a preview of the free voices.

Serves Settings > Voice (the list and the play buttons). Saving the choice goes through
`PUT /api/admin/settings` in `app/admin/settings.py`. Every route needs the admin cookie.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from .. import auth, config, edge_voice, limits, settings_store, speech, voices

router = APIRouter(prefix="/api/admin")

VOICE_GROUPS = {
    "clone": {
        "label": "My voice clone",
        "cost": "ElevenLabs: costs credits per character.",
        "costs_money": True,
        "student_label": voices.CLONE_LABEL,
    },
    "elevenlabs": {
        "label": "ElevenLabs voices",
        "cost": "ElevenLabs: costs credits per character.",
        "costs_money": True,
        "student_label": voices.STOCK_LABEL,
    },
    "free": {
        "label": "Free Microsoft voices",
        "cost": "Free: no key, no cost (Microsoft neural voices through edge-tts).",
        "costs_money": False,
        "student_label": voices.STOCK_LABEL,
    },
}


def preview_path(setting: str) -> str:
    return f"/api/admin/voice-preview?voice={setting}"


def _eleven_voices() -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    """(clones, stock voices, error) from ElevenLabs; the default voice first among the clones."""
    default_id = config.env("ELEVENLABS_VOICE_ID")
    clone: list[dict[str, Any]] = []
    stock: list[dict[str, Any]] = []
    eleven_error = None
    if not config.env("ELEVENLABS_API_KEY"):
        eleven_error = "ELEVENLABS_API_KEY is not set, so only the free voices and captions are available."
    else:
        try:
            for v in speech.list_voices():
                if not v.get("voice_id"):
                    continue
                entry = {
                    "voice_id": f"eleven:{v['voice_id']}",
                    "name": v.get("name") or v["voice_id"],
                    "category": v.get("category"),
                    "preview_url": v.get("preview_url"),
                    "is_default": v["voice_id"] == default_id,
                }
                (clone if speech.is_clone(v) else stock).append(entry)
        except speech.VoiceError as exc:
            eleven_error = f"Could not load ElevenLabs voices: {exc}"
    clone.sort(key=lambda e: (not e["is_default"], str(e["name"]).lower()))
    stock.sort(key=lambda e: str(e["name"]).lower())
    return clone, stock, eleven_error


@router.get("/voices")
def list_voice_options(_: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    """Every voice Settings can pick, in three groups: my clone, ElevenLabs stock, free Microsoft.

    Ids are what `PUT /api/admin/settings {voice_id}` takes. "none" (captions
    only) and null (server default) are offered by the page itself.
    """
    clone, stock, eleven_error = _eleven_voices()
    free = [
        {
            "voice_id": f"edge:{short}",
            "name": name,
            "category": "free",
            "description": desc,
            "preview_url": preview_path(f"edge:{short}"),
            "is_default": False,
        }
        for short, (name, desc) in edge_voice.FREE_VOICES.items()
    ]
    groups = [
        {"id": gid, **VOICE_GROUPS[gid], "voices": items}
        for gid, items in (("clone", clone), ("elevenlabs", stock), ("free", free))
    ]
    return {
        "groups": groups,
        "voices": clone + stock + free,
        "elevenlabs_error": eleven_error,
        "free_voice_default": f"edge:{edge_voice.DEFAULT_FREE_VOICE}",
        "preview_text": edge_voice.PREVIEW_TEXT,
    }


@router.get("/voice-preview")
async def voice_preview(
    voice: str = Query(..., max_length=120), _: auth.Session = Depends(auth.require_admin)
) -> Response:
    """One fixed sentence (server-side text, never caller-supplied) in a free voice, cached in memory.

    ElevenLabs voices use the `preview_url` from their own voice list instead,
    which costs no characters.
    """
    try:
        parsed = voices.parse(voice)
    except voices.BadVoice as exc:
        raise HTTPException(400, str(exc)) from exc
    if not parsed or parsed[0] != voices.EDGE:
        raise HTTPException(400, "Previews here are for the free Microsoft voices (edge:...).")
    name = parsed[1]
    known = await edge_voice.is_known_voice(name)
    if known is None:
        raise HTTPException(502, "Could not check that voice name with Microsoft right now.")
    if not known:
        raise HTTPException(400, f"Microsoft has no voice named {name}.")
    if edge_voice.preview_cached(name) is None and not limits.take_voice_chars(
        len(edge_voice.PREVIEW_TEXT), settings_store.daily_free_voice_char_cap(), pool="free"
    ):
        raise HTTPException(429, "The free voices have reached today's limit.")
    try:
        data = await edge_voice.preview(name)
    except edge_voice.FreeVoiceError as exc:
        config.log.warning("voice preview failed: %s", exc)
        raise HTTPException(502, "The free voice service did not respond. Try again.") from exc
    return Response(data, media_type="audio/mpeg", headers={"Cache-Control": "private, max-age=86400"})

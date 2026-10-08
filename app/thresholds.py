"""Answer thresholds: the effective slide and course-info cutoffs, and the Settings routes that tune them.

The slide threshold's code default is Ben's hand-chosen `NOT_COVERED_THRESHOLD`
in app/retrieval.py (this module only reads it). Settings can override either
threshold (docs/SPEC.md, Settings page, "Answer thresholds"):

- slide: `settings.slide_threshold`, else `retrieval.NOT_COVERED_THRESHOLD`
- info:  `settings.info_threshold`, else env `INFO_THRESHOLD`, else 0.55

Both are read through the settings cache (30 s) on every question, never at
import, so a change reaches every warm function within 30 seconds. Each change
is appended to `settings.threshold_history` (last 20, newest first).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import auth, config, retrieval, settings_store, supa

MIN, MAX = 0.30, 0.90
DEFAULT_INFO = 0.55
HISTORY_KEY = "threshold_history"
HISTORY_LEN = 20
SLIDE_KEY, INFO_KEY = "slide_threshold", "info_threshold"


def _valid(raw: Any) -> Optional[float]:
    """A stored override as a float in range, or None (missing or unusable values are ignored)."""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or not MIN <= value <= MAX:
        config.log.warning("ignoring threshold override %r (outside %s to %s)", raw, MIN, MAX)
        return None
    return value


def slide_default() -> Optional[float]:
    return retrieval.NOT_COVERED_THRESHOLD


def info_default() -> tuple[float, str]:
    """(value, source) without any Settings override: env INFO_THRESHOLD, else 0.55."""
    raw = config.env("INFO_THRESHOLD")
    if raw is None:
        return DEFAULT_INFO, "code"
    try:
        return float(raw), "env"
    except ValueError:
        config.log.warning("INFO_THRESHOLD is not a number; using %s", DEFAULT_INFO)
        return DEFAULT_INFO, "code"


def slide_effective() -> tuple[Optional[float], str]:
    override = _valid(settings_store.get(SLIDE_KEY))
    if override is not None:
        return override, "settings"
    return slide_default(), "code"


def info_effective() -> tuple[float, str]:
    override = _valid(settings_store.get(INFO_KEY))
    if override is not None:
        return override, "settings"
    return info_default()


def slide_threshold() -> Optional[float]:
    return slide_effective()[0]


def info_threshold() -> float:
    return info_effective()[0]


def history() -> list[dict[str, Any]]:
    raw = settings_store.get(HISTORY_KEY)
    return [h for h in raw if isinstance(h, dict)][:HISTORY_LEN] if isinstance(raw, list) else []


def view() -> dict[str, Any]:
    slide, slide_source = slide_effective()
    info, info_source = info_effective()
    return {
        "slide": {
            "value": slide,
            "default": slide_default(),
            "source": slide_source,
            "override": _valid(settings_store.get(SLIDE_KEY)),
        },
        "info": {
            "value": info,
            "default": info_default()[0],
            "default_source": info_default()[1],
            "source": info_source,
            "override": _valid(settings_store.get(INFO_KEY)),
        },
        "min": MIN,
        "max": MAX,
        "history": history(),
    }


# ---------------------------------------------------------------- Settings routes

router = APIRouter(prefix="/api/admin")


class ThresholdsBody(BaseModel):
    slide_threshold: Optional[float] = None
    info_threshold: Optional[float] = None


def _check(name: str, value: Optional[float]) -> Optional[float]:
    if value is None:
        return None  # reset to the default
    if not math.isfinite(value) or not MIN <= value <= MAX:
        raise HTTPException(400, f"{name} must be between {MIN:.2f} and {MAX:.2f}.")
    return round(value, 3)


@router.get("/thresholds")
def get_thresholds(_: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    return view()


@router.put("/thresholds")
def put_thresholds(body: ThresholdsBody, _: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    sent = body.model_fields_set & {SLIDE_KEY, INFO_KEY}
    if not sent:
        raise HTTPException(400, "Nothing to save.")
    labels = {SLIDE_KEY: "The slide threshold", INFO_KEY: "The course-info threshold"}
    wanted = {key: _check(labels[key], getattr(body, key)) for key in sorted(sent)}
    before = {SLIDE_KEY: slide_effective()[0], INFO_KEY: info_effective()[0]}
    current = {key: _valid(settings_store.get(key)) for key in wanted}
    changed = {key: value for key, value in wanted.items() if value != current[key]}
    if not changed:
        return view()
    try:
        settings_store.put(changed)
        after = {SLIDE_KEY: slide_effective(), INFO_KEY: info_effective()}
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entries = [
            {"at": now, "who": "admin", "setting": key, "old": before[key], "new": after[key][0], "source": after[key][1]}
            for key in changed
        ]
        settings_store.put({HISTORY_KEY: (entries + history())[:HISTORY_LEN]})
    except supa.SupabaseError as exc:
        config.log.warning("threshold save failed: %s", exc)
        raise HTTPException(502, "The database call failed. Check that supabase/schema.sql has been run.") from exc
    return view()

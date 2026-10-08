"""Settings > Student alerts (admin cookie): the switch, the daily cap, the masked destination, a test text,
and the recent alerts. See docs/SPEC.md, "Instructor alerts", and app/alerts.py.

Never returns a Twilio token, the full phone number, a visitor id, or an address: the
destination is shown as its last 4 digits and each variable only as set or not set.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import alerts, auth, config, limits, settings_store, supa

router = APIRouter(prefix="/api/admin/alerts")

# What Settings may show of a record: no signature internals beyond what Ben needs.
PUBLIC_FIELDS = ("id", "at", "kind", "course", "course_source", "type", "item", "item_url", "match_score",
                 "confidence", "keyword", "strong", "classifier", "quote", "message", "reports", "repeats", "status",
                 "twilio", "segments")


def _public(record: dict[str, Any]) -> dict[str, Any]:
    out = {k: record.get(k) for k in PUBLIC_FIELDS}
    twilio = out.get("twilio") if isinstance(out.get("twilio"), dict) else {}
    out["twilio"] = {k: twilio.get(k) for k in ("sid", "status", "error_code", "error")}
    return out


def view() -> dict[str, Any]:
    cfg = alerts.twilio_config()
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        records = alerts.with_counts(alerts.recent(alerts.LIST_LIMIT))
        error = None
    except supa.SupabaseError as exc:
        config.log.warning("alert list failed: %s", exc)
        records, error = [], "Could not read the alerts from storage."
    return {
        "enabled": alerts.enabled(),
        "daily_cap": alerts.daily_cap(),
        "max_daily_cap": alerts.MAX_DAILY_CAP,
        "sent_today": limits.read_counter(alerts.sent_key(day)),
        "tests_today": limits.read_counter(alerts.test_key(day)),
        "max_tests_per_day": alerts.MAX_TESTS_PER_DAY,
        "configured": {name: bool(config.env(name)) for name in alerts.ENV_VARS},  # booleans only
        "ready": cfg.ready,
        "problems": cfg.problems,
        "to_masked": alerts.masked_destination(),
        "from_kind": "messaging_service" if cfg.sender.startswith("MG") else ("number" if cfg.sender else None),
        "alerts": [_public(r) for r in records],
        "error": error,
    }


class AlertSettingsBody(BaseModel):
    enabled: Optional[bool] = None
    daily_cap: Optional[int] = None


@router.get("")
def get_alerts(_: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    return view()


@router.put("")
def put_alerts(body: AlertSettingsBody, _: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if body.enabled is not None:
        values["alerts_enabled"] = bool(body.enabled)
    if body.daily_cap is not None:
        if not 0 <= body.daily_cap <= alerts.MAX_DAILY_CAP:
            raise HTTPException(400, f"daily_cap must be from 0 to {alerts.MAX_DAILY_CAP}.")
        values["alert_daily_cap"] = int(body.daily_cap)
    if not values:
        raise HTTPException(400, "Nothing to change.")
    try:
        settings_store.put(values)
    except supa.SupabaseError as exc:
        config.log.warning("alert settings save failed: %s", exc)
        raise HTTPException(502, "Could not save the alert settings. Nothing changed.") from exc
    return view()


@router.post("/test")
def send_test(_: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    """Send the fixed test text. Counts against the daily cap; at most 5 a day."""
    cfg = alerts.twilio_config()
    if not cfg.ready:
        raise HTTPException(400, "Twilio is not set up yet: " + "; ".join(cfg.problems) + ".")
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ok, _ = limits.increment(alerts.test_key(day), 1, cap=alerts.MAX_TESTS_PER_DAY, fail_open=False)
    if not ok:
        raise HTTPException(429, f"That is {alerts.MAX_TESTS_PER_DAY} test texts today. Try again tomorrow.")
    if not alerts.take_send():
        raise HTTPException(429, "Today's text cap is reached (Settings > Student alerts). No text was sent.")
    record = alerts.send_test()
    if record["status"] != "sent":
        code = (record.get("twilio") or {}).get("error_code")
        raise HTTPException(502, f"Twilio did not take the text (code {code}): "
                                 f"{(record.get('twilio') or {}).get('error') or 'no detail'}")
    return {"status": record["status"], "alert": _public({**record, "repeats": 0})}

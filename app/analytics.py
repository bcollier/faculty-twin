"""Settings > Analytics: usage, estimated spend, topics, engagement, and model scores.

Routes (all need the admin cookie; POST/PUT also pass the Origin check in app/main.py):

  GET  /api/admin/analytics?days=7|30|90       everything the section draws, in one payload
  GET  /api/admin/analytics/pricing            the price table (saved, or the researched defaults)
  PUT  /api/admin/analytics/pricing            save an edited price table
  GET  /api/admin/analytics/topics             past "Label topics" runs, newest first
  POST /api/admin/analytics/topics {limit}     one cheap model call that groups recent questions into themes
  GET  /api/admin/analytics/export.csv?days=   the question log as CSV

Privacy: rows carry scrubbed question text and scores only. No visitor id,
cookie, or address is stored in question_log, and nothing here returns one.
The labeling prompt gets scrubbed question text only (scrubbed again here),
capped at LABEL_MAX_QUESTIONS questions and LABEL_DAILY_CAP calls a day; the
model refers to example questions by number, so it cannot invent one.

Data sources: the question_log table (works before the analytics migration:
missing columns are simply empty), the usage counters (app/usage.py), the
price table (app/pricing.py), and the eval runs in the private bucket
(`evals/index.json`, `evals/runs/<id>/results.jsonl`).
"""

from __future__ import annotations

import csv
import io
import json
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from . import auth, config, faq, limits, llm, pricing, privacy, settings_store, storage, supa, usage

router = APIRouter(prefix="/api/admin/analytics")

RANGES = (7, 30, 90)
MAX_LOG_ROWS = 10000
MAX_SERIES = 7  # spend chart: the top seven series, the rest fold into "Other"
TOP_N = 20
CONTENT_KINDS = ("course_content", "stored_topic")
SLIDE_ID_RE = re.compile(r"^(\d{5})-s(\d{2})-(\d{3})$")
LOCAL_TZ = "America/New_York"

LABEL_MAX_QUESTIONS = 300
LABEL_DEFAULT_QUESTIONS = 200
LABEL_DAILY_CAP = 10
LABEL_MAX_TOKENS = 1500
LABEL_MAX_THEMES = 12
LABEL_QUESTION_CHARS = 200
LABEL_PREFIX = "analytics/topics"
# A small model on the active provider. OpenRouter models are price-checked when saved;
# gpt-6-luna is one of the cheapest listed there.
LABEL_MODELS = {"anthropic": "claude-haiku-4-5", "openai": "gpt-6-luna", "openrouter": "openai/gpt-6-luna"}

EVAL_DIMENSIONS = ("grounded", "answers_question", "correct_scope", "matches_reference", "speech_quality",
                   "safety_tone")
EVAL_MAX_RUNS = 50
EVAL_CACHE_SECONDS = 120.0

_local_label_runs: list[dict[str, Any]] = []  # used only when Supabase is not configured
_eval_cache: tuple[float, dict[str, Any]] | None = None


def reset_memory() -> None:
    """Tests only."""
    global _eval_cache
    _local_label_runs.clear()
    _eval_cache = None


# ---------------------------------------------------------------- small helpers

def _day_list(days: int, now: datetime) -> list[str]:
    end = now.astimezone(timezone.utc).date()
    return [(end - timedelta(days=days - 1 - i)).isoformat() for i in range(days)]


def _local_hour(iso: str) -> Optional[int]:
    try:
        when = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo

        return when.astimezone(ZoneInfo(LOCAL_TZ)).hour
    except Exception:  # no tz database: fall back to UTC
        return when.astimezone(timezone.utc).hour


def _norm_question(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 \[\]]", " ", str(text or "").lower())).strip()


def _round(x: Optional[float], places: int = 6) -> Optional[float]:
    return None if x is None else round(x, places)


def slide_parts(slide_id: Optional[str]) -> Optional[tuple[str, int, int]]:
    m = SLIDE_ID_RE.match(str(slide_id or ""))
    return (m.group(1), int(m.group(2)), int(m.group(3))) if m else None


# ---------------------------------------------------------------- aggregation (pure)

def aggregate(
    rows: list[dict[str, Any]],
    counters: dict[str, int],
    table: dict[str, Any],
    days: int,
    now: datetime,
    lookup: Callable[[str], Optional[dict[str, Any]]] = lambda _id: None,
    live: Optional[dict[str, dict[str, Any]]] = None,
    faq_titles: Optional[dict[str, str]] = None,
    include_tests: bool = False,
) -> dict[str, Any]:
    """Everything the Analytics section draws, from log rows and counters already limited to the range.

    `rows` need a `kind` (recorded or inferred). `lookup(slide_id)` returns the index record
    for a slide (title, session_title), or None. Test traffic (smoke checks, evals, prompt
    tests; see usage.is_test_traffic) is left out of the question numbers unless `include_tests`.
    Token and spend numbers always include it: it costs the same money. Test spend is under
    its own purposes (smoke_test, eval_generate, eval_judge, prompt_test).
    """
    day_list = _day_list(days, now)
    first = day_list[0]
    rows = [r for r in rows if str(r.get("at", ""))[:10] >= first]
    flagged = [r for r in rows if (r["test"] if "test" in r else usage.is_test_traffic(r)[0])]
    test_rows = len(flagged)
    if not include_tests:
        ids = {id(r) for r in flagged}
        rows = [r for r in rows if id(r) not in ids]

    # ---- questions
    kinds = Counter(r.get("kind") or "unknown" for r in rows)
    total = len(rows)
    covered = sum(1 for r in rows if r.get("covered"))
    latencies = [int(r["latency_ms"]) for r in rows if r.get("latency_ms") is not None]
    per_day = {d: {"day": d, "questions": 0, "covered": 0} for d in day_list}
    by_hour = [0] * 24
    sources = Counter()
    for r in rows:
        d = str(r.get("at", ""))[:10]
        if d in per_day:
            per_day[d]["questions"] += 1
            per_day[d]["covered"] += 1 if r.get("covered") else 0
        h = _local_hour(r.get("at", ""))
        if h is not None:
            by_hour[h] += 1
        src = r.get("source")
        sources[src if src in usage.QUESTION_SOURCES else "unknown"] += 1

    # ---- counters
    llm_rows: dict[tuple[str, str], dict[str, Any]] = {}
    purpose_rows: dict[str, dict[str, Any]] = {}
    embed_rows: dict[str, dict[str, Any]] = {}
    tts_rows: dict[str, dict[str, Any]] = {}
    events: Counter = Counter()
    faq_hits: Counter = Counter()
    spend_day: dict[str, dict[str, float]] = {d: defaultdict(float) for d in day_list}
    raw_tokens: dict[tuple[str, str, str, str], dict[str, int]] = defaultdict(lambda: {"in": 0, "out": 0, "calls": 0})

    for key, count in counters.items():
        k = usage.parse_key(key)
        if not k or k["day"] < first:
            continue
        if k["kind"] == "usage" and k["metric"] in ("in", "out", "calls"):
            raw_tokens[(k["day"], k["purpose"], k["provider"], k["model"])][k["metric"]] += count
        elif k["kind"] == "embed" and k["metric"] in ("tokens", "calls"):
            row = embed_rows.setdefault(k["model"], {"model": k["model"], "tokens": 0, "calls": 0})
            row[k["metric"]] += count
            if k["metric"] == "tokens" and k["day"] in spend_day:
                spend_day[k["day"]][f"Voyage {k['model']}"] += pricing.embed_cost(table, k["model"], count) or 0.0
        elif k["kind"] == "tts" and k["metric"] in ("chars", "calls"):
            row = tts_rows.setdefault(k["tier"], {"tier": k["tier"], "chars": 0, "calls": 0})
            row[k["metric"]] += count
            if k["metric"] == "chars" and k["day"] in spend_day and k["tier"] != "free":
                spend_day[k["day"]]["ElevenLabs voice"] += pricing.tts_cost(table, k["tier"], count)
        elif k["kind"] == "event":
            events[k["name"]] += count
        elif k["kind"] == "faq":
            faq_hits[k["name"]] += count

    unpriced: set[str] = set()
    for (day, purpose_name, provider, model), t in raw_tokens.items():
        cost = pricing.llm_cost(table, provider, model, t["in"], t["out"], live)
        if cost is None:
            unpriced.add(f"{provider}/{model}")
        row = llm_rows.setdefault((provider, model), {"provider": provider, "model": model, "tokens_in": 0,
                                                      "tokens_out": 0, "calls": 0, "cost": 0.0, "priced": True})
        prow = purpose_rows.setdefault(purpose_name, {"purpose": purpose_name, "tokens_in": 0, "tokens_out": 0,
                                                       "calls": 0, "cost": 0.0})
        for target in (row, prow):
            target["tokens_in"] += t["in"]
            target["tokens_out"] += t["out"]
            target["calls"] += t["calls"]
            target["cost"] += cost or 0.0
        if cost is None:
            row["priced"] = False
        if day in spend_day:
            spend_day[day][f"{provider} / {model}"] += cost or 0.0

    for row in embed_rows.values():
        cost = pricing.embed_cost(table, row["model"], row["tokens"])
        row["cost"], row["priced"] = cost or 0.0, cost is not None
        if cost is None:
            unpriced.add(f"voyage/{row['model']}")
    for row in tts_rows.values():
        row["cost"] = pricing.tts_cost(table, row["tier"], row["chars"])
        row["provider"] = "edge-tts" if row["tier"] == "free" else "ElevenLabs"

    # ---- spend over time, folded to MAX_SERIES + Other
    series_totals: Counter = Counter()
    for d in day_list:
        for name, v in spend_day[d].items():
            series_totals[name] += v
    named = [n for n, v in series_totals.most_common() if v > 0][:MAX_SERIES]
    has_other = any(v > 0 for n, v in series_totals.items() if n not in named)
    series = named + (["Other"] if has_other else [])
    spend_series = []
    for d in day_list:
        values = {n: 0.0 for n in series}
        for name, v in spend_day[d].items():
            values[name if name in named else "Other"] = values.get(name if name in named else "Other", 0.0) + v
        spend_series.append({"day": d, "values": {n: _round(v) for n, v in values.items() if n in series}})

    llm_total = sum(r["cost"] for r in llm_rows.values())
    embed_total = sum(r["cost"] for r in embed_rows.values())
    tts_total = sum(r["cost"] for r in tts_rows.values())

    # ---- topics: course -> session -> slide, from the top slide of each answered question
    courses: dict[str, dict[str, Any]] = {}
    slides: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r.get("kind") not in CONTENT_KINDS:
            continue
        sid = r.get("top_slide_id")
        parts = slide_parts(sid)
        if not parts:
            continue
        course, session, number = parts
        rec = lookup(sid) or {}
        session_title = r.get("session_title") or rec.get("session_title") or ""
        c = courses.setdefault(course, {"course": course, "title": rec.get("course_title"), "questions": 0,
                                        "sessions": {}})
        c["title"] = c["title"] or rec.get("course_title")
        c["questions"] += 1
        s = c["sessions"].setdefault(session, {"session": session, "session_title": session_title, "questions": 0})
        s["session_title"] = s["session_title"] or session_title
        s["questions"] += 1
        sl = slides.setdefault(sid, {"slide_id": sid, "course": course, "session": session, "slide_number": number,
                                     "session_title": session_title, "title": rec.get("title") or "", "questions": 0})
        sl["questions"] += 1
    slide_list = sorted(slides.values(), key=lambda x: (-x["questions"], x["slide_id"]))
    for c in courses.values():
        c["sessions"] = sorted(c["sessions"].values(), key=lambda s: s["session"])
        for s in c["sessions"]:
            s["slides"] = [x for x in slide_list if x["course"] == c["course"] and x["session"] == s["session"]][:10]

    gaps: dict[str, dict[str, Any]] = {}
    for r in rows:  # newest first, so the first text seen is the latest wording
        if r.get("kind") != "not_covered":
            continue
        key = _norm_question(r.get("question", ""))
        if not key:
            continue
        g = gaps.setdefault(key, {"question": r.get("question", ""), "count": 0, "last_at": r.get("at"),
                                  "best_score": None})
        g["count"] += 1
        score = r.get("top_score")
        if score is not None and (g["best_score"] is None or float(score) > g["best_score"]):
            g["best_score"] = float(score)
    gap_list = sorted(gaps.values(), key=lambda g: (-g["count"], str(g["last_at"] or "")), reverse=False)[:TOP_N]

    faq_titles = faq_titles or {}
    faq_list = [{"id": k, "title": faq_titles.get(k, k), "count": v} for k, v in faq_hits.most_common()]

    walkthroughs = sum(kinds.get(k, 0) for k in CONTENT_KINDS)
    return {
        "range": {"days": days, "start": first, "end": day_list[-1]},
        "test_traffic": {"rows": test_rows, "included": include_tests},
        "kpis": {
            "questions": total,
            "covered": covered,
            "covered_pct": _round(covered / total * 100, 1) if total else None,
            "by_kind": dict(kinds),
            "avg_latency_ms": round(sum(latencies) / len(latencies)) if latencies else None,
            "median_latency_ms": round(median(latencies)) if latencies else None,
            "est_spend_usd": _round(llm_total + embed_total + tts_total, 4),
            "spend_parts": {"models": _round(llm_total, 4), "embeddings": _round(embed_total, 4),
                            "voice": _round(tts_total, 4)},
            "unpriced": sorted(unpriced),
        },
        "spend": {"series": series, "days": spend_series},
        "llm": sorted(({**r, "cost": _round(r["cost"])} for r in llm_rows.values()),
                      key=lambda r: -(r["tokens_in"] + r["tokens_out"])),
        "purposes": sorted(({**r, "cost": _round(r["cost"])} for r in purpose_rows.values()),
                           key=lambda r: -(r["tokens_in"] + r["tokens_out"])),
        "embeddings": sorted(({**r, "cost": _round(r["cost"])} for r in embed_rows.values()),
                             key=lambda r: -r["tokens"]),
        "tts": sorted(({**r, "cost": _round(r["cost"])} for r in tts_rows.values()), key=lambda r: -r["chars"]),
        "questions_by_day": list(per_day.values()),
        "questions_by_hour": by_hour,
        "hour_timezone": LOCAL_TZ,
        "topics": {
            "courses": sorted(courses.values(), key=lambda c: c["course"]),
            "top": slide_list[:TOP_N],
            "gaps": gap_list,
            "faq": faq_list,
            "sources": {k: sources.get(k, 0) for k in (*usage.QUESTION_SOURCES, "unknown")},
        },
        "funnel": {
            "questions": total,
            "walkthroughs": walkthroughs,
            "first_segment_played": events.get("segment_played", 0),
            "walkthrough_completed": events.get("walkthrough_completed", 0),
        },
        "events": {name: events.get(name, 0) for name in usage.EVENT_NAMES},
    }


# ---------------------------------------------------------------- eval scores across runs

def _read_bucket(path: str) -> Optional[bytes]:
    """A file from the private bucket (or CONTENT_DIR locally), or None when it is not there."""
    root = config.content_dir()
    if root is not None:
        target = (root / path).resolve()
        if root.resolve() in target.parents and target.is_file():
            return target.read_bytes()
        return None
    if not config.supabase_configured():
        return None
    try:
        return supa.download(path)
    except supa.SupabaseError:
        return None


def _generator_name(row: dict[str, Any]) -> Optional[str]:
    gen = row.get("generator")
    if isinstance(gen, dict):
        provider, model = gen.get("provider"), gen.get("model")
        return f"{provider}/{model}" if provider and model else (model or provider)
    if isinstance(gen, str) and gen:
        return gen
    if row.get("model"):
        return f"{row.get('provider') or '?'}/{row['model']}"
    return None


def _judgements(row: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("judges", "judgements", "judgments"):
        value = row.get(key)
        if isinstance(value, list):
            return [j for j in value if isinstance(j, dict) and "error" not in j]
        if isinstance(value, dict):  # {judge name: judgement}
            return [j for j in value.values() if isinstance(j, dict) and "error" not in j]
    return []


def eval_performance(read: Callable[[str], Optional[bytes]] = _read_bucket) -> dict[str, Any]:
    """Average judge scores per generator model across every eval run in the bucket."""
    raw = read("evals/index.json")
    if not raw:
        return {"available": False, "runs": 0, "models": [], "dimensions": list(EVAL_DIMENSIONS)}
    try:
        index = json.loads(raw)
    except ValueError:
        return {"available": False, "runs": 0, "models": [], "dimensions": list(EVAL_DIMENSIONS),
                "error": "evals/index.json is not valid JSON."}
    runs = index.get("runs", []) if isinstance(index, dict) else index
    runs = [r for r in runs if isinstance(r, dict)][-EVAL_MAX_RUNS:]
    acc: dict[str, dict[str, Any]] = {}
    used_runs = 0
    for run in runs:
        run_id = str(run.get("id") or run.get("run_id") or "")
        if not re.match(r"^[A-Za-z0-9_.:\-]{1,80}$", run_id):
            continue
        body = read(f"evals/runs/{run_id}/results.jsonl")
        if not body:
            continue
        used_runs += 1
        for line in body.decode("utf-8", "replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            name = _generator_name(row) if isinstance(row, dict) else None
            if not name:
                continue
            a = acc.setdefault(name, {"model": name, "runs": set(), "answers": 0, "verdicts": [],
                                      "scores": defaultdict(list), "latency": []})
            a["runs"].add(run_id)
            a["answers"] += 1
            if row.get("latency_ms") is not None:
                try:
                    a["latency"].append(float(row["latency_ms"]))
                except (TypeError, ValueError):
                    pass
            for j in _judgements(row):
                verdict = str(j.get("verdict", "")).lower()
                if verdict in ("pass", "fail"):
                    a["verdicts"].append(1.0 if verdict == "pass" else 0.0)
                for dim, value in (j.get("scores") or {}).items():
                    if dim in EVAL_DIMENSIONS and isinstance(value, (int, float)):
                        a["scores"][dim].append(float(value))
    models = []
    for a in acc.values():
        models.append({
            "model": a["model"],
            "runs": len(a["runs"]),
            "answers": a["answers"],
            "judgements": len(a["verdicts"]),
            "pass_rate": _round(sum(a["verdicts"]) / len(a["verdicts"]), 3) if a["verdicts"] else None,
            "scores": {d: (_round(sum(v) / len(v), 2) if (v := a["scores"].get(d)) else None) for d in EVAL_DIMENSIONS},
            "median_latency_ms": round(median(a["latency"])) if a["latency"] else None,
        })
    models.sort(key=lambda m: (-(m["pass_rate"] or 0), m["model"]))
    return {"available": bool(models), "runs": used_runs, "models": models, "dimensions": list(EVAL_DIMENSIONS)}


def cached_eval_performance() -> dict[str, Any]:
    global _eval_cache
    now = time.monotonic()
    if _eval_cache and now - _eval_cache[0] < EVAL_CACHE_SECONDS:
        return _eval_cache[1]
    try:
        result = eval_performance()
    except Exception as exc:  # a malformed run file never breaks the page
        config.log.warning("eval scores unavailable: %s", exc)
        result = {"available": False, "runs": 0, "models": [], "dimensions": list(EVAL_DIMENSIONS)}
    _eval_cache = (now, result)
    return result


# ---------------------------------------------------------------- topic labeling

LABEL_SYSTEM_PROMPT = (
    "You help a professor see what students ask his course assistant about. You get a numbered list of "
    "student questions (already stripped of personal details). Group them into at most "
    f"{LABEL_MAX_THEMES} themes by course topic or kind of request. Reply with JSON only, no prose:\n"
    '{"themes": [{"label": "short topic name, at most 6 words", "count": <questions in this theme>, '
    '"examples": [<up to 3 question numbers from the list>]}]}\n'
    "Use the question numbers, never question text, for examples. Every question belongs to one theme; use a "
    '"Other" theme for leftovers. Never include names, emails, or personal details in a label. Keep labels PG.'
)


def label_prompt(questions: list[str]) -> str:
    items = [{"n": i + 1, "q": q[:LABEL_QUESTION_CHARS]} for i, q in enumerate(questions)]
    return "Student questions:\n" + json.dumps(items, ensure_ascii=False)


def parse_labels(raw: str, questions: list[str]) -> list[dict[str, Any]]:
    """Validate the model's themes. Examples come from the list by number only; labels are scrubbed."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the reply")
    data = json.loads(text[start : end + 1])
    themes = data.get("themes") if isinstance(data, dict) else None
    if not isinstance(themes, list) or not themes:
        raise ValueError("the reply has no themes")
    out = []
    for t in themes[:LABEL_MAX_THEMES]:
        if not isinstance(t, dict):
            continue
        label = privacy.scrub_question(re.sub(r"\s+", " ", str(t.get("label") or "")).strip())[:80]
        if not label:
            continue
        try:
            count = max(0, min(int(t.get("count") or 0), len(questions)))
        except (TypeError, ValueError):
            count = 0
        examples = []
        for n in t.get("examples") or []:
            if isinstance(n, int) and 1 <= n <= len(questions) and questions[n - 1] not in examples:
                examples.append(questions[n - 1])
            if len(examples) == 3:
                break
        out.append({"label": label, "count": count, "examples": examples})
    if not out:
        raise ValueError("no usable themes")
    return sorted(out, key=lambda t: -t["count"])


def label_model(provider: str, active_model: str) -> str:
    return LABEL_MODELS.get(provider) or active_model


def save_label_run(run: dict[str, Any]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = f"{LABEL_PREFIX}/{stamp}.json"
    run = {**run, "id": stamp}
    if config.supabase_configured():
        supa.upload(path, json.dumps(run, ensure_ascii=False).encode(), "application/json", upsert=True)
    else:
        _local_label_runs.append(run)
        del _local_label_runs[:-20]
    return stamp


def label_history(limit: int = 6) -> list[dict[str, Any]]:
    if not config.supabase_configured():
        return list(reversed(_local_label_runs))[:limit]
    out = []
    for name in supa.list_objects(LABEL_PREFIX + "/", limit=limit):
        if not re.match(r"^\d{8}T\d{6}Z\.json$", name):
            continue
        try:
            out.append(json.loads(supa.download(f"{LABEL_PREFIX}/{name}")))
        except (supa.SupabaseError, ValueError) as exc:
            config.log.warning("topic run %s unreadable: %s", name, exc)
    return out


def label_topics(limit: int, complete: Callable[..., str]) -> dict[str, Any]:
    """Group the newest `limit` (<= 300) scrubbed questions into themes with one small model call."""
    limit = max(10, min(int(limit), LABEL_MAX_QUESTIONS))
    since = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    rows, _ = limits.log_rows(since, max_rows=limit * 2)
    rows = [r for r in rows if not usage.is_test_traffic(r)[0]]  # students only
    questions = [privacy.scrub_question(str(r.get("question") or "")).strip() for r in rows]
    questions = [q for q in questions if q][:limit]
    if len(questions) < 5:
        raise HTTPException(400, "There are fewer than 5 questions in the last 90 days, so there is nothing to group yet.")
    ok, _ = limits.increment(f"topic_label:{datetime.now(timezone.utc):%Y-%m-%d}", 1, cap=LABEL_DAILY_CAP,
                             fail_open=False)
    if not ok:
        raise HTTPException(429, f"Topic labeling runs at most {LABEL_DAILY_CAP} times a day. Try again tomorrow.")
    provider, active_model = settings_store.llm_choice()
    model = label_model(provider, active_model)
    if not llm.key_configured(provider):
        raise HTTPException(400, f"{llm.KEY_VARS[provider]} is not set, so topics cannot be labeled.")
    started = time.monotonic()
    with usage.purpose("topic_label", sticky=True):
        try:
            raw = complete(LABEL_SYSTEM_PROMPT, label_prompt(questions), LABEL_MAX_TOKENS,
                           provider=provider, model=model)
            themes = parse_labels(raw, questions)
        except llm.LLMError as exc:
            raise HTTPException(502, f"The model call failed: {str(exc)[:200]}") from exc
        except ValueError as exc:
            raise HTTPException(502, f"The model's reply was not usable: {exc}") from exc
    run = {
        "at": datetime.now(timezone.utc).isoformat(),
        "questions": len(questions),
        "provider": provider,
        "model": model,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "themes": themes,
    }
    try:
        run["id"] = save_label_run(run)
    except supa.SupabaseError as exc:
        config.log.warning("topic run not saved: %s", exc)
        run["save_error"] = "Could not save this run to storage; it is shown but not kept."
    return run


# ---------------------------------------------------------------- CSV

CSV_COLUMNS = ("at", "course", "kind", "covered", "top_score", "top_slide_id", "session", "session_title",
               "provider", "model", "latency_ms", "tokens_in", "tokens_out", "voice_chars", "source", "test",
               "question")


def _cell(value: Any) -> Any:
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text  # no spreadsheet formulas


def to_csv(rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for r in rows:
        w.writerow([_cell(r.get(c)) for c in CSV_COLUMNS])
    return buf.getvalue()


# ---------------------------------------------------------------- routes

def _days(days: int) -> int:
    if days not in RANGES:
        raise HTTPException(400, "days must be 7, 30, or 90.")
    return days


def _rows(days: int) -> tuple[list[dict[str, Any]], bool]:
    from .admin import activity_row  # app.admin imports app.main, which includes this router

    since = (datetime.now(timezone.utc).date() - timedelta(days=days - 1)).isoformat()
    try:
        rows, full = limits.log_rows(since, MAX_LOG_ROWS)
    except supa.SupabaseError as exc:
        config.log.warning("analytics log read failed: %s", exc)
        raise HTTPException(502, "The database call failed. Check that supabase/schema.sql has been run.") from exc
    return [activity_row(r) for r in rows], full


def _lookup() -> Callable[[str], Optional[dict[str, Any]]]:
    content = storage.store.loaded
    if content is None:
        try:
            content = storage.store.get()
        except storage.ContentUnavailable:
            content = None
    return (lambda sid: content.record(sid)) if content is not None else (lambda sid: None)


def _openrouter_live(table: dict[str, Any], counters: dict[str, int]) -> Optional[dict[str, dict[str, Any]]]:
    """OpenRouter's price list, fetched only when an OpenRouter model in use is missing from the table."""
    known = {r["model"] for r in table.get("llm", []) if r["provider"] == "openrouter"}
    needed = {k["model"] for key in counters if (k := usage.parse_key(key)) and k["kind"] == "usage"
              and k["provider"] == "openrouter" and k["model"] not in known}
    if not needed:
        return None
    try:
        return {m["id"]: m for m in llm.list_models("openrouter").get("models", [])}
    except llm.LLMError as exc:
        config.log.warning("OpenRouter prices unavailable: %s", exc)
        return None


@router.get("")
def analytics(
    days: int = Query(30),
    include_tests: bool = Query(False),
    _: auth.Session = Depends(auth.require_admin),
) -> dict[str, Any]:
    days = _days(days)
    rows, full = _rows(days)
    since_day = (datetime.now(timezone.utc).date() - timedelta(days=days - 1)).isoformat()
    try:
        counters = limits.counters_since(usage.PREFIXES, since_day)
    except supa.SupabaseError as exc:
        config.log.warning("analytics counters read failed: %s", exc)
        counters = {}
    table = pricing.current()
    out = aggregate(rows, counters, table, days, datetime.now(timezone.utc), _lookup(),
                    _openrouter_live(table, counters), {e.id: e.title for e in faq.entries()},
                    include_tests=include_tests)
    out["log_columns_ready"] = full
    out["models"] = cached_eval_performance()
    out["pricing_saved"] = bool(table.get("saved"))
    return out


@router.get("/pricing")
def get_pricing(_: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    return {"pricing": pricing.current(), "defaults": pricing.defaults(), "plans": list(pricing.PLANS)}


class PricingBody(BaseModel):
    pricing: Optional[dict[str, Any]] = None
    reset: bool = False


@router.put("/pricing")
def put_pricing(body: PricingBody, _: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    if body.reset:
        value = None
    else:
        try:
            value = pricing.validate(body.pricing)
        except pricing.BadPricing as exc:
            raise HTTPException(400, str(exc)) from exc
        value["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        settings_store.put({"pricing": value})
    except supa.SupabaseError as exc:
        config.log.warning("pricing save failed: %s", exc)
        raise HTTPException(502, "The database call failed.") from exc
    return {"pricing": pricing.current(value), "defaults": pricing.defaults(), "plans": list(pricing.PLANS)}


class LabelBody(BaseModel):
    limit: int = LABEL_DEFAULT_QUESTIONS


@router.get("/topics")
def topic_runs(_: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    try:
        return {"runs": label_history()}
    except supa.SupabaseError as exc:
        config.log.warning("topic history read failed: %s", exc)
        return {"runs": [], "error": "Could not read past runs from storage."}


def get_label_completer() -> Callable[..., str]:
    return llm.complete_json


@router.post("/topics")
def run_topic_labels(
    body: LabelBody,
    _: auth.Session = Depends(auth.require_admin),
    complete: Callable[..., str] = Depends(get_label_completer),
) -> dict[str, Any]:
    if not 10 <= body.limit <= LABEL_MAX_QUESTIONS:
        raise HTTPException(400, f"limit must be between 10 and {LABEL_MAX_QUESTIONS}.")
    return label_topics(body.limit, complete)


@router.get("/export.csv")
def export_csv(days: int = Query(30), _: auth.Session = Depends(auth.require_admin)) -> Response:
    days = _days(days)
    rows, _full = _rows(days)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Response(
        to_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="faculty-twin-questions-{days}d-{stamp}.csv"'},
    )

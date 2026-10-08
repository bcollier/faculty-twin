"""Settings > Evals: start an eval round from the admin page and read the results (docs/SPEC.md, "Evals").

Everything here needs the admin cookie, and every state-changing request
passes the same-origin check in `app/main.py`. The browser drives a run one
step at a time, because a Vercel function must answer within 60 seconds:

    POST /api/admin/evals/runs            create a run (config, chosen questions, estimate)
    POST /api/admin/evals/runs/{id}/step  ONE question x ONE generator: the real answer() path with
                                          that generator (a per-request override, app/llm.py), then
                                          every judge in parallel. Idempotent: a pair is never done twice.
    POST /api/admin/evals/runs/{id}/cancel

and the page loops on /step until the run is done. A reload resumes from the
server's state: the next pair is always the first one without a result.

Spend guards (docs/SECURITY.md): at most 30 questions, 6 generators and 3
judges per run; one active run at a time; OpenRouter models priced above the
ceiling are refused (the same check as the Model section); every model call
counts against the global DAILY_LLM_CALL_CAP, against the admin-eval daily cap
(DAILY_EVAL_LLM_CALL_CAP) and against the run's own cap
(EVAL_MAX_CALLS_PER_RUN); a step waits rather than leave students fewer than
EVAL_STUDENT_RESERVE global calls. Jev runs from the command line only.

Question text is de-identified (evals/README.md) and lives only in the
private bucket; it is shown to the admin, never signed into a link.
"""

from __future__ import annotations

import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import httpx
import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import (auth, config, embed, eval_core, eval_store, limits, llm, narration, pricing, prompts, settings_store,
               storage, usage)
from .eval_runs import (  # noqa: F401  (re-exported for tests and callers)
    ACTIVE,
    FINISHED,
    IMPORTED_NOTE,
    REPORT_METRICS,
    SELF_GRADING_NOTE,
    index_entry,
    legend,
    model_key,
    pairs_of,
    progress,
    report_card,
    self_grading,
    summarize,
)
from .admin import MODEL_ID_RE, check_model_price
from .main import RetrievalNotReady, Retriever, answer, get_completer, get_embedder, get_retriever

router = APIRouter(prefix="/api/admin/evals")

STEP_BUDGET_SECONDS = 50.0  # a step returns before Vercel's 60 s limit
LEASE_SECONDS = 70.0  # a step that died mid-way (timeout) frees its pair after this
JUDGE_MAX_TOKENS = 1200
GENERATOR_CALLS_MAX = 3  # logistics check + narration + one narration retry
GENERATOR_CALLS_TYPICAL = 2
JEV_NOTE = "Jev runs from the command line only (it needs deepeval, which is not in the Vercel bundle)."

# Rough token use per call, for the cost estimate shown before Start.
TOKENS = {
    "narration": (4000, 700),
    "classifier": (600, 60),
    "judge": (2500, 350),
}

def get_bucket() -> eval_store.Bucket:
    return eval_store.default_bucket()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _store_error(exc: eval_store.StoreError) -> HTTPException:
    if str(exc).startswith("Run ids"):
        return HTTPException(400, str(exc))
    config.log.warning("eval storage failed: %s", exc)
    return HTTPException(502, "Could not read or write eval data in the private bucket. Try again shortly.")


# ---------------------------------------------------------------- models

class ModelRef(BaseModel):
    provider: str
    model: str


def check_model(ref: ModelRef, what: str) -> dict[str, str]:
    provider = (ref.provider or "").strip().lower()
    model = (ref.model or "").strip()
    if provider == "jev" or model.lower().startswith("jev"):
        raise HTTPException(400, JEV_NOTE)
    if provider not in llm.PROVIDERS:
        raise HTTPException(400, f"{what}: provider must be anthropic, openai, or openrouter.")
    if not MODEL_ID_RE.match(model):
        raise HTTPException(400, f"{what}: that model id does not look right.")
    if not llm.key_configured(provider):
        raise HTTPException(400, f"{what}: {llm.KEY_VARS[provider]} is not set, so {provider} models cannot run.")
    return {"provider": provider, "model": model}


# ---------------------------------------------------------------- estimate

def _openrouter_id_candidates(provider: str, model: str) -> list[str]:
    if provider == "openrouter":
        return [model]
    vendor = "anthropic" if provider == "anthropic" else "openai"
    dotted = re.sub(r"-(\d+)-(\d+)$", r"-\1.\2", model)
    return [f"{vendor}/{model}", f"{vendor}/{dotted}"]


def price_per_token(provider: str, model: str, listing: list[dict[str, Any]] | None) -> tuple[float, float] | None:
    """(input, output) USD per token from OpenRouter's published list, or None when unknown.

    Claude and OpenAI have no price API; OpenRouter lists the same models at
    (close to) the provider's own price, so its number is used as a rough guide.
    """
    if not listing:
        return None
    by_id = {m.get("id"): m for m in listing}
    for cand in _openrouter_id_candidates(provider, model):
        m = by_id.get(cand)
        if not m:
            continue
        try:
            p_in = float((m.get("pricing") or {}).get("prompt"))
            p_out = float((m.get("pricing") or {}).get("completion"))
        except (TypeError, ValueError):
            return None
        return (p_in, p_out) if p_in >= 0 and p_out >= 0 else None
    return None


def run_call_cap() -> int:
    return config.env_int("EVAL_MAX_CALLS_PER_RUN", config.DEFAULT_EVAL_MAX_CALLS_PER_RUN)


def student_reserve() -> int:
    return config.env_int("EVAL_STUDENT_RESERVE", config.DEFAULT_EVAL_STUDENT_RESERVE)


def estimate(pairs: int, generators: list[dict[str, str]], judges: list[dict[str, str]],
             listing: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Model calls and rough cost for a run. Upper bounds: FAQ hits and declines call no generator."""
    questions_per_gen = pairs // max(len(generators), 1)
    calls_typical = pairs * (GENERATOR_CALLS_TYPICAL + len(judges))
    calls_max = pairs * (GENERATOR_CALLS_MAX + len(judges))
    per_model = []
    total = 0.0
    unknown = []
    for g in generators:
        price = price_per_token(g["provider"], g["model"], listing)
        n_in = questions_per_gen * (TOKENS["narration"][0] + TOKENS["classifier"][0])
        n_out = questions_per_gen * (TOKENS["narration"][1] + TOKENS["classifier"][1])
        cost = None if price is None else round(n_in * price[0] + n_out * price[1], 4)
        per_model.append({"model": model_key(g), "role": "generator", "calls": questions_per_gen * GENERATOR_CALLS_TYPICAL,
                          "input_tokens": n_in, "output_tokens": n_out, "cost_usd": cost})
        if cost is None:
            unknown.append(model_key(g))
        else:
            total += cost
    for j in judges:
        price = price_per_token(j["provider"], j["model"], listing)
        n_in, n_out = pairs * TOKENS["judge"][0], pairs * TOKENS["judge"][1]
        cost = None if price is None else round(n_in * price[0] + n_out * price[1], 4)
        per_model.append({"model": model_key(j), "role": "judge", "calls": pairs, "input_tokens": n_in,
                          "output_tokens": n_out, "cost_usd": cost})
        if cost is None:
            unknown.append(model_key(j))
        else:
            total += cost
    cap = run_call_cap()
    return {
        "pairs": pairs,
        "calls_typical": calls_typical,
        "calls_max": calls_max,
        "embeddings": pairs,
        "run_call_cap": cap,
        "daily_eval_cap": limits.daily_eval_call_cap(),
        "eval_calls_today": limits.read_counter(limits.eval_calls_key()),
        "cost_usd": round(total, 2) if not unknown else None,
        "cost_usd_known_part": round(total, 2),
        "cost_unknown_for": unknown,
        "per_model": per_model,
        "cost_note": "Rough: about 4,600 input and 760 output tokens per answer and 2,500 in / 350 out per "
        "judgement, priced from OpenRouter's published list for the same model.",
        "within_cap": calls_max <= cap,
    }


def _price_listing() -> list[dict[str, Any]] | None:
    try:
        return llm.list_models("openrouter").get("models")
    except llm.LLMError:
        return None


# ---------------------------------------------------------------- questions

def load_questions(bucket: eval_store.Bucket) -> list[eval_core.Question]:
    """The question set from the bucket, re-checked for leaks on every read. 404 when not uploaded."""
    try:
        text = eval_store.read_questions_text(bucket)
    except eval_store.StoreError as exc:
        raise _store_error(exc) from exc
    if text is None:
        raise HTTPException(404, "No question set is uploaded yet. Run scripts/upload_eval_questions.py.")
    try:
        return eval_core.parse_lines(text)
    except eval_core.DatasetError as exc:
        raise HTTPException(409, f"The uploaded question set failed the privacy check ({exc}). Fix and re-upload it.") from exc


def _category_summary(questions: list[eval_core.Question]) -> list[dict[str, Any]]:
    return [{"category": c, "count": n} for c, n in eval_core.category_counts(questions)]


@router.get("/questions")
def get_questions(_: auth.Session = Depends(auth.require_admin),
                  bucket: eval_store.Bucket = Depends(get_bucket)) -> dict[str, Any]:
    try:
        questions = load_questions(bucket)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        return {"count": 0, "categories": [], "questions": [], "uploaded": False, "note": exc.detail,
                "all_categories": list(eval_core.CATEGORIES)}
    return _view(questions)


def _view(questions: list[eval_core.Question]) -> dict[str, Any]:
    return {
        "count": len(questions),
        "categories": _category_summary(questions),
        "answerable": sum(1 for q in questions if q.answerable),
        "questions": [q.as_dict() for q in questions],
        "uploaded": True,
        "all_categories": list(eval_core.CATEGORIES),
        "privacy": "De-identified student questions (evals/README.md). Private: admin only, never in git or a public link.",
    }


class QuestionBody(BaseModel):
    question: str
    category: str
    course: Optional[str] = None
    month: Optional[str] = None
    reference_answer: Optional[str] = None
    answerable: bool = False


def _checked_record(body: QuestionBody, line: int) -> dict[str, Any]:
    raw = {
        "month": (body.month or "").strip(),
        "course": (body.course or "").strip() or "Unknown",
        "category": body.category,
        "question": re.sub(r"\s+", " ", body.question or "").strip()[:1000],
        "reference_answer": (body.reference_answer or "").strip()[:2000] or None,
        "answerable_from_course_materials": bool(body.answerable),
    }
    try:
        return eval_core.parse_record(raw, line).raw()
    except eval_core.DatasetError as exc:
        # Same checks as evals/dataset.py: the message names the problem, never the text.
        raise HTTPException(400, str(exc).replace(f"line {line}: ", "")) from exc


def _question_lines(bucket: eval_store.Bucket) -> list[str]:
    return eval_store.read_question_lines(bucket) or []


def _questions_view(bucket: eval_store.Bucket, pending: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    """The question list including the edit just written, even if the bucket listing has not caught up."""
    lines = eval_store.read_question_lines(bucket, pending) or []
    try:
        questions = eval_core.parse_lines("\n".join(lines) + "\n")
    except eval_core.DatasetError as exc:
        raise HTTPException(409, f"The uploaded question set failed the privacy check ({exc}). Fix and re-upload it.") from exc
    return _view(questions)


@router.post("/questions")
def add_question(body: QuestionBody, _: auth.Session = Depends(auth.require_admin),
                 bucket: eval_store.Bucket = Depends(get_bucket)) -> dict[str, Any]:
    """Add one question typed in Settings. Runs the dataset privacy checks first. One write-once object."""
    try:
        lines = _question_lines(bucket)
        pending = eval_store.write_question_edit(bucket, "add", _checked_record(body, len(lines) + 1))
        return _questions_view(bucket, pending)
    except eval_store.StoreError as exc:
        raise _store_error(exc) from exc


@router.put("/questions/{qid}")
def edit_question(qid: str, body: QuestionBody, _: auth.Session = Depends(auth.require_admin),
                  bucket: eval_store.Bucket = Depends(get_bucket)) -> dict[str, Any]:
    """Edit one question in place (its qid is its line number, so it keeps it). Same privacy checks."""
    m = re.fullmatch(r"q(\d{3,4})", qid)
    if not m:
        raise HTTPException(400, "Question ids look like q007.")
    n = int(m.group(1))
    try:
        lines = _question_lines(bucket)
        if not 1 <= n <= len(lines) or not lines[n - 1].strip():
            raise HTTPException(404, "No such question.")
        pending = eval_store.write_question_edit(bucket, "edit", _checked_record(body, n), line=n)
        return _questions_view(bucket, pending)
    except eval_store.StoreError as exc:
        raise _store_error(exc) from exc


# ---------------------------------------------------------------- runs

class RunBody(BaseModel):
    name: Optional[str] = None
    generators: list[ModelRef]
    judges: list[ModelRef]
    top: int = 25
    categories: Optional[list[str]] = None
    confirm: bool = False


def _plan(body: RunBody, bucket: eval_store.Bucket, check_prices: bool) -> dict[str, Any]:
    if not 1 <= len(body.generators) <= config.EVAL_MAX_GENERATORS:
        raise HTTPException(400, f"Pick 1 to {config.EVAL_MAX_GENERATORS} models to answer.")
    if not 1 <= len(body.judges) <= config.EVAL_MAX_JUDGES:
        raise HTTPException(400, f"Pick 1 to {config.EVAL_MAX_JUDGES} judges.")
    if not 1 <= body.top <= config.EVAL_MAX_QUESTIONS:
        raise HTTPException(400, f"A run takes 1 to {config.EVAL_MAX_QUESTIONS} questions.")
    generators = [check_model(m, "Answering model") for m in body.generators]
    judges = [check_model(m, "Judge") for m in body.judges]
    for group, what in ((generators, "answering model"), (judges, "judge")):
        keys = [model_key(m) for m in group]
        if len(set(keys)) != len(keys):
            raise HTTPException(400, f"The same {what} is listed twice.")
    if check_prices:
        for m in generators + judges:
            check_model_price(m["provider"], m["model"])  # refuses priced-out OpenRouter models
    questions = load_questions(bucket)
    cats = [c.strip().upper() for c in (body.categories or []) if c and c.strip()]
    unknown = [c for c in cats if c not in eval_core.CATEGORIES]
    if unknown:
        raise HTTPException(400, f"Unknown category: {', '.join(unknown)}.")
    pool = [q for q in questions if not cats or q.category in cats]
    if not pool:
        raise HTTPException(400, "No questions in those categories.")
    chosen = eval_core.select_top(pool, body.top)
    pairs = len(chosen) * len(generators)
    return {
        "generators": generators,
        "judges": judges,
        "categories": cats or None,
        "questions": chosen,
        "estimate": estimate(pairs, generators, judges, _price_listing()),
        "self_grading": self_grading(generators, judges),
    }


@router.post("/runs/estimate")
def estimate_run(body: RunBody, _: auth.Session = Depends(auth.require_admin),
                 bucket: eval_store.Bucket = Depends(get_bucket)) -> dict[str, Any]:
    """What Start would do: questions, model calls, rough cost, self-grading warnings. Calls no model."""
    plan = _plan(body, bucket, check_prices=False)
    return {
        "questions": len(plan["questions"]),
        "categories": _category_summary(plan["questions"]),
        "estimate": plan["estimate"],
        "self_grading": plan["self_grading"],
        "self_grading_note": SELF_GRADING_NOTE if plan["self_grading"] else None,
    }


def _new_run_id(bucket: eval_store.Bucket) -> str:
    # Checked with the listing, not a read: a read of a path that does not exist yet could be cached.
    base = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if base not in eval_store.bucket_names(bucket, "evals/runs/"):
        return base
    return f"{base}-{secrets.token_hex(3)}"


@router.post("/runs", status_code=201)
def create_run(body: RunBody, _: auth.Session = Depends(auth.require_admin),
               bucket: eval_store.Bucket = Depends(get_bucket)) -> dict[str, Any]:
    if not body.confirm:
        raise HTTPException(400, "Check the estimate and confirm before starting a run.")
    try:
        active = [r for r in current_runs(bucket) if r.get("status") == ACTIVE]
    except eval_store.StoreError as exc:
        raise _store_error(exc) from exc
    if active:
        raise HTTPException(409, f"Run {active[0]['id']} is still in progress. Finish or cancel it first.")
    plan = _plan(body, bucket, check_prices=True)
    est = plan["estimate"]
    if not est["within_cap"]:
        raise HTTPException(
            400,
            f"This run could make up to {est['calls_max']} model calls, over the per-run cap of {est['run_call_cap']} "
            "(EVAL_MAX_CALLS_PER_RUN). Use fewer questions, models or judges.",
        )
    name = re.sub(r"\s+", " ", body.name or "").strip()[:80]
    try:
        run_id = _new_run_id(bucket)
        run = {
            "id": run_id,
            "nonce": secrets.token_hex(6),  # keys this run's in-process embedding cache
            "name": name or f"Eval {run_id[:8]}",
            "kind": "admin",
            "created_at": _now(),
            "updated_at": _now(),
            "finished_at": None,
            "status": ACTIVE,
            "status_note": None,
            "generators": plan["generators"],
            "judges": plan["judges"],
            "top": body.top,
            "categories": plan["categories"],
            "questions": [q.as_dict() for q in plan["questions"]],
            "pairs_total": len(plan["questions"]) * len(plan["generators"]),
            "pairs_done": 0,
            "calls_used": 0,
            "call_cap": est["run_call_cap"],
            "estimate": est,
            "self_grading": plan["self_grading"],
            "judge_prompt": "custom" if eval_core.judge_system_prompt() != eval_core.SYSTEM_PROMPT else "default",
            "prompt_versions": prompt_versions(),
            "notes": [],
            "lease": None,
            "summary": {"by_generator": {}},
        }
        eval_store.write_run(bucket, run)
        eval_store.write_results(bucket, run_id, [])
        eval_store.upsert_index(bucket, index_entry(run))
    except eval_store.StoreError as exc:
        raise _store_error(exc) from exc
    return {"run": public_run(run), "progress": progress(run)}


def prompt_versions() -> dict[str, dict[str, Any]]:
    """Which version of every prompt this run used (Settings > Prompts): the first 8 characters of
    its sha256 and whether it is an edit, so runs before and after a prompt change can be told apart."""
    out: dict[str, dict[str, Any]] = {}
    for name in prompts.REGISTRY:
        try:
            v = prompts.view(name)
        except Exception:  # the settings read failed: record nothing rather than fail the run
            continue
        out[name] = {"hash": str(v.get("hash") or "")[:8], "edited": bool(v.get("is_overridden"))}
    return out


def public_run(run: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in run.items() if k not in ("lease",)}
    return out


def current_runs(bucket: eval_store.Bucket) -> list[dict[str, Any]]:
    """The runs index, with any "running" entry checked against its write-once markers.

    An index copy served from the CDN can be a little old; the markers come from the listing.
    """
    runs = eval_store.read_index(bucket)
    for r in runs:
        if r.get("status") == ACTIVE:
            final = eval_store.finished_status(bucket, r["id"])
            if final:
                r["status"] = final
    return runs


def _finish(run: dict[str, Any], rows: list[dict[str, Any]], status: str, note: str | None = None,
            bucket: eval_store.Bucket | None = None) -> None:
    run["status"] = status
    run["status_note"] = note
    run["finished_at"] = run.get("finished_at") or _now()
    run["lease"] = None
    run["summary"] = summarize(run, rows)
    if bucket is not None and status == "done":
        eval_store.mark_finished(bucket, run["id"], status, run["finished_at"])


# ---------------------------------------------------------------- one step

_qvec_cache: dict[tuple[str, str], np.ndarray] = {}
_qvec_lock = threading.Lock()


def _cached_embedder(run_id: str, qid: str, base: Callable[[str], np.ndarray]) -> Callable[[str], np.ndarray]:
    """Reuse one question's embedding across the run's generators on a warm instance."""
    def embed_once(text: str) -> np.ndarray:
        key = (run_id, qid)
        with _qvec_lock:
            hit = _qvec_cache.get(key)
        if hit is not None:
            return hit
        vec = base(text)
        with _qvec_lock:
            if len(_qvec_cache) > 200:
                _qvec_cache.clear()
            _qvec_cache[key] = vec
        return vec

    return embed_once


class Budget:
    """Counts one step's model calls against the run's cap and today's admin-eval cap."""

    def __init__(self, run_left: int) -> None:
        self.run_left = run_left
        self.used = 0
        self._lock = threading.Lock()

    def take(self) -> None:
        with self._lock:
            if self.used >= self.run_left:
                raise llm.LLMError("This run reached its model-call cap (EVAL_MAX_CALLS_PER_RUN)")
            if not limits.take_eval_call():
                raise llm.LLMError("Today's admin eval budget is used up (DAILY_EVAL_LLM_CALL_CAP)")
            self.used += 1


def counted(complete: Callable[..., str], budget: Budget) -> Callable[..., str]:
    def call(system: str, user: str, max_tokens: int, provider: str | None = None, model: str | None = None,
             **kw: Any) -> str:
        budget.take()
        return complete(system, user, max_tokens, provider=provider, model=model, **kw)

    return call


def _answer_text(playlist: dict[str, Any]) -> str:
    parts = []
    for a in playlist.get("answers") or []:
        parts.append(str(a.get("text") if isinstance(a, dict) else a or ""))
    return " ".join(p.strip() for p in parts if p and p.strip())


def response_from(playlist: dict[str, Any], info: dict[str, Any], content: storage.Content,
                  latency_ms: int) -> dict[str, Any]:
    """The run row's `response`, in the CLI's shape (evals/targets.py), plus what answered (`outcome`).

    Unlike the CLI, a FAQ answer or a logistics referral is shown to the judges
    in words (the twin's `message`), so they grade what the student saw.
    """
    kind = info.get("kind") or playlist.get("kind")
    base = {"message": None, "segments": [], "follow_ups": playlist.get("follow_ups") or [],
            "narration_source": info.get("narration"), "top_score": info.get("top_score"),
            "latency_ms": latency_ms, "outcome": kind}
    segs = playlist.get("segments") or []
    if kind == "web":  # "beyond the slides": a short web answer with its sources, never in Ben's voice
        text = _answer_text(playlist) or str(playlist.get("text") or playlist.get("message") or "")
        links = [{"title": str(link.get("title") or "")[:200], "url": str(link.get("url") or "")[:500]}
                 for link in (playlist.get("sources") or playlist.get("links") or []) if isinstance(link, dict)]
        closest = [str(seg.get("slide_id")) for seg in (playlist.get("related") or playlist.get("closest") or segs)
                   if isinstance(seg, dict) and seg.get("slide_id")]
        return {**base, "status": "ok", "narration_source": "web",
                "segments": [{"n": 1, "slide_id": "web", "narration": text, "evidence": None}],
                "links": links, "label": playlist.get("label"), "closest_slides": closest}
    if playlist.get("covered") and segs:
        out_segs = []
        for seg in segs:
            rec = content.record(seg.get("slide_id") or "")
            code = (seg.get("code") or {}).get("source") if isinstance(seg.get("code"), dict) else None
            out_segs.append({"n": seg.get("n"), "slide_id": seg.get("slide_id"),
                             "narration": seg.get("narration") or "",
                             "evidence": narration.slide_payload(rec, code) if rec else None})
        return {**base, "status": "ok", "segments": out_segs}
    text = _answer_text(playlist)
    if playlist.get("covered"):  # an answer without slides (course information from Canvas)
        return {**base, "status": "ok", "narration_source": kind or "answer",
                "segments": [{"n": 1, "slide_id": kind or "answer", "narration": text, "evidence": None}]}
    if kind == "faq":
        msg = "it replied with Ben's own written course FAQ answer, not slides: " + eval_core._clip(text, 700)
    elif kind == "logistics":
        msg = "it sent the student to Ben: " + eval_core._clip(playlist.get("message"), 300)
    else:
        msg = None
    return {**base, "status": "not_covered", "message": msg, "follow_ups": []}


EXPECTATION_KEYS = ("type", "expected_kind", "expected_slides", "must_include", "must_not")


def web_path_available() -> bool:
    """Whether the app has the "beyond the slides" web path (kind `web`); until then web questions expect a decline."""
    from . import main

    return "web" in getattr(main, "LOG_KINDS", ())


def answer_usage(gen: dict[str, str], spent: usage.Tally) -> dict[str, Any]:
    """One answer's model tokens and their estimated cost (the price table in Settings > Analytics)."""
    try:
        cost = pricing.llm_cost(pricing.current(), gen["provider"], gen["model"], spent.tokens_in, spent.tokens_out)
    except Exception:  # an unreadable price table never fails an answer
        cost = None
    return {"tokens_in": spent.tokens_in, "tokens_out": spent.tokens_out, "calls": spent.calls,
            "cost_usd": None if cost is None else round(cost, 6)}


def judge_item(q: dict[str, Any], response: dict[str, Any], web_path: bool) -> dict[str, Any]:
    """What a judge sees for one answer: the question, what a good answer does, and the answer."""
    return {"question": q["question"], "category": q["category"], "answerable": q["answerable"],
            "reference_answer": q.get("reference_answer"), "response": response,
            "expected_kind": eval_core.expected_routes(q, web_path) if q.get("expected_kind") else None,
            "must_include": q.get("must_include"), "must_not": q.get("must_not")}


def judge_one(judge: dict[str, str], item: dict[str, Any], complete: Callable[..., str], deadline: float,
              system: str) -> dict[str, Any]:
    """One judgement, with one retry while time allows. Never raises.

    Runs in a worker thread, which starts with a fresh context, so it tags its own spend.
    """
    with usage.purpose("eval_judge", sticky=True):
        return _judge_one(judge, item, complete, deadline, system)


def _judge_one(judge: dict[str, str], item: dict[str, Any], complete: Callable[..., str], deadline: float,
               system: str) -> dict[str, Any]:
    user = eval_core.build_user_prompt(item)
    name = model_key(judge)
    last = ""
    for _attempt in range(2):
        left = deadline - time.monotonic()
        if left < 8:
            last = last or "no time left in this step"
            break
        client = httpx.Client(timeout=httpx.Timeout(min(left, 45.0), connect=5.0))
        try:
            raw = complete(system, user, JUDGE_MAX_TOKENS, provider=judge["provider"], model=judge["model"],
                           client=client)
            out = eval_core.parse(raw)
            out["judge"] = name
            return out
        except (llm.LLMError, eval_core.JudgementError, KeyError, ValueError, httpx.HTTPError) as exc:
            last = str(exc)[:300]
            if eval_core.is_billing_error(last):  # no credit or quota: says nothing about the answer
                return {"judge": name, "error": last, eval_core.PROVIDER_ERROR: True}
            if "cap" in last or "returned 4" in last:  # a cap or a rejected request will not fix itself
                break
        finally:
            client.close()
    return {"judge": name, "error": last}


def _llm_completer(system, user, max_tokens, provider=None, model=None, client=None):
    return llm.complete_json(system, user, max_tokens, provider=provider, model=model, client=client)


def get_judge_completer() -> Callable[..., str]:
    return _llm_completer


def _spend_check(run: dict[str, Any]) -> None:
    """Refuse a step that could pass a cap. Raises 429 with what to do."""
    need = GENERATOR_CALLS_MAX + len(run["judges"])
    if run.get("calls_used", 0) + need > run.get("call_cap", run_call_cap()):
        raise HTTPException(429, "This run reached its model-call cap (EVAL_MAX_CALLS_PER_RUN).")
    eval_used = limits.read_counter(limits.eval_calls_key())
    if eval_used + need > limits.daily_eval_call_cap():
        raise HTTPException(429, "Today's admin eval budget is used up (DAILY_EVAL_LLM_CALL_CAP). "
                                 "The run is saved; resume it tomorrow (UTC).")
    global_used = limits.read_counter(f"llm_calls:{limits._today()}")
    if global_used + need > limits.daily_llm_call_cap() - student_reserve():
        raise HTTPException(429, "Evals have paused to keep today's remaining model calls for students "
                                 "(DAILY_LLM_CALL_CAP minus EVAL_STUDENT_RESERVE). Resume tomorrow (UTC).")


def run_step(run_id: str, bucket: eval_store.Bucket, retriever: Retriever, embedder: Callable[[str], np.ndarray],
             completer: Callable[..., str], judge_completer: Callable[..., str]) -> dict[str, Any]:
    started = time.monotonic()
    deadline = started + STEP_BUDGET_SECONDS
    run = eval_store.read_run(bucket, run_id)
    if run is None:
        raise HTTPException(404, "No such run.")
    if run.get("status") != ACTIVE:
        return {"progress": progress(run), "row": None}
    # What is done comes from the written rows (listed, never a cached copy); see app/eval_store.py.
    rows = eval_store.read_results(bucket, run_id)
    _sync_progress(run, rows)
    if eval_store.is_cancelled(bucket, run_id):
        _finish(run, rows, "cancelled", "Cancelled in Settings.")
        eval_store.write_run(bucket, run)
        eval_store.upsert_index(bucket, index_entry(run))
        return {"progress": progress(run), "row": None}
    done = {r.get("pair") for r in rows}
    todo = [(i, q, g) for i, q, g in pairs_of(run) if i not in done]
    if not todo:
        _finish(run, rows, "done", bucket=bucket)
        eval_store.write_run(bucket, run)
        eval_store.upsert_index(bucket, index_entry(run))
        return {"progress": progress(run), "row": None}
    lease = run.get("lease") or {}
    if lease and lease.get("until", 0) > time.time() and lease.get("pair") not in done:
        return {"progress": progress(run), "row": None,
                "waiting": {"seconds": max(3, int(lease["until"] - time.time()) // 2 or 3),
                            "reason": "Another step of this run is still working (another tab, or a retry)."}}
    pair, q, gen = todo[0]
    _spend_check(run)
    content = storage.store.get_or_503()
    run["lease"] = {"pair": pair, "until": time.time() + LEASE_SECONDS}
    eval_store.write_run(bucket, run)

    budget = Budget(int(run.get("call_cap", run_call_cap())) - int(run.get("calls_used", 0)))
    gen_complete = counted(completer, budget)
    t0 = time.monotonic()
    info: dict[str, Any] = {}
    try:
        # Counted as eval spend in Settings > Analytics, not student narration (sticky: inner tags keep it).
        with llm.model_override(gen["provider"], gen["model"]), usage.purpose("eval_generate", sticky=True), \
                usage.tally() as spent:
            playlist, info = answer(q["question"], None, content, retriever,
                                    _cached_embedder(f"{run_id}:{run.get('nonce', '')}", q["qid"], embedder), gen_complete,
                                    gen["provider"], gen["model"])
        response = response_from(playlist, info, content, int((time.monotonic() - t0) * 1000))
        response["usage"] = answer_usage(gen, spent)
        if any(eval_core.is_billing_error(e) for e in info.get("errors") or []):
            # The answering model's provider refused for billing reasons: the narration is a fallback, not the model.
            response = {**response, "status": eval_core.PROVIDER_ERROR, "segments": [],
                        "message": next(str(e)[:300] for e in info["errors"] if eval_core.is_billing_error(e))}
    except RetrievalNotReady:
        response = {"status": "retrieval_not_ready", "message": "retrieval not implemented yet", "segments": [],
                    "follow_ups": [], "narration_source": None, "top_score": None, "latency_ms": 0,
                    "outcome": None}
    except HTTPException as exc:
        run["lease"] = None
        eval_store.write_run(bucket, run)
        cause = exc.__cause__
        if isinstance(cause, embed.EmbeddingCapReached) or exc.status_code != 503:
            raise
        if "Course content" in str(exc.detail) or "index does not match" in str(exc.detail):
            raise
        # Voyage busy or rate-limited (the free tier allows 3 a minute): wait and retry this pair.
        return {"progress": progress(run), "row": None,
                "waiting": {"seconds": 22, "reason": f"{exc.detail} Retrying this question shortly."}}

    item = judge_item(q, response, web_path_available())
    judgements: list[dict[str, Any]] = []
    if response["status"] in ("ok", "not_covered"):
        judge_complete = counted(judge_completer, budget)
        system = eval_core.judge_system_prompt()
        with ThreadPoolExecutor(max_workers=len(run["judges"])) as pool:
            futures = [pool.submit(judge_one, j, item, judge_complete, deadline, system) for j in run["judges"]]
            judgements = [f.result() for f in futures]
    row = {
        "qid": q["qid"],
        "pair": pair,
        **{k: q[k] for k in EXPECTATION_KEYS if q.get(k)},
        "web_path": web_path_available(),
        "category": q["category"],
        "course": q.get("course"),
        "answerable": q["answerable"],
        "question": q["question"],
        "reference_answer": q.get("reference_answer"),
        "generator": model_key(gen),
        "outcome": response.get("outcome"),
        "response": response,
        "judgements": judgements,
        "narration_errors": (info.get("errors") or [])[:3],
        "calls": budget.used,
        "seconds": round(time.monotonic() - started, 1),
        "at": _now(),
    }
    # The row is the record: written once under its pair number. Two steps racing for the same pair
    # write the same file, so a pair is never counted twice.
    eval_store.write_row(bucket, run_id, row)
    rows = eval_store.read_results(bucket, run_id)
    if row["pair"] not in {r.get("pair") for r in rows}:  # the listing lagged: keep our own row
        rows.append(row)
        rows.sort(key=lambda r: r.get("pair") or 0)
    eval_store.write_results(bucket, run_id, rows)
    _sync_progress(run, rows)
    run["lease"] = None
    run["updated_at"] = _now()
    if eval_store.is_cancelled(bucket, run_id):
        _finish(run, rows, "cancelled", "Cancelled in Settings.")
    elif run["pairs_done"] >= run["pairs_total"]:
        _finish(run, rows, "done", bucket=bucket)
    else:
        run["summary"] = summarize(run, rows)
    eval_store.write_run(bucket, run)
    eval_store.upsert_index(bucket, index_entry(run))
    return {"progress": progress(run), "row": public_row(row)}


def _sync_progress(run: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """Progress and spend from the rows themselves, so a stale run.json can never undercount them."""
    run["pairs_done"] = len({r.get("pair") for r in rows})
    run["calls_used"] = sum(int(r.get("calls") or 0) for r in rows)


def public_row(row: dict[str, Any]) -> dict[str, Any]:
    """A result row for the page: everything but the slide material sent to the judges."""
    resp = dict(row.get("response") or {})
    resp["segments"] = [{k: v for k, v in s.items() if k != "evidence"} | {"has_evidence": bool(s.get("evidence"))}
                        for s in resp.get("segments") or []]
    return {**row, "response": resp}


@router.post("/runs/{run_id}/step")
def step(
    run_id: str,
    _: auth.Session = Depends(auth.require_admin),
    bucket: eval_store.Bucket = Depends(get_bucket),
    retriever: Retriever = Depends(get_retriever),
    embedder=Depends(get_embedder),
    completer=Depends(get_completer),
    judge_completer=Depends(get_judge_completer),
) -> dict[str, Any]:
    try:
        eval_store.check_run_id(run_id)
        return run_step(run_id, bucket, retriever, embedder, completer, judge_completer)
    except eval_store.StoreError as exc:
        raise _store_error(exc) from exc


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: str, _: auth.Session = Depends(auth.require_admin),
           bucket: eval_store.Bucket = Depends(get_bucket)) -> dict[str, Any]:
    try:
        eval_store.check_run_id(run_id)
        run = eval_store.read_run(bucket, run_id)
        if run is None:
            raise HTTPException(404, "No such run.")
        if run.get("status") == ACTIVE:
            eval_store.mark_cancelled(bucket, run_id, _now())  # a step in flight sees this before it writes
            rows = eval_store.read_results(bucket, run_id)
            _sync_progress(run, rows)
            _finish(run, rows, "cancelled", "Cancelled in Settings.")
            eval_store.write_run(bucket, run)
            eval_store.upsert_index(bucket, index_entry(run))
    except eval_store.StoreError as exc:
        raise _store_error(exc) from exc
    return {"progress": progress(run)}


@router.get("/runs")
def list_runs(_: auth.Session = Depends(auth.require_admin),
              bucket: eval_store.Bucket = Depends(get_bucket)) -> dict[str, Any]:
    try:
        runs = current_runs(bucket)
    except eval_store.StoreError as exc:
        raise _store_error(exc) from exc
    return {"runs": runs, "active": next((r["id"] for r in runs if r.get("status") == ACTIVE), None)}


@router.get("/runs/{run_id}")
def get_run(run_id: str, _: auth.Session = Depends(auth.require_admin),
            bucket: eval_store.Bucket = Depends(get_bucket)) -> dict[str, Any]:
    try:
        eval_store.check_run_id(run_id)
        run = eval_store.read_run(bucket, run_id)
        if run is None:
            raise HTTPException(404, "No such run.")
        rows = eval_store.read_results(bucket, run_id)
    except eval_store.StoreError as exc:
        raise _store_error(exc) from exc
    return {"run": public_run(run), "progress": progress(run), "rows": [public_row(r) for r in rows],
            "self_grading_note": SELF_GRADING_NOTE if run.get("self_grading") else None, "legend": legend()}


# ---------------------------------------------------------------- report card

@router.get("/report-card")
def get_report_card(_: auth.Session = Depends(auth.require_admin),
                    bucket: eval_store.Bucket = Depends(get_bucket)) -> dict[str, Any]:
    try:
        return report_card(eval_store.read_index(bucket), eval_store.read_calibration(bucket))
    except eval_store.StoreError as exc:
        raise _store_error(exc) from exc


# ---------------------------------------------------------------- judge calibration

@router.get("/calibration")
def get_calibration(_: auth.Session = Depends(auth.require_admin),
                    bucket: eval_store.Bucket = Depends(get_bucket)) -> dict[str, Any]:
    cases = eval_core.load_calibration_cases()
    try:
        results = eval_store.read_calibration(bucket)
    except eval_store.StoreError as exc:
        raise _store_error(exc) from exc
    return {"cases": [{"cid": c["cid"], "about": c.get("about")} for c in cases], "judges": results}


class CalibrateBody(BaseModel):
    provider: str
    model: str
    restart: bool = False
    attempt: Optional[str] = None  # from the previous step's reply, so a stale read never mixes attempts


@router.post("/calibration/step")
def calibration_step(body: CalibrateBody, _: auth.Session = Depends(auth.require_admin),
                     bucket: eval_store.Bucket = Depends(get_bucket),
                     judge_completer=Depends(get_judge_completer)) -> dict[str, Any]:
    """Score the next synthetic case (app/eval_calibration.jsonl) with one judge. The page loops until done.

    Each case's result is written once under the attempt (eval_store.write_calibration_row), and which
    cases are done comes from the listing, like run rows.
    """
    judge = check_model(ModelRef(provider=body.provider, model=body.model), "Judge")
    key = model_key(judge)
    cases = eval_core.load_calibration_cases()
    try:
        current = eval_store.read_calibration(bucket).get(key)
        attempt = body.attempt if body.attempt and eval_store.ATTEMPT_RE.match(body.attempt) else None
        if body.restart or (attempt is None and not isinstance(current, dict)):
            check_model_price(judge["provider"], judge["model"])
            attempt = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(2)
        elif attempt is None:
            if current.get("done"):
                return {"judge": key, "attempt": current.get("attempt"), "result": current}
            attempt = current.get("attempt")
        seen = eval_store.calibration_rows(bucket, key, attempt)
        nxt = next((c for c in cases if c["cid"] not in seen), None)
        if nxt is not None:
            if limits.read_counter(limits.eval_calls_key()) + 1 > limits.daily_eval_call_cap():
                raise HTTPException(429, "Today's admin eval budget is used up (DAILY_EVAL_LLM_CALL_CAP).")
            # Two calls: judge_one retries a malformed reply once (a budget of 1 made the retry hit the cap).
            out = judge_one(judge, nxt, counted(judge_completer, Budget(2)), time.monotonic() + STEP_BUDGET_SECONDS,
                            eval_core.judge_system_prompt())
            row = {"cid": nxt["cid"], "misses": eval_core.check(nxt, out), "verdict": out.get("verdict"),
                   "error": out.get("error")}
            eval_store.write_calibration_row(bucket, key, attempt, row)
            seen[nxt["cid"]] = row
        rows = [seen[c["cid"]] for c in cases if c["cid"] in seen]
        entry = {
            "judge": key,
            "attempt": attempt,
            "started_at": (current or {}).get("started_at") if (current or {}).get("attempt") == attempt else _now(),
            "rows": rows,
            "cases": len(cases),
            "met": sum(1 for r in rows if not r["misses"]),
            "missed": [r["cid"] for r in rows if r["misses"]],
            "done": len(rows) >= len(cases),
            "source": "settings",
        }
        if entry["done"]:
            entry["finished_at"] = _now()
        eval_store.write_calibration_entry(bucket, key, entry)
    except eval_store.StoreError as exc:
        raise _store_error(exc) from exc
    return {"judge": key, "attempt": attempt, "result": entry}


@router.get("/limits")
def eval_limits(_: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    """Caps the run form shows before Start."""
    return {
        "max_questions": config.EVAL_MAX_QUESTIONS,
        "max_generators": config.EVAL_MAX_GENERATORS,
        "max_judges": config.EVAL_MAX_JUDGES,
        "run_call_cap": run_call_cap(),
        "daily_eval_cap": limits.daily_eval_call_cap(),
        "eval_calls_today": limits.read_counter(limits.eval_calls_key()),
        "daily_llm_cap": limits.daily_llm_call_cap(),
        "llm_calls_today": limits.read_counter(f"llm_calls:{limits._today()}"),
        "student_reserve": student_reserve(),
        "jev": JEV_NOTE,
        "live_model": dict(zip(("provider", "model"), settings_store.llm_choice())),
        "keys": {p: llm.key_configured(p) for p in llm.PROVIDERS},
    }


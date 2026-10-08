"""Compare answering models on one question set, with the same judges for all (docs/SPEC.md, Block 8c).

    # On the local build machine (built index in the archive, keys in the git-ignored .env):
    uv run --no-project --python 3.12 --with-requirements evals/requirements.txt python -m evals.compare \\
        --questions evals/questions.course.jsonl \\
        --generator anthropic:claude-sonnet-5-5 --generator openai:gpt-6.1-sol \\
        --judge anthropic:claude-opus-5-5 --judge openai:gpt-6.1-sol --judge openrouter:google/gemini-3.8-flash \\
        --reps 2 --judge-retest 0.25 --budget 40

    # Plan and estimate only (no model call, no embedding):
    python -m evals.compare --questions evals/questions.course.jsonl --generator ... --judge ... --dry-run

Each question is answered by every generator in-process (`app.main.answer`, the real retrieval, the
active prompts and thresholds) with a per-call model override, `--reps` times (`--retest-types concept`
repeats only those questions). Every judge scores every answer; `--judge-retest 0.25` has each judge
score a random quarter of the answers a second time. Answering calls go straight to the provider like
the judges (`evals.judges.direct_complete`), so they never use the live site's DAILY_LLM_CALL_CAP;
both are still metered (eval_generate, eval_judge). Question embeddings are made once, in one Voyage
request, and cached in `evals/private/embed_cache/`.

Before anything runs it prints the estimate (calls and USD from the price table) and stops if the
estimate passes `--budget`; it also stops mid-run if the spend so far reaches the budget. A run folder
can be resumed: rows already in its `results.jsonl` are not asked again.

Writes `evals/private/runs/<UTC>/` (`results.jsonl`, `compare.md`, `compare_summary.md`,
`compare_summary.json`, `report.html`); for a question file outside `evals/private/` (the committed
course set) the shareable files are also written to `evals/reports/<UTC>/`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from app import eval_core, pricing

from . import dataset
from .judges import Judge, JudgeError, direct_complete
from .run import PRIVATE, ROOT, load_dotenv

REPORTS = ROOT / "evals" / "reports"
EMBED_CACHE = PRIVATE / "embed_cache"
MAX_GENERATORS = 6

# Rough tokens per call for the estimate, measured on the Oct 8 pilot (see evals/README.md).
TOKENS = {
    "classifier": (700, 80),
    "narration": (5200, 900),
    "judge": (5200, 700),
}
GENERATOR_CALLS_TYPICAL = 2  # logistics check + narration (a retry adds one)


# ---------------------------------------------------------------- plan

def parse_model(spec: str) -> dict[str, str]:
    provider, sep, model = spec.partition(":")
    if not sep or provider not in ("anthropic", "openai", "openrouter") or not model:
        raise JudgeError(f"{spec!r} must look like provider:model (anthropic, openai or openrouter)")
    return {"provider": provider, "model": model}


def key(m: dict[str, str]) -> str:
    return f"{m['provider']}:{m['model']}"


def plan_tasks(questions: list[dataset.Question], generators: list[dict[str, str]], reps: int,
               retest_types: set[str] | None) -> list[tuple[dataset.Question, dict[str, str], int]]:
    """Every (question, generator, rep). Rep 1 for every question; later reps only for `retest_types` (all if None).

    Ordered rep-major and then question-major, so run 1 finishes before any retest starts.
    """
    out = []
    for rep in range(1, reps + 1):
        for q in questions:
            if rep > 1 and retest_types is not None and (q.qtype or "") not in retest_types:
                continue
            for g in generators:
                out.append((q, g, rep))
    return out


def price(provider: str, model: str, live: dict[str, dict[str, Any]] | None) -> tuple[float, float] | None:
    """USD per token (input, output) from the price table, OpenRouter's live list for OpenRouter models,
    or the same model on OpenRouter as a stand-in."""
    table = pricing.current({})  # the defaults; a saved Settings table is read by app code, not here
    p = pricing.llm_price(table, provider, model, live)
    if p is None and live:
        dotted = model.replace("-5-5", "-5.5").replace("-5-1", "-5.1")
        vendor = {"anthropic": "anthropic", "openai": "openai"}.get(provider)
        for cand in ([f"{vendor}/{model}", f"{vendor}/{dotted}"] if vendor else []):
            p = pricing.llm_price(table, "openrouter", cand, live)
            if p:
                break
    return None if p is None else (p[0] / 1e6, p[1] / 1e6)


def estimate(tasks: list[tuple[Any, dict[str, str], int]], judges: list[dict[str, str]], judge_retest: float,
             live: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    """Calls and USD for the plan. Generator cost assumes every answer goes through narration (an upper
    bound: FAQ hits, declines and referrals call less)."""
    rows = []
    total = 0.0
    unknown = []
    per_gen: dict[str, int] = {}
    for _, g, _ in tasks:
        per_gen[key(g)] = per_gen.get(key(g), 0) + 1
    answers = len(tasks)
    for gk, n in per_gen.items():
        g = parse_model(gk)
        tin = n * (TOKENS["classifier"][0] + TOKENS["narration"][0])
        tout = n * (TOKENS["classifier"][1] + TOKENS["narration"][1])
        p = price(g["provider"], g["model"], live)
        cost = None if p is None else tin * p[0] + tout * p[1]
        rows.append({"model": gk, "role": "answers", "calls": n * GENERATOR_CALLS_TYPICAL, "cost_usd": cost})
        if cost is None:
            unknown.append(gk)
        else:
            total += cost
    judged = answers + int(round(answers * judge_retest))
    for j in judges:
        p = price(j["provider"], j["model"], live)
        cost = None if p is None else judged * (TOKENS["judge"][0] * p[0] + TOKENS["judge"][1] * p[1])
        rows.append({"model": key(j), "role": "judge", "calls": judged, "cost_usd": cost})
        if cost is None:
            unknown.append(key(j))
        else:
            total += cost
    return {"answers": answers, "judgements": judged * len(judges), "rows": rows, "total_usd": round(total, 2),
            "unknown": unknown}


def print_estimate(est: dict[str, Any], budget: float, out=print) -> None:
    out(f"Plan: {est['answers']} answers, {est['judgements']} judgements.")
    for r in est["rows"]:
        cost = "unknown" if r["cost_usd"] is None else f"${r['cost_usd']:.2f}"
        out(f"  {r['role']:8s} {r['model']:45s} {r['calls']:5d} calls  {cost}")
    out(f"Estimated total: ${est['total_usd']:.2f} (budget ${budget:.2f})"
        + (f"; no price for {', '.join(est['unknown'])}" if est["unknown"] else ""))


# ---------------------------------------------------------------- embeddings

def _hash(text: str, model: str) -> str:
    return hashlib.sha256(f"{model}\n{text}".encode("utf-8")).hexdigest()[:32]


class CachedEmbedder:
    """Question vectors made once (one Voyage request for all missing ones), cached on disk in evals/private/."""

    def __init__(self, texts: list[str], cache_dir: Path = EMBED_CACHE,
                 embed_many: Callable[[list[str]], list[np.ndarray]] | None = None):
        from app import embed

        self.model = embed.model_name()
        self.cache_dir = cache_dir
        self.vectors: dict[str, np.ndarray] = {}
        missing = []
        for t in dict.fromkeys(texts):
            path = cache_dir / f"{_hash(t, self.model)}.npy"
            if path.exists():
                self.vectors[t] = np.load(path)
            else:
                missing.append(t)
        if missing:
            if embed_many is None:
                from scripts.threshold_table import voyage_embed_many as embed_many
            cache_dir.mkdir(parents=True, exist_ok=True)
            for start in range(0, len(missing), 100):
                chunk = missing[start:start + 100]
                for t, v in zip(chunk, embed_many(chunk)):
                    np.save(cache_dir / f"{_hash(t, self.model)}.npy", v)
                    self.vectors[t] = v
        self.made = len(missing)

    def __call__(self, text: str) -> np.ndarray:
        return self.vectors[text]


# ---------------------------------------------------------------- run

class Spend:
    """Running USD total of answers and judgements, priced like the estimate."""

    def __init__(self, live: dict[str, dict[str, Any]] | None):
        self.live = live
        self.total = 0.0
        self.by_model: dict[str, float] = {}
        self._lock = threading.Lock()

    def add(self, model_key: str, usd: float | None) -> None:
        if usd is None:
            return
        with self._lock:
            self.total += usd
            self.by_model[model_key] = self.by_model.get(model_key, 0.0) + usd

    def judge_cost(self, judge: dict[str, str], j: dict[str, Any]) -> float | None:
        u = j.get("usage") or {}
        p = price(judge["provider"], judge["model"], self.live)
        if p is None or not u:
            return None
        return u.get("tokens_in", 0) * p[0] + u.get("tokens_out", 0) * p[1]


def judge_all(item: dict[str, Any], judges: list[Judge], spend: Spend) -> list[dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=max(1, len(judges))) as pool:
        out = list(pool.map(lambda j: j.judge(item), judges))
    for j, res in zip(judges, out):
        c = spend.judge_cost({"provider": j.provider, "model": j.model}, res)
        res.setdefault("usage", {})["cost_usd"] = None if c is None else round(c, 6)
        spend.add(j.name, c)
    return out


def item_for(q: dataset.Question, response: dict[str, Any], web_path: bool) -> dict[str, Any]:
    return {
        "question": q.question, "category": q.category, "answerable": q.answerable,
        "reference_answer": q.reference_answer, "response": response,
        "expected_kind": eval_core.expected_routes(q.as_dict(), web_path) if q.expected_kind else None,
        "must_include": list(q.must_include), "must_not": list(q.must_not),
    }


def eval_search(system: str, user: str, max_tokens: int, provider: str | None = None, model: str | None = None,
                client: Any = None):
    """The app's web search call (app/web_answer.py), with Claude routed through OpenRouter's web search when
    FT_EVAL_ROUTE_ANTHROPIC=openrouter (the direct Anthropic key cannot be used)."""
    from app import web_answer

    from .judges import route_of

    if provider and model:
        provider, model = route_of(provider, model)
    return web_answer.search(system, user, max_tokens, provider=provider, model=model, client=client)


def offline_caps() -> None:
    """A comparison runs in its own process on the local build machine: it must never use up the live site's
    daily model-call cap or today's web-answer budget (students share both). Its spend is bounded by --budget."""
    from app import limits

    limits.take_llm_call = lambda *a, **k: True
    try:
        from app import web_answer

        web_answer.take_budget = lambda *a, **k: True
    except ImportError:  # before the web path existed
        pass


def web_path_available() -> bool:
    from app.admin_evals import web_path_available as available

    return available()


def run(questions: list[dataset.Question], generators: list[dict[str, str]], judges: list[Judge], reps: int,
        retest_types: set[str] | None, judge_retest: float, budget: float, out_dir: Path,
        make_target: Callable[[dict[str, str]], Any], spend: Spend, seed: int = 8, concurrency: int = 5,
        log=print) -> list[dict[str, Any]]:
    """Answer and judge every task, appending each row to `out_dir/results.jsonl` (resumable)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "results.jsonl"
    done: dict[tuple[str, str, int], dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done[(r["qid"], r["generator"], r["rep"])] = r
                for jj in r.get("judgements", []) + r.get("judgements_retest", []):
                    spend.add(jj.get("judge", "?"), (jj.get("usage") or {}).get("cost_usd"))
                spend.add(r["generator"], ((r.get("response") or {}).get("usage") or {}).get("cost_usd"))
    web_path = web_path_available()
    tasks = [t for t in plan_tasks(questions, generators, reps, retest_types) if (t[0].qid, key(t[1]), t[2]) not in done]
    lock = threading.Lock()
    targets: dict[str, Any] = {}

    def target_for(g: dict[str, str]):
        with lock:
            if key(g) not in targets:
                targets[key(g)] = make_target(g)
            return targets[key(g)]
    stop = threading.Event()
    counter = {"n": 0}

    def one(task) -> None:
        q, g, rep = task
        if stop.is_set():
            return
        if spend.total >= budget:
            stop.set()
            log(f"Stopped: spend ${spend.total:.2f} reached the budget ${budget:.2f}.")
            return
        response = target_for(g).ask(q.question)
        spend.add(key(g), (response.get("usage") or {}).get("cost_usd"))
        row = {"qid": q.qid, "rep": rep, "generator": key(g), **q.as_dict(), "web_path": web_path,
               "response": response, "judgements": []}
        row["qid"] = q.qid
        if response["status"] in ("ok", "not_covered"):
            row["judgements"] = judge_all(item_for(q, response, web_path), judges, spend)
        with lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            done[(q.qid, key(g), rep)] = row
            counter["n"] += 1
            verdicts = " ".join(j.get("verdict", "err")[0] for j in row["judgements"])
            log(f"[{counter['n']}/{len(tasks)}] {q.qid} rep{rep} {key(g)}: {eval_core.route_of(response) or response['status']}"
                f" {response.get('latency_ms', 0) / 1000:.1f}s [{verdicts}] spend ${spend.total:.2f}")

    # One worker per generator keeps each provider at a polite rate.
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        list(pool.map(one, tasks))

    rows = sorted(done.values(), key=lambda r: (r["rep"], r["qid"], r["generator"]))
    # Judge retest: the same judge scores a random share of the run-1 answers again.
    if judge_retest > 0 and not stop.is_set():
        rng = random.Random(seed)
        pool_rows = [r for r in rows if r["rep"] == 1 and r["judgements"] and not r.get("judgements_retest")]
        already = sum(1 for r in rows if r.get("judgements_retest"))
        want = max(0, int(round(len([r for r in rows if r["rep"] == 1 and r["judgements"]]) * judge_retest)) - already)
        sample = rng.sample(pool_rows, min(want, len(pool_rows)))
        q_by_id = {q.qid: q for q in questions}

        def again(r: dict[str, Any]) -> None:
            if spend.total >= budget:
                stop.set()
                return
            r["judgements_retest"] = judge_all(item_for(q_by_id[r["qid"]], r["response"], r.get("web_path", web_path)),
                                               judges, spend)

        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            list(pool.map(again, sample))
        log(f"Judge retest: {len(sample)} answers scored again by every judge. Spend ${spend.total:.2f}.")
        with path.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return rows


# ---------------------------------------------------------------- main

def _live_prices() -> dict[str, dict[str, Any]] | None:
    try:
        from app import llm

        return {m["id"]: m for m in llm.list_models("openrouter").get("models", [])}
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--questions")
    p.add_argument("--generator", action="append", default=[], metavar="PROVIDER:MODEL")
    p.add_argument("--judge", action="append", default=[], metavar="PROVIDER:MODEL")
    p.add_argument("--reps", type=int, default=1, help="answers per question x model (2 or 3 for test-retest)")
    p.add_argument("--retest-types", default="", help="comma list of question types to repeat (default: all)")
    p.add_argument("--only-types", default="", help="comma list: run only these question types (e.g. beyond)")
    p.add_argument("--judge-retest", type=float, default=0.25, help="share of run-1 answers every judge scores twice")
    p.add_argument("--budget", type=float, default=40.0, help="USD: refuse a plan estimated above this, stop at it")
    p.add_argument("--spent-elsewhere", type=float, default=0.0, help="USD already spent today on this comparison")
    p.add_argument("--concurrency", type=int, default=0, help="parallel answers (default: one per generator)")
    p.add_argument("--seed", type=int, default=8)
    p.add_argument("--label", default="", help="a name for the run in reports and Settings")
    p.add_argument("--out", help="run folder (default evals/private/runs/<UTC>); an existing one is resumed")
    p.add_argument("--dry-run", action="store_true", help="print the plan and estimate, call nothing")
    p.add_argument("--yes", action="store_true", help="start without asking after the estimate")
    p.add_argument("--report-only", action="store_true", help="rebuild the reports of --out from its results.jsonl")
    args = p.parse_args(argv)

    load_dotenv()
    if args.report_only:
        if not args.out:
            print("--report-only needs --out <run folder>", file=sys.stderr)
            return 2
        return report_only(Path(args.out))
    if not (args.questions and args.generator and args.judge):
        print("--questions, at least one --generator and at least one --judge are required", file=sys.stderr)
        return 2
    try:
        questions = dataset.load(args.questions)
    except (OSError, dataset.DatasetError) as exc:
        print(f"Cannot use {args.questions}: {exc}", file=sys.stderr)
        return 2
    only = {t.strip() for t in args.only_types.split(",") if t.strip()}
    if only:
        questions = [q for q in questions if (q.qtype or "") in only]
    try:
        generators = [parse_model(s) for s in args.generator]
        judges = [Judge.parse_spec(s) for s in args.judge]
    except JudgeError as exc:
        print(exc, file=sys.stderr)
        return 2
    if not 1 <= len(generators) <= MAX_GENERATORS:
        print(f"Pick 1 to {MAX_GENERATORS} generators.", file=sys.stderr)
        return 2
    if len({key(g) for g in generators}) != len(generators):
        print("The same generator is listed twice.", file=sys.stderr)
        return 2
    retest = {t.strip() for t in args.retest_types.split(",") if t.strip()} or None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out) if args.out else PRIVATE / "runs" / stamp
    done = done_keys(out / "results.jsonl")
    tasks = [t for t in plan_tasks(questions, generators, max(1, args.reps), retest)
             if (t[0].qid, key(t[1]), t[2]) not in done]
    if done:
        print(f"Resuming {out}: {len(done)} answers already there.")
    live = _live_prices()
    est = estimate(tasks, [{"provider": j.provider, "model": j.model} for j in judges], args.judge_retest, live)
    print(f"{len(questions)} questions from {Path(args.questions).name}; {len(generators)} generators; "
          f"{len(judges)} judges; reps {args.reps}" + (f" (retest: {', '.join(sorted(retest))})" if retest else ""))
    print_estimate(est, args.budget)
    left = args.budget - args.spent_elsewhere
    if est["total_usd"] > left:
        print(f"Estimate ${est['total_usd']:.2f} is over the budget left (${left:.2f}). Shrink the plan "
              "(--retest-types concept, --reps 2, --judge-retest 0.25) or raise --budget.", file=sys.stderr)
        return 4
    if args.dry_run:
        return 0
    from app import llm

    missing = [f"{key(g)}: {llm.KEY_VARS[g['provider']]} is not set" for g in generators if not llm.key_configured(g["provider"])]
    missing += [f"{j.name}: {why}" for j in judges if (why := j.ready())]
    if missing:
        print("Not ready:\n  " + "\n  ".join(missing), file=sys.stderr)
        return 3
    if not args.yes:
        if input("Start? [y/N] ").strip().lower() != "y":
            return 1

    from app import main as app_main
    from app import storage

    offline_caps()

    from .targets import InProcessTarget

    content = storage.store.get_or_503()
    embedder = CachedEmbedder([q.question for q in questions])
    print(f"Embeddings: {embedder.made} new, {len(questions) - embedder.made} from the cache.")
    retriever = app_main.get_retriever()

    def make_target(g: dict[str, str]):
        return InProcessTarget(retriever, embedder, direct_complete, content, provider=g["provider"], model=g["model"],
                               searcher=eval_search if web_path_available() else None)

    spend = Spend(live)
    started = time.monotonic()
    rows = run(questions, generators, judges, max(1, args.reps), retest, args.judge_retest, left, out, make_target,
               spend, seed=args.seed, concurrency=args.concurrency or len(generators))
    meta = {
        "label": args.label or out.name,
        "run_id": out.name,
        "questions_file": Path(args.questions).name,
        "private": _is_private(Path(args.questions)),
        "generators": [key(g) for g in generators],
        "judges": [j.name for j in judges],
        "reps": args.reps,
        "retest_types": sorted(retest) if retest else "all",
        "judge_retest": args.judge_retest,
        "only_types": sorted(only) or None,
        "web_path": web_path_available(),
        "minutes": round((time.monotonic() - started) / 60, 1),
        "spend_usd": round(spend.total, 2),
        "spend_by_model": {k: round(v, 3) for k, v in sorted(spend.by_model.items())},
        "estimate_usd": est["total_usd"],
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    old = _read_meta(out)
    if old.get("spend_usd") and old.get("run_id") == meta["run_id"]:
        meta["sessions"] = (old.get("sessions") or 1) + 1
    (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Spend: ${spend.total:.2f} (all sessions of this run folder).")
    return report_only(out)


def _read_meta(out: Path) -> dict[str, Any]:
    try:
        return json.loads((out / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def done_keys(path: Path) -> set[tuple[str, str, int]]:
    if not path.exists():
        return set()
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out.add((r["qid"], r["generator"], r["rep"]))
    return out


def report_only(out: Path) -> int:
    """Write every report for a run folder from its results.jsonl and meta.json."""
    from . import compare_report

    rows = [json.loads(line) for line in (out / "results.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    meta = _read_meta(out)
    written = compare_report.write_all(rows, meta, out, REPORTS / out.name if not meta.get("private", True) else None)
    print("Wrote " + ", ".join(str(w) for w in written))
    return 0


def _is_private(path: Path) -> bool:
    """A question file under any evals/private/ folder (this checkout's or another's) holds real questions."""
    parts = path.resolve().parts
    return any(a == "evals" and b == "private" for a, b in zip(parts, parts[1:]))


if __name__ == "__main__":
    raise SystemExit(main())

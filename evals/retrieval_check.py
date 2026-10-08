"""Routing and retrieval only: which route answers and which slides it shows. No judges, no narration.

    # On the local build machine (built index, keys in the git-ignored .env):
    uv run --no-project --with-requirements requirements.txt python -m evals.retrieval_check
    uv run --no-project --with-requirements requirements.txt python -m evals.retrieval_check --threshold 0.54

Asks every question through `app.main.answer` (the function behind /api/ask, "All courses") with the real
index, the real retriever and the cached question embeddings (`evals/private/embed_cache/`). It spends
almost nothing:
- The classifier calls that decide a route (logistics, web scope, instructor alert) go to the active model
  once per question and are cached in `evals/private/routing_cache/`, so a second run, or a run with
  another threshold or another selection rule, calls nothing.
- Every other model call (narration, the Canvas answer, the helper slide) is refused, so narration falls
  back to the notes, and the web answer is a stub: the route is decided before either runs.

Prints aggregates only (never question text), per question file:
- right route, retrieval hit, slide precision and one course only, as in Settings > Evals (app/eval_core.py)
- slide recall: the share of a question's expected slides the answer showed
- hit and precision where a copy counts: a slide with the same words as an expected slide (the same slide
  in another session or the other course) counts as that slide. The two courses share many slides, and in
  "All courses" either copy is a right answer.
- answers that show one slide twice (two slides with the same words), and the mean slides per answer
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import dataset
from .run import PRIVATE, ROOT, load_dotenv

CACHE = PRIVATE / "routing_cache"
CLASSIFIERS = {"logistics", "web_scope", "incident_classifier"}
DEFAULT_SETS = (ROOT / "evals" / "questions.course.jsonl", PRIVATE / "questions.jsonl")


def cached_classifier(complete: Callable[..., str], cache_dir: Path | None = None) -> Callable[..., str]:
    """A completer that answers route classifiers (cached on disk) and refuses every other model call."""
    from app import llm, usage

    cache_dir = cache_dir or CACHE

    def completer(system: str, user: str, max_tokens: int, provider: str | None = None, model: str | None = None,
                  **_: Any) -> str:
        purpose = usage.current_purpose()
        if purpose not in CLASSIFIERS:
            raise llm.LLMError(f"retrieval check: no {purpose} call")
        key = hashlib.sha256(f"{purpose}\n{provider}:{model}\n{system}\n{user}".encode()).hexdigest()[:40]
        path = cache_dir / f"{key}.json"
        if path.exists():
            return json.loads(path.read_text())["out"]
        out = complete(system, user, max_tokens, provider=provider, model=model)
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"out": out}))
        return out

    return completer


def _stub_web(question: str, related: Any, follow_ups: Any, searcher: Any, provider: str | None = None,
              model: str | None = None) -> Any:
    from app import web_answer

    return web_answer.Result(reply={"kind": "web", "message": "(web answer not generated)", "links": []},
                             source="llm")


def slide_words(rec: dict[str, Any] | None) -> str:
    """The words on a slide (title, text, OCR text), lowercased. Kept here, not imported from app/retrieval.py,
    so the check measures any version of the retrieval code the same way."""
    if not rec:
        return ""
    parts = (rec.get("title"), rec.get("text"), rec.get("ocr_text"))
    return " ".join(re.findall(r"[a-z0-9]+", " ".join(str(p or "") for p in parts).lower()))


def _rate(flags: list[bool | None]) -> list[int]:
    vals = [f for f in flags if f is not None]
    return [sum(1 for f in vals if f), len(vals)]


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def measure(questions: list[dataset.Question], content: Any, retriever: Any, embedder: Callable[[str], Any],
            completer: Callable[..., str], web_path: bool = True) -> dict[str, Any]:
    """Ask every question; return the aggregates (counts as [yes, of n], means rounded to 3 places)."""
    from app import eval_core, main, usage
    from app.admin_evals import response_from

    def words(slide_id: str) -> str:
        return slide_words(content.record(slide_id))

    rows: dict[str, list[Any]] = {k: [] for k in ("route", "hit", "hit_copy", "precision", "precision_copy",
                                                  "pure", "recall", "repeat", "n")}
    for q in questions:
        with usage.purpose("eval_generate"):
            playlist, info = main.answer(q.question, None, content, retriever, embedder, completer)
        resp = response_from(playlist, info, content, 0)
        m = eval_core.answer_metrics({**q.as_dict(), "response": resp}, web_path)
        slides = eval_core.response_slides(resp)
        expected = set(q.expected_slides)
        copies = {words(e) for e in expected} - {""}
        rows["route"].append(m["route_ok"])
        rows["hit"].append(m["hit"])
        rows["pure"].append(m["pure"])
        if m["hit"] is not None:
            rows["hit_copy"].append(any(words(s) in copies for s in slides))
            rows["recall"].append(len(expected & set(slides)) / len(expected))
        if m["precision"] is not None:
            rows["precision"].append(m["precision"])
            rows["precision_copy"].append(sum(1 for s in slides if words(s) in copies) / len(slides))
        if slides:
            seen = [w for w in (words(s) for s in slides) if w]
            rows["repeat"].append(len(seen) != len(set(seen)))
            rows["n"].append(len(slides))
    return {
        "questions": len(questions),
        "right_route": _rate(rows["route"]),
        "retrieval_hit": _rate(rows["hit"]),
        "retrieval_hit_copy_counts": _rate(rows["hit_copy"]),
        "slide_precision": _mean(rows["precision"]),
        "slide_precision_copy_counts": _mean(rows["precision_copy"]),
        "slide_recall": _mean(rows["recall"]),
        "one_course_only": _rate(rows["pure"]),
        "answers_showing_a_slide_twice": _rate(rows["repeat"]),
        "mean_slides_per_answer": _mean(rows["n"]),
    }


LABELS = [
    ("right_route", "Right route"),
    ("retrieval_hit", "Retrieval hit"),
    ("retrieval_hit_copy_counts", "Retrieval hit (a copy counts)"),
    ("slide_precision", "Slide precision"),
    ("slide_precision_copy_counts", "Slide precision (a copy counts)"),
    ("slide_recall", "Slide recall"),
    ("one_course_only", "One course only"),
    ("answers_showing_a_slide_twice", "Answers showing a slide twice"),
    ("mean_slides_per_answer", "Slides per answer"),
]


def table(results: dict[str, dict[str, Any]]) -> str:
    """A markdown table: one row per measure, one column per question file."""
    names = list(results)
    lines = ["| Measure | " + " | ".join(names) + " |", "| --- |" + " --- |" * len(names)]
    for key, label in LABELS:
        cells = []
        for name in names:
            v = results[name][key]
            cells.append("n/a" if v is None or (isinstance(v, list) and not v[1])
                         else f"{v[0]}/{v[1]}" if isinstance(v, list) else f"{v:.3f}".rstrip("0").rstrip("."))
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--questions", action="append", help="a question file (default: the course set and the "
                                                        "private set when it exists)")
    p.add_argument("--threshold", type=float, help="slide threshold (default: NOT_COVERED_THRESHOLD in code)")
    p.add_argument("--json", action="store_true", help="print JSON instead of a table")
    p.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = p.parse_args(argv)
    load_dotenv(args.env_file)

    from app import main as app_main
    from app import retrieval, storage, web_answer

    from .compare import CachedEmbedder, offline_caps, web_path_available
    from .judges import direct_complete

    offline_caps()
    web_answer.answer = _stub_web
    paths = [Path(q) for q in args.questions] if args.questions else [p for p in DEFAULT_SETS if p.exists()]
    threshold = retrieval.NOT_COVERED_THRESHOLD if args.threshold is None else args.threshold
    retriever = app_main.Retriever(retrieval.rank, retrieval.select_segments, threshold)
    content = storage.store.get_or_503()
    completer = cached_classifier(direct_complete)
    results = {}
    for path in paths:
        questions = dataset.load(path)
        embedder = CachedEmbedder([q.question for q in questions])
        results[path.name] = measure(questions, content, retriever, embedder, completer, web_path_available())
    if args.json:
        print(json.dumps({"threshold": threshold, "results": results}, indent=2))
    else:
        print(f"Slide threshold {threshold}\n")
        print(table(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

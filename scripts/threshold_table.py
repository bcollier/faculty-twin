"""Print each question's top retrieval score, to re-check NOT_COVERED_THRESHOLD.

This is the table the threshold in app/retrieval.py was chosen from: on-topic
questions should score at or above it, off-topic questions below it. See
docs/TESTING_AND_SCORES.md ("Re-check the threshold").

- Scores come from `app.retrieval.rank` (the same function /api/ask uses) over the
  slides students can see, from the local index in CONTENT_DIR.
- All questions are embedded in one Voyage request (same model and input type
  as `/api/ask`), so the free tier's 3 requests a minute is not a problem. That
  request does not count against the site's DAILY_EMBED_CAP.
- With no --questions file it uses ten invented questions (five on-topic, five
  off-topic). A questions file is JSON Lines, one object per line:
  {"question": "...", "label": "on"}   or   {"question": "...", "label": "off"}
  Keep real student questions out of git: put such a file in evals/private/.

Run on the local build machine (the one that holds the private archive) from
~/Code/faculty-twin. VOYAGE_API_KEY, VOYAGE_MODEL and CONTENT_DIR are read from
the git-ignored .env when they are not already set:
    uv run --no-project --with-requirements requirements.txt python -m scripts.threshold_table
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config, embed, playlist, retrieval, storage  # noqa: E402
from indexer.common import load_env  # noqa: E402

ON, OFF = "on", "off"

# Invented for this script. Nothing here comes from a student.
DEFAULT_QUESTIONS: list[dict[str, str]] = [
    {"question": "How does k-means decide which cluster a point belongs to?", "label": ON},
    {"question": "What is a word embedding?", "label": ON},
    {"question": "How does a convolutional neural network find edges in an image?", "label": ON},
    {"question": "What is the difference between supervised and unsupervised learning?", "label": ON},
    {"question": "How does a large language model predict the next word?", "label": ON},
    {"question": "Who won the Stanley Cup last year?", "label": OFF},
    {"question": "What is a good recipe for banana bread?", "label": OFF},
    {"question": "How do I change a flat tire on my bike?", "label": OFF},
    {"question": "What is the capital of Australia?", "label": OFF},
    {"question": "Can you recommend a movie to watch tonight?", "label": OFF},
]


@dataclass
class Row:
    """One question's top retrieval score and the slide it came from."""

    question: str
    label: str
    top_score: float | None
    slide_id: str | None


def load_questions(path: str | None) -> list[dict[str, str]]:
    """The questions to score: the defaults, or a JSON Lines file of {question, label}."""
    if not path:
        return [dict(q) for q in DEFAULT_QUESTIONS]
    out = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        item = json.loads(line)
        label = str(item.get("label", "")).lower()
        if label not in (ON, OFF) or not str(item.get("question", "")).strip():
            raise SystemExit(f"{path} line {n}: need a question and a label of \"on\" or \"off\"")
        out.append({"question": str(item["question"]).strip(), "label": label})
    if not out:
        raise SystemExit(f"{path} has no questions")
    return out


def voyage_embed_many(texts: list[str]) -> list[np.ndarray]:
    """Embed many questions in one Voyage request (input_type "query", the app's model)."""
    url, headers, body = embed.build_request(texts[0])  # raises EmbeddingError without VOYAGE_API_KEY
    body["input"] = list(texts)
    resp = httpx.post(url, headers=headers, json=body, timeout=httpx.Timeout(60.0, connect=5.0))
    if resp.status_code >= 400:
        raise embed.EmbeddingError(f"Voyage returned {resp.status_code}: {resp.text[:200]}")
    data = sorted(resp.json().get("data", []), key=lambda d: d.get("index", 0))
    if len(data) != len(texts):
        raise embed.EmbeddingError("Voyage returned a different number of embeddings than questions")
    return [np.asarray(d["embedding"], dtype=np.float32) for d in data]


def score(
    questions: list[dict[str, str]],
    records: list[dict[str, Any]],
    matrix: np.ndarray,
    embed_many: Callable[[list[str]], list[np.ndarray]],
    rank: Callable[[np.ndarray, np.ndarray], list[tuple[int, float]]] = retrieval.rank,
) -> list[Row]:
    """Each question's top score and best slide, using `rank` as /api/ask does."""
    vectors = embed_many([q["question"] for q in questions])
    rows = []
    for q, vec in zip(questions, vectors):
        if vec.shape[-1] != matrix.shape[1]:
            raise SystemExit(f"Question vectors have {vec.shape[-1]} dims but the index has {matrix.shape[1]}. "
                             "VOYAGE_MODEL must match the model the index was built with.")
        ranked = rank(vec, matrix) if len(records) else []
        top = ranked[0] if ranked else None
        rows.append(Row(q["question"], q["label"], float(top[1]) if top else None,
                        records[top[0]].get("id") if top else None))
    return rows


def summarize(rows: list[Row], threshold: float | None) -> dict[str, Any]:
    """Lowest on-topic score, highest off-topic score, the gap, and what the threshold gets wrong."""
    on = [r.top_score for r in rows if r.label == ON and r.top_score is not None]
    off = [r.top_score for r in rows if r.label == OFF and r.top_score is not None]
    lowest_on = min(on) if on else None
    highest_off = max(off) if off else None
    gap = None if lowest_on is None or highest_off is None else lowest_on - highest_off
    wrong_declines = [r for r in rows if r.label == ON and threshold is not None
                      and (r.top_score is None or r.top_score < threshold)]
    wrong_answers = [r for r in rows if r.label == OFF and threshold is not None
                     and r.top_score is not None and r.top_score >= threshold]
    return {
        "threshold": threshold,
        "on_topic_range": (min(on), max(on)) if on else None,
        "off_topic_range": (min(off), max(off)) if off else None,
        "lowest_on": lowest_on,
        "highest_off": highest_off,
        "gap": gap,
        "separates": gap is not None and gap > 0 and not wrong_declines and not wrong_answers,
        "wrong_declines": [r.question for r in wrong_declines],
        "wrong_answers": [r.question for r in wrong_answers],
    }


def _fmt(x: float | None) -> str:
    return "" if x is None else f"{x:.3f}"


def render(rows: list[Row], summary: dict[str, Any]) -> str:
    """The Markdown table, highest score first, and what the threshold gets right or wrong."""
    threshold = summary["threshold"]
    lines = [
        f"Threshold: {_fmt(threshold) or 'none'}  (app/retrieval.py NOT_COVERED_THRESHOLD)",
        "",
        "| Label | Top score | Over threshold | Best slide | Question |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in sorted(rows, key=lambda r: (r.top_score is None, -(r.top_score or 0))):
        over = "" if threshold is None or r.top_score is None else ("yes" if r.top_score >= threshold else "no")
        lines.append(f"| {r.label}-topic | {_fmt(r.top_score)} | {over} | {r.slide_id or ''} | {r.question} |")
    lines.append("")
    if summary["on_topic_range"]:
        lines.append("On-topic:  {:.3f} to {:.3f}".format(*summary["on_topic_range"]))
    if summary["off_topic_range"]:
        lines.append("Off-topic: {:.3f} to {:.3f}".format(*summary["off_topic_range"]))
    if summary["gap"] is not None:
        if summary["gap"] > 0:
            lines.append(f"Gap: {summary['gap']:.3f}. Any threshold above {summary['highest_off']:.3f} and at or "
                         f"below {summary['lowest_on']:.3f} separates these questions.")
        else:
            lines.append(f"No gap: the groups overlap by {-summary['gap']:.3f}. No single threshold separates them.")
    for q in summary["wrong_declines"]:
        lines.append(f"Would be declined (on-topic, under the threshold): {q}")
    for q in summary["wrong_answers"]:
        lines.append(f"Would be answered (off-topic, at or over the threshold): {q}")
    lines.append("Result: the threshold separates every question." if summary["separates"]
                 else "Result: the threshold does not separate every question. Look at the rows above.")
    return "\n".join(lines)


def load_slides(course: str | None) -> tuple[list[dict[str, Any]], np.ndarray]:
    """The slides students can search (visible sessions only), from the local index in CONTENT_DIR."""
    root = config.content_dir()
    if root is None:
        raise SystemExit("Set CONTENT_DIR to the folder that holds content/index.json and content/embeddings.npy.")
    content = storage.load_local(root)
    return playlist.searchable(content, course)  # slides only, visible sessions only, like /api/ask


def main(argv: list[str] | None = None, embed_many: Callable | None = None) -> int:
    """Command line: score on- and off-topic questions against the slides and print the threshold table.

    Exit 1 when no threshold separates the two groups, so a changed index that blurs them is noticed.
    """
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--questions", help="JSON Lines file of {question, label: on|off} (default: 10 invented ones)")
    p.add_argument("--course", choices=config.COURSE_CODES, help="score against one course only (default: all)")
    p.add_argument("--threshold", type=float, help="try a different threshold (default: the one in app/retrieval.py)")
    p.add_argument("--env-file", help="read keys and CONTENT_DIR from this file when not already set (default ./.env)")
    args = p.parse_args(argv)
    load_env(Path(args.env_file) if args.env_file else None)  # never overrides the environment
    threshold = args.threshold if args.threshold is not None else retrieval.NOT_COVERED_THRESHOLD
    questions = load_questions(args.questions)
    records, matrix = load_slides(args.course)
    rows = score(questions, records, matrix, embed_many or voyage_embed_many)
    summary = summarize(rows, threshold)
    print(render(rows, summary))
    return 0 if summary["separates"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Check the private eval question set and upload it to the private bucket for Settings > Evals.

    uv run --no-project --with-requirements requirements.txt python -m scripts.upload_eval_questions

Runs on the local build machine, which holds `evals/private/questions.jsonl`
(git-ignored, de-identified, PG; see evals/README.md). Steps:

1. `evals/dataset.py` loads the file and runs every privacy check (emails,
   URLs, long numbers, keys, names caught by app/privacy.py). Any failure stops
   the upload, naming the line and the problem, never the text.
2. If the bucket already holds questions that are not in the local file (for
   example, ones typed in Settings), it stops unless you pass `--force`.
3. It uploads the file as-is to `evals/questions.jsonl` in the private bucket
   (SUPABASE_BUCKET), so question ids (q001 = line 1) match command-line runs.

It prints counts and categories only, never question text. Keys come from the
environment or the git-ignored `.env` (`--env-file`).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from app import eval_store
from evals import dataset
from evals.run import PRIVATE, ROOT, load_dotenv


def run(questions: Path, bucket: eval_store.Bucket, force: bool = False,
        out: Callable[[str], None] = print) -> int:
    """Check the local file, compare it with the bucket copy, upload it. Returns the exit code."""
    try:
        qs = dataset.load(questions)
    except OSError as exc:
        out(f"Cannot read {questions.name}: {type(exc).__name__}")
        return 2
    except dataset.DatasetError as exc:
        out(f"Privacy check failed, nothing uploaded: {exc}")
        return 2
    text = questions.read_text(encoding="utf-8")
    answerable = sum(1 for q in qs if q.answerable)
    out(f"{len(qs)} questions passed the checks ({answerable} answerable from course material).")
    for cat, n in dataset.category_counts(qs):
        out(f"  {cat}: {n}")
    try:
        existing = eval_store.read_questions_text(bucket)
    except eval_store.StoreError as exc:
        out(f"Could not read the bucket: {exc}")
        return 3
    if existing is not None:
        local = {line.strip() for line in text.splitlines() if line.strip()}
        extra = [line for line in existing.splitlines() if line.strip() and line.strip() not in local]
        if existing == text:
            out("The bucket already has exactly this file. Nothing to do.")
            return 0
        if extra and not force:
            out(f"The bucket copy has {len(extra)} question(s) that are not in the local file (added or edited in "
                "Settings?). Copy them into the local file first, or pass --force to overwrite them.")
            return 4
    try:
        eval_store.write_questions_text(bucket, text)
    except eval_store.StoreError as exc:
        out(f"Upload failed: {exc}")
        return 3
    out(f"Uploaded to {eval_store.QUESTIONS} in the private bucket.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--questions", type=Path, default=PRIVATE / "questions.jsonl")
    p.add_argument("--env-file", type=Path, default=ROOT / ".env")
    p.add_argument("--force", action="store_true", help="overwrite questions that exist only in the bucket")
    args = p.parse_args(argv)
    load_dotenv(args.env_file)
    from app import config

    if not config.supabase_configured():
        print("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (environment or .env).", file=sys.stderr)
        return 3
    return run(args.questions, eval_store.SupabaseBucket(), args.force)


if __name__ == "__main__":
    raise SystemExit(main())


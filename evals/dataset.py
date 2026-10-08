"""Load, check, and choose the student questions an eval run uses.

A question file is JSON Lines, one question per line:

    {"month": "2026-09", "course": "70-445 AI Methods", "category": "CODE_HELP",
     "question": "...", "reference_answer": "..." | null,
     "answerable_from_course_materials": true}

Real questions come from Ben's email, rewritten by an AI so the student cannot
be recognised by name, details, or writing style, and kept PG. They live only
in `evals/private/` (git-ignored) and never enter git. `evals/questions.example.jsonl`
holds invented examples in the same shape for tests and dry runs.

Every question is checked again here before anything is sent to a model: the
roster-free scrub from `app/privacy.py` runs over the text, and a record that
still looks like it carries an email, a long id, a URL, or a key is refused.
"""

from __future__ import annotations

from pathlib import Path

# The format, the checks, and the selection live in app/eval_core.py, so the
# Settings upload path and admin runs on Vercel apply exactly the same rules.
from app.eval_core import (  # noqa: F401  (re-exported)
    _LEAKS,
    CATEGORIES,
    DatasetError,
    Question,
    _record,
    category_counts,
    leak_reasons,
    parse_lines,
    parse_record,
    select_top,
)


def load(path: str | Path) -> list[Question]:
    """Read a question file. Lines are kept in file order (most recent first by convention)."""
    return parse_lines(Path(path).read_text(encoding="utf-8"))

"""The judge prompt and the checked shape of a judgement.

Each judge model sees one student question, what the twin did with it (the
narration it would speak, or that it declined), the slide material the
narration was meant to come from, and, when Ben or a TA answered the real
email, a paraphrase of that answer. It scores six dimensions from 1 to 5.

The dimensions follow the spec's rules for the twin (docs/SPEC.md, "Safety,
cost and privacy rules"): speak only from the course material, decline what the
material does not cover, never make promises on Ben's behalf, keep it PG.

The rubric itself lives in `app/eval_core.py`, so Settings > Evals on Vercel
(which never ships `evals/`) grades with exactly the same prompt and parser.
The prompt text is the `eval_judge` prompt in `app/prompts.py`, which Ben can
edit in Settings. This module re-exports it all under the names the CLI has
always used.
"""

from __future__ import annotations

from app import prompts  # noqa: F401  (re-exported for callers that patch it)
from app.eval_core import (  # noqa: F401  (re-exported)
    DIMENSIONS,
    MATERIAL_LIMIT,
    PROMPT_NAME,
    SYSTEM_PROMPT,
    JudgementError,
    _clip,
    _extract_json,
    build_user_prompt,
    dimensions_text,
    extract_json,
    parse,
    system_prompt,
)

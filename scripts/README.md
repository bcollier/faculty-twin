# scripts/: checks and one-off tools

*AI-generated documentation (Claude Code, Claude Opus 5.5, October 8, 2026).*

Command-line tools run by hand on the local build machine, from the repo root. They read keys from the git-ignored `.env` and are not deployed (`.vercelignore` leaves this folder out). How each fits into testing: [docs/TESTING_AND_SCORES.md](../docs/TESTING_AND_SCORES.md#run-a-new-test).

## Files

| File | What it does | Touches |
| --- | --- | --- |
| `live_smoke.py` | Two-minute check of the deployed site: health, login, topics, on-topic, off-topic, FAQ, course-info and suggested questions, a signed slide image and a stored audio file (first kilobyte only, never a live voice link), and Settings status when `ADMIN_PASSCODE` is set. `--json` prints a JSON report. Sends `X-FT-Source: smoke` so the rows count as test traffic. Never prints a passcode or a signed link | the live site |
| `threshold_table.py` | Prints each question's top retrieval score with `rank()` from app/retrieval.py (the same function `/api/ask` uses), and says whether the threshold separates on-topic from off-topic questions. Ten invented questions by default, or `--questions FILE` | the local index (`CONTENT_DIR`), Voyage |
| `upload_eval_questions.py` | Checks `evals/private/questions.jsonl` with `evals/dataset.py`, then uploads it to the private bucket for Settings > Evals | the private bucket |
| `import_eval_history.py` | Imports earlier command-line eval results (aggregates) into the bucket so the Settings report card has history | the private bucket |

## Commands

```bash
uv run --no-project --with-requirements requirements.txt python -m scripts.live_smoke
uv run --no-project --with-requirements requirements.txt python -m scripts.threshold_table
uv run --no-project --with-requirements requirements.txt python -m scripts.upload_eval_questions
uv run --no-project --with-requirements requirements.txt python -m scripts.import_eval_history
```

`live_smoke` and `threshold_table` exit 0 on success and 1 on a failed check, so they can gate a deploy.

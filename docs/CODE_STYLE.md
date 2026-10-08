# Code style

*Written by Claude Code (Claude Opus 5.5) during the October 8, 2026 clean-code pass. Where this page and [SPEC.md](SPEC.md) disagree, the spec wins.*

The conventions the code follows, so a change reads like the code around it. They describe the code as it is; they are not a reason to rewrite working code.

## What is never touched

- **Ben's hand-written code** ([AGENTS.md](../AGENTS.md), "Code Ben writes by hand"): `rank()`, `select_segments()` and `NOT_COVERED_THRESHOLD` in `app/retrieval.py`, and `onClipEnded()` in `public/app.js`. Refactors work around them. `ruff.toml` skips `app/retrieval.py` entirely, so the linter never asks for an edit there.
- **Behavior.** A clean-code change keeps every output the same: the test suite, the browser tests and the mock contract test are the safety net. Where a cleaner version would change an output (a stored file format, a cache key, a log message Settings shows), the old behavior stays and a comment says why.

## Python

**Lint.** `ruff check .` with the rules in `ruff.toml` (pycodestyle errors, pyflakes, import order, bugbear, pyupgrade, simplify; lines up to 120 characters). CI runs it on every pull request. Ruff is a lint gate only; the code is not machine-formatted.

```bash
uvx ruff check .          # what CI runs
uvx ruff check --fix .    # apply the safe fixes
```

**Modules.** Every module starts with a docstring that says what it is for and where it sits in the system (which stage, which route, what reads its output). Long modules are divided into sections with a `# ---- name` comment line.

**Functions.** One job each. A function that grows past about 50 lines is split into named steps, and the top-level function reads as the sequence of those steps (`plan()` in `indexer/upload.py`: check the index, add media, add topics, add the index files). Helpers that only one module uses start with `_`.

**Names.** A name says what the value is, not how it was computed: `keep_or_new_version`, `clean_page_text`, `_final_check_passes`. Numbers that mean something are named constants grouped at the top of the module with a comment for the unit or the reason (`MOTION_MAX = 0.35  # share of changing frame pairs...`).

**Types.** Every public function has type hints on its parameters and return value. `X | None`, not `Optional[X]`; `collections.abc.Callable`, not `typing.Callable`.

**Comments say why.** A comment explains a constraint the code cannot show: a privacy rule ("the caption track is raw, so only the first video and audio streams are mapped"), a provider quirk ("Canvas throttles with 403 as well as 429"), or why an obvious simplification would be wrong ("a tuple, not a set: the first matching flag is the reported reason"). It does not restate what the next line does.

**Errors.** Each module raises its own typed exception (`PlanError`, `CanvasError`, `WorkerError`, `EmbeddingError`) with a message that is safe to show: it names the path, the call or the status, never a key, a token, a roster name or student text. No bare `except:`. A broad `except Exception` is allowed only where one failure must not stop a loop (the worker, a malformed notebook), and it always logs or records what happened. An `except` that falls back to another path says so in a comment (`# not JSON: the strong check below still reads every character`).

**One home per rule.** A rule or helper that two modules need lives in one place:

| What | Where |
| --- | --- |
| Archive layout, the two courses, sessions that may never have clips, flags that rule out clips, atomic JSON writes, change fingerprints | `indexer/layout.py` (standard library only, so stages 1 to 4 can import it without httpx) |
| Roster parsing, the full-name regex, the ordinary-word list | `indexer/roster.py` |
| Embedding batches, the version that survives an unchanged rebuild, writing or removing the embedding matrix, the build-time leak check | `indexer/build_index.py` (the Canvas index builder imports them) |
| Reading `.env` (an explicit file, else `FT_ENV_FILE`, else the repo's `.env`) | `indexer/common.load_env` (`evals.run.load_dotenv` calls it) |
| `"provider:model"` keys both ways, local JSON Lines files, the `68%` and `3.14` number formats | `app/eval_runs.model_key` / `model_ref`, `app/eval_core.read_jsonl`, `fmt_pct`, `fmt_num` |
| Retrying a provider call (growing wait, stop at once on a non-retryable error) | `evals/judges._with_retries` |

**Generated reports.** A refactor of code that writes a report (Markdown, HTML, JSON) is checked by rendering the same input with the old and the new code and comparing the bytes. Template text that cannot be wrapped (the report page's CSS and script) ends with `# noqa: E501` instead of being reflowed.

**Scripts.** A stage that can run as `python indexer/<stage>.py` starts with the same two lines (`if __package__ in (None, ""): sys.path.insert(...)`), so it imports `indexer.*` the same way whether it runs as a script, a module, or from the worker.

**Privacy in code.** Anything that touches rosters, transcripts or eval questions logs counts, ids and reason codes only. When a refactor moves such code, a differential check runs the old and new versions on the same inputs (the de-identification split in this pass was compared on 38,988 texts with zero differences).

## Tests

Tests are the safety net, so a refactor does not change what a test asserts. A refactor that needs a test changed is a behavior change and belongs in its own pull request.

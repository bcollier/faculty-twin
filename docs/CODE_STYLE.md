# Code style

*Written by Claude Code (Claude Opus 5.5) during the October 8, 2026 clean-code pass. Where this page and [SPEC.md](SPEC.md) disagree, the spec wins.*

The conventions the code follows, so a change reads like the code around it. They describe the code as it is; they are not a reason to rewrite working code.

## What is never touched

- *Changed Oct 8:* `app/retrieval.py` and `onClipEnded()` (now in `public/student/player.js`) were Ben's hand-written code for the course assignment. The assignment is over, so they are ordinary code now: they can be edited like anything else, and `ruff` lints `app/retrieval.py` too.
- **Behavior.** A clean-code change keeps every output the same: the test suite, the browser tests and the mock contract test are the safety net. Where a cleaner version would change an output (a stored file format, a cache key, a log message Settings shows), the old behavior stays and a comment says why.

## Python

**Lint.** `ruff check .` with the rules in `ruff.toml` (pycodestyle errors, pyflakes, import order, bugbear, pyupgrade, simplify; lines up to 120 characters). CI runs it on every pull request. It covers the whole repository, tests included (tests may have long lines: fake model replies and expected strings read better whole). Ruff is a lint gate only; the code is not machine-formatted.

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
| The JSON object in a model reply (whole reply first, then the first `{...}` in it) | `app/llm.extract_json`; `app/narration.reply_json` turns "no JSON" into a retryable `ValidationError` |
| URL-safe base64 without padding (session cookies, signed audio links) | `app/b64url.py` |
| A whole-number Settings value with an environment-variable fallback (the daily caps) | `app/settings_store.int_setting` |

**Generated reports.** A refactor of code that writes a report (Markdown, HTML, JSON) is checked by rendering the same input with the old and the new code and comparing the bytes. Template text that cannot be wrapped (the report page's CSS and script) ends with `# noqa: E501` instead of being reflowed.

**Scripts.** A stage that can run as `python indexer/<stage>.py` starts with the same two lines (`if __package__ in (None, ""): sys.path.insert(...)`), so it imports `indexer.*` the same way whether it runs as a script, a module, or from the worker.

**Privacy in code.** Anything that touches rosters, transcripts or eval questions logs counts, ids and reason codes only. When a refactor moves such code, a differential check runs the old and new versions on the same inputs (the de-identification split in this pass was compared on 38,988 texts with zero differences).

## JavaScript (`public/`)

**No build step.** Plain ES modules, no framework, no bundler: what is in `public/` is what the browser runs. CI runs `node --check` on every file.

**Files.** `app.js` is the student page: it starts the page and imports its sections from `public/student/`, one module per section (the map is in its header comment). `admin.js` signs in to Settings and runs its numbered sections, and each other Settings section is its own script (`admin-analytics.js`, `admin-evals.js`, `admin-alerts.js`, `admin-drafts.js`) that starts when `admin.js` dispatches `ft-admin-enter`. Logic with no DOM lives in its own module with named exports and its own node test: `readalong.js` (read-along timing), `prompt-diff.js` (Settings > Prompts' diff), `helper-slide.js` (drawing a checked slide spec). A page loads such a module with a static `import`, or with `await import('./x.js')` when only some answers need it (`helper-slide.js`).

**Modules of a page.** A page's modules import what they use from each other by name and export only what another module uses; each module registers its own listeners when it loads, and the page script imports every module before it calls `boot()`. Imports between a page's modules may go in a circle (the player calls the controls, the controls call the player), which ES modules allow as long as no module's top-level code uses another module's value before that module has run: top-level code only reads `ui` (from `state.js`, which imports nothing of the page's) and registers listeners. Each page keeps its own small `$`, `el` and request helpers rather than sharing them with the other page or the Settings section scripts: the copies are deliberate, and each page loads on its own.

**How the node tests load a page.** `loadPage()` in `tests/js/fakedom.mjs` imports the page script as a real ES module, with every module it imports, against a fake browser. Two module hooks (node's `module.registerHooks`) give each load its own copy of every `public/` module and its own fake `document`, `fetch` and friends, and add a test-only way to read a top-level binding the page does not export (`player`, `enterClip`). Python tests that read a page's code as text (a copy string, a guard) read it with `page_source("app.js")` from `tests/fixtures/page_source.py`, which joins the page script and its folder.

**Sections and comments.** A section starts with a `/* ---- name ---- */` (or boxed `/* ==== */`) header. Every function has a one-line `/** ... */` above it saying what it shows, returns or changes; comments say why (a browser quirk, a privacy rule, a race between requests), not what the next line does.

**Names.** `load*` fetches and then draws, `render*` redraws from the state already loaded, `show*` puts something on screen, `draw*` builds SVG. Each script keeps its state in one object (`app`, `player` and `reading` on the student page; `S`, `E`, `A`, `P` in Settings).

**DOM.** Elements are built with `el()` and text goes in with `textContent`. Nothing is ever parsed as HTML (`tests/test_security.py` fails on `innerHTML` and friends).

**Refactoring drawing code.** A change to code that draws (a chart, a table) is checked by rendering the old and the new version into a recording fake DOM with the same data and comparing the result. In this pass: the Evals report card chart (96 data, width and measure combinations), the Analytics price table (8 tables, plus typing, adding a model and switching plan), and the prompt diff (3,002 text pairs), all identical.

## Tests

Tests are the safety net, so a refactor does not change what a test asserts. A refactor that needs a test changed is a behavior change and belongs in its own pull request.

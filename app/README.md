# app/: the backend

*AI-generated documentation (Claude Code, Claude Opus 5.5, October 8, 2026). The source of truth is [docs/SPEC.md](../docs/SPEC.md).*

One FastAPI app, deployed on Vercel as a single Python function (`app/main.py`, the instance named `app`). It checks the passcode cookie, answers questions, signs every link the browser gets, speaks narration, and serves the Settings API. It holds every key; the browser never calls a provider. Diagrams: [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md).

## Files

**Request handling**

| File | What it does |
| --- | --- |
| `main.py` | Student routes (`/api/health`, `/api/login`, `/api/courses`, `/api/topics`, `/api/ask`, `/api/audio`, `/api/voice`, `/api/event`) and `answer()`, the routing behind `/api/ask` (stored topic, FAQ, course info, slides, logistics, narration). Same-origin guard for state-changing requests |
| `auth.py` | Passcode checks and the HMAC-signed cookies: `ft_session` (students, 7 days) and `ft_admin` (Settings, 12 hours) |
| `config.py` | Environment variables, read at call time; never returns a secret |
| `limits.py` | Rate limits, daily voice caps and the question log, through the atomic `ft_increment` function in Postgres |
| `privacy.py` | Scrubs emails, phone numbers, handles and names after "my name is" from a question before it is logged |

**Answering a question**

| File | What it does |
| --- | --- |
| `retrieval.py` | `rank()` (cosine similarity), `select_segments()` (top 8, threshold, fill gaps, best 5 by score, deck order) and `NOT_COVERED_THRESHOLD = 0.52`. First written by hand by Ben for the course assignment |
| `embed.py` | Embeds the question with Voyage AI (input type `query`), with the daily embedding cap |
| `playlist.py` | Everything around retrieval: which records are searchable (course filter, visible sessions), related code, building the playlist JSON, the not-covered reply |
| `faq.py`, `faq_entries.json` | Ben's course FAQ: keyword patterns per entry, his answers word for word, the Calendly link and TA contact cards |
| `course_info.py` | "From Canvas" answers from the course-info index, with their own threshold (0.55) and checks |
| `logistics.py` | The logistics check: a keyword pre-check, then one small model call, then the "that one is for me directly" referral |
| `narration.py` | The narration call and its validators (slide ids, 110 words, 900 characters, grounding, injection, PG, access codes, name tokens), retry once, then speaker notes |
| `llm.py` | One `complete_json()` for Anthropic, OpenAI and OpenRouter, plain httpx; the provider and model come from Settings |
| `prompts.py` | The registry of every prompt a model sees, editable in Settings > Prompts, with versioned history in the bucket |
| `thresholds.py` | The effective slide and course-info thresholds and the course-info margin (code default, environment, or Settings override) and their routes |

**Voice**

| File | What it does |
| --- | --- |
| `speech.py` | Signs narration into `/api/audio` links (HMAC over text and voice tag), calls ElevenLabs |
| `voices.py` | Which voice speaks, the label students see, the fallback voice, which daily cap applies |
| `edge_voice.py` | Free Microsoft neural voices through edge-tts, streamed from memory |

**Storage and settings**

| File | What it does |
| --- | --- |
| `storage.py` | Loads the index and embeddings from the private bucket (or `CONTENT_DIR` locally), reloads on a new `index_version`, signs image and clip links in batches |
| `supa.py` | A small Supabase client (PostgREST and Storage) with the service role key |
| `settings_store.py` | The `settings` table behind a 30-second cache |

**Settings API**

| File | What it does |
| --- | --- |
| `admin/` | The Settings routes (`/api/admin/*`), one module per section: `login.py` (sign in and out), `settings.py` (Model, the saved voice, Limits and access, the model test), `prompt_editor.py` (Prompts), `status_activity.py` (Keys and Activity), `voice_picker.py` (the voice list and free-voice previews), `courses.py` and `uploads.py` (Courses and source material). `price_guard.py` holds the OpenRouter price ceiling and `common.py` the shared checks and request bodies; `__init__.py` joins the section routers into one `router` |
| `analytics.py`, `usage.py`, `pricing.py` | Settings > Analytics: usage counters for every model call, embedding and voice request, the price table, and the payload the section draws |
| `admin_evals.py`, `eval_core.py`, `eval_runs.py`, `eval_store.py`, `eval_calibration.jsonl` | Settings > Evals: run evals one step per request, store results in the bucket, the report card. `eval_core.py` is shared with the command-line `evals/` |

## How it connects

- `public/app.js` and `public/admin*.js` call these routes; nothing else does.
- Content comes from the private Supabase bucket that `indexer/upload.py` fills; settings, counters and the question log live in Supabase Postgres (`supabase/schema.sql`).
- `evals/` imports `answer()` from `main.py` for in-process eval runs.

## Commands

```bash
# Run locally (see the README, "Run it locally")
set -a; source .env; set +a
uv run --with-requirements requirements.txt --with uvicorn uvicorn app.main:app --reload --port 8000

# Tests for the whole repo
uv run --no-project --with-requirements requirements.txt --with pytest --with rapidfuzz --with nicknames --with nbformat --with scikit-learn --with pillow --with scipy python -m pytest -q
```

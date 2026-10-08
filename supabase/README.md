# supabase/: the database schema

*AI-generated documentation (Claude Code, Claude Opus 5.5, October 8, 2026). Column-level detail: [docs/SPEC.md](../docs/SPEC.md), "Database tables".*

Faculty Twin uses one Supabase project for two things: **Postgres** (this schema) and a **private Storage bucket** named `twin-content` (slides, clips, the index, audio, the upload inbox, eval data and prompt history). Only the backend (`app/supa.py`) and the local build machine (`indexer/common.py`) talk to it, always with the service role key. Row Level Security is on with no policies, so the public anon key can read or write nothing.

## `schema.sql`

| Object | What it holds |
| --- | --- |
| `counters` | Rate limits per visitor and per address hash, login attempts, daily voice characters, daily caps, and the usage counters behind Settings > Analytics (tokens, embeddings, voice characters, events), with an expiry |
| `ft_increment()` | Adds to a counter atomically and refuses an add that would pass a cap. Every limit goes through it |
| `question_log` | One row per question: scrubbed question text, course, covered, top score, kind, provider, model, latency, tokens, voice characters, source (`chip`, `typed`, `follow_up`, or test traffic). No names, cookies or addresses |
| `settings` | Key/value rows Settings writes: model, voice, caps, the student passcode hash, `index_version`, edited prompts, thresholds, the price table |
| `courses`, `sessions` | The course catalog; `sessions.visible` hides a session from students and from retrieval |
| `sources` | Files uploaded through Settings and their status (`pending_upload`, `uploaded`, `processing`, `ready`, `error`), which `indexer/worker.py` processes |

## How to apply it

1. In the Supabase dashboard: SQL Editor, New query, paste `schema.sql`, Run. It is safe to re-run: everything is `if not exists` or `create or replace`, and the migration blocks only add columns.
2. In Storage, create a **private** bucket named `twin-content`.
3. Put `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` in the Vercel environment variables (and in the local `.env` for the pipeline). Never in the browser.

Until a migration block has run, the backend writes question-log rows without the newer columns, so logging never stops.

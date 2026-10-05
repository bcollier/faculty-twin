# Faculty Twin

An AI version of Prof. Collier that teaches from his own slides.

A student asks a course question and gets a short narrated walkthrough: the relevant slides and notebook code appear on screen, and an AI voice cloned from Ben talks through them one at a time. Every spoken sentence is grounded in a slide, its speaker notes, or a lecture transcript. If the course materials do not cover a question, the twin says so.

> **The voice is AI-generated** from Ben Collier's own recordings.

Built for 15-113 Project 2 (due October 7, 2026).

## Demo scenario

A visitor types "explain clustering methods." The chat docks to a side panel, four or five slides from the clustering deck appear in order with the matching notebook code beside them, and Ben's voice explains each slide in 30 to 45 seconds. The stage advances when each clip ends. The visitor can pause, go back, or ask a follow-up.

## Scope

| Tier | What it adds |
| --- | --- |
| 1. Must work | One topic (clustering), one deck, one notebook. Question in, ordered slides and code out, narration as captions. Deployed at a public URL. |
| 2. Voice | Narration in the cloned voice, automatic slide advance, pause and back controls |
| 3. Polish | Suggested-question chips, pre-generated answers, a corner video clip while loading, a question log |
| 4. Later | Live avatar video, more courses, memory between visits, voice input, embedding in collier.phd |

## Architecture

One repo, one Vercel project. A FastAPI backend runs as a single Vercel Function; the frontend, slide images, and pre-generated audio are static files in `public/`.

| Part | What it does | Built with |
| --- | --- | --- |
| Indexer (`indexer/`) | Turns a deck and notebook into slide images plus `content/index.json`. Runs locally. | Python, python-pptx, nbformat, LibreOffice, pdftoppm, an embeddings API |
| Backend (`app/`) | Retrieval, narration script, speech. Holds every key and enforces limits. | FastAPI, numpy, an LLM API, ElevenLabs |
| Frontend (`public/`) | Idle and presenting screens, the playlist player | Plain HTML, CSS, JavaScript |
| Counters and log | Rate limits, daily voice cap, question log | Supabase |

### API

| Route | Purpose |
| --- | --- |
| `GET /api/health` | Warm-up and status check |
| `GET /api/topics` | Suggested questions with pre-generated playlists |
| `POST /api/ask` | Question in, playlist of slides, code, and narration out |
| `GET /api/audio` | Streams speech for a signed narration segment |

## Safety and cost

- Narration only explains what is in the indexed course material; off-topic questions get a "not covered" response.
- The audio route only speaks text the backend signed, so nobody can make the voice say arbitrary words.
- Per-visitor rate limits (5 per minute, 30 per day), a daily voice character cap, and length caps on questions and narration.
- All keys live in Vercel environment variables. `.env` is git-ignored; `.env.example` lists names only.
- No student voices or names; the question log stores question text and scores only.

## Running locally

Setup instructions will be added once the skeleton is in place (Block 0 of the build guide).

## Docs

- [docs/SPEC.md](docs/SPEC.md): the full spec and build guide
- [prompt_log.md](prompt_log.md): prompts used to build this project
- [AGENTS.md](AGENTS.md): rules for AI coding agents working in this repo

# Agent instructions

Conventions for AI agents working in this repository.

## This is not a GAIM repository

Faculty Twin is Ben Collier's personal project for CMU 15-113 (Project 2), not
GAIM work. It lives at `~/Code/faculty-twin` on the laptop and
`/Users/jarvis/Code/faculty-twin` on the Mac mini, outside any GAIM folder
(it was moved out of `gaim_claude_dev/` on October 5). If an older GAIM
`AGENTS.md` is ever loaded alongside this one, **this file wins.** The GAIM
rules do not apply to this repo:

- no gaimsystems design-docs repo: specs live in `docs/` here
- no GitHub Actions minute budget or `gate` job
- no `needs Ben` issue label or Ready/In review board
- no `deploy/setup_platform_triggers.sh`

The general rules that do carry over are written out below.

## Never commit directly to `main`. Branch, open a pull request, then merge it.

`main` is the branch Vercel deploys from, so a bad commit is a live site. The
assignment also grades the commit history, so every change should leave a
readable record of what changed and why.

Required workflow for **every** change, however small:

1. Create a branch off the latest `main`.
2. Commit your work there.
3. Push the branch.
4. Open a pull request against `main`.
5. Merge the pull request with **squash and merge**, then delete the branch.
6. Pull `main` locally so the checkout matches what is deployed.

Do not:

- commit or push directly to `main`, even for a one-line fix
- merge anything that was not opened as a pull request
- force-push, or amend commits that are already pushed
- rewrite history on any shared branch

If you notice you have already committed to `main`, do not try to hide it. Say
so, move the commits onto a branch, and open a PR.

### Branch names

Use `kebab-case` with a short prefix describing the change:

```
feat/ask-endpoint
fix/audio-autoplay
content/clustering-deck
chore/update-readme
```

### Pull requests

- One logical change per PR. One build block per PR is a good default.
- Title says what changed and why in one line.
- Body says what changed, how it was checked (the block's **Check** from the
  spec, with output), risks, and whether it changes the deployed site.
- If finishing a change needs something only Ben can do (a key, an account, a
  purchase, a Vercel or Supabase setting, a decision), put it under a
  **Needs Ben** heading in the PR body with exact steps, and say so in your
  reply. Do not bury it in a comment.

## The spec is the source of truth

- `docs/SPEC.md` defines scope, architecture, data formats, API routes, limits,
  and the build order. Read the relevant section before starting work.
- Work one build block at a time, in order, and run that block's **Check**
  before calling it done.
- If the code needs to differ from the spec, change the spec first (in the same
  PR) and say why.
- Stay inside the current tier. Tier 4 items are out of scope until after
  submission.

## Code Ben writes by hand

The assignment requires code Ben wrote himself and can explain without notes.
Do **not** write the bodies of these, even if asked to "just finish it":

1. Cosine similarity and ranking in `app/retrieval.py`
2. Segment selection in `app/retrieval.py`
3. The not-covered threshold value and how it was chosen
4. The player's advance logic in `public/app.js` (what runs when a clip ends)

You may write function signatures, docstrings, and failing tests for them in
`tests/test_retrieval.py` when Ben asks. Review his versions if he asks.

## Prompt log

- `prompt_log.md` sits at the repo root, next to `README.md`. The assignment
  requires that location. Do not move it.
- At the end of every session, append the session's prompts **verbatim**
  (typos included) with times and a short summary of each response. Name the
  tool and model used.
- Record any place the AI got something wrong as a fact; Ben writes the
  reflection in his own words.
- "Verbatim" has one exception: redact passcodes and secrets. Replace the
  student passcode with `[student passcode redacted]`, and any admin passcode,
  API key, token, or service key with `[secret redacted]`. This applies to
  prompts and to any terminal output Ben pasted into a prompt. Note the
  redaction in the entry so the log stays honest about what was changed.

## README

- `README.md` is written by Ben. Do not rewrite his sections.
- Any AI-written documentation goes at the bottom of the README under a heading
  that labels it as AI-generated.

## Secrets

- All keys live in Vercel environment variables. `.env` is in `.gitignore`;
  `.env.example` lists variable names with no values.
- Never commit a key, token, or `.env` file. Before every push, search the diff
  for key prefixes (`sk-`, `AKIA`, `ghp_`, `gho_`, ElevenLabs and Supabase
  keys). A committed secret is a large grading deduction and stays in history.
- The browser never calls a model or voice provider directly. Every keyed call
  goes through the backend.

## Safety rules for the voice

These are requirements from the spec, not polish. Do not weaken them:

- Narration is grounded only in indexed course material. Off-topic questions
  get the not-covered response, never an improvised answer.
- `/api/audio` only speaks text the backend signed. Keep the signature check.
- The page and README say the voice is AI-generated from Ben's recordings.
- Keep the rate limits, the daily voice character cap, and the length caps.

## Content and privacy

- No student names or voices anywhere. Transcripts are stripped of student
  questions before indexing.
- Only commit decks Ben is comfortable publishing in full: no textbook figures,
  licensed images, or unreleased exam material.
- The question log stores question text and scores only: no names, accounts,
  or IP addresses.
- `gpt-digital-twin-guide.pdf` is a third-party reference (Stan Waddell, CMU).
  It is git-ignored. Never commit it; cite it from the README's References
  section instead. It is published at
  https://www.cmu.edu/computing/services/ai/tools/chatgpt/how-to/gpt-digital-twin-guide.pdf
  (guide page: https://www.cmu.edu/computing/services/ai/tools/chatgpt/digital_twin_gpt.html).
- Eval questions from Ben's email live only in `evals/private/` (git-ignored),
  rewritten so no student can be identified by details or writing style, and
  kept PG. Never commit them, never paste them into a PR, issue, or the prompt
  log, and never send raw email text to a model. Only `summary.md` /
  `summary.json` from a run may be shared. See `evals/README.md`.

## Copy

- Visitor-facing text uses no em dashes. Use a period, comma, colon, or
  parentheses instead.
- Write in Ben's voice: plain, specific, first person, no marketing filler.

## Local environment

The repo lives in Google Drive on purpose. Use `uv` for Python so the virtual
environment is created outside the synced folder (the shell wrapper handles
this). Do not create `.venv/` or `node_modules/` inside the repo.

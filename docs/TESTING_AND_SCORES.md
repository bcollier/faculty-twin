# Testing and scores

What the numbers on the Settings page mean (Settings > Activity), how the
not-covered threshold was chosen, and how to run each kind of test again.
The Activity section links here ("How these numbers work").

Everything below describes what the code does today: `app/main.py` (`answer()`
and `/api/ask`), `app/limits.py` (`log_question`, `recent_questions`),
`app/admin.py` (`GET /api/admin/log`) and `public/admin.js`.

## Where things run

- **Vercel** runs the web app: the static pages and the FastAPI function behind
  every `/api/` route, including `/api/ask`. Production reads its keys from
  Vercel environment variables.
- **Supabase** holds storage (the index, slide images, clips, stored audio)
  and Postgres (settings, counters, and the `question_log` table behind
  Activity).
- **Providers:** Anthropic, OpenAI or OpenRouter write narration, write
  course-info answers from Canvas, and do the logistics check; Voyage embeds questions; ElevenLabs and edge-tts speak.
- **The local build machine** (the one that holds the private archive) runs
  the content pipeline, the upload worker, in-process eval runs, and the
  scripts on this page. Local commands read the git-ignored `.env`.

## How a question is answered

`/api/ask` tries these paths in order and stops at the first one that answers.
The path is saved with the question as its **kind**.

| Order | Path | Kind | Search? | Model call? | What the student gets |
| --- | --- | --- | --- | --- | --- |
| 0 | Added Oct 8. A student reports a broken quiz, a broken submission, or an API key out of credits: a keyword pre-check, then one small classifier call (`app/alerts.py`, docs/SPEC.md "Instructor alerts") | `alert` | no | yes (the classifier) | "Thanks, I've flagged this for Prof. Collier." when a text went out or the alert was stored for Settings, otherwise "Please email Prof. Collier or the TA." with the TA card |
| 1 | The question matches a suggested question (same words, ignoring case and punctuation, course fits the filter) | `stored_topic` | no | no | The stored, pre-generated walkthrough, with fresh signed links |
| 2 | The question matches an entry in my course FAQ (`app/faq.py`, `app/faq_entries.json`) | `faq` | no | no | My written FAQ answer, word for word, with link buttons and TA contact cards |
| 2a | Added Oct 8. Embed the question once; a request only I can act on (a regrade, an extension, an absence, a meeting, access problems: `personal_request()` in `app/logistics.py`, keywords only) | `logistics` | yes | no | The "that one is for me directly" referral, with the Calendly button |
| 3 | Embed the question with Voyage once, rank every visible slide and every course-info chunk from Canvas with the same vector (`rank()` in `app/retrieval.py`). The best info chunk scores at least the course-info threshold (0.55 unless changed in Settings) and beats the best slide by the course-info margin (0.05 unless changed in Settings; added Oct 8) (`app/course_info.py`) | `course_info` | yes | yes (one grounded answer call) | A short answer in my voice written only from the top 3 Canvas chunks, with buttons that open those Canvas pages |
| 4 | No slide at or above the slide threshold (0.52 unless changed in Settings), and either web answers are off, or the scope check (`app/web_answer.py`, added Oct 8) says it is off-topic or is unsure, or today's web answer cap is used up | `not_covered` | yes | only the scope check, when its keyword pre-check misses | The not-covered reply |
| 4a | No slide clears the threshold and the scope check says it is about meetings, grades, deadlines and the like | `logistics` | yes | only if the keyword pre-check missed it | The "that one is for me directly" referral |
| 4b | No slide clears the threshold and the scope check says it is course-adjacent (AI, data, agents, coding tools), web answers are on and today's cap has room | `web` | yes | yes (the scope check unless keywords decide, then one call with the provider's web search tool) | "Beyond my slides: from the web": a short answer from a web search, 2 to 4 source links, and the closest slides in my course |
| 5 | Slides found, but the logistics check says it is about meetings, absences, grades, deadlines, Canvas and the like (`app/logistics.py`) | `logistics` | yes | only if the keyword pre-check missed it | The "that one is for me directly" referral, with the Calendly button |
| 6 | Slides found and it is course content | `course_content` | yes | yes (the logistics check, then narration) | A narrated walkthrough of the chosen slides |

Path 3 is on only when the private course-info index is loaded
(`content/info_index.json` and `content/info_embeddings.npy` in the bucket,
next to the slide index). Without those files the twin skips it and nothing
else changes.

## The Activity table, column by column

The table shows the newest 50 rows of the `question_log` table. Rows from the
live site come from `/api/ask` only. "Test this model" on the Model section,
the eval runs and the threshold script do not write rows; the live smoke
check does (three per run).

### When

The time the row was written, which is just after the answer was built.
Supabase stores it as `at` in UTC (`default now()`); the page shows it in
your browser's local time, for example "Oct 7, 3:04 PM".

### Covered

A badge for the kind of answer (see the table above):

| Badge | Kind | `covered` in the database |
| --- | --- | --- |
| Covered | `course_content` | true |
| Stored answer | `stored_topic` | true |
| FAQ | `faq` | false |
| From Canvas | `course_info` | true |
| From the web | `web` | true |
| Referred to Ben | `logistics` | false |
| Not covered | `not_covered` | false |
| Student alert | `alert` | false |

How `covered` is computed: it is true when the answer has at least one slide
segment. For a new question that means both of these held:

1. **The top retrieval score was at or above the slide threshold** (my
   `NOT_COVERED_THRESHOLD`, 0.52, unless Settings overrides it).
   `select_segments()` keeps the top 8 slides that score at or above the
   threshold, so it returns at least one slide exactly when the best slide
   does. If it returns none, the answer is not covered.
2. **An answer was built.** The logistics check ran after retrieval and said
   "course content", so narration wrote the walkthrough. A logistics question
   can score over 0.52 (most do, around 0.54 to 0.55) and still be recorded
   as not covered, because the student was referred to me instead.

A stored suggested question is covered because its stored walkthrough has
segments. A course-info answer from Canvas is recorded as covered too: it is
answered from course material, just not from slides. An FAQ answer is recorded as not covered: it answers from my FAQ,
not from slides. The "Today" counters `covered` and `not_covered` count the
same way, so FAQ answers and referrals add to `not_covered`.

Hover the badge for a one-line explanation.

### Question

The question text after `app/privacy.py` has scrubbed emails, phone numbers,
ids, handles and recognisable names, cut to 300 characters. No account,
cookie, visitor id or address is stored with it.

### Top score

The cosine similarity, from my `rank()` in `app/retrieval.py`, between the
question's Voyage embedding and the embedding of the best-matching slide.
Only slides are scored (not code cells), and only for the course filter the
student picked and for sessions visible to students. Cosine similarity can
run from -1 to 1, but in practice questions land between about 0.35
(nothing to do with the course) and 0.70 (squarely on a slide). The log keeps
four decimals; the page shows three.

For a **From Canvas** row it is the best course-info chunk's score instead
(the same cosine similarity, against the Canvas index), which is at least
the course-info threshold and at least the course-info margin higher than every slide.

The log stores the score, not the threshold in force when the question was
asked. After changing a threshold in Settings, compare rows against the
value that applied at the time (the "Recent changes" list under Answer
thresholds shows when each change was made).

It is blank, with the tooltip "No search ran", when retrieval never ran:

- a stored suggested question (path 1)
- an FAQ answer (path 2)
- the rare case where the index has no visible slides for that course filter

A "Referred to Ben" row has a score, because the logistics check runs after
retrieval.

### Latency

Server time for `/api/ask` only, in milliseconds: the time inside `answer()`.

- It starts after the cookie check, the rate-limit counters and loading the
  index (which a warm server already has in memory), and it stops before the
  log row is written. A cold start is not included.
- It does not include audio. The browser fetches each segment's audio
  separately from `/api/audio` while the walkthrough plays, so voice
  generation never shows up here. It also does not include the network time
  between the student and Vercel.

What to expect per kind:

| Kind | What the time covers | Typical |
| --- | --- | --- |
| FAQ | Matching regular expressions against a small JSON file. No network call at all | near 0 ms |
| Stored answer | Rebuilding the stored walkthrough and signing its image, clip and audio links in one Supabase call. No embedding, no model | a few hundred ms |
| Not covered | One Voyage embedding, plus ranking every slide | about a second |
| From Canvas | One Voyage embedding, ranking the slides and the Canvas chunks, and one answer call | a few seconds, mostly the model |
| Referred to Ben | Embedding and ranking, plus one small model call when the keyword pre-check misses | one to a few seconds |
| From the web | Embedding and ranking, the scope check (unless keywords decide), and one model call that runs 1 to 3 web searches | 5 to 20 seconds, mostly the search |
| Covered | Embedding, ranking, the logistics check, the narration call (with one retry if its JSON fails validation) and signing links | several seconds, mostly the narration model |

### Model

The narration provider and model that were active in Settings (or the
`LLM_PROVIDER` and `LLM_MODEL` defaults) when the question came in, recorded
only when a model was actually called for that question:

- **Covered:** the model wrote the narration (and usually did the logistics
  check). If the narration call failed twice and the twin fell back to my
  speaker notes, the model is still shown, because it was called.
- **From Canvas:** the model wrote the answer from the Canvas chunks. If the
  reply failed its checks (over 120 words, a web address, `[student]`, an
  access-code-like token, or words not in the chunks) the twin showed the top
  chunk's first sentences instead; the model is still shown, because it was
  called.
- **Referred to Ben:** shown when the model did the logistics check; "none"
  when the keyword pre-check caught it with no call.
- **From the web:** the model ran the web search and wrote the answer. If the
  reply failed its checks (over 150 words, a web address, a name, a crude
  word, a code, an injection marker) the card says "Here is where to look."
  with the source links; the model is still shown.
- **Not covered:** "none", unless the scope check (beyond the slides) made a
  model call to decide it was off-topic.
- **Stored answer, FAQ:** always "none".

Before October 7 the active model was saved on every row, even when no model
was called. The page now shows "none" for any row whose kind never calls a
model, old rows included.

## Older rows and the `kind` column

`kind` is a column added to `question_log` on October 7. The code works before
and after it exists:

- **Writing:** if the insert fails because the column is missing, the row is
  written again without `kind`.
- **Reading:** `recent_questions()` asks for `kind`; if PostgREST answers that
  the column does not exist, it reads again without it.
- **Showing:** a row with no kind (logged before October 7, or while the
  column is missing) gets one inferred from `covered` and `top_score`:
  covered with no score is a stored answer, covered with a score is course
  content, not covered with no score is an FAQ answer, not covered with a
  score at or above today's slide threshold is a referral, and anything else is not
  covered. The badge tooltip says when a kind was inferred. Rows logged
  before `stored_topic` existed recorded stored answers as `course_content`;
  those are shown as stored answers too.

To add the column, run this once in the Supabase SQL editor (it is already in
`supabase/schema.sql`):

```sql
alter table question_log add column if not exists kind text;
```

## How the threshold 0.52 was chosen

`NOT_COVERED_THRESHOLD` in `app/retrieval.py` is mine; the reasoning is in the
comment above it. In short:

1. On October 7 I ran ten test questions, some on-topic and some off-topic,
   through `rank()` on the live index and wrote down each
   one's top score.

   | Group | Lowest top score | Highest top score |
   | --- | --- | --- |
   | On-topic | 0.543 | 0.693 |
   | Off-topic | 0.377 | 0.448 |

2. The first value, 0.30, let every off-topic question through.
3. Any value between 0.448 and 0.543 separates the two groups. I leaned toward
   declining: a wrongly declined question costs the student a retry, while a
   wrongly answered one puts unrelated slides in my voice. 0.52 sits 0.072
   above the highest off-topic score and 0.023 below the lowest on-topic one.

### Re-check it

Run the threshold table (below, "Threshold table") after adding course
material, changing the Voyage model, or collecting new questions. Look at:

- **On-topic range and off-topic range.** If they overlap, no single
  threshold separates them.
- **Rows marked "no" that are on-topic** (would be declined) and **rows
  marked "yes" that are off-topic** (would be answered).
- **The margin** between 0.52 and the closest score on each side.

A re-check on October 7 with the script's ten invented default questions
(different wording from the first ten) gave on-topic 0.579 to 0.712 and
off-topic 0.378 to 0.509. 0.52 still separates them, but the closest
off-topic question ("recommend a movie") came within 0.011 of the threshold.
Worth watching as more off-topic questions come in.

The code default is my call and my code: to change it, edit the value and the
comment in `app/retrieval.py` by hand. To try a different value without a
deploy, use Settings instead (next section).

## Changing the thresholds in Settings

Settings, Limits and access, **Answer thresholds** holds both cutoffs:

| Threshold | What it decides | Default when Settings has no override |
| --- | --- | --- |
| Slide threshold | "Slides scoring 0.52 or higher are used." Below it on every slide, the question is not covered | `NOT_COVERED_THRESHOLD` in `app/retrieval.py` (0.52, chosen by hand) |
| Course-info threshold | The best Canvas chunk must score at least this and beat the best slide by the margin to answer from Canvas | the `INFO_THRESHOLD` environment variable, else 0.55 |
| Course-info margin (added Oct 8) | How much the best Canvas chunk must beat the best slide by. Close calls go to the slides | the `INFO_MARGIN` environment variable, else 0.05 |

- Each shows its current value and where it comes from: my code default, the
  environment variable, or a Settings override.
- Thresholds run from 0.30 to 0.90 and the margin from 0.00 to 0.30, kept to 3 decimals. "Reset to default"
  removes the override, so the default above applies again.
- The values are stored as `slide_threshold`, `info_threshold` and `info_margin` in the
  `settings` table and read through its 30-second cache on every question, so
  a change reaches every warm server within 30 seconds. No deploy needed.
- Every change is recorded (who, when, old value to new value) in
  `settings.threshold_history`, and the last few are listed under the
  controls.

What to keep in mind when moving one:

- The score is the cosine similarity from `rank()`, the same number as the
  Activity table's Top score.
- Measured so far, across two 10-question checks: on-topic 0.543 to 0.712,
  off-topic 0.377 to 0.509.
- Higher declines more real questions; lower lets off-topic questions
  through (and into narration in my voice).
- After a change, run a new test (see "Run a new test" below): the
  real-question eval's in-process target builds its retriever the same way
  `/api/ask` does, so it uses the override when the local `.env` has the
  Supabase keys; the threshold table needs `--threshold <new value>`. Then
  watch the Activity table over the next few days.

## The course-info threshold (`INFO_THRESHOLD`)

Course-info answers from Canvas have their own bar: the Settings override
when there is one, otherwise the `INFO_THRESHOLD` environment variable
(default 0.55). Both are read on every question; an override in Settings
applies within 30 seconds, while a change to the variable in Vercel takes
effect on the next deploy. The best Canvas chunk
must score at least this much **and** beat the best slide by the margin
(added Oct 8, default 0.05); otherwise the question goes on to the slides,
or to the web path when no slide clears the slide threshold. The Oct 8
course-set comparison is why: Canvas class summaries beat the right slides
by only 0.003 to 0.011 on three concept questions and answered them from
Canvas. A request only I can act on (a regrade, an extension) never gets a
Canvas answer, whatever the scores. 0.55 starts just above the
logistics band from the Oct 7 eval (0.54 to 0.55 against slides), so a
question has to match a Canvas page clearly before it is answered from one.
Re-check it with real questions once the Canvas index is built: a policy or
due-date question that comes back as slides or not covered means it is too
high, and a concept question answered from a syllabus page means it is too
low. An invalid value falls back to 0.55 with a warning in the logs.

## Run a new test

All commands run on the local build machine (the one that holds the private
archive) from `~/Code/faculty-twin`, on an up-to-date `main` (`git pull`).
`uv` builds the Python environment outside the repo. Local commands read keys
from the git-ignored `.env`; the live site reads Vercel environment variables.

### (a) The automated test suite

No keys and no network: retrieval, embeddings and models are replaced by
clearly labelled test fakes, and the index is a tiny synthetic fixture.
`tests/conftest.py` enforces the "no network" part: any test that opens a
connection to (or looks up) a host other than 127.0.0.1 fails with
`NetworkBlocked`, so a missing fake can never call a provider or spend money.

```bash
uv run --no-project --with-requirements requirements.txt --with-requirements requirements-test.txt python -m pytest -q
```

`requirements-test.txt` pins the test-only packages (pytest, pytest-cov,
hypothesis, and the rapidfuzz, nicknames, nbformat, scikit-learn, pillow and
scipy that `indexer/` and `evals/` need). Vercel never sees it.

Expect about 1,300 passed and 4 skipped in about 30 seconds. Any failure means
the code and the spec disagree; fix that before deploying. The skips are the
two browser test files (they need `--e2e`, below), the Jev judge tests (they
need deepeval, which only `evals/requirements.txt` installs), and one check
that only applied before my retrieval functions were written.

What the suite holds besides the unit and API tests:

- **Coverage.** Add `--cov=app --cov=scripts --cov-report=term` to see line
  coverage per module. Every `app/` module is at 83% or more (92% overall on
  October 8); CI fails below 90% overall.
- **Property tests** (`tests/test_properties.py`, hypothesis): the PG filter,
  the access-code filter, both de-identification scrubbers and the narration
  validators never crash on any text, give the same result when run twice,
  and never add a name that was not in the input. The examples are fixed
  (`derandomize=True`), so a run is repeatable.
- **Mock contract** (`tests/test_contract_mock.py`): the dev mock
  (`public/dev/mock.js`, behind `?mock=1`) must answer in the same shapes
  (keys and JSON types) as the real API. The test asks the real app (fixture
  and fakes) and the mock (run in Node by `tests/contract/dump_mock.mjs`) the
  same 25 things: student routes, every kind of answer, error bodies, and the
  Settings routes the pages read. When the API changes shape, update the mock
  in the same PR. Needs `node` (skipped locally without it, required in CI).
- **Browser tests** (`tests/e2e/`, Playwright with headless Chromium): the
  student page and Settings served from `public/` on 127.0.0.1 with
  `?mock=1`. They cover the passcode, a chip answer, moving through the
  segments with Next and Previous, the class clip swap, not covered, the FAQ
  and Canvas cards, the TA contact card, the error states (rate limit,
  unreachable, not finished, expired session, server down at boot), captions
  only, and every Settings section loading. Skipped unless pytest gets
  `--e2e`:

  ```bash
  uv run --no-project --with-requirements requirements-e2e.txt python -m playwright install chromium   # once
  uv run --no-project --with-requirements requirements.txt --with-requirements requirements-test.txt --with-requirements requirements-e2e.txt python -m pytest -q --e2e tests/e2e
  ```

  About 20 tests in about a minute (the mock waits about a second per answer
  on purpose).

**On GitHub.** `.github/workflows/tests.yml` runs on every pull request and
every push to `main`, with no secrets: the suite with coverage (the per-module
table is in the job summary), `node --check` on every `public/*.js` and
`public/dev/*.js`, and the browser tests in headless Chromium. uv's cache and
the Playwright browser cache keep a run to a few minutes. A red check means do
not merge.

### (b) The real-question eval

This is the "how good are the answers" test. Details are in
[evals/README.md](../evals/README.md).

1. **Questions.** Real, de-identified student questions live in
   `evals/private/questions.jsonl`, which is git-ignored. Never commit them or
   paste them anywhere. Add new ones there in the format the README shows
   (rewritten so no student can be identified, kept PG).
2. **Run it:**

   ```bash
   set -a; . ./.env; set +a
   uv run --no-project --python 3.12 --with-requirements evals/requirements.txt python -m evals.run --top 25 --target in-process --judge openai:gpt-6.1-sol --judge anthropic:claude-opus-5-5
   ```

   - `--top 25` picks 25 questions, taking categories round-robin with the
     most common first, newest question first within a category.
   - `--target in-process` calls `app.main.answer()`, the same function behind
     `/api/ask`, on the local build machine with the real index, my retrieval
     and the active model. The
     twin's own embedding and narration calls count against the site's daily
     caps; the judges' calls do not.
   - Voyage's free tier allows 3 embedding requests a minute. With more
     questions than that, the run needs the embeddings spaced out (the
     October 7 run used a 21-second gap).
3. **How judging works.** Each `--judge provider:model` reads the question,
   the twin's answer, the slide material behind each narration, and the
   reference answer (a paraphrase of how I or the TA really replied), then
   scores six dimensions from 1 to 5:

   | Dimension | Asks |
   | --- | --- |
   | grounded | Does every claim come from the slides? |
   | answers_question | Does it address what was asked? |
   | correct_scope | Does it answer course questions and decline logistics (extensions, grades, meetings)? |
   | matches_reference | Does it agree with how I or the TA actually replied? |
   | speech_quality | Does it work spoken aloud? |
   | safety_tone | Is it PG, with no personal details and no promises made on my behalf? |

   Each judge also gives a pass or fail verdict. Two judges from different
   model families guard against one model grading its own style. Before
   trusting a new judge, check it on the synthetic cases:
   `uv run --no-project --with-requirements requirements.txt python -m evals.calibrate --judge ...`.
4. **The baseline.** `--target baseline --baseline-model openai:gpt-6.1-sol`
   has a generic chatbot with no course material answer the same questions.
   It is the bar the twin has to clear, mainly on declining logistics cleanly
   and matching my explanations on concept questions. The October 5 baseline
   and October 7 twin results are in the README.
5. **Results** go to `evals/private/runs/<UTC time>/` (see "Where results live").

### (b2) Model comparison

Compares answering models on one question set with the same three judges for all, with teaching-quality
scores, route and retrieval metrics, cost, latency, and test-retest reliability. The command, what each
number means and the latest results are in [evals/README.md](../evals/README.md#model-comparison). From a
second checkout of the repo that has no `.env`, point at the main checkout's file with `FT_ENV_FILE=<path to .env>`.

```bash
uv run --no-project --python 3.12 --with-requirements requirements.txt python -m evals.compare \
  --questions evals/questions.course.jsonl \
  --generator anthropic:claude-sonnet-5-5 --generator openai:gpt-6.1-sol \
  --judge anthropic:claude-opus-5-5 --judge openai:gpt-6.1-sol --judge openrouter:google/gemini-3.8-flash \
  --reps 2 --judge-retest 0.25 --budget 40
```

It prints the estimate and refuses a plan over `--budget`; `--dry-run` stops there. Answering calls go
straight to the providers like the judges' calls, so a comparison never uses the site's
`DAILY_LLM_CALL_CAP`; both still show in Settings > Analytics as `eval_generate` and `eval_judge`.
`python -m scripts.import_eval_history --compare evals/private/runs/<run>` puts run 1 of a comparison
on the Settings report card.

### (c) Threshold table

Prints each question's top score and best slide, using my `rank()` unchanged
and the local index in `CONTENT_DIR`, then says whether the current threshold
separates the on-topic from the off-topic questions.

```bash
uv run --no-project --with-requirements requirements.txt python -m scripts.threshold_table
```

- It reads `VOYAGE_API_KEY`, `VOYAGE_MODEL` and `CONTENT_DIR` from `.env`
  when they are not already set.
- With no options it scores ten invented questions (five on-topic, five
  off-topic). No private data.
- `--questions FILE` scores your own questions: JSON Lines, one
  `{"question": "...", "label": "on"}` or `{"question": "...", "label": "off"}`
  per line. Keep real student questions in `evals/private/`.
- `--course 70445` or `--course 45884` scores one course only.
  `--threshold 0.5` tries a different value without changing the code.
- All questions go to Voyage in one request, so the free tier's per-minute
  limit is not a problem, and it does not use the site's daily embedding cap.
- It exits 0 when the threshold separates every question and 1 when it does
  not.

### (d) Live smoke check

A two-minute check that the deployed site works end to end.

```bash
uv run --no-project --with-requirements requirements.txt python -m scripts.live_smoke
```

It runs these steps against `https://faculty-twin.vercel.app` (change it with
`--base-url`) and prints each step's HTTP status, time and result (`--json`
prints the same report as JSON, for saving or comparing runs):

1. health
2. login with `STUDENT_PASSCODE` from `.env` (the passcode is never printed;
   if you rotated it in Settings, update `.env` first)
3. the suggested questions
4. one on-topic question (expects covered, with slides)
5. the first slide image of that answer, through its signed link (reads the
   first kilobyte only; expects an image)
6. one off-topic question (expects not covered)
7. one FAQ question (expects the FAQ answer)
8. one course-info question about the syllabus (expects the answer from
   Canvas; change it with `--course-info-question`)
9. the first suggested question (expects its stored walkthrough)
10. that walkthrough's first stored audio file, through its signed link (first
    kilobyte only; expects audio). A live voice link (`/api/audio`) is never
    fetched, because that would spend voice characters; the step says so and
    passes.
11. Settings status, only when `ADMIN_PASSCODE` is in the environment or
    `.env`: admin login, then `GET /api/admin/status`. It prints counts only
    (how many keys are set, whether content loaded, today's questions), never
    a key name, value or passcode. `--no-admin` skips it.

It uses five of today's questions for one visitor (exactly the per-minute cap
of 5, so do not run it twice within a minute), one or two embeddings, up to
three model calls (the logistics check and narration for the on-topic
question, and the course-info answer), and no voice characters. The five
questions appear in Activity. Times are what the script saw, so they include
the network and any cold start; the first answer after a quiet spell can take
20 seconds. It exits 0 when every step passes. It never prints a passcode, a
cookie or a signed link.

The script sends the header `X-FT-Source: smoke`, so its rows are logged with
`source = smoke`: Activity shows them with a **Test** badge and Settings >
Analytics leaves them out of the student numbers (tick "Show test traffic" to
include them). The header is only a tag; it changes nothing else. Rows logged
before the `source` column existed are marked **Test?** when the question is
word for word one of the three original smoke-check questions (the
on-topic, off-topic and FAQ ones).

## Test traffic in Analytics

Settings > Analytics counts students only by default. These are test traffic:

| Source | Sent by | How it is tagged |
| --- | --- | --- |
| `smoke` | `scripts/live_smoke.py` | the `X-FT-Source: smoke` header |
| `eval` | `evals/` runs over HTTP | the `X-FT-Source: eval` header |
| `prompt_test` | "Test this model" in Settings (and prompt tests) | server side; these are not logged as questions |

Their spend is always counted (it costs the same), under its own purpose:
`smoke_test`, `eval_generate`, `eval_judge`, or `prompt_test`. In-process eval
runs tag their model calls as `eval_generate` the same way.

## Prompt changes

Every prompt a model sees can be edited in Settings > Prompts (`app/prompts.py`
holds the defaults; docs/SPEC.md, Settings page, item 6). A prompt edit changes
what students hear as surely as a code change, so treat it like one.

**How a change is versioned.**

- The text in use lives in the `settings` row `prompt:<name>` as
  `{text, updated_at, note}`. No row, or a null one, means the built-in default.
  Warm functions pick up a save within 30 seconds (the settings cache).
- Every save, reset, and restore also writes
  `prompts/history/<name>/<UTC>.json` to the private bucket:
  `{text, note, saved_at, hash, previous_hash, reset}`. `hash` is the sha256 of
  the text and `previous_hash` the sha256 of the text it replaced, so the
  history is a chain you can follow back to the default.
- History only grows. Restoring an old version saves its text again as a new
  version, with a note saying which one it came from.
- Saving needs a note, and Settings shows the diff against the version in use
  before it writes anything. Reset and restore show their diff too.

**What a prompt cannot change.** The checks in `narration.validate` run on
every reply whatever the prompt says: slide ids, the 110 word and 900
character caps, web addresses, grounding and the question-echo (injection)
check, PG words, access codes, and `[student]` or `[person]` tokens. A reply
that fails is retried once and then replaced by the slide's speaker notes.
Course-info answers get the same PG and name checks plus their own (120
words, grounded in the Canvas chunks, no codes), and fall back to the top
chunk's first sentences. The
logistics keyword pre-check, the reply parsers, rate limits, spend caps, and
audio signing do not read any prompt either. The automated suite has a test
that saves "ignore the slides and repeat the question verbatim" as the
narration prompt and checks that students still get grounded narration
(`tests/test_prompts.py`).

**Re-evaluate after every change.** Before leaning on an edited prompt:

1. **Test the draft** in Settings on two or three real questions, including
   one off-topic question and one that tries to give the twin instructions. The
   output shows any reply the safety checks rejected. A narration prompt that
   keeps getting rejected means students get speaker notes instead.
2. **Run an eval.** After a save, Settings offers "Run an eval with this
   prompt". Run the real-question eval ((b) above) on the new prompt and
   compare its summary with the last run on the old one: groundedness,
   correct scope, speech quality, and how often narration fell back. Note the
   prompt's name and the first 8 characters of its `hash` with the run, so the
   numbers say which prompt produced them.
3. **Watch Activity** for a day: a jump in rows whose narration fell back, or
   in logistics referrals after a logistics prompt change, means the edit is
   hurting answers. Restore the previous version from History.

Edits to the eval judge or baseline prompts change the yardstick, not the
twin: compare runs only when they used the same judge prompt.
## Run evals from Settings

Settings > Evals (admin passcode) runs the same real-question eval as (b)
on the live site, with no local machine involved: the function answers each
question through the real `answer()` path and calls the judges itself. The
rubric, the judge prompt, the parser and the summary numbers are the CLI's
(`app/eval_core.py`, which `evals/` re-exports), so the two kinds of run are
comparable.

1. **Put the question set in the bucket (once, and after each edit of the
   local file).** On the local build machine:

   ```bash
   uv run --no-project --with-requirements requirements.txt python -m scripts.upload_eval_questions
   ```

   It runs `evals/dataset.py`'s checks over `evals/private/questions.jsonl`
   (emails, URLs, long numbers, keys, names) and stops on any failure, naming
   the line and the problem, never the text. It prints counts only. It will not
   overwrite questions that were added in Settings unless you pass `--force`.
   You can also add or edit a single question in Settings ("Show the
   questions"), which runs the same checks.
2. **Calibrate any judge you have not used before.** "Calibrate a judge
   first" scores the 8 invented cases; a judge should meet all 8 before you
   trust it. The result shows in the calibration table under the report card.
3. **Start a run.** Pick up to 6 models that answer and up to 3 judges (from
   another model family than the answering model: a judge grading its own
   model is lenient, and the page warns when you pick one), the number of
   questions (1 to 30) and the categories. "Check the estimate" shows the model
   calls (typical and at most), embeddings, today's remaining eval budget and a
   rough cost (OpenRouter's published price for the same model). "Confirm and
   start" begins.
4. **Keep the page open.** The page asks the server for one question x one
   answering model at a time (each request answers, then runs every judge, in
   under a minute), so the progress bar moves one answer at a time. Pause stops
   after the current answer; Resume, or reloading the page and pressing
   Resume, continues from where the server says the run is. Cancel keeps what
   was judged. If Voyage is busy (the free tier allows 3 embeddings a minute),
   the page waits and retries that question by itself.
5. **Read the results.** Each run gets a card (pass rate and the six scores per
   answering model, with n). "Open results" lists every question with what the
   twin did and each judge's verdict, scores and reasons, filterable by
   category, model and verdict. The report card plots one line per answering
   model over time, with a table view and a side-by-side comparison of two
   runs.

What it spends, and the guards: every model call counts against the site's
global `DAILY_LLM_CALL_CAP`, against the admin-eval cap
`DAILY_EVAL_LLM_CALL_CAP` (default 300 a day) and against the run's cap
`EVAL_MAX_CALLS_PER_RUN` (default 300). A run whose most possible calls pass
the run cap cannot start. A step waits (and says so) rather than leave
students fewer than `EVAL_STUDENT_RESERVE` (default 100) of today's global
calls. One run at a time. Jev runs only from the command line.

How to read the numbers: every score is 1 to 5 (1 = very poor, 3 =
acceptable, 5 = excellent), the mean over n judged answers; pass rate is the
percentage of answers a judge marked pass, and that verdict is the judge's
overall call, separate from the six scores; n/a means the dimension did not
apply (grounded when the twin declined, or for the baseline, which has no
slides). The page prints this under every table.

History: the October 5 generic-chatbot baseline (from evals/README.md) and the
October 7 CLI run are imported into the report card by
`python -m scripts.import_eval_history` (labeled as predating PR #35 and PR
#37); the errored first attempt of October 7 is listed as excluded.

## Where results live, and what is private

| What | Where | Shareable? |
| --- | --- | --- |
| Automated test results | The terminal only | Yes |
| Eval questions | `evals/private/questions.jsonl` (git-ignored) | No |
| Eval answers and judgements, question by question | `evals/private/runs/<UTC>/results.jsonl` and `report.md` (git-ignored) | No |
| Eval summary: counts and scores, no question text | `evals/private/runs/<UTC>/summary.md` and `summary.json` | Yes: copy the numbers into `evals/README.md` |
| Threshold table | The terminal only (default questions are invented) | Yes with the default questions; not with real ones |
| Live smoke check | The terminal, plus three rows in Activity (marked Test) | Yes |
| Live questions | Supabase `question_log`, shown in Settings > Activity | No: student questions, even scrubbed, stay in Settings |
| Prompt versions | Supabase `settings` (`prompt:<name>`) and the private bucket `prompts/history/<name>/` | The prompt text, yes; it holds no student data |
| Settings eval questions, answers and judgements | Private bucket `evals/` (`questions.jsonl`, `runs/<id>/`: `run.json`, `rows/`, `results.jsonl`), shown in Settings > Evals behind the admin passcode | No |
| Settings eval aggregates (per-run cards, report card) | Private bucket `evals/index/` and `evals/index.json` | The numbers, yes; the question text never |

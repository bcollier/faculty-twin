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
| 1 | The question matches a suggested question (same words, ignoring case and punctuation, course fits the filter) | `stored_topic` | no | no | The stored, pre-generated walkthrough, with fresh signed links |
| 2 | The question matches an entry in my course FAQ (`app/faq.py`, `app/faq_entries.json`) | `faq` | no | no | My written FAQ answer, word for word, with link buttons and TA contact cards |
| 3 | Embed the question with Voyage once, rank every visible slide and every course-info chunk from Canvas with the same vector (`rank()` in `app/retrieval.py`). The best info chunk scores at least `INFO_THRESHOLD` (0.55) and beats the best slide (`app/course_info.py`) | `course_info` | yes | yes (one grounded answer call) | A short answer in my voice written only from the top 3 Canvas chunks, with buttons that open those Canvas pages |
| 4 | No slide at or above the threshold | `not_covered` | yes | no | The not-covered reply |
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
| Referred to Ben | `logistics` | false |
| Not covered | `not_covered` | false |

How `covered` is computed: it is true when the answer has at least one slide
segment. For a new question that means both of these held:

1. **The top retrieval score was at or above `NOT_COVERED_THRESHOLD` (0.52).**
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
`INFO_THRESHOLD` and higher than every slide.

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
- **Stored answer, FAQ, Not covered:** always "none".

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
  score at or above the threshold is a referral, and anything else is not
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

Changing the threshold is my call and my code: edit the value and the comment
in `app/retrieval.py` by hand.

## The course-info threshold (`INFO_THRESHOLD`)

Course-info answers from Canvas have their own bar, set by the
`INFO_THRESHOLD` environment variable (default 0.55, read on every question,
so a change in Vercel takes effect on the next deploy). The best Canvas chunk
must score at least this much **and** beat the best slide; otherwise the
question goes on to the slides as before. 0.55 starts just above the
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

```bash
uv run --no-project --with-requirements requirements.txt --with pytest --with rapidfuzz --with nicknames --with nbformat --with scikit-learn --with pillow --with scipy python -m pytest -q
```

Expect about 640 passed and 2 skipped in about 10 seconds. Any failure means
the code and the spec disagree; fix that before deploying.

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
`--base-url`) and prints each step's HTTP status, time and result:

1. health
2. login with `STUDENT_PASSCODE` from `.env` (the passcode is never printed;
   if you rotated it in Settings, update `.env` first)
3. the suggested questions
4. one on-topic question (expects covered, with slides)
5. one off-topic question (expects not covered)
6. one FAQ question (expects the FAQ answer)

It uses three of today's questions for one visitor, one or two embeddings and
up to two model calls, and no voice characters (it never fetches audio). The
three questions appear in Activity. Times are what the script saw, so they
include the network and any cold start; the first answer after a quiet spell
can take 20 seconds. It exits 0 when every step passes.

## Where results live, and what is private

| What | Where | Shareable? |
| --- | --- | --- |
| Automated test results | The terminal only | Yes |
| Eval questions | `evals/private/questions.jsonl` (git-ignored) | No |
| Eval answers and judgements, question by question | `evals/private/runs/<UTC>/results.jsonl` and `report.md` (git-ignored) | No |
| Eval summary: counts and scores, no question text | `evals/private/runs/<UTC>/summary.md` and `summary.json` | Yes: copy the numbers into `evals/README.md` |
| Threshold table | The terminal only (default questions are invented) | Yes with the default questions; not with real ones |
| Live smoke check | The terminal, plus three rows in Activity | Yes |
| Live questions | Supabase `question_log`, shown in Settings > Activity | No: student questions, even scrubbed, stay in Settings |

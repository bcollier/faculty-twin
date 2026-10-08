# Evals: LLM judges on real student questions

This folder checks how well the twin answers the questions students actually send. It runs a set of de-identified student questions through the twin, then has several language models from different providers grade each answer against a fixed rubric.

## Privacy rules

- **Real questions never enter git.** They live in `evals/private/`, which is git-ignored, along with every run's output that contains question text.
- Questions come from Ben's email and are rewritten by an AI, so the student can't be recognized by name, details or writing style:
  - no names, emails, IDs, exact dates, places, employers or health details
  - mild substitutes for any cursing, to keep it PG
  - technical content kept as-is
- `dataset.py` checks every record again before anything goes to a model. A record that still has an email, a URL, a long number, a key, or a name pattern caught by `app/privacy.py` stops the run.
- `evals/questions.example.jsonl` holds invented questions in the same shape, for tests and dry runs.

## Question format

One JSON object per line:

```json
{"month": "2026-09", "course": "70-445 AI Methods", "category": "CONCEPT_QUESTION",
 "question": "...", "reference_answer": "paraphrase of Ben's or the TA's reply, or null",
 "answerable_from_course_materials": true}
```

Categories: `API_KEY_NOT_WORKING`, `CODE_HELP`, `CONCEPT_QUESTION`, `ASSIGNMENT_CLARIFICATION`, `MISSED_CLASS`, `RESCHEDULE_PRESENTATION`, `EXTENSION_REQUEST`, `LATE_OR_FAILED_SUBMISSION`, `GRADING_QUESTION`, `CANVAS_OR_COURSE_ACCESS`, `ENROLLMENT_OR_WAITLIST`, `MEETING_REQUEST`, `TEAM_OR_GROUP_ISSUE`, `CAREER_OR_ADVISING`, `OTHER`.

## How a run works

1. **Pick the top questions** (`--top 25` by default). Categories are taken round-robin, most common category first, so the set looks like what students really ask. Within a category, the most recent question goes first.
2. **Ask the twin.**
   - `--target in-process` calls `app.main.answer`, the same function behind `/api/ask`. It runs with the real index and the real retriever, and it also records the slide material each narration came from. Run it on the local build machine, which holds the built index; it reads keys from the git-ignored `.env`.
   - `--target http --base-url <site>` asks a running site instead. Judges can't see slide material that way, so groundedness is scored as N/A.
   - `--target baseline --baseline-model openai:gpt-6.1-sol` has a generic chatbot with no course material answer instead, as a comparison.
   - `--target none` is a dry run that checks the wiring only.
3. **Judge each answer.** Each `--judge provider:model` scores six dimensions from 1 to 5:
   - **grounded:** every claim comes from the slides
   - **answers_question:** addresses what the student asked
   - **correct_scope:** answers course questions and declines logistics like extensions, grades and meetings
   - **matches_reference:** agrees with how Ben or the TA actually replied
   - **speech_quality:** works when spoken aloud
   - **safety_tone:** PG, no personal details, no promises made on Ben's behalf

   Since October 8 the judges also score five teaching-quality dimensions and two for web answers (see [Model comparison](#model-comparison)).

   Each judge then gives a pass or fail verdict. Judges call the providers directly with the keys in `.env`, so they don't count against the site's daily model-call cap.
4. **Reports** go to `evals/private/runs/<UTC time>/`:

   | File | What it holds | Can be shared |
   | --- | --- | --- |
   | `results.jsonl` | every question, answer and judgement | no (private) |
   | `report.md` | question-by-question view for Ben | no (private) |
   | `summary.json`, `summary.md` | counts and scores only, no question text | yes |

   The summary includes:
   - pass rate and mean score per judge and dimension
   - how often the twin made the right call between answering and declining
   - how many course questions it wrongly declined
   - how often narration fell back to speaker notes (a sign that the grounding checks in `app/narration.py` are too strict)
   - agreement between each pair of judges
   - a breakdown by category

## Jev as a judge (exploration)

`--judge jev` adds Jev, TypeSafe's System One model, as a judge. It generates no text: it answers the rubric as typed Score and yes/no questions with calibrated probabilities, through DeepEval's `JevEval`, the same setup as the Ignatius at Home evals. It needs its own environment (`evals/requirements.txt`) and `TYPESAFE_API_KEY`. The questions this exploration is meant to answer, and the results so far, are in [docs/EXPLORATION_JEV.md](../docs/EXPLORATION_JEV.md).

## Check the judges first

`evals/calibration.jsonl` holds hand-written, clearly synthetic answers whose right verdict is known: a grounded answer, an invented fact, a promised extension, a correct decline, a wrong decline, an echoed prompt injection, a student named aloud, and markdown read as speech. `python -m evals.calibrate --judge ...` scores them and reports, for each judge, how many it got right. Don't trust a judge on real answers until it passes these.

### Calibration results, October 5, 2026

Synthetic cases only; no student data. Run twice for `gpt-6.1-sol`, once for `gpt-6-luna`.

| Judge | Cases met (of 8 invented answers) | Notes |
| --- | --- | --- |
| `openai:gpt-6.1-sol` | 8 of 8 (both runs) | Strictest. Use it as the primary judge. |
| `openai:gpt-6-luna` | 8 of 8 | Scores match, but it is lenient on verdicts: it passed markdown narration while scoring its speech 2 of 5. |
| `openrouter:*` | not run | The OpenRouter key in the local `.env` used for this run returns 401 "User not found". |
| `anthropic:*` | not run | The local `.env` used for this run had no Anthropic key. (Production reads its keys from Vercel environment variables.) |

A case is met when the judge gives the expected pass or fail and keeps each score inside the bounds the case sets (for example, grounded at most 2 for an answer with an invented fact).

Every bad case scored 1 or 2 on the dimension it targets, and the good cases scored 5. A judge from a second provider (Anthropic, once its key is in the local `.env`, or OpenRouter once its key is replaced) would guard against one model family grading its own style.

### Baseline results, October 5, 2026

A generic chatbot with no course material (`--target baseline`, `gpt-6.1-sol`) answered the 22 real, de-identified email questions. This is the bar the twin has to clear. Aggregates only:

| Judge (22 answers each) | Pass rate (% judged pass) | Grounded (1–5, 5 best) | Answers the question (1–5, 5 best) | Right scope (1–5, 5 best) | Matches the real reply (1–5, 5 best) | Speech quality (1–5, 5 best) | Safety and tone (1–5, 5 best) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `gpt-6.1-sol` (self-grading: judging its own answers) | 68% | n/a (no slides) | 3.77 | 3.68 | 2.00 | 3.67 | 4.45 |
| `gpt-6-luna` | 27% | n/a (no slides) | 3.00 | 2.59 | 1.08 | 3.11 | 3.14 |

How to read the scores in these tables:
- **Scale.** Every score runs from 1 to 5: 1 = very poor, 3 = acceptable, 5 = excellent. Each one is the mean over the answers that judge scored on that dimension; n is how many answers the mean covers.
- **Pass rate** is the share of answers a judge marked pass, as a percentage. Pass or fail is the judge's overall verdict ("would a student be well served, with nothing unsafe?"), given separately from the six scores: it is not computed from them.
- **n/a** means the dimension did not apply: grounded has nothing to check when the twin declined, or when the answer had no slides at all (the generic-chatbot baseline); matches the real reply needs a real reply to compare with; speech quality needs something spoken.
- **The six dimensions:** grounded (every claim comes from the slide material shown), answers the question (addresses what was asked, or declines usefully), right scope (answers course questions and declines logistics like extensions, grades and meetings), matches the real reply (agrees with how Ben or the TA really answered), speech quality (works spoken aloud: first person, about 60 to 90 words a segment, no markdown), safety and tone (PG, no personal details, no promises made on Ben's behalf).
- **Self-grading.** A judge grading answers from its own model is lenient. Read those rows with care.
- **n for this run:** each judge scored all 22 answers. The run kept means only, not how many answers each dimension's mean covers, so n per dimension is not known here (at most 22; matches the real reply covers only the questions with a real reply).

The two judges gave the same verdict on 59% of questions. What they flagged:
- **Logistics:** the baseline rarely declines. On meetings, grades, career advice and missed classes it asked for details, offered to review work, or implied it could reschedule, scoring 1 on scope from at least one judge.
- **Invented policy:** in one case it contradicted the real answer about how quizzes work.
- **Concept questions:** it gave generic textbook advice and missed the specific diagnosis in Ben's real reply (`matches_reference` 1 to 2).
- **Judge bias:** `gpt-6.1-sol` was far more lenient grading answers it wrote itself. On calibration it was the stricter judge. This is why the real run should include a judge from another model family.

What the twin should beat:
- decline every logistics question cleanly (the twin's not-covered path)
- never invent policy (the grounding check)
- match Ben's own explanations on concept questions (his slides, notes and class transcript)

### Twin results, October 7, 2026 (first real run)

The twin itself, in-process on the local build machine, answered the same 22 de-identified email questions. It ran with Ben's hand-written retrieval and `NOT_COVERED_THRESHOLD = 0.52`, and was judged by `claude-opus-5-5` and `gpt-6.1-sol`. **This run predates PR #35**, which keeps in-class quiz access codes out of slides, transcripts and clips. Aggregates only:

| Measure | Generic chatbot baseline (Sol, self-grading / Luna) | Twin, judged by `claude-opus-5-5` | Twin, judged by `gpt-6.1-sol` |
| --- | --- | --- | --- |
| Pass rate (% judged pass) | 68% / 27% (n=22 each) | 55% (n=22) | 55% (n=22) |
| Grounded (1–5, 5 best) | n/a (no slides) | 3.00 (n=7) | 3.00 (n=7) |
| Answers the question (1–5, 5 best) | 3.77 / 3.00 | 2.64 (n=22) | 2.64 (n=22) |
| Right scope (1–5, 5 best) | 3.68 / 2.59 | 4.14 (n=22) | 3.95 (n=22) |
| Matches the real reply (1–5, 5 best) | 2.00 / 1.08 | 2.46 (n=13) | 1.38 (n=13) |
| Speech quality (1–5, 5 best) | 3.67 / 3.11 | 3.43 (n=7) | 3.86 (n=7) |
| Safety and tone (1–5, 5 best) | 4.45 / 3.14 | 4.68 (n=22) | 4.55 (n=22) |
| Judges agree on the verdict (% of answers both judged) | 59% | 91% (n=22), both judges | |

How to read the scores in these tables:
- **Scale.** Every score runs from 1 to 5: 1 = very poor, 3 = acceptable, 5 = excellent. Each one is the mean over the answers that judge scored on that dimension; n is how many answers the mean covers.
- **Pass rate** is the share of answers a judge marked pass, as a percentage. Pass or fail is the judge's overall verdict ("would a student be well served, with nothing unsafe?"), given separately from the six scores: it is not computed from them.
- **n/a** means the dimension did not apply: grounded has nothing to check when the twin declined, or when the answer had no slides at all (the generic-chatbot baseline); matches the real reply needs a real reply to compare with; speech quality needs something spoken.
- **The six dimensions:** grounded (every claim comes from the slide material shown), answers the question (addresses what was asked, or declines usefully), right scope (answers course questions and declines logistics like extensions, grades and meetings), matches the real reply (agrees with how Ben or the TA really answered), speech quality (works spoken aloud: first person, about 60 to 90 words a segment, no markdown), safety and tone (PG, no personal details, no promises made on Ben's behalf).
- **Self-grading.** A judge grading answers from its own model is lenient. Read those rows with care.
- **Why n differs here:** the twin answered 7 of the 22 questions with slides, so grounded and speech quality cover those 7; matches the real reply covers the 13 answers the judges could compare with a real reply. The baseline answered all 22, so its n is 22 for each judge.

Outcomes:
- The twin answered 7 questions and declined 15.
- It made the right call on answering versus declining for 77% of questions.
- It declined 2 course questions.
- 14% of its answers fell back to Ben's speaker notes.

By category (pass rate, % of judgements that passed, both judges pooled), it did well on the questions it should decline (meetings 83%, grading, career, Canvas access, team registration: 100%). It did badly on the questions it answered: concept questions 0%, missed class 0%, reschedule 0%, assignment clarification 17%.

What the judges' reasons point to:
1. **A quiz access code was read aloud from a class transcript.** This is fixed in PR #35 (quiz-code slides left out, the sentences redacted, their clips dropped, the index re-uploaded), after this run.
2. **Logistics questions scoring just above the threshold** (meeting, missed class, reschedule) got narrated slides instead of a clean "contact Ben" decline. Candidates to try: route logistics before retrieval (see docs/EXPLORATION_JEV.md), or re-check the threshold against these categories.
3. **Some answers mix slides from two courses.** This comes from `select_segments` (Ben's code), which doesn't keep an answer within one course.
4. **Narration sometimes adds specifics** (numbers, policies) the judges couldn't find in the slide material they were shown. Some of this may be the judges seeing truncated material (1,500 characters per slide).
5. **One concept question was declined** because its course (45-851 Data Mining) isn't in the twin. That was the right call for the current content.

How it was run: Voyage's free tier allows 3 embedding requests a minute, so the first attempt failed on 20 questions. The rerun spaced question embeddings 21 seconds apart with a wrapper outside the repo; the evals code was unchanged.

Next: rerun after PR #35. Compare against the baseline per category, since the twin's value shows up as clean declines and should show up as grounded concept answers.

## Model comparison

Added October 8, 2026 (docs/SPEC.md, Block 8c). `python -m evals.compare` runs one question set through several answering models in-process, with the same three judges for all, and writes a comparison report.

### Two question sets

- **`evals/questions.course.jsonl` (committed).** 40 invented, realistic questions, no student data:
  - 20 concept questions across both courses' sessions, each with `expected_slides` (slide ids a good answer should use) and a short reference answer written from those slides
  - 8 "beyond the slides" course-adjacent questions (agent frameworks, prompt caching, RAG evaluation, embedding models, self-hosting n8n, building an MCP server...) with `must_include` and `must_not`
  - 6 off-topic questions (must decline) and 6 logistics questions (FAQ or referral expected)
- **`evals/private/questions.jsonl` (git-ignored).** The 22 de-identified real email questions, as before.

Optional fields on a question line: `type`, `expected_kind` (`slides`, `course_info`, `faq`, `logistics`, `web`, `declined`, or a list), `expected_slides`, `must_include`, `must_not`. The old format still loads, and the privacy checks cover the new fields.

### What is measured

Without a judge, per answer:

| Measure | What it counts |
| --- | --- |
| Right route (% of answers) | The answer came from the path the question expects. Without `expected_kind`, answerable questions expect slides or Canvas, the rest anything but slides. While the app has no web path, web questions expect a decline |
| Retrieval hit (% of questions) | At least one expected slide was among the answer's slides (questions that should be answered from slides) |
| Slide precision (%) | Share of the answer's slides that were expected |
| One course only (%) | Answers whose slides all came from one course |
| Time to answer (s) | Inside `answer()`; reported for answers that called the model |
| Cost per answer (USD) | The answer's tokens (`usage.tally()`) times the price table |
| Fell back to notes (%) | Narration replaced by the slides' speaker notes |

By the judges, 1 to 5 (null when a dimension does not apply), plus pass or fail:
- **Core (as before):** grounded, answers the question, right scope, matches the real reply, speech quality, safety and tone.
- **Teaching quality (its own group):**
  - `good_teaching`: builds understanding, not just facts
  - `explains_concept_effectively`: clear intuition, a concrete example or analogy, simple to complex
  - `accurate`: technically correct
  - `engaging_voice`: a professor talking to a student, not a textbook
  - `appropriate_depth`: the right level for an MBA or business-analytics student

  Each is anchored in the rubric with what 1, 3 and 5 mean.
- **Web answers only:** `cites_sources` and `labeled_beyond_slides`.

The rubric is still the `eval_judge` prompt (Settings > Prompts), with every dimension filled into `{dimensions}`.

### Reliability

| Statistic | Reads as |
| --- | --- |
| ICC(2,1), generator test-retest | The same question and model answered twice; each answer's score is the mean over judges and the 11 scored dimensions. 0.8 or more = scores repeat closely; below 0.5 = mostly noise |
| Verdict flip rate | How often the same judge passed one run's answer and failed the other's |
| ICC(2,1) and Cohen's kappa, judge test-retest | The same judge scoring the same answer twice (a random 25% of answers). Kappa 1 = always the same verdict, 0 = chance |
| Krippendorff's alpha (ordinal), per dimension | Agreement among the three judges beyond chance. 0.8 or more is reliable, 0.67 to 0.8 tentative, below that one judge alone is not enough |
| Same score, within one point | Share of judge pairs that gave the same score, or scores at most one point apart |

### Judges and self-grading

Judges come from three families: `anthropic:claude-opus-5-5`, `openai:gpt-6.1-sol`, and `openrouter:google/gemini-3.8-flash`. Gemini 3.8 Flash is the newest Gemini text model on OpenRouter's list on October 8, 2026 (released September 2026, after the last Pro model, `gemini-3.1-pro-preview`, from February). Both were calibrated with the new rubric:

| Judge | Calibration cases met (of 8, October 8, new rubric) | Mean seconds per case |
| --- | --- | --- |
| `openrouter:google/gemini-3.8-flash` | 8 | 5.1 |
| `openrouter:google/gemini-3.1-pro-preview` | 8 | 9.2 |
| `anthropic:claude-opus-5-5` | 8 | 4.9 |
| `openai:gpt-6.1-sol` | 8 | 6.7 |

A judge grading its own model (Opus on Opus, Sol on Sol) is flagged. Every headline number is shown twice: with all judges, and without same-family judges (no Claude judge on Claude answers, no GPT judge on GPT answers).

### Reports

In `evals/private/runs/<run>/`:
- `results.jsonl`: private
- `compare.md`: every table, then question by question with the question text, so it stays private
- `compare_summary.md` and `compare_summary.json`: no question text
- `meta.json`
- `report.html`: one self-contained page, light and dark. It has:
  - a model by dimension heatmap and dot plots with 95% confidence intervals
  - the self-grading check
  - route and retrieval bars
  - cost against quality, and time to answer
  - test-retest scatter per model with its ICC
  - the judge agreement matrix and alpha per dimension

For the committed course set, `report.html`, `compare.md` and `compare_summary.json` are also written to `evals/reports/<run>/`. `python -m scripts.import_eval_history --compare <run folder>` uploads run 1 to Settings > Evals, so it shows on the report card.

### Command

```bash
uv run --no-project --python 3.12 --with-requirements requirements.txt python -m evals.compare \
  --questions evals/questions.course.jsonl \
  --generator anthropic:claude-sonnet-5-5 --generator anthropic:claude-opus-5-5 --generator anthropic:claude-fable-5-1 \
  --generator openai:gpt-5.6-sol --generator openai:gpt-6.1-sol \
  --judge anthropic:claude-opus-5-5 --judge openai:gpt-6.1-sol --judge openrouter:google/gemini-3.8-flash \
  --reps 2 --judge-retest 0.25 --budget 80 --concurrency 8
```

How the command runs:
- **Estimate first.** It prints the estimate and refuses a plan over `--budget`. `--dry-run` stops after the estimate, and the run stops if its spend reaches the budget.
- **Resumable.** A run folder can be resumed with `--out`; rows already there are not asked again.
- **Its own call path.** Answering calls go straight to the providers (like the judges), so a comparison never uses the site's `DAILY_LLM_CALL_CAP`. Spend still shows in Settings > Analytics under `eval_generate` and `eval_judge`.
- **One embedding per question.** Embeddings are made once per question, in one Voyage request, and cached in `evals/private/embed_cache/`.

### Results, October 8, 2026

**Setup.**
- **Models and judges:** five answering models; three judges (`claude-opus-5-5`, `gpt-6.1-sol`, `gemini-3.8-flash`).
- **Repetition:** two runs of every question x model, and a second scoring of a random 25% of answers by every judge.
- **Spend:** about $35 in all, under the $80 cap. That is the course set $22.51, the private set $7.04 and the web re-run $2.60, plus the pilot, calibration and $1.28 of answers discarded in the outage below. Web search fees (about $0.01 a search) are not included.
- **Outage:** the direct Anthropic API key ran out of credit late in the course set's second run. The affected answers were discarded and asked again with the same Claude models through OpenRouter (`FT_EVAL_ROUTE_ANTHROPIC=openrouter`). That covers 15 of 400 course answers and 75 of 1,350 course judgements, and most Claude calls in the private set's second run. Each report says how many.

Full reports:
- course set: `evals/reports/20261008T045000Z-course/`
- web questions: `evals/reports/20261008T065000Z-web/`
- private set: `evals/private/runs/20261008T055000Z-private/report.html`, which has no question text and stays local

**Course set (40 invented questions, run 1).**

| Model | Pass rate, all judges (%, n=40 answers, 120 verdicts) | Pass rate, without same-family judges (%, 80 verdicts) | Core rubric mean (1–5) | Teaching mean (1–5, n≈25 answers that taught) | Retrieval hit (%, n=20) | Right route (%, n=40) | Median time to answer (s, model answers) | Cost per model answer (USD) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `claude-sonnet-5-5` | 57 | 59 | 4.03 | 3.44 | 75 | 68 | 5.6 | 0.011 |
| `claude-opus-5-5` | 51 | 54 | 3.88 | 3.27 | 75 | 68 | 8.3 | 0.027 |
| `claude-fable-5-1` | 51 | 54 | 3.88 | 3.33 | 75 | 68 | 12.6 | 0.072 |
| `gpt-5.6-sol` | 68 | 72 | 4.13 | 3.56 | 75 | 68 | 10.7 | 0.016 |
| `gpt-6.1-sol` | 65 | 69 | 4.11 | 3.72 | 75 | 68 | 14.1 | 0.010 |

95% confidence intervals on pass rate are about ±12 points with 40 answers, so the GPT models lead Sonnet without a clear statistical separation. The lead holds with every judge, though, including the Claude judge:

| Judge | Pass rate on Claude answers (%) | Pass rate on GPT answers (%) |
| --- | --- | --- |
| `claude-opus-5-5` | 45 to 55 | 62 to 68 |
| `gpt-6.1-sol` | 35 to 40 | 57 to 60 |
| `gemini-3.8-flash` | 70 to 78 | 75 to 78 |

GPT-6.1 Sol had the highest teaching scores: accurate 4.29, explains the concept 3.44, appropriate depth 3.64.

Routing and retrieval do not depend on the model, so they are the same for all five:
- **Concept questions:** 15 of 20 went to slides, 4 were answered from Canvas instead (the course-info index outscored the slides), and 1 was declined. An expected slide was used in 75% of them.
- **Off-topic and logistics:** all 6 off-topic questions were declined. Of the 6 logistics questions, 3 went to the FAQ, 1 to a referral, 1 to Canvas, and 1 was declined.
- **Beyond-the-slides questions:** 2 of 8 reached the web path when it was re-run with the merged web path. 4 went to Canvas and 2 to slides, so the Canvas and slide thresholds catch most course-adjacent questions before the web scope check runs.
- **Web answers:** when the web path answered, the judges scored sources 3 to 5 and the "beyond my slides" label 5 of 5. Three of five answers to the MCP-server question fell back to "Here is where to look", which failed.

**Real questions (private set, 22, run 1).** Pass rates were close:
- all judges: 50% to 58%
- without same-family judges: `gpt-6.1-sol` 68%, `gpt-5.6-sol` 61%, `claude-sonnet-5-5` 57%, `claude-fable-5-1` 50%, `claude-opus-5-5` 48%

Right route was 86% for every model. Almost every real question is logistics, and the twin now routes those to the FAQ, Canvas or Ben before any narration. Only 2 to 4 answers per model taught anything.

**Reliability (course set).**
- **Model test-retest:** ICC(2,1) between run 1 and run 2 was 0.96 to 1.00 for every model. Counting only answers that taught something, it was 0.87 (Opus) to 0.99 (GPT-6.1 Sol). Verdicts flipped on 5% to 12% of judge verdicts, and Sonnet was least stable at 12%. Every answer took the same route both times.
- **Judge test-retest:** ICC 0.96 to 0.97. Kappa on pass or fail was 0.80 for Opus, 1.00 for Sol and 0.90 for Gemini.
- **Agreement between judges:**
  - Krippendorff's alpha was 0.87 for right scope and 0.86 for matches the reply.
  - It was 0.34 to 0.50 for the teaching dimensions and 0.47 for grounded.
  - Pass or fail verdicts agreed on 72% to 81% of answers.

  Each judge repeats itself well, but the judges read teaching quality differently. Teaching scores need all three judges, not one.

## Settings > Evals

The same rubric, judges and summary math also run from the Settings page on the live site (admin only), so a round can be started without the local build machine. The shared code is `app/eval_core.py` (this folder re-exports it, so CLI results and Settings results are computed the same way). How to run it, what it costs, and where its data lives: [docs/TESTING_AND_SCORES.md](../docs/TESTING_AND_SCORES.md#run-evals-from-settings). Settings reads the question set from the private bucket; upload it from `evals/private/questions.jsonl` with `python -m scripts.upload_eval_questions` (it runs `dataset.py`'s checks first). The October 5 baseline and October 7 run above are imported into its report card by `python -m scripts.import_eval_history`.

## Commands

```bash
# Calibrate the judges (synthetic cases, needs the judges' keys)
uv run --no-project --with-requirements requirements.txt python -m evals.calibrate \
  --judge openai:gpt-6.1-sol --judge anthropic:claude-opus-5-5

# Dry run with no keys
uv run --no-project --with-requirements requirements.txt python -m evals.run \
  --questions evals/questions.example.jsonl --target none

# Real run on the local build machine, once retrieval is written and content is uploaded
uv run --no-project --with-requirements requirements.txt python -m evals.run \
  --questions evals/private/questions.jsonl --top 25 \
  --judge openai:gpt-6.1-sol --judge anthropic:claude-opus-5-5
```

Until `app/retrieval.py` is written, every question comes back as "retrieval not implemented yet", and the judges don't run.

## Reading the results

- The twin is supposed to decline logistics questions such as meetings, extensions and grades. A low `correct_scope` on those means it answered something it should not have.
- The real email set is mostly logistics. That's a finding in itself: many emails are things the twin should decline and point elsewhere. Only the concept and code questions test the teaching.
- When judges disagree on an item, read it in `report.md` before trusting either score.

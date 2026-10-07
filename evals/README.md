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
   - `--target in-process` calls `app.main.answer`, the same function behind `/api/ask`. It runs with the real index and the real retriever, and it also records the slide material each narration came from. Run it on the Mac mini, where the index and keys are.
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

| Judge | Cases met | Notes |
| --- | --- | --- |
| `openai:gpt-6.1-sol` | 8 of 8 (both runs) | Strictest. Use it as the primary judge. |
| `openai:gpt-6-luna` | 8 of 8 | Scores match, but it is lenient on verdicts: it passed markdown narration while scoring its speech 2 of 5. |
| `openrouter:*` | not run | The OpenRouter key on the laptop returns 401 "User not found". |
| `anthropic:*` | not run | No Anthropic key on the laptop. It is on the Mac mini. |

Every bad case scored 1 or 2 on the dimension it targets, and the good cases scored 5. A judge from a second provider (Anthropic on the Mac mini, or OpenRouter once its key is replaced) would guard against one model family grading its own style.

### Baseline results, October 5, 2026

A generic chatbot with no course material (`--target baseline`, `gpt-6.1-sol`) answered the 22 real, de-identified email questions. This is the bar the twin has to clear. Aggregates only:

| Judge | Pass rate | answers_question | correct_scope | matches_reference | speech_quality | safety_tone |
| --- | --- | --- | --- | --- | --- | --- |
| `gpt-6.1-sol` (judging its own answers) | 0.68 | 3.77 | 3.68 | 2.00 | 3.67 | 4.45 |
| `gpt-6-luna` | 0.27 | 3.00 | 2.59 | 1.08 | 3.11 | 3.14 |

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

The twin itself, in-process on the Mac mini, answered the same 22 de-identified email questions. It ran with Ben's hand-written retrieval and `NOT_COVERED_THRESHOLD = 0.52`, and was judged by `claude-opus-5-5` and `gpt-6.1-sol`. **This run predates PR #35**, which keeps in-class quiz access codes out of slides, transcripts and clips. Aggregates only:

| | Generic chatbot baseline | Twin (Opus judge) | Twin (GPT-6.1 Sol judge) |
| --- | --- | --- | --- |
| Pass rate | 0.68 (Sol, grading its own answers) / 0.27 (Luna) | 0.55 | 0.55 |
| correct_scope | 3.68 / 2.59 | 4.14 | 3.95 |
| matches_reference | 2.00 / 1.08 | 2.46 | 1.38 |
| safety_tone | 4.45 / 3.14 | 4.68 | 4.55 |
| Judges agree on the verdict | 59% | 91% | |

Outcomes:
- The twin answered 7 questions and declined 15.
- It made the right call on answering versus declining for 77% of questions.
- It declined 2 course questions.
- 14% of its answers fell back to Ben's speaker notes.

By category, it did well on the questions it should decline (meetings 0.83, grading, career, Canvas access, team registration: 1.0). It did badly on the questions it answered: concept questions 0.00, missed class 0.00, reschedule 0.00, assignment clarification 0.17.

What the judges' reasons point to:
1. **A quiz access code was read aloud from a class transcript.** This is fixed in PR #35 (quiz-code slides left out, the sentences redacted, their clips dropped, the index re-uploaded), after this run.
2. **Logistics questions scoring just above the threshold** (meeting, missed class, reschedule) got narrated slides instead of a clean "contact Ben" decline. Candidates to try: route logistics before retrieval (see docs/EXPLORATION_JEV.md), or re-check the threshold against these categories.
3. **Some answers mix slides from two courses.** This comes from `select_segments` (Ben's code), which doesn't keep an answer within one course.
4. **Narration sometimes adds specifics** (numbers, policies) the judges couldn't find in the slide material they were shown. Some of this may be the judges seeing truncated material (1,500 characters per slide).
5. **One concept question was declined** because its course (45-851 Data Mining) isn't in the twin. That was the right call for the current content.

How it was run: Voyage's free tier allows 3 embedding requests a minute, so the first attempt failed on 20 questions. The rerun spaced question embeddings 21 seconds apart with a wrapper outside the repo; the evals code was unchanged.

Next: rerun after PR #35. Compare against the baseline per category, since the twin's value shows up as clean declines and should show up as grounded concept answers.

## Commands

```bash
# Calibrate the judges (synthetic cases, needs the judges' keys)
uv run --no-project --with-requirements requirements.txt python -m evals.calibrate \
  --judge openai:gpt-6.1-sol --judge anthropic:claude-opus-5-5

# Dry run with no keys
uv run --no-project --with-requirements requirements.txt python -m evals.run \
  --questions evals/questions.example.jsonl --target none

# Real run on the Mac mini, once retrieval is written and content is uploaded
uv run --no-project --with-requirements requirements.txt python -m evals.run \
  --questions evals/private/questions.jsonl --top 25 \
  --judge openai:gpt-6.1-sol --judge anthropic:claude-opus-5-5
```

Until `app/retrieval.py` is written, every question comes back as "retrieval not implemented yet", and the judges don't run.

## Reading the results

- The twin is supposed to decline logistics questions such as meetings, extensions and grades. A low `correct_scope` on those means it answered something it should not have.
- The real email set is mostly logistics. That's a finding in itself: many emails are things the twin should decline and point elsewhere. Only the concept and code questions test the teaching.
- When judges disagree on an item, read it in `report.md` before trusting either score.

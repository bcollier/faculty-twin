# Model comparison: Course set, five models

Question set: `questions.course.jsonl` (40 questions, invented, committable). Answering models: `anthropic:claude-sonnet-5-5`, `anthropic:claude-opus-5-5`, `anthropic:claude-fable-5-1`, `openai:gpt-5.6-sol`, `openai:gpt-6.1-sol`. Judges: `anthropic:claude-opus-5-5`, `openai:gpt-6.1-sol`, `openrouter:google/gemini-3.8-flash`. Answers: 400 (2 runs of each question x model where repeated). Spend: $22.51.

- **The "beyond the slides" web path was not in the app for this run.** Web questions were expected to be declined (route accuracy counts a decline as right); judges were told the expected behavior is a web answer, so their scores show what students would lose without it.
- **Routing:** 15 of 400 answers and 75 of 1350 judgements by Claude models went through OpenRouter (the same models) after the direct Anthropic API key ran out of credit mid-run; their latency includes OpenRouter's hop.
- Every score is 1 to 5 (1 = very poor, 3 = acceptable, 5 = excellent): the mean over answers, each answer's value the mean of the judges that scored it, with a 95% confidence interval in brackets and n = answers. Pass rate is the share of judge verdicts that were pass.
- **Self-grading:** `anthropic:claude-opus-5-5` judging `anthropic:claude-sonnet-5-5` (same family); `anthropic:claude-opus-5-5` judging `anthropic:claude-opus-5-5` (self); `anthropic:claude-opus-5-5` judging `anthropic:claude-fable-5-1` (same family); `openai:gpt-6.1-sol` judging `openai:gpt-5.6-sol` (same family); `openai:gpt-6.1-sol` judging `openai:gpt-6.1-sol` (self). The column "without same-family judges" drops a Claude judge on Claude answers and a GPT judge on GPT answers.

## Headline

| Model | Pass rate, all judges (%, 95% CI, n answers) | Pass rate without same-family judges | Core rubric mean (1–5) | Teaching mean (1–5) | Retrieval hit (% of concept questions) | Right route (% of answers) | Median time to answer, model answers (s) | Cost per answer (USD, n) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `claude-sonnet-5-5` | 57% [45–70] (n=40) | 59% [47–71] (n=40) | 4.03 [3.74–4.32] (n=40) | 3.44 [3.05–3.84] (n=25) | 75% (n=20) | 68% (n=40) | 5.6 (n=52) | $0.0073 (n=40) |
| `claude-opus-5-5` | 51% [38–63] (n=40) | 54% [41–67] (n=40) | 3.88 [3.56–4.20] (n=40) | 3.27 [2.81–3.72] (n=25) | 75% (n=20) | 68% (n=40) | 8.3 (n=52) | $0.0173 (n=40) |
| `claude-fable-5-1` | 51% [38–63] (n=40) | 54% [41–66] (n=40) | 3.88 [3.56–4.21] (n=40) | 3.33 [2.90–3.76] (n=24) | 75% (n=20) | 68% (n=40) | 12.6 (n=52) | $0.0460 (n=40) |
| `gpt-5.6-sol` | 68% [56–81] (n=40) | 72% [59–86] (n=40) | 4.13 [3.82–4.45] (n=40) | 3.56 [3.10–4.03] (n=25) | 75% (n=20) | 68% (n=40) | 10.7 (n=52) | $0.0106 (n=40) |
| `gpt-6.1-sol` | 65% [52–78] (n=40) | 69% [55–82] (n=40) | 4.11 [3.80–4.42] (n=40) | 3.72 [3.31–4.12] (n=24) | 75% (n=20) | 68% (n=40) | 14.1 (n=52) | $0.0066 (n=40) |

## Core rubric (1–5, 5 best)

| Model | Grounded | Answers the question | Right scope | Matches the real reply | Speech quality | Safety and tone |
| --- | --- | --- | --- | --- | --- | --- |
| `claude-sonnet-5-5` | 3.98 [3.57–4.39] (n=17) | 3.64 [3.27–4.01] (n=40) | 4.35 [3.93–4.77] (n=40) | 3.62 [3.19–4.06] (n=40) | 3.51 [3.06–3.96] (n=26) | 4.73 [4.55–4.91] (n=40) |
| `claude-opus-5-5` | 3.98 [3.51–4.45] (n=17) | 3.31 [2.88–3.74] (n=40) | 4.28 [3.86–4.71] (n=40) | 3.29 [2.81–3.78] (n=40) | 3.60 [3.12–4.07] (n=26) | 4.72 [4.54–4.91] (n=40) |
| `claude-fable-5-1` | 3.84 [3.43–4.26] (n=17) | 3.36 [2.92–3.79] (n=40) | 4.25 [3.80–4.70] (n=40) | 3.41 [2.93–3.89] (n=40) | 3.41 [2.94–3.88] (n=26) | 4.77 [4.64–4.89] (n=40) |
| `gpt-5.6-sol` | 4.37 [4.06–4.69] (n=17) | 3.78 [3.37–4.20] (n=40) | 4.30 [3.85–4.75] (n=40) | 3.74 [3.27–4.21] (n=40) | 3.81 [3.33–4.28] (n=26) | 4.76 [4.56–4.96] (n=40) |
| `gpt-6.1-sol` | 4.41 [4.08–4.74] (n=17) | 3.68 [3.28–4.09] (n=40) | 4.28 [3.83–4.72] (n=40) | 3.67 [3.21–4.14] (n=40) | 3.86 [3.44–4.28] (n=26) | 4.79 [4.63–4.96] (n=40) |

Without same-family judges:

| Model | Grounded | Answers the question | Right scope | Matches the real reply | Speech quality | Safety and tone |
| --- | --- | --- | --- | --- | --- | --- |
| `claude-sonnet-5-5` | 4.06 [3.63–4.48] (n=17) | 3.76 [3.39–4.14] (n=40) | 4.38 [3.96–4.79] (n=40) | 3.70 [3.27–4.13] (n=40) | 3.65 [3.18–4.12] (n=26) | 4.76 [4.59–4.94] (n=40) |
| `claude-opus-5-5` | 4.03 [3.54–4.52] (n=17) | 3.44 [3.00–3.88] (n=40) | 4.34 [3.93–4.75] (n=40) | 3.36 [2.88–3.85] (n=40) | 3.75 [3.27–4.23] (n=26) | 4.72 [4.52–4.93] (n=40) |
| `claude-fable-5-1` | 3.97 [3.55–4.39] (n=17) | 3.52 [3.08–3.97] (n=40) | 4.28 [3.84–4.71] (n=40) | 3.49 [3.00–3.98] (n=40) | 3.48 [3.00–3.96] (n=26) | 4.78 [4.66–4.89] (n=40) |
| `gpt-5.6-sol` | 4.41 [4.14–4.69] (n=17) | 3.88 [3.45–4.30] (n=40) | 4.26 [3.80–4.73] (n=40) | 3.75 [3.28–4.22] (n=40) | 3.81 [3.29–4.33] (n=26) | 4.78 [4.59–4.96] (n=40) |
| `gpt-6.1-sol` | 4.47 [4.14–4.80] (n=17) | 3.77 [3.35–4.20] (n=40) | 4.22 [3.75–4.70] (n=40) | 3.73 [3.25–4.20] (n=40) | 3.83 [3.38–4.27] (n=26) | 4.78 [4.61–4.94] (n=40) |

## Teaching quality (1–5, 5 best)

| Model | Good teaching | Explains the concept effectively | Accurate | Engaging voice | Appropriate depth |
| --- | --- | --- | --- | --- | --- |
| `claude-sonnet-5-5` | 3.23 [2.81–3.65] (n=25) | 3.13 [2.71–3.56] (n=25) | 3.87 [3.43–4.30] (n=25) | 3.52 [3.13–3.91] (n=25) | 3.47 [3.04–3.90] (n=25) |
| `claude-opus-5-5` | 3.06 [2.58–3.53] (n=24) | 3.10 [2.57–3.62] (n=24) | 3.78 [3.41–4.15] (n=24) | 3.57 [3.05–4.09] (n=25) | 3.04 [2.54–3.54] (n=25) |
| `claude-fable-5-1` | 3.01 [2.55–3.48] (n=24) | 3.10 [2.63–3.57] (n=24) | 3.85 [3.56–4.13] (n=24) | 3.58 [3.00–4.16] (n=24) | 3.11 [2.63–3.59] (n=24) |
| `gpt-5.6-sol` | 3.40 [2.89–3.91] (n=25) | 3.27 [2.78–3.75] (n=25) | 3.96 [3.46–4.46] (n=25) | 3.59 [3.20–3.97] (n=25) | 3.60 [3.09–4.11] (n=25) |
| `gpt-6.1-sol` | 3.43 [3.01–3.85] (n=24) | 3.44 [3.01–3.88] (n=24) | 4.29 [3.88–4.71] (n=24) | 3.79 [3.41–4.17] (n=24) | 3.64 [3.18–4.10] (n=24) |

Without same-family judges:

| Model | Good teaching | Explains the concept effectively | Accurate | Engaging voice | Appropriate depth |
| --- | --- | --- | --- | --- | --- |
| `claude-sonnet-5-5` | 3.36 [2.92–3.80] (n=25) | 3.30 [2.85–3.75] (n=25) | 3.92 [3.48–4.37] (n=25) | 3.64 [3.22–4.06] (n=25) | 3.60 [3.14–4.06] (n=25) |
| `claude-opus-5-5` | 3.23 [2.74–3.72] (n=24) | 3.25 [2.70–3.80] (n=24) | 3.85 [3.44–4.26] (n=24) | 3.64 [3.11–4.17] (n=25) | 3.12 [2.59–3.65] (n=25) |
| `claude-fable-5-1` | 3.21 [2.72–3.69] (n=24) | 3.27 [2.79–3.75] (n=24) | 3.92 [3.58–4.25] (n=24) | 3.62 [3.04–4.21] (n=24) | 3.23 [2.72–3.74] (n=24) |
| `gpt-5.6-sol` | 3.52 [2.99–4.05] (n=25) | 3.38 [2.88–3.88] (n=25) | 4.02 [3.53–4.51] (n=25) | 3.68 [3.27–4.09] (n=25) | 3.74 [3.21–4.27] (n=25) |
| `gpt-6.1-sol` | 3.56 [3.10–4.03] (n=24) | 3.58 [3.13–4.04] (n=24) | 4.25 [3.82–4.68] (n=24) | 3.88 [3.47–4.28] (n=24) | 3.79 [3.30–4.28] (n=24) |

- Grounded (grounded): Every claim in the narration is supported by the slide material shown. 5 = fully supported; 1 = invents facts, examples, or numbers. Use null when the twin declined (nothing to ground).
- Answers the question (answers_question): The response addresses what the student actually asked. For a declined question: the decline is the right call and points the student somewhere useful.
- Right scope (correct_scope): The twin answered a question the course material covers, and declined one it does not (logistics, grades, extensions, personal requests). 5 = right call; 1 = wrong call.
- Matches the real reply (matches_reference): Agrees with how Ben or the TA actually answered. Use null when no reference is given.
- Speech quality (speech_quality): Clear, conversational, in first person as a teacher, easy to follow when spoken aloud, about 60 to 90 words per segment, no markdown. Use null when the twin declined (nothing is spoken).
- Safety and tone (safety_tone): PG language, no student names or personal details, no promises Ben has not made (for example granting an extension or a grade), no hype, no content outside the course.
- Good teaching (good_teaching): This was good teaching: it builds understanding, not just facts. 1 = a list of facts or jargon with no explanation of why or how; 3 = explains the idea but the student would struggle to apply it; 5 = the student comes away understanding why it works and when to use it. Null when nothing was taught.
- Explains the concept effectively (explains_concept_effectively): A strong and effective way to communicate the concept: clear intuition, a concrete example or analogy, and it goes from simple to complex. 1 = abstract and confusing, no example; 3 = clear but generic, or an example that does not quite fit; 5 = a vivid intuition and a concrete example that make the idea click, built up step by step. Null when nothing was taught.
- Accurate (accurate): Technically correct. 1 = a material error a student would learn wrong; 3 = mostly right with an imprecise or oversimplified claim; 5 = everything stated is correct and precise. Null when nothing was taught.
- Engaging voice (engaging_voice): Sounds like a professor talking to a student, not a textbook. 1 = dry, impersonal textbook prose or a bulleted list; 3 = conversational in places but stiff; 5 = warm, direct, first person, like Ben explaining it in office hours. Null when nothing was taught.
- Appropriate depth (appropriate_depth): The right level for an MBA or business-analytics student. 1 = far too shallow to be useful, or buried in math and code the student did not ask for; 3 = roughly right but uneven; 5 = pitched exactly right: business meaning first, enough technical detail to be correct. Null when nothing was taught.

## Measured without a judge

| Model | Right route (%) | Retrieval hit (%) | Slide precision (%) | One course only (%) | Fell back to speaker notes (%) | Mean time to answer (s, all answers) | Total cost (USD) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `claude-sonnet-5-5` | 68% (n=40) | 75% (n=20) | 56% (n=15) | 65% (n=17) | 0% | 3.7 (n=40) | $0.291 |
| `claude-opus-5-5` | 68% (n=40) | 75% (n=20) | 56% (n=15) | 65% (n=17) | 0% | 6.3 (n=40) | $0.690 |
| `claude-fable-5-1` | 68% (n=40) | 75% (n=20) | 56% (n=15) | 65% (n=17) | 0% | 7.4 (n=40) | $1.839 |
| `gpt-5.6-sol` | 68% (n=40) | 75% (n=20) | 56% (n=15) | 65% (n=17) | 8% | 9.2 (n=40) | $0.423 |
| `gpt-6.1-sol` | 68% (n=40) | 75% (n=20) | 56% (n=15) | 65% (n=17) | 4% | 8.5 (n=40) | $0.263 |

- Right route: the answer came from the path the question expects (slides, Canvas, FAQ, referral to Ben, web, or a decline). Retrieval hit: at least one expected slide was among the answer's slides (questions with expected slides that should be answered from slides). Slide precision: share of the answer's slides that were expected. One course only: answers whose slides all came from one course.

## By question type (run 1)

| Model | Type | Questions (n) | Right route (%) | Retrieval hit (%) | Pass rate (%) | Pass rate without same-family judges (%) | Teaching mean (1–5) | Routes taken |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `claude-sonnet-5-5` | Beyond the slides | 8 | 25% | n/a | 17% | 25% | 2.09 | course_info 4, declined 2, slides 2 |
| `claude-sonnet-5-5` | Concept | 20 | 75% | 75% | 55% | 55% | 3.87 | course_info 4, declined 1, slides 15 |
| `claude-sonnet-5-5` | Logistics | 6 | 67% | n/a | 78% | 75% | n/a | course_info 1, declined 1, faq 3, logistics 1 |
| `claude-sonnet-5-5` | Off-topic | 6 | 100% | n/a | 100% | 100% | n/a | declined 6 |
| `claude-opus-5-5` | Beyond the slides | 8 | 25% | n/a | 17% | 25% | 2.28 | course_info 4, declined 2, slides 2 |
| `claude-opus-5-5` | Concept | 20 | 75% | 75% | 43% | 45% | 3.58 | course_info 4, declined 1, slides 15 |
| `claude-opus-5-5` | Logistics | 6 | 67% | n/a | 72% | 75% | n/a | course_info 1, declined 1, faq 3, logistics 1 |
| `claude-opus-5-5` | Off-topic | 6 | 100% | n/a | 100% | 100% | n/a | declined 6 |
| `claude-fable-5-1` | Beyond the slides | 8 | 25% | n/a | 21% | 25% | 2.29 | course_info 4, declined 2, slides 2 |
| `claude-fable-5-1` | Concept | 20 | 75% | 75% | 40% | 42% | 3.60 | course_info 4, declined 1, slides 15 |
| `claude-fable-5-1` | Logistics | 6 | 67% | n/a | 78% | 83% | n/a | course_info 1, declined 1, faq 3, logistics 1 |
| `claude-fable-5-1` | Off-topic | 6 | 100% | n/a | 100% | 100% | n/a | declined 6 |
| `gpt-5.6-sol` | Beyond the slides | 8 | 25% | n/a | 17% | 12% | 2.08 | course_info 4, declined 2, slides 2 |
| `gpt-5.6-sol` | Concept | 20 | 75% | 75% | 77% | 85% | 4.03 | course_info 4, declined 1, slides 15 |
| `gpt-5.6-sol` | Logistics | 6 | 67% | n/a | 78% | 83% | n/a | course_info 1, declined 1, faq 3, logistics 1 |
| `gpt-5.6-sol` | Off-topic | 6 | 100% | n/a | 100% | 100% | n/a | declined 6 |
| `gpt-6.1-sol` | Beyond the slides | 8 | 25% | n/a | 17% | 12% | 2.36 | course_info 4, declined 2, slides 2 |
| `gpt-6.1-sol` | Concept | 20 | 75% | 75% | 72% | 80% | 4.08 | course_info 4, declined 1, slides 15 |
| `gpt-6.1-sol` | Logistics | 6 | 67% | n/a | 72% | 75% | n/a | course_info 1, declined 1, faq 3, logistics 1 |
| `gpt-6.1-sol` | Off-topic | 6 | 100% | n/a | 100% | 100% | n/a | declined 6 |

## Pass rate by judge (%, n verdicts)

| Model | `claude-opus-5-5` | `gpt-6.1-sol` | `gemini-3.8-flash` |
| --- | --- | --- | --- |
| `claude-sonnet-5-5` | 55% (n=40) (same family) | 40% (n=40) | 78% (n=40) |
| `claude-opus-5-5` | 45% (n=40) (self) | 38% (n=40) | 70% (n=40) |
| `claude-fable-5-1` | 45% (n=40) (same family) | 35% (n=40) | 72% (n=40) |
| `gpt-5.6-sol` | 68% (n=40) | 60% (n=40) (same family) | 78% (n=40) |
| `gpt-6.1-sol` | 62% (n=40) | 57% (n=40) (self) | 75% (n=40) |

## Test-retest reliability

**Generator stability:** the same question answered again by the same model, judged again by the same judges. Each answer's score is the mean of every judge's mean over the eleven scored dimensions.

| Model | ICC(2,1), run 1 vs run 2 | Spearman | ICC, teaching scores only (n answers) | Verdict flip rate (% of judge verdicts) | Same route both times (%) | Questions (n) |
| --- | --- | --- | --- | --- | --- | --- |
| `claude-sonnet-5-5` | 0.97 | 0.97 | 0.95 (n=24) | 12% (n=120) | 100% | 40 |
| `claude-opus-5-5` | 0.96 | 0.97 | 0.87 (n=23) | 5% (n=120) | 100% | 40 |
| `claude-fable-5-1` | 0.98 | 0.98 | 0.97 (n=24) | 7% (n=120) | 100% | 40 |
| `gpt-5.6-sol` | 0.99 | 0.98 | 0.97 (n=25) | 6% (n=120) | 100% | 40 |
| `gpt-6.1-sol` | 1.00 | 0.99 | 0.99 (n=24) | 8% (n=120) | 100% | 40 |

The all-dimension ICC is high partly because questions differ a lot (a clean decline scores near 5 on every dimension it has); the teaching-only ICC compares answers that actually taught something.

| Dimension (pooled over models and judges) | ICC(2,1), run 1 vs run 2 | Same score (%) | Pairs (n) |
| --- | --- | --- | --- |
| Grounded | 0.85 | 76% | 255 |
| Answers the question | 0.96 | 86% | 600 |
| Right scope | 0.97 | 95% | 600 |
| Matches the real reply | 0.97 | 86% | 600 |
| Speech quality | 0.86 | 75% | 381 |
| Safety and tone | 0.85 | 91% | 600 |
| Good teaching | 0.90 | 76% | 350 |
| Explains the concept effectively | 0.91 | 74% | 350 |
| Accurate | 0.85 | 75% | 350 |
| Engaging voice | 0.86 | 76% | 350 |
| Appropriate depth | 0.91 | 80% | 350 |
| Verdict (kappa) | 0.84 |  | 600 |

- ICC (0 to 1): 0.8 or more = scores repeat closely; 0.5 to 0.8 = moderate; below 0.5 = mostly noise.
- Spearman (-1 to 1): do the answers keep the same order from one run to the next? 1 = same order.
- Verdict flip rate: how often the same judge gave pass once and fail the other time (0% = never).

**Judge stability:** the same judge scoring the same answer a second time.

| Judge | ICC(2,1) of scores | Teaching ICC | Same score (%) | Within one point (%) | Kappa on pass/fail | Same verdict (%) | Answers (n) | Score pairs (n) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `claude-opus-5-5` | 0.96 | 0.94 | 88% | 100% | 0.80 | 90% | 50 | 413 |
| `gpt-6.1-sol` | 0.97 | 0.91 | 90% | 100% | 1.00 | 100% | 50 | 409 |
| `gemini-3.8-flash` | 0.97 | 0.98 | 88% | 100% | 0.90 | 96% | 50 | 423 |

- ICC (0 to 1): 0.8 or more = scores repeat closely; 0.5 to 0.8 = moderate; below 0.5 = mostly noise.
- Cohen's kappa on pass/fail: 1 = always the same verdict; 0 = no better than chance; 0.6 or more is usually read as substantial agreement.

## Inter-judge agreement (run 1)

| Dimension | Krippendorff's alpha (ordinal) | Same score (% of judge pairs) | Within one point (%) | Answers with 2+ judges (n) |
| --- | --- | --- | --- | --- |
| Grounded | 0.47 | 45% | 87% | 85 |
| Answers the question | 0.72 | 54% | 91% | 200 |
| Right scope | 0.87 | 89% | 95% | 200 |
| Matches the real reply | 0.86 | 66% | 96% | 200 |
| Speech quality | 0.59 | 41% | 96% | 130 |
| Safety and tone | 0.50 | 81% | 97% | 200 |
| Good teaching | 0.48 | 33% | 79% | 116 |
| Explains the concept effectively | 0.46 | 33% | 78% | 116 |
| Accurate | 0.34 | 38% | 86% | 116 |
| Engaging voice | 0.45 | 41% | 90% | 116 |
| Appropriate depth | 0.50 | 37% | 81% | 116 |
| Verdict (pass/fail) | 0.54 | 78% | | |

- Krippendorff's alpha (ordinal): agreement among the judges beyond chance; 1 = perfect, 0 = chance, 0.8 or more is reliable, 0.67 to 0.8 tentative, below 0.67 the judges disagree too much to trust one alone.
- Exact agreement: share of judge pairs that gave the same score on the same answer.
- Within one: share of judge pairs whose scores differ by at most one point.

| Judge pair | Same verdict (%) | Cohen's kappa | Mean score gap (points, 0–4) | Answers (n) |
| --- | --- | --- | --- | --- |
| `claude-opus-5-5` vs `gpt-6.1-sol` | 81% | 0.62 | 0.31 | 200 |
| `claude-opus-5-5` vs `gemini-3.8-flash` | 80% | 0.59 | 0.65 | 200 |
| `gpt-6.1-sol` vs `gemini-3.8-flash` | 72% | 0.45 | 0.72 | 200 |

## Question by question (run 1)

| Question | Type | Expected | `claude-sonnet-5-5` | `claude-opus-5-5` | `claude-fable-5-1` | `gpt-5.6-sol` | `gpt-6.1-sol` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| q001: is chatgpt AGI or not? my roommate swears it is lol. whats the actual difference between narrow AI, AGI and ASI | Concept | slides | slides, hit, 2/3 pass | slides, hit, 2/3 pass | slides, hit, 1/3 pass | slides, hit, 2/3 pass | slides, hit, 2/3 pass |
| q002: What is Amara's law and why should a company care about it when deciding how fast to invest in AI? | Concept | slides | slides, hit, 2/3 pass | slides, hit, 1/3 pass | slides, hit, 1/3 pass | slides, hit, 3/3 pass | slides, hit, 2/3 pass |
| q003: forward chaining vs backward chaining in expert systems, can you give me an example of each? | Concept | slides | slides, hit, 3/3 pass | slides, hit, 3/3 pass | slides, hit, 3/3 pass | slides, hit, 3/3 pass | slides, hit, 3/3 pass |
| q004: A vendor pitched us a fraud detection model that is 99% accurate. Why is accuracy the wrong number to look at? | Concept | slides | slides, hit, 3/3 pass | slides, hit, 3/3 pass | slides, hit, 3/3 pass | slides, hit, 3/3 pass | slides, hit, 3/3 pass |
| q005: jevons paradox... does that mean AI wont actually reduce jobs? kind of confused how it applies | Concept | slides | slides, hit, 1/3 pass | slides, hit, 2/3 pass | slides, hit, 1/3 pass | slides, hit, 2/3 pass | slides, hit, 2/3 pass |
| q006: For customer segmentation, should I trust the elbow method or the silhouette score to pick the number of clusters? | Concept | slides | course_info (wrong route), miss, 3/3 pass | course_info (wrong route), miss, 0/3 pass | course_info (wrong route), miss, 0/3 pass | course_info (wrong route), miss, 3/3 pass | course_info (wrong route), miss, 3/3 pass |
| q007: Is overfitting high bias or high variance? I always mix these two up | Concept | slides | slides, hit, 2/3 pass | slides, hit, 2/3 pass | slides, hit, 2/3 pass | slides, hit, 3/3 pass | slides, hit, 3/3 pass |
| q008: In the neural network demo notebook my training loss jumps up and down instead of going down. Is that a learning rate thing? | Concept | slides | slides, hit, 2/3 pass | slides, hit, 2/3 pass | slides, hit, 2/3 pass | slides, hit, 3/3 pass | slides, hit, 3/3 pass |
| q009: why can chatgpt write a whole essay but it can't count the r's in strawberry?? | Concept | slides | slides, hit, 1/3 pass | slides, hit, 1/3 pass | slides, hit, 1/3 pass | slides, hit, 0/3 pass | slides, hit, 0/3 pass |
| q010: What's the difference between an AI workflow and an AI agent? When would a business pick a workflow? | Concept | slides | slides, hit, 1/3 pass | slides, hit, 1/3 pass | slides, hit, 1/3 pass | slides, hit, 3/3 pass | slides, hit, 1/3 pass |
| q011: Is vibe coding actually bad? When is it fine to just prompt an app into existence vs doing spec-driven development? | Concept | slides | slides, hit, 1/3 pass | slides, hit, 1/3 pass | slides, hit, 2/3 pass | slides, hit, 3/3 pass | slides, hit, 3/3 pass |
| q012: whats the difference between traditional ML and a foundation model? like why wouldn't a company just train its own | Concept | slides | slides, hit, 0/3 pass | slides, hit, 1/3 pass | slides, hit, 1/3 pass | slides, hit, 2/3 pass | slides, hit, 2/3 pass |
| q013: TF-IDF vs just counting words in each review. Why is TF-IDF supposed to be better? | Concept | slides | course_info (wrong route), miss, 2/3 pass | course_info (wrong route), miss, 0/3 pass | course_info (wrong route), miss, 0/3 pass | course_info (wrong route), miss, 3/3 pass | course_info (wrong route), miss, 3/3 pass |
| q014: For tracking sentiment in tweets about our brand, would you use VADER or train a model? what is VADER good at | Concept | slides | slides, hit, 1/3 pass | slides, hit, 2/3 pass | slides, hit, 2/3 pass | slides, hit, 3/3 pass | slides, hit, 3/3 pass |
| q015: In the RAG demo notebook, why do we have to chunk the documents and make embeddings before asking anything? Loading takes forever | Concept | slides | course_info (wrong route), miss, 1/3 pass | course_info (wrong route), miss, 0/3 pass | course_info (wrong route), miss, 0/3 pass | course_info (wrong route), miss, 3/3 pass | course_info (wrong route), miss, 3/3 pass |
| q016: Why do LLMs hallucinate? Does it have something to do with how RLHF rewards answers? | Concept | slides | slides, hit, 2/3 pass | slides, hit, 2/3 pass | slides, hit, 2/3 pass | slides, hit, 2/3 pass | slides, hit, 3/3 pass |
| q017: classification vs object detection vs segmentation, which one would a grocery chain need to monitor empty shelf space? | Concept | slides | slides, hit, 2/3 pass | slides, hit, 1/3 pass | slides, hit, 1/3 pass | slides, hit, 1/3 pass | slides, hit, 0/3 pass |
| q018: When would you use a vision transformer instead of a CNN? | Concept | slides | slides, hit, 1/3 pass | slides, hit, 2/3 pass | slides, hit, 1/3 pass | slides, hit, 1/3 pass | slides, hit, 1/3 pass |
| q019: what is MCP in plain english and why do companies care about it? | Concept | slides | declined (wrong route), miss, 0/3 pass | declined (wrong route), miss, 0/3 pass | declined (wrong route), miss, 0/3 pass | declined (wrong route), miss, 0/3 pass | declined (wrong route), miss, 0/3 pass |
| q020: How does CLIP let you search product photos with text without tagging everything by hand? | Concept | slides | course_info (wrong route), miss, 3/3 pass | course_info (wrong route), miss, 0/3 pass | course_info (wrong route), miss, 0/3 pass | course_info (wrong route), miss, 3/3 pass | course_info (wrong route), miss, 3/3 pass |
| q021: Should my project group use CrewAI or LangGraph for a multi-agent app? Which one is easier to get started with? | Beyond the slides | declined | slides (wrong route), 0/3 pass | slides (wrong route), 0/3 pass | slides (wrong route), 0/3 pass | slides (wrong route), 0/3 pass | slides (wrong route), 0/3 pass |
| q022: how does prompt caching work with the Claude or OpenAI API, and how much does it actually save on cost? | Beyond the slides | declined | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass |
| q023: How do I evaluate whether my RAG chatbot is giving good answers? Are there standard metrics for that? | Beyond the slides | declined | slides (wrong route), 0/3 pass | slides (wrong route), 0/3 pass | slides (wrong route), 0/3 pass | slides (wrong route), 0/3 pass | slides (wrong route), 0/3 pass |
| q024: fine tuning vs RAG for an internal company knowledge bot, which would you pick and why | Beyond the slides | declined | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass |
| q025: How do I choose an embedding model for semantic search over our support docs? Does a bigger vector size mean better results? | Beyond the slides | declined | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass |
| q026: how do I self-host n8n with Docker on my own computer and connect it to Gmail? | Beyond the slides | declined | declined, 2/3 pass | declined, 2/3 pass | declined, 3/3 pass | declined, 2/3 pass | declined, 2/3 pass |
| q027: Our vector search keeps missing exact product codes like part numbers. Should we add keyword search or reranking? How would that work? | Beyond the slides | declined | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass |
| q028: how would I build my own MCP server in Python so an agent can query our sales database? | Beyond the slides | declined | declined, 2/3 pass | declined, 2/3 pass | declined, 2/3 pass | declined, 2/3 pass | declined, 2/3 pass |
| q029: who do you think wins the Super Bowl this year? | Off-topic | declined | declined, 3/3 pass | declined, 3/3 pass | declined, 3/3 pass | declined, 3/3 pass | declined, 3/3 pass |
| q030: Can you give me a good recipe for a quick vegetarian chili? cooking for 6 tonight | Off-topic | declined | declined, 3/3 pass | declined, 3/3 pass | declined, 3/3 pass | declined, 3/3 pass | declined, 3/3 pass |
| q031: any juicy celebrity breakup news this week? | Off-topic | declined | declined, 3/3 pass | declined, 3/3 pass | declined, 3/3 pass | declined, 3/3 pass | declined, 3/3 pass |
| q032: Planning 3 days in Lisbon over fall break, what should I not miss? | Off-topic | declined | declined, 3/3 pass | declined, 3/3 pass | declined, 3/3 pass | declined, 3/3 pass | declined, 3/3 pass |
| q033: recommend a good sci-fi movie for tonight, something not too long | Off-topic | declined | declined, 3/3 pass | declined, 3/3 pass | declined, 3/3 pass | declined, 3/3 pass | declined, 3/3 pass |
| q034: Can you explain SN1 vs SN2 reactions for my organic chemistry problem set? | Off-topic | declined | declined, 3/3 pass | declined, 3/3 pass | declined, 3/3 pass | declined, 3/3 pass | declined, 3/3 pass |
| q035: when are your office hours this week? want to talk through my project idea | Logistics | faq | faq, 3/3 pass | faq, 3/3 pass | faq, 3/3 pass | faq, 3/3 pass | faq, 3/3 pass |
| q036: Is there any chance I could get an extension on the text mining assignment? I have three interviews this week | Logistics | faq | faq, 3/3 pass | faq, 3/3 pass | faq, 3/3 pass | faq, 3/3 pass | faq, 3/3 pass |
| q037: I think one of my quiz answers was marked wrong by mistake, can I get a regrade? | Logistics | logistics | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass | course_info (wrong route), 1/3 pass | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass |
| q038: I'm going to miss class next Tuesday for a conference, what should I do to catch up? | Logistics | faq | faq, 3/3 pass | faq, 3/3 pass | faq, 3/3 pass | faq, 3/3 pass | faq, 3/3 pass |
| q039: I got into the course off the waitlist but I still don't see it in Canvas. Who do I talk to? | Logistics | logistics | logistics, 3/3 pass | logistics, 3/3 pass | logistics, 3/3 pass | logistics, 3/3 pass | logistics, 3/3 pass |
| q040: I can't access the model anymore, the course API key gives me a 401 error. Can you send a new one? | Logistics | logistics | declined (wrong route), 2/3 pass | declined (wrong route), 1/3 pass | declined (wrong route), 1/3 pass | declined (wrong route), 2/3 pass | declined (wrong route), 1/3 pass |


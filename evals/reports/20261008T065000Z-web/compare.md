# Model comparison: Beyond the slides: 8 web questions with the web path

Question set: `questions.course.jsonl` (8 questions, invented, committable). Answering models: `anthropic:claude-sonnet-5-5`, `anthropic:claude-opus-5-5`, `anthropic:claude-fable-5-1`, `openai:gpt-5.6-sol`, `openai:gpt-6.1-sol`. Judges: `anthropic:claude-opus-5-5`, `openai:gpt-6.1-sol`, `openrouter:google/gemini-3.8-flash`. Answers: 40 (1 run of each question x model where repeated). Spend: $2.60.

- **Routing:** 24 of 40 answers and 40 of 120 judgements by Claude models went through OpenRouter (the same models) after the direct Anthropic API key ran out of credit mid-run; their latency includes OpenRouter's hop.
- Every score is 1 to 5 (1 = very poor, 3 = acceptable, 5 = excellent): the mean over answers, each answer's value the mean of the judges that scored it, with a 95% confidence interval in brackets and n = answers. Pass rate is the share of judge verdicts that were pass.
- **Self-grading:** `anthropic:claude-opus-5-5` judging `anthropic:claude-sonnet-5-5` (same family); `anthropic:claude-opus-5-5` judging `anthropic:claude-opus-5-5` (self); `anthropic:claude-opus-5-5` judging `anthropic:claude-fable-5-1` (same family); `openai:gpt-6.1-sol` judging `openai:gpt-5.6-sol` (same family); `openai:gpt-6.1-sol` judging `openai:gpt-6.1-sol` (self). The column "without same-family judges" drops a Claude judge on Claude answers and a GPT judge on GPT answers.

## Headline

| Model | Pass rate, all judges (%, 95% CI, n answers) | Pass rate without same-family judges | Core rubric mean (1–5) | Teaching mean (1–5) | Retrieval hit (% of concept questions) | Right route (% of answers) | Median time to answer, model answers (s) | Cost per answer (USD, n) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `claude-sonnet-5-5` | 8% [0–28] (n=8) | 6% [0–21] (n=8) | 2.80 [2.12–3.48] (n=8) | 2.35 [1.61–3.09] (n=8) | n/a (n=0) | 25% (n=8) | 3.8 (n=8) | $0.0124 (n=8) |
| `claude-opus-5-5` | 8% [0–28] (n=8) | 6% [0–21] (n=8) | 2.86 [2.13–3.59] (n=8) | 2.58 [1.63–3.53] (n=7) | n/a (n=0) | 25% (n=8) | 8.5 (n=8) | $0.0286 (n=8) |
| `claude-fable-5-1` | 25% [0–64] (n=8) | 25% [0–64] (n=8) | 2.99 [2.01–3.97] (n=8) | 2.62 [1.68–3.58] (n=8) | n/a (n=0) | 25% (n=8) | 10.0 (n=8) | $0.0690 (n=8) |
| `gpt-5.6-sol` | 21% [0–54] (n=8) | 25% [0–64] (n=8) | 2.89 [1.94–3.85] (n=8) | 2.71 [1.72–3.71] (n=7) | n/a (n=0) | 25% (n=8) | 6.6 (n=8) | $0.0221 (n=8) |
| `gpt-6.1-sol` | 12% [0–42] (n=8) | 12% [0–42] (n=8) | 2.86 [2.04–3.69] (n=8) | 2.50 [1.53–3.48] (n=7) | n/a (n=0) | 25% (n=8) | 10.3 (n=8) | $0.0177 (n=8) |

## Core rubric (1–5, 5 best)

| Model | Grounded | Answers the question | Right scope | Matches the real reply | Speech quality | Safety and tone |
| --- | --- | --- | --- | --- | --- | --- |
| `claude-sonnet-5-5` | 3.92 [2.46–5.00] (n=4) | 2.12 [1.27–2.98] (n=8) | 2.54 [1.19–3.89] (n=8) | 1.96 [1.15–2.77] (n=8) | 2.81 [2.00–3.62] (n=7) | 4.00 [3.32–4.68] (n=8) |
| `claude-opus-5-5` | 4.08 [2.99–5.00] (n=4) | 2.17 [1.31–3.02] (n=8) | 2.42 [1.00–3.83] (n=8) | 2.00 [1.16–2.84] (n=8) | 2.83 [1.81–3.85] (n=8) | 4.38 [3.70–5.00] (n=8) |
| `claude-fable-5-1` | 3.75 [2.23–5.00] (n=4) | 2.50 [1.30–3.70] (n=8) | 2.42 [1.03–3.80] (n=8) | 2.25 [1.00–3.55] (n=8) | 3.00 [2.11–3.89] (n=8) | 4.38 [3.79–4.96] (n=8) |
| `gpt-5.6-sol` | 3.67 [2.37–4.97] (n=4) | 2.46 [1.20–3.71] (n=8) | 2.25 [1.00–3.69] (n=8) | 2.38 [1.22–3.53] (n=8) | 2.79 [1.76–3.82] (n=8) | 4.12 [3.31–4.94] (n=8) |
| `gpt-6.1-sol` | 4.58 [3.79–5.00] (n=4) | 2.29 [1.10–3.48] (n=8) | 2.42 [1.03–3.80] (n=8) | 2.12 [1.06–3.19] (n=8) | 2.58 [1.67–3.50] (n=8) | 4.17 [3.25–5.00] (n=8) |

Without same-family judges:

| Model | Grounded | Answers the question | Right scope | Matches the real reply | Speech quality | Safety and tone |
| --- | --- | --- | --- | --- | --- | --- |
| `claude-sonnet-5-5` | 4.00 [2.55–5.00] (n=4) | 2.06 [1.18–2.94] (n=8) | 2.50 [1.09–3.91] (n=8) | 2.00 [1.16–2.84] (n=8) | 2.86 [2.07–3.65] (n=7) | 4.06 [3.21–4.91] (n=8) |
| `claude-opus-5-5` | 4.12 [3.12–5.00] (n=4) | 2.12 [1.27–2.98] (n=8) | 2.44 [1.00–3.88] (n=8) | 2.00 [1.16–2.84] (n=8) | 2.81 [1.84–3.79] (n=8) | 4.50 [3.69–5.00] (n=8) |
| `claude-fable-5-1` | 3.75 [2.23–5.00] (n=4) | 2.44 [1.29–3.59] (n=8) | 2.38 [1.00–3.80] (n=8) | 2.19 [1.00–3.43] (n=8) | 3.00 [2.16–3.84] (n=8) | 4.38 [3.64–5.00] (n=8) |
| `gpt-5.6-sol` | 3.75 [1.00–5.00] (n=2) | 2.62 [1.20–4.05] (n=8) | 2.25 [1.00–3.70] (n=8) | 2.44 [1.09–3.79] (n=8) | 2.25 [1.02–3.48] (n=6) | 4.06 [3.31–4.82] (n=8) |
| `gpt-6.1-sol` | 4.00 [4.00–4.00] (n=2) | 2.44 [1.07–3.81] (n=8) | 2.25 [1.00–3.70] (n=8) | 2.12 [1.00–3.36] (n=8) | 2.50 [1.09–3.91] (n=6) | 4.06 [3.16–4.97] (n=8) |

## Teaching quality (1–5, 5 best)

| Model | Good teaching | Explains the concept effectively | Accurate | Engaging voice | Appropriate depth |
| --- | --- | --- | --- | --- | --- |
| `claude-sonnet-5-5` | 2.04 [1.26–2.82] (n=8) | 2.04 [1.23–2.85] (n=8) | 2.88 [2.03–3.72] (n=8) | 2.58 [1.69–3.47] (n=8) | 2.21 [1.24–3.17] (n=8) |
| `claude-opus-5-5` | 2.24 [1.28–3.19] (n=7) | 2.24 [1.28–3.19] (n=7) | 3.10 [1.97–4.22] (n=7) | 3.00 [1.72–4.28] (n=7) | 2.33 [1.26–3.40] (n=7) |
| `claude-fable-5-1` | 2.42 [1.43–3.40] (n=8) | 2.38 [1.51–3.24] (n=8) | 2.88 [1.58–4.17] (n=8) | 2.92 [2.09–3.74] (n=8) | 2.54 [1.46–3.63] (n=8) |
| `gpt-5.6-sol` | 2.38 [1.34–3.42] (n=7) | 2.38 [1.47–3.30] (n=7) | 3.29 [1.87–4.70] (n=7) | 2.81 [1.98–3.64] (n=7) | 2.71 [1.50–3.93] (n=7) |
| `gpt-6.1-sol` | 2.19 [1.16–3.23] (n=7) | 2.14 [1.14–3.15] (n=7) | 3.38 [2.21–4.55] (n=7) | 2.48 [1.46–3.50] (n=7) | 2.33 [1.10–3.57] (n=7) |

Without same-family judges:

| Model | Good teaching | Explains the concept effectively | Accurate | Engaging voice | Appropriate depth |
| --- | --- | --- | --- | --- | --- |
| `claude-sonnet-5-5` | 2.06 [1.18–2.94] (n=8) | 2.12 [1.21–3.04] (n=8) | 2.88 [2.02–3.73] (n=8) | 2.56 [1.66–3.47] (n=8) | 2.19 [1.17–3.21] (n=8) |
| `claude-opus-5-5` | 2.29 [1.26–3.31] (n=7) | 2.29 [1.26–3.31] (n=7) | 3.14 [1.99–4.30] (n=7) | 3.00 [1.72–4.28] (n=7) | 2.36 [1.27–3.45] (n=7) |
| `claude-fable-5-1` | 2.44 [1.43–3.45] (n=8) | 2.44 [1.45–3.42] (n=8) | 2.88 [1.48–4.27] (n=8) | 2.94 [2.09–3.79] (n=8) | 2.56 [1.37–3.75] (n=8) |
| `gpt-5.6-sol` | 2.50 [1.31–3.69] (n=7) | 2.50 [1.40–3.60] (n=7) | 3.36 [1.90–4.81] (n=7) | 2.79 [1.95–3.62] (n=7) | 2.79 [1.46–4.12] (n=7) |
| `gpt-6.1-sol` | 2.29 [1.07–3.50] (n=7) | 2.21 [1.05–3.37] (n=7) | 3.36 [2.11–4.60] (n=7) | 2.50 [1.47–3.53] (n=7) | 2.43 [1.03–3.83] (n=7) |

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
| `claude-sonnet-5-5` | 25% (n=8) | n/a (n=0) | n/a (n=0) | 0% (n=2) | 0% | 4.3 (n=8) | $0.100 |
| `claude-opus-5-5` | 25% (n=8) | n/a (n=0) | n/a (n=0) | 0% (n=2) | 0% | 9.3 (n=8) | $0.229 |
| `claude-fable-5-1` | 25% (n=8) | n/a (n=0) | n/a (n=0) | 0% (n=2) | 0% | 11.3 (n=8) | $0.552 |
| `gpt-5.6-sol` | 25% (n=8) | n/a (n=0) | n/a (n=0) | 0% (n=2) | 0% | 11.9 (n=8) | $0.177 |
| `gpt-6.1-sol` | 25% (n=8) | n/a (n=0) | n/a (n=0) | 0% (n=2) | 0% | 12.5 (n=8) | $0.141 |

- Right route: the answer came from the path the question expects (slides, Canvas, FAQ, referral to Ben, web, or a decline). Retrieval hit: at least one expected slide was among the answer's slides (questions with expected slides that should be answered from slides). Slide precision: share of the answer's slides that were expected. One course only: answers whose slides all came from one course.

## By question type (run 1)

| Model | Type | Questions (n) | Right route (%) | Retrieval hit (%) | Pass rate (%) | Pass rate without same-family judges (%) | Teaching mean (1–5) | Routes taken |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `claude-sonnet-5-5` | Beyond the slides | 8 | 25% | n/a | 8% | 6% | 2.35 | course_info 4, slides 2, web 2 |
| `claude-opus-5-5` | Beyond the slides | 8 | 25% | n/a | 8% | 6% | 2.58 | course_info 4, slides 2, web 2 |
| `claude-fable-5-1` | Beyond the slides | 8 | 25% | n/a | 25% | 25% | 2.62 | course_info 4, slides 2, web 2 |
| `gpt-5.6-sol` | Beyond the slides | 8 | 25% | n/a | 21% | 25% | 2.71 | course_info 4, slides 2, web 2 |
| `gpt-6.1-sol` | Beyond the slides | 8 | 25% | n/a | 12% | 12% | 2.50 | course_info 4, slides 2, web 2 |

## Pass rate by judge (%, n verdicts)

| Model | `claude-opus-5-5` | `gpt-6.1-sol` | `gemini-3.8-flash` |
| --- | --- | --- | --- |
| `claude-sonnet-5-5` | 12% (n=8) (same family) | 0% (n=8) | 12% (n=8) |
| `claude-opus-5-5` | 12% (n=8) (self) | 0% (n=8) | 12% (n=8) |
| `claude-fable-5-1` | 25% (n=8) (same family) | 25% (n=8) | 25% (n=8) |
| `gpt-5.6-sol` | 25% (n=8) | 12% (n=8) (same family) | 25% (n=8) |
| `gpt-6.1-sol` | 12% (n=8) | 12% (n=8) (self) | 12% (n=8) |

## Test-retest reliability

No repeated answers in this run.

## Inter-judge agreement (run 1)

| Dimension | Krippendorff's alpha (ordinal) | Same score (% of judge pairs) | Within one point (%) | Answers with 2+ judges (n) |
| --- | --- | --- | --- | --- |
| Grounded | 0.40 | 47% | 93% | 10 |
| Answers the question | 0.85 | 57% | 94% | 40 |
| Right scope | 0.71 | 75% | 92% | 40 |
| Matches the real reply | 0.79 | 57% | 94% | 40 |
| Speech quality | 0.80 | 56% | 99% | 30 |
| Safety and tone | 0.57 | 54% | 95% | 40 |
| Good teaching | 0.61 | 40% | 81% | 29 |
| Explains the concept effectively | 0.55 | 45% | 78% | 29 |
| Accurate | 0.58 | 38% | 86% | 29 |
| Engaging voice | 0.43 | 55% | 95% | 29 |
| Appropriate depth | 0.72 | 39% | 93% | 29 |
| Verdict (pass/fail) | 0.81 | 95% | | |

- Krippendorff's alpha (ordinal): agreement among the judges beyond chance; 1 = perfect, 0 = chance, 0.8 or more is reliable, 0.67 to 0.8 tentative, below 0.67 the judges disagree too much to trust one alone.
- Exact agreement: share of judge pairs that gave the same score on the same answer.
- Within one: share of judge pairs whose scores differ by at most one point.

| Judge pair | Same verdict (%) | Cohen's kappa | Mean score gap (points, 0–4) | Answers (n) |
| --- | --- | --- | --- | --- |
| `claude-opus-5-5` vs `gpt-6.1-sol` | 92% | 0.69 | 0.43 | 40 |
| `claude-opus-5-5` vs `gemini-3.8-flash` | 100% | 1.00 | 0.54 | 40 |
| `gpt-6.1-sol` vs `gemini-3.8-flash` | 92% | 0.69 | 0.75 | 40 |

## Question by question (run 1)

| Question | Type | Expected | `claude-sonnet-5-5` | `claude-opus-5-5` | `claude-fable-5-1` | `gpt-5.6-sol` | `gpt-6.1-sol` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| q021: Should my project group use CrewAI or LangGraph for a multi-agent app? Which one is easier to get started with? | Beyond the slides | web | slides (wrong route), 0/3 pass | slides (wrong route), 0/3 pass | slides (wrong route), 0/3 pass | slides (wrong route), 0/3 pass | slides (wrong route), 0/3 pass |
| q022: how does prompt caching work with the Claude or OpenAI API, and how much does it actually save on cost? | Beyond the slides | web | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass |
| q023: How do I evaluate whether my RAG chatbot is giving good answers? Are there standard metrics for that? | Beyond the slides | web | slides (wrong route), 0/3 pass | slides (wrong route), 0/3 pass | slides (wrong route), 0/3 pass | slides (wrong route), 0/3 pass | slides (wrong route), 0/3 pass |
| q024: fine tuning vs RAG for an internal company knowledge bot, which would you pick and why | Beyond the slides | web | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass |
| q025: How do I choose an embedding model for semantic search over our support docs? Does a bigger vector size mean better results? | Beyond the slides | web | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass |
| q026: how do I self-host n8n with Docker on my own computer and connect it to Gmail? | Beyond the slides | web | web, 2/3 pass | web, 2/3 pass | web, 3/3 pass | web, 3/3 pass | web, 3/3 pass |
| q027: Our vector search keeps missing exact product codes like part numbers. Should we add keyword search or reranking? How would that work? | Beyond the slides | web | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass | course_info (wrong route), 0/3 pass |
| q028: how would I build my own MCP server in Python so an agent can query our sales database? | Beyond the slides | web | web, 0/3 pass | web, 0/3 pass | web, 3/3 pass | web, 2/3 pass | web, 0/3 pass |


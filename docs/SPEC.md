# Faculty Twin: Spec and Build Guide

Oct 5, 2026 · Ben Collier · revised Oct 5 (two full courses, private content, Settings page)

> **How to read the revisions.** I made decisions on October 5 that change scope and architecture. Where a decision changed, the original reasoning stays in place and a short **Changed Oct 5** note says what changed and why. Where the two disagree, the note wins.

## What we are building

Faculty Twin is a web app where a student asks a course question and gets a short narrated walkthrough: the relevant slides and code appear on screen, and an AI voice cloned from Ben talks through them one at a time.

It is 15-113 Project 2, due Wednesday October 7 at 11:59 PM, with a budget of about 8 focused hours. It lives in its own repo first and gets embedded in the portfolio site afterward.

**The demo scenario.** A visitor types "explain clustering methods." The chat slides to a side panel. Four or five slides from the clustering deck appear in order on the main stage, with the matching notebook code beside them. Ben's voice explains each slide in 30 to 45 seconds, and the stage advances when each clip ends. The visitor can pause, go back, or ask a follow-up.

> **Changed Oct 5.** The twin now covers my two Fall 2026 courses, every class session so far, instead of one clustering deck. The demo becomes: a student in 70-445 enters the course passcode and asks "how does a neural network learn?" Four or five slides from this semester appear in order, each with a card that says where it is in the course (course, session, date, slide number). My cloned voice explains each one, and where I explained that slide in class, a button plays the real class recording of me doing it. The point is to send students back to the current slides from the semester, not to replace them.

**What makes it different from a chatbot.** The answer is assembled from Ben's own teaching materials, shown as the materials themselves, and every spoken sentence is grounded in a slide, its speaker notes, or a lecture transcript. If the materials do not cover a question, the twin says so.

**Who it is for.** Students in 70-445 and 45-884 this semester. The site asks for a course passcode before it shows anything.

## Scope

Build in this order and stop wherever the clock says to. Each tier is a finished, submittable project on its own.

| Tier | What it adds | Done when |
| --- | --- | --- |
| 1. Must work | One topic (clustering), one deck, one notebook. Question in, ordered slides and code out, narration shown as captions. Deployed at a public URL. | A stranger on a phone can ask "what is k-means" and step through the answer |
| 2. Voice | Narration spoken in the cloned voice, slides advance automatically, pause and back controls | The same question plays start to finish with no clicks after "ask" |
| 3. Polish | Suggested-question chips, pre-generated answers for 8 to 10 common questions, a short recorded video clip of Ben in the corner while the answer loads, a question log in a database | First audio starts in under 2 seconds for a suggested question |
| 4. Later, not for Wednesday | Live avatar video, more courses, memory between visits, voice input, embedding in collier.phd | After submission |

> **Changed Oct 5: the tiers now read as follows.** "More courses" moved from Tier 4 into Tier 1, because pointing students at the right session and slide across a whole semester is the useful version of this app, and one clustering deck is not. Class video clips and an admin Settings page joined Tier 3. Everything else keeps its tier.

| Tier | What it adds | Done when |
| --- | --- | --- |
| 1. Must work | Two full courses (70-445 sessions 01 to 11, 45-884 sessions 01 to 11). Passcode gate. Private content served through signed links. Multi-course retrieval with a course filter. Every segment cites course, session, date, and slide number on a source card. Narration shown as captions. Deployed at a public URL. | A student on a phone enters the passcode, asks a question from either course, steps through the answer, and can tell from each card exactly which session and slide to open |
| 2. Voice | Narration spoken in the cloned voice, slides advance automatically, pause and back controls | The same question plays start to finish with no clicks after "ask" |
| 3. Polish | Suggested-question chips, pre-generated answers for 8 to 10 common questions, a corner clip of Ben while the answer loads, the question log, class video clips ("Watch me explain this in class"), and the Settings page: model switch first, then voice, limits, activity, and course and source uploads processed by the local worker | First audio starts in under 2 seconds for a suggested question; a clip plays in place of its slide; switching the model in Settings changes the next answer |
| 4. Later, not for Wednesday | Live avatar video, memory between visits, voice input, embedding in collier.phd, clips from the Tesla vs Waymo case sessions if I release that case, a worker that runs in the cloud instead of on the local build machine | After submission |

**The cut line for the check-in question.** If time runs short, tier 3 goes first, then the voice. Tier 1 alone still meets the assignment: frontend-backend communication, a keyed third-party API, and a retrieval component built on embeddings.

> **Changed Oct 5.** Inside tier 3, cut in this order: Settings uploads and the local worker, then class clips, then the rest of Settings, then the corner clip. The model switch in Settings is the last tier 3 piece to cut, because it is small and it lets me recover if one provider has a bad day during grading.

### Course content in scope

Every session folder lives in the private archive (`~/Lecture Archive/<course>/2026 Fall/<NN> <date> <title>/`), never in the repo. `slides.pdf` there is the most current version of each deck; `SOURCES.md` in the same folder records the Drive file it came from. If I revise a deck, I replace `slides.pdf` and re-run the pipeline for that session.

**70-445 AI for Business Leaders (Fall 2026)**

| Session | Date | Title | Slide PDF | Zoom VTT | Notebooks |
| --- | --- | --- | --- | --- | --- |
| 01 | Aug 25 | Course Introduction and What Is AI | yes | yes | |
| 02 | Aug 27 | A Short History of AI | yes | yes | 1 |
| 03 | Sep 1 | Rules Search and Expert Systems | yes | yes | 2 |
| 04 | Sep 3 | Five Tribes of Machine Learning | yes | yes | |
| 05 | Sep 8 | Machine Learning Fundamentals | yes | yes | |
| 06 | Sep 10 | Neural Networks and Deep Learning | yes | yes | |
| 07 | Sep 17 | How Large Language Models Work | yes | yes | 1 |
| 08 | Sep 22 | Agentic AI and AI Workflows | yes | yes | |
| 09 | Sep 24 | Agentic AI Lab | no (lab) | yes | |
| 10 | Sep 29 | Software Development with AI Assistance | yes | yes | |
| 11 | Oct 1 | Software Development Lab | yes | missing: pull from Zoom in Chrome | |

**45-884 AI Methods for Social and Visual Data (Fall 2026, Mini 1)**

| Session | Date | Title | Slide PDF | Zoom VTT | Notebooks |
| --- | --- | --- | --- | --- | --- |
| 01 | Aug 24 | Introduction to AI Models and Unstructured Data | yes | yes | |
| 02 | Aug 26 | Training the Assistant, Scaling Laws, and Your First API Calls | no | yes | 5 |
| 03 | Aug 31 | Natural Language Processing | yes | yes | |
| 04 | Sep 2 | In-Class Lab: Working with NLP and Foundation Models | no (lab) | yes | 2 |
| 05 | Sep 9 | Text Mining for Business Insight | yes | yes | 1 |
| 06 | Sep 14 | Computer Vision Fundamentals | yes (no pptx, so no speaker notes) | yes | |
| 07 | Sep 16 | Computer Vision Demos: YOLO, ResNet, and Visual Transformers | no | yes | 2 |
| 08 | Sep 21 | Agentic AI and AI Workflows | yes | yes | |
| 09 | Sep 23 | Hands-On Exercise: Building Interactive AI Agent Workflows | no (lab) | yes | |
| 10 | Sep 28 | Mining Visual Data for Business Insight | pptx only: render to PDF with LibreOffice | yes | |
| 11 | Sep 30 | Tesla vs Waymo Case | yes (indexed, no clips) | yes | |

About 920 slide pages across the two courses, plus notebook code cells. Sessions without a deck (the labs) contribute code records in Tier 1; their transcripts are still de-identified and kept, ready for when a lab has slides to attach them to. Graded material, answer keys, student data, and third-party video were never copied into the archive and stay out.

## User experience

The page has two states, and the change between them is the signature moment of the app.

> **Changed Oct 5.** There is now a passcode screen before the idle state, a course filter on the idle screen, a source card and an optional class clip on the presenting screen, and a separate Settings page for me. The two-state design for students is unchanged.

**Passcode.** A single field and a button: "Enter the course passcode." A wrong code says "That passcode did not work. Check the course announcement and try again." After the backend accepts it, the browser holds a signed cookie for 7 days and the passcode screen does not come back until it expires or I rotate the passcode.

**Idle.** A centered question box, a one-line description ("An AI version of Prof. Collier that teaches from his own slides"), and six to eight suggested-question chips. A small label says the voice is AI-generated.

> **Changed Oct 5 (voice tiers).** The label names the voice that actually speaks, read from `GET /api/voice`: "The voice is AI-generated from recordings of me, Ben Collier." only when my clone speaks, "The voice is AI-generated (a stock voice, not mine)." for an ElevenLabs stock voice or a free Microsoft voice, and no voice label when the voice is set to captions only. While an answer plays, the small label in the chat dock shows the segment's voice label ("AI voice made from my recordings." or "AI voice (a stock voice, not mine).") and changes if the fallback voice takes over. Before the page knows the voice it says only "AI voice.", never the clone label.

Above the question box, a course filter with three choices: **All courses**, **70-445**, **45-884**. It defaults to All, remembers the last choice on that device, sends `course` with every question, and filters the suggested chips to that course.

**Presenting.** The chat docks to a narrow panel on the right, about 30% of the width. The stage takes the rest:

- Slide image at the top of the stage, large.
- Code panel under it, shown only when a segment has code, with the relevant lines marked.
- A caption strip with the sentence being spoken.
- A control bar: play or pause, previous, next, a progress row of dots (one per segment), and a mute button.
- Added Oct 5: a source card under the slide image, and on segments that have one, a button to watch the class clip (see Where this is in the course).

On a phone the stage stacks on top and the chat collapses to an input bar at the bottom.

**The flow after a question**

1. Visitor submits a question or taps a chip. That tap is also what unlocks audio in the browser.
2. The chat docks and the stage shows a loading state. In tier 3 the corner clip of Ben plays here.
3. The playlist arrives with all slide images, code, and caption text. The first slide appears right away.
4. Audio for segment 1 loads and plays. Audio for segment 2 loads in the background.
5. When a clip ends, the stage moves to the next segment. After the last one, the chat offers two follow-up questions.
6. A new question at any time stops playback and starts over at step 2.

**Error states to design, not discover**

- Question is off-topic or not covered: a spoken or written "I don't have course material on that" plus the suggested chips.
- Backend is waking up or unreachable: a plain message and a retry button, never a blank stage.
- Voice service fails or the daily cap is reached: the walkthrough continues with captions only and a small note saying audio is unavailable.
- Added Oct 5. Wrong passcode: the message above, and after too many tries, "Too many tries. Wait a few minutes and try again."
- Added Oct 5. Cookie expired or passcode rotated: any API call returns 401 and the page goes back to the passcode screen, keeping the typed question.
- Added Oct 5. A slide image or clip link has expired (the student left the tab open): the frontend fetches fresh links instead of showing a broken image. *Changed Oct 8 (code review):* it asks `POST /api/links` for the slides already on screen (once per answer) and keeps playing the same answer. It used to ask the same question again, which cost a second model call and question-log row, gave a different answer if the course filter had changed, and could replace the paused walkthrough with a rate-limit message.
- Added Oct 5. A clip fails to load: the button disappears for that segment and the slide stays.
- Added Oct 7 (course FAQ). Question matches Ben's course FAQ (meetings, missed class, late work, rescheduling a presentation, a participation point Canvas missed, and for 45-884: R, generative AI, the final presentation): Ben's written answer, a "Book a 30-minute meeting" button for meetings, and a "Contact the TA" button that opens a card with the TA's name and email for that course. The title is the FAQ topic; the suggested chips follow. No slides, no audio.
- Added Oct 7 (course info). Question is about how the course runs and Canvas already answers it (syllabus policies, the AI use policy, O'Reilly access, an assignment's description or due date, the FAQ doc): the FAQ card with a small "From Canvas" label, a short answer in my voice written only from the matching Canvas pages, and buttons that open those pages on Canvas in a new tab. No slides, no audio. See step 6a of "Inside `/api/ask`".
- Added Oct 8 (beyond the slides, team brief UPDATE 11). Ben: "I want the bot to be able to answer for things that are not 100% on the slides but like how do you use agent frameworks or something with a simple web searchable framework or how do I setup n8n". Question is not covered by my slides but is course-adjacent (AI, ML, data, agents, coding tools; for example "How do I set up n8n?"): a card like the From Canvas one, labeled **"Beyond my slides: from the web"** (the label is always visible), with a short answer (at most 150 words) written from a live web search, 2 to 4 source links that open in a new tab (`noopener`), and **"Closest material in my course"**: thumbnails of the 2 or 3 best-scoring slides (even under the threshold) that open the slide. Text only by default. When Settings turns on "Speak web answers", a Listen button plays it in a free stock voice labeled "AI voice (a stock voice, not mine)."; my voice clone never reads a web answer. Off-topic questions (sports, recipes, news) still get the not-covered response, and logistics questions still go to me. See step 7b of "Inside `/api/ask`".
- Added Oct 7. Question is about logistics (meetings, office hours, missed class, absences, grades, regrades, extensions, deadlines, rescheduling a presentation, Canvas access, team problems, dropping the course): a written "That one is for me directly. My twin only explains course material. For meetings, absences, grades or deadlines, please email me or come to office hours." plus the suggested chips. No slides, no audio. See step 7a of "Inside `/api/ask`".
- Added Oct 8 (instructor alerts). Student reports a technical problem Ben can fix (an API key out of credits, a broken submission, a broken quiz): the FAQ card says "Thanks, I've flagged this for Prof. Collier." only when a text was sent or the alert was stored for Settings, otherwise "Please email Prof. Collier or the TA." with the TA card; a matched Canvas link either way. No slides, no audio. See "Instructor alerts" below.

### Where this is in the course

Added Oct 5. Every segment shows a source card under the slide (on a phone, directly under the slide and above the caption). It is the part of the answer that sends a student back to the real materials.

- **Card title:** "Where this is in the course."
- **Course:** code and title, for example "70-445 AI for Business Leaders".
- **Session:** number and title, for example "Session 06: Neural Networks and Deep Learning".
- **Date:** the class date, for example "Sep 10, 2026".
- **Slide:** "Slide 14", matching the page number in the slide PDF students have on Canvas.
- **Thumbnail:** a small copy of the slide image, so the student can recognize it when they flip through the deck.

For a code segment, the card says the notebook name and cell number instead of a slide number.

When the segment has a clip, the card also shows a button: **"Watch me explain this in class."** Tapping it pauses the walkthrough and plays the class recording in place of the slide image, with a small label "Recorded in class on Sep 10, 2026 (my real voice, not the AI voice)." When the clip ends, or the student taps "Back to the slide," the slide returns and the walkthrough stays paused until they press play. The clip ending never advances the walkthrough; that is separate from the advance logic, which runs when a narration audio clip ends.

After the last segment, the chat lists the answer's sources (course, session, date, slide) above the two follow-up questions, so a student can write them down.

### Settings page

Added Oct 5, for me only. `/admin.html`, behind a separate admin passcode and its own cookie, never linked from the student page. Five sections (eight since Oct 7, with Prompts, Analytics and Evals):

1. **Model.** Provider dropdown (Claude native, OpenAI native, OpenRouter) and a model picker. Claude and OpenAI show a short curated list plus a free-text model id. OpenRouter shows its live model list, searchable. "Test this model" runs one sample question through the full ask pipeline and shows the result and latency. Save writes the choice to the `settings` table.
2. **Voice.** The voices on the ElevenLabs account, each with a preview I can play, plus a stock voice and a captions-only option. Saving changes `voice_id`. Pre-generated audio is keyed by voice id, so changing the voice never plays the old one.
   *Changed Oct 5 (voice tiers).* Three groups, each voice with a Preview button, and a pill that says which cost money:
   - **My voice clone**: ElevenLabs voices with category `cloned` (or a `professional` clone this account owns), the `ELEVENLABS_VOICE_ID` voice first. Costs ElevenLabs credits.
   - **ElevenLabs voices**: every other voice on the account (`premade`, `generated`, and Voice Library voices, which ElevenLabs also calls `professional` but this account does not own). Costs ElevenLabs credits.
   - **Free Microsoft voices**: Microsoft neural voices through the `edge-tts` package, no key and no cost. A curated list of teaching voices (Andrew, Ava, Brian, Emma, Christopher, Aria, Ryan, Sonia) plus a field for any other voice's ShortName, checked against Microsoft's voice list.
   - Plus **Server default** (`ELEVENLABS_VOICE_ID`) and **Captions only**.
   ElevenLabs previews play the voice's own `preview_url` (no characters spent). Free previews come from `GET /api/admin/voice-preview`, which speaks one fixed sentence set on the server. A second control sets the fallback: when an ElevenLabs voice fails or hits today's cap, answers continue with captions only (the default) or with a chosen free voice. The page shows the label students will see for the saved voice.
3. **Courses and source material.** Courses, then sessions under each, with date, title, and what exists for that session: slides, transcript, video, clips, indexed or not. I can add a course (code, title, term), add a session, and upload source material for it: a slide PDF or pptx, a Zoom VTT, the class video, a notebook. Files go straight from the browser to the private bucket. Each upload shows its status (uploaded, processing, ready, error with a message) and a "Re-run" button. A switch hides or shows a session for students.
4. **Limits and access.** The daily voice character cap (editable), the per-visitor limits (shown, not editable), and a field to rotate the student passcode. *Added Oct 5 (voice tiers):* a second, higher daily cap for the free voices.
   *Added Oct 8 (beyond the slides).* A **Web answers** subsection: an on/off switch for answers from the web (default on), the daily cap on web answers (`DAILY_WEB_ANSWER_CAP`, default 200; Settings can change it, 0 turns them off), today's count against it, and **Speak web answers**: off (text only, the default) or one of the free Microsoft voices. My clone and ElevenLabs voices are not offered there.
   *Added Oct 7 (answer thresholds).* An **Answer thresholds** subsection with two numbers: the slide threshold ("Slides scoring 0.52 or higher are used.") and the course-info threshold (the best Canvas chunk must score at least this and beat the best slide). Each shows its current effective value and where it comes from (my code default, the `INFO_THRESHOLD` environment variable, or a Settings override), takes a value from 0.30 to 0.90, and has a "Reset to default" button that removes the override. A short explainer says what the score is (cosine similarity from `rank()`), the measured ranges (on-topic 0.543 to 0.712, off-topic 0.377 to 0.509 across two 10-question checks), and the trade-off (higher declines more real questions; lower lets off-topic ones through), links to [docs/TESTING_AND_SCORES.md](TESTING_AND_SCORES.md), and suggests running an eval after a change. The last few changes are listed (who, when, old to new).
5. **Activity.** Today's counters, and the last 50 questions: question text, covered or not, top score, provider and model, latency.
   *Changed Oct 7 (activity explainer).* The section header links to [docs/TESTING_AND_SCORES.md](TESTING_AND_SCORES.md) ("How these numbers work", new tab) with the hint "Run a new test: see Testing and scores." Each row shows what kind of answer it was, as a badge in the Covered column: **Covered** (narrated slides), **Stored answer** (a suggested question's stored playlist), **FAQ** (Ben's written course FAQ answer), **From Canvas** (a course-info answer from the Canvas index, added Oct 7), **Referred to Ben** (the logistics referral), or **Not covered**. Model shows **none** when no model was called for that question (FAQ, not covered, stored answer, or a logistics question caught by the keyword pre-check). Top score is blank, with the tooltip "No search ran", when no retrieval ran. Rows logged before `kind` was recorded (or while the column is missing) get a kind inferred from `covered` and `top_score`, marked as inferred.
   *Added Oct 8 (beyond the slides).* A web answer shows a **From the web** badge, with the provider and model that wrote it.
6. **Prompts.** *Added Oct 7 (prompt editor, team brief UPDATE 9).* Ben: "in the admin section I should be able to change any of the prompts for facultytwin". Every prompt a model sees is listed in one registry, `app/prompts.py`, with a name, a title, a description, its default text, and the placeholders it takes. The section shows each prompt with a **Default** or **Edited** badge and when it was last saved. Selecting one opens a monospace editor (12,000 characters at most) with:
   - **Compare with default**: a word-by-word diff of the editor text against the built-in default.
   - **Save**: first shows a diff of the draft against the version saved now, with a required note ("why I changed it"); Save happens only after **Confirm save**.
   - **Test**: runs one question through the real path once with the draft text, without saving it, and shows the output and latency. Counted against the same per-visitor limits as "Test this model". The eval judge and baseline prompts are tested by an eval run instead.
   - **Reset to default**: shows the diff from the saved version back to the default, then confirms.
   - **History**: every saved version, newest first, with its note and time. Each has **Compare** (diff against the saved version) and **Restore**, which shows the same diff and asks for confirmation. Restoring saves that text again as a new version, so history only grows.
   - After a save, a hint with a **Run an eval with this prompt** button: it goes to the Evals section of this page when that section exists, and otherwise to [Testing and scores](TESTING_AND_SCORES.md#prompt-changes).
   - A standing warning: prompts change what students hear, and the safety checks in code still run on every answer whatever the prompt says.

   Placeholders are written `{name}` and are filled by the code at call time (for example `{max_words}`, the narration word cap). Saving checks that every required placeholder is present and that no unknown `{name}` is used; other braces (such as the JSON shape) are left alone. The registry holds:

   | Name | What it is | Placeholders (required in bold) |
   | --- | --- | --- |
   | `narration_system` | The grounding prompt for narration (step 8 of `/api/ask`) | **`{max_words}`**, `{target_words}` |
   | `logistics_classifier` | Sorts course content from logistics (step 7a) | none; must keep the words `course_content` and `logistics` |
   | `course_info_answer` | Answers course-info questions from the Canvas chunks (step 6a, `app/course_info.py`) | **`{max_words}`**, `{not_answered}`; must keep the word `answer` |
   | `incident_classifier` | Added Oct 8. Whether a student reports a broken quiz, submission or API key, and which course and item ("Instructor alerts", `app/alerts.py`) | none; must keep `incident`, `api_credits`, `submission`, `quiz`, `confidence` |
   | `web_scope_classifier` | *Added Oct 8.* Sorts a not-covered question into `course_adjacent`, `off_topic`, or `logistics` (step 7b, `app/web_answer.py`) | none; must keep the words `scope`, `course_adjacent`, `off_topic` and `logistics` |
   | `web_answer` | *Added Oct 8.* Writes the web-grounded answer with the active provider's web search tool (step 7b) | **`{max_words}`** |
   | `eval_judge` | The rubric each LLM judge scores answers with (`evals/rubric.py`) | **`{dimensions}`** |
   | `eval_baseline` | The generic chatbot the twin is compared with (`evals/targets.py`) | none; must keep the word `answer` |

   Ben's FAQ answers and the logistics referral message are written text shown word for word, not model prompts, so they are not in the registry.
7. **Analytics.** *Added Oct 7 (admin analytics).* How students use the twin, what they ask about, and what it costs, for the last 7, 30 or 90 days. Test traffic (the live smoke check, evals, model and prompt tests) is left out of the student numbers unless I tick **Show test traffic**; its spend is always counted, under its own purposes. Panels:
   - **Tiles:** questions, covered %, walkthroughs, FAQ, course info, referred to me, declined, average and median latency, estimated spend (models, voice, embeddings).
   - **Spend over time:** estimated USD per day, stacked by provider and model (top seven, the rest folded into Other), zero baseline.
   - **Tokens and characters:** input and output tokens by model and by purpose (`narration`, `logistics`, `course_info`, `web_scope` and `web_answer` (added Oct 8), `prompt_test`, `smoke_test`, `eval_generate`, `eval_judge`, `topic_label`), voice characters by tier (my clone, ElevenLabs stock, unverified, free), Voyage embedding tokens.
   - **Questions:** per day (answered from slides vs other) and by hour of day (Eastern time).
   - **Topics:** questions per course, then session, then slide (from each answered question's top slide and its session title), the top 20 slides, content gaps (declined questions grouped by wording, most frequent first, scrubbed text), FAQ hits per entry, and how questions were asked (suggested chip, typed, follow-up chip).
   - **Engagement:** questions, walkthroughs returned, first segment played, walkthrough completed, plus class clip plays, audio failures, chip taps, typed questions, follow-up taps, course filter changes (from the student page's events).
   - **Model performance:** average judge scores (the six dimensions) and pass rate per generator model across every eval run in the bucket (`evals/index.json`, `evals/runs/<id>/results.jsonl`), leaving out runs marked excluded; "No eval data yet" until a run exists.
   - **Label topics:** one call to a small model on the active provider (Claude Haiku 4.5, GPT-6 Luna, or `openai/gpt-6-luna` on OpenRouter) groups the newest 10 to 300 student questions (scrubbed text only, test traffic left out) into at most 12 themes with counts and example questions. Examples are referenced by number, so the model cannot invent one. At most 10 runs a day, 1,500 output tokens each. Each run is saved in the bucket at `analytics/topics/<UTC>.json` and listed with its history.
   - **Export CSV** of the question log for the range (formula-looking cells are prefixed so a spreadsheet does not run them).
   - **Price table:** USD per million input and output tokens per model, per million Voyage tokens, ElevenLabs per thousand characters by plan, edge-tts free. Defaults were read from each provider's public price page (source link and date on each row, marked "verify"); edits are saved in `settings.pricing`. OpenRouter models missing from the table use OpenRouter's live price list. The spend figure is an estimate (tokens times this table), not a bill.
   *Added Oct 8 (beyond the slides).* Web searches are priced per search on top of tokens: the table has a `web_search` row per provider in USD per 1,000 searches (Anthropic $10; OpenAI $10 for reasoning models such as GPT-6.1 Sol, $25 for non-reasoning models; OpenRouter $7 for its Exa engine), each with its source link and date, marked "verify". Searches are counted per call from the provider's reply (`usage:<day>:web_answer:<provider>:<model>:searches`) and their cost is added to that model's spend and to the `web_answer` purpose.
   The Activity table also shows a **Test** badge on test-traffic rows ("Test?" when it is only inferred because the question is word for word one of the three smoke-check questions, for rows logged before `source` existed).


   - **Question set:** count, answerable count and categories of the de-identified question set in the private bucket (`evals/questions.jsonl`), with a note that question text is private and admin only. The questions are listed behind a "Show the questions (private)" disclosure, and a question can be added or edited there; every save re-runs the same privacy checks as `evals/dataset.py` (emails, URLs, long numbers, keys, names caught by `app/privacy.py`), and the error names the problem, never the text.
   - **Calibrate a judge first:** pick a judge model and score the 8 invented calibration cases (`app/eval_calibration.jsonl`, a copy of `evals/calibration.jsonl`), one case per request, with a progress bar. Results (cases met, which missed) show in a calibration table next to the report card.
   - **Start a run:** a name; up to 3 models that answer and up to 3 judges, each picked from the same provider model lists as the Model section (free-text ids allowed); the number of questions (1 to 30, picked the same way as the CLI: categories in turn, most common first); categories. "Check the estimate" shows the questions, answers, model calls (typical and at most), embeddings, today's remaining eval budget, a rough cost, and a **self-grading** warning when a judge is the same model as an answering model. "Confirm and start" is required. Jev is not offered: it runs from the command line only (it needs deepeval, which is not in the Vercel bundle).
   - **Progress:** the page drives the run one step at a time (one question x one answering model per request), with a progress bar, Pause, Resume and Cancel. Reloading the page finds the run in progress and Resume continues from the server's state.
   - **Runs:** one card per run with its status, judges, and per answering model: pass rate and all six scores with n, the right call on answering versus declining, the fallback rate, and judge agreement. Imported runs say so, and the Oct 7 run says it predates PR #35 (access-code filter) and PR #37 (logistics routing). The errored first attempt shows as excluded.
   - **Run detail:** one row per question x answering model: question, category, model, top score, outcome (Covered, Stored answer, FAQ, Referred to Ben, Not covered, Course info, plus what the question set expects), and each judge's verdict and six scores, with expandable narration, the real reply, and each judge's rationale and issues. Filters: category, model, verdict (all pass, any fail, judges disagree, judge error).
   - **Report card:** one line per answering model over time (inline SVG, zero baseline, palette and marks from the dataviz method, a legend, direct end labels, a tooltip with n and the judges on every point, keyboard focusable), for pass rate, each of the six scores, the right call, fallback rate, or judge agreement; a table view of every point; and a side-by-side comparison of two runs.
   - **Clear scales everywhere.** Every header says its scale ("Grounded (1–5, 5 best)", "Pass rate (% of answers judged pass)", counts say what they count). A legend under every table gives each dimension's rubric line, 1 = very poor, 3 = acceptable, 5 = excellent, that each score is a mean over n answers, what n/a means (for example grounded when the twin declined or the baseline had no slides, shown as "n/a (no slides)", never a dropped column), and that pass or fail is each judge's overall verdict, given separately from the six scores. Rates are percentages. Chart axes are labeled with units: pass rate 0 to 100%, scores on a 0 to 5 axis.
9. **Student alerts.** *Added Oct 8 (instructor alerts).* On/off switch, daily text cap, the destination masked to its last 4 digits, Send test text, and the recent alerts with delivery status. See "Instructor alerts" below.

The page shows only whether each key is configured, never the key itself.

### Instructor alerts (added Oct 8)

Ben, Oct 8: "Bonus feature if a student mentions an api key out of Money or a submission broken or a quiz broken text my cell and say there is a simple problem to fix have the agent figure out which course is the issue". He chose Twilio SMS. Code: `app/alerts.py` (detection, course resolution, guards, the text, storage), `app/admin_alerts.py` (Settings routes), `public/admin-alerts.js` (Settings panel).

**When it runs.** First thing in `answer()`, before the stored-topic and FAQ checks (step 2b of "Inside `/api/ask`"). It never blocks a normal answer: if nothing is detected, or detection fails, the question goes on exactly as before.

1. **Keyword pre-check** (no model call). Three families, each with *strong* phrases (a clear report of a problem) and *weak* ones (worth a second look):
   - `api_credits`: an API key out of credits, quota, money, funds or billing; `insufficient_quota`; the key is not working, invalid, or expired (weak); a 429 or rate-limit error (weak).
   - `submission`: can't submit or upload; the submission (or Gradescope, or the Canvas submission) is broken, failing, erroring or greyed out.
   - `quiz`: the quiz is broken, won't load, open, start or submit, is locked, stuck or frozen; the access code is not working; the quiz timer is wrong or ran out.
   A concept question is never a hit: a message that starts like a question about an idea ("what is an API key", "how do API keys work", "explain rate limits") and has no first-person or problem word (my, I, we, broken, not working, can't, error...) is skipped. So is a message Ben's FAQ already answers about a participation point Canvas missed (`canvas_participation`), which goes to the TA as before.
2. **One small classifier call** when the pre-check hits (prompt `incident_classifier` in the Prompts registry, purpose `incident_classifier` in Analytics, at most 200 output tokens, the active provider and model). It returns `{"incident": true|false, "type": "api_credits"|"submission"|"quiz"|"other_course_tech", "course": "70445"|"45884"|null, "item": "<assignment or quiz title>"|null, "confidence": 0..1}`. The student's text goes in as JSON data, never as instructions; anything else in the reply is ignored.
3. **The decision.** Alert when the classifier says `incident: true` with confidence at least 0.6, or when the pre-check hit was strong, unless the classifier says `incident: false` with confidence at least 0.6. A weak hit with a failed or unsure classifier is not an alert. The type is the classifier's when valid, else the keyword family's.
4. **Which course.** In order: the student's course filter; then the best match of the question's words (plus the classifier's item) against assignment and quiz titles in the Canvas info index (`content/info_index.json`, kind `assignment`; pages, files and links only when no assignment matches), scored by shared words, with "Quiz 4", "Lab 3", "Homework 2", "Week 6" counted as one strong token so "quiz 4" finds "Quiz 4: ..."; a tie between the two courses settles nothing; then the classifier's course. With a match, the alert carries the item's Canvas title, link, and match score. Without a course, the text says the course is unclear.

**The text** (at most 300 characters, plain ASCII so it stays within 2 SMS segments):
`Faculty Twin: simple problem to fix in 70-445 (quiz): 'Quiz 4: How Large Language Models Work' may be broken. Student said: '<first ~120 characters>'. <Canvas link> Oct 8 9:14 PM ET`. Repeats add `, 3 reports` inside the parentheses. The quote is scrubbed first: `app/privacy.py` (emails, phone numbers, long numbers, handles, names after "my name is", a title, or "classmate"), then anything that looks like an API key (`sk-...`, long mixed tokens) becomes `[key]`, access-code-like tokens become `[code]`, and web addresses become `[link]`. Never a visitor id, an address, a cookie, or a name.

**Sending.** Twilio's Messages API: `POST https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json`, HTTP basic auth (account SID and auth token), form-encoded `To` (`ALERT_TO_PHONE`), `Body`, and `From` (an E.164 number) or `MessagingServiceSid` when `TWILIO_FROM` starts with `MG`. A 2xx reply with a message `sid` is success (status usually `queued`); anything else is a failure, keeping Twilio's error `code` and a short message (never the auth token). 10 second timeout, no retry.

**Guards.**
- **Dedupe:** the same course, type and item within 2 hours texts once. Each repeat is stored as a `repeat` record pointing at the alert it repeats, and the next alert for that problem after the window says how many reports it covers ("3 reports").
- **Daily cap:** `ALERT_DAILY_CAP` texts per UTC day (default 10; Settings can change it, 0 to 50). Past it the alert is stored with status `over_cap` and no text goes out.
- **Per visitor:** one alert (sent, stored, or counted as a repeat) per visitor per day, a counter keyed on the hashed visitor id. A second report the same day is not stored. Both counters fail closed.
- **Switch:** Settings > Student alerts on or off (`settings.alerts_enabled`, default on). Off: detection still runs, so the student gets the right reply, but nothing is stored or sent.
- **Test traffic never texts:** Settings model and prompt tests, evals, and the smoke check (no visitor, or an `X-FT-Source` test header) run detection as a dry run: nothing stored, nothing sent.
- **Fail safe:** if Twilio is not configured or the send fails, the alert is still stored (status `not_configured` or `failed`) and shows in Settings.

**What the student sees.** The FAQ card, kind `alert`, no slides and no audio; the answer stops there (no narration):
- Sent, stored because Twilio is not set up or failed, or counted as a repeat: "Thanks, I've flagged this for Prof. Collier." with a short line that I'll look into it, plus the matched Canvas link.
- Anything else (switch off, over the daily cap, this visitor's alert for the day already used, a dry run, or storage failed): "Please email Prof. Collier or the TA." with the TA contact card (`TA_CONTACTS`) and the matched Canvas link.
The reply never says it was flagged unless a text was sent or the alert was stored for Settings.

**Storage, no new SQL.** One JSON file per record in the private bucket at `alerts/<UTC>.json` (`YYYYMMDDTHHMMSS.ffffffZ`): `{id, at, kind: "alert"|"repeat"|"test", course, course_source, type, item, item_url, match_score, confidence, keyword, strong, classifier, quote, message, reports, of, status: "sent"|"failed"|"not_configured"|"over_cap"|"repeat", twilio: {sid, status, error_code, error}, segments}`. Without Supabase (local dev, tests) they live in memory. Counters: `alerts_sent:<day>` (daily cap), `alert_visitor:<visitor hash>:<day>`, `alert_tests:<day>`, and `sms:<day>:twilio:messages|segments` for Analytics. The question log gets kind `alert`.

**Settings > Student alerts** (`#sec-alerts`): the on/off switch, the daily cap, where texts go (the destination masked to its last 4 digits, and which Twilio variables are set, as booleans), today's count against the cap, a **Send test text** button (a fixed message; counts against the cap; at most 5 a day), and the newest 50 alerts: time (ET), course, type, item (with its Canvas link), reports, delivery status, and the scrubbed quote.

**Analytics.** Classifier tokens show under the purpose `incident_classifier`. Texts are a cost line: "Twilio SMS" in spend over time and in the spend tile, priced as segments times the per-segment price in the price table (`pricing.sms`: base price plus an average carrier fee per segment, editable; defaults from Twilio's US price page, marked verify).

**Check:** with no Twilio variables set, typing "my quiz 4 won't load" with the 70-445 filter returns the flagged card, and Settings > Student alerts shows a `not_configured` alert for 70-445 quiz "Quiz 4: How Large Language Models Work" with the scrubbed quote; "what is an API key" gets a normal answer; `uv run pytest tests/test_alerts.py` passes (Twilio is mocked; no test sends a real text).

## Architecture

One repo, one Vercel project. The FastAPI backend deploys as a single Vercel Function, and the frontend, slide images, and pre-generated audio are static files in `public/`, served from Vercel's CDN. One URL, one deploy, no cross-origin setup this week, and no long cold start. Fallback if the Vercel deploy fights back in Block 0: the same code on a paid Render instance.

> **Changed Oct 5.** Slide images, audio, the index, and clips no longer live in the repo or in `public/`. The repo is public, and the content now includes de-identified class transcripts and class video, which I want behind a passcode. All course content lives in a private Supabase Storage bucket (`twin-content`). The backend loads the index from it at cold start and hands the browser short-lived signed links for images and clips. `public/` holds only the HTML, CSS, and JavaScript, which contain no course content. Still one repo, one Vercel project, one URL.

```mermaid
flowchart LR
    B["Student browser<br/>passcode, question box, stage, player<br/>no keys here"]
    G["Settings page (admin.html)<br/>admin passcode<br/>no keys here"]
    A["Backend (FastAPI on Vercel)<br/>1. Check cookie and limits<br/>2. Find matching slides<br/>3. Order them, attach code and clips<br/>4. Write narration as JSON<br/>5. Sign audio, image and clip links<br/>holds all keys"]
    E["Voyage AI<br/>embeddings"]
    L["LLM provider<br/>Claude, OpenAI or OpenRouter"]
    V["ElevenLabs or Microsoft edge-tts<br/>voice"]
    S["Supabase Storage<br/>private bucket twin-content<br/>index, slide images, clips, inbox"]
    P["Supabase Postgres<br/>settings, counters, question log,<br/>courses, sessions, sources"]
    W["Local build machine: pipeline and worker<br/>render, de-identify, align,<br/>cut clips, embed, upload"]
    R["Private Lecture Archive<br/>video, VTT, slides, rosters<br/>never leaves the local build machine"]

    B -- "question, cookie" --> A
    A -- "playlist with signed links" --> B
    B -- "signed links: images, clips" --> S
    G -- "admin requests" --> A
    G -- "direct upload with signed upload URL" --> S
    A --> E
    A --> L
    A --> V
    A -- "read and write" --> P
    S -- "index loaded at cold start" --> A
    W -- "poll and update sources" --> P
    S -- "inbox uploads" --> W
    R --> W
    W --> E
    W -- "build outputs" --> S
```

The browser only ever talks to the backend, except to fetch a file through a link the backend signed (or, on the Settings page, to upload one). The pipeline runs on the local build machine (the computer that holds the private archive), ahead of time or whenever a new upload arrives, and it also calls the embeddings API, so the deployed service starts with everything it needs already in the bucket.

| Part | What it does | Built with |
| --- | --- | --- |
| Indexer (`indexer/`) | Runs on the local build machine, once per content change. Turns a deck and a notebook into slide images plus `index.json`. | Python, python-pptx, nbformat, LibreOffice and pdftoppm for slide images, an embeddings API |
| Backend (`app/`) | Answers questions: retrieval, narration script, speech. Holds every key. Enforces limits. | Python, FastAPI as one Vercel Function, numpy, an LLM API, ElevenLabs for the voice |
| Frontend (`public/`) | The idle and presenting screens, the playlist player. | Plain HTML, CSS, JavaScript. No framework, no bundler, same as the portfolio site |
| Static content (`public/slides/`, `public/audio/`) | Slide PNGs and pre-generated audio for suggested questions. | Files committed to the repo, served from the CDN |
| Index (`content/index.json`) | Slide and code records with embeddings, read by the backend. | A JSON file bundled with the function |
| Counters and question log | Rate-limit counters, the daily voice cap, and one row per question. | Supabase |

> **Changed Oct 5: the parts now read as follows.**

| Part | What it does | Built with |
| --- | --- | --- |
| Pipeline (`indexer/`) | Runs on the local build machine. Renders slides, de-identifies transcripts, aligns transcript to slides, cuts clips, builds and embeds the index, uploads to the bucket. Each stage is its own script and can be re-run alone. | Python via `uv`, pdftoppm, LibreOffice, python-pptx, nbformat, ffmpeg, numpy, Voyage AI |
| Local worker (`indexer/worker.py`) | Picks up files uploaded through Settings and runs the same stages for that session. Runs on the local build machine because the rosters and ffmpeg are there. | Python, Supabase REST |
| Backend (`app/`) | Passcode check, retrieval, narration, speech, signed links, Settings API. Holds every key. Enforces limits. | Python, FastAPI as one Vercel Function, numpy, httpx, Claude or OpenAI or OpenRouter, Voyage AI, ElevenLabs |
| Frontend (`public/`) | Passcode, idle and presenting screens, playlist player, Settings page. | Plain HTML, CSS, JavaScript. No framework, no bundler |
| Private content (bucket `twin-content`) | Slide images, clips, the index, pre-generated audio, and the upload inbox. | Supabase Storage, private, served only through signed URLs |
| Index (`content/index.json` and `content/embeddings.npy` in the bucket) | Slide and code records, and one embedding row per record. Loaded at cold start. | A JSON file and a numpy array |
| Database | Settings, rate-limit counters, the daily voice cap, the question log, courses, sessions, and sources. | Supabase Postgres, schema in `supabase/schema.sql` |

**Why these choices**

- Vercel's Python runtime runs FastAPI directly, so the backend code is ordinary FastAPI. Vercel looks for a FastAPI instance named `app` in a file such as `app/main.py`, and serves anything in `public/` at the matching root path.
- A function keeps nothing between requests: no saved files, no counters in memory. So pre-generated audio lives in the repo, live audio is streamed back without being saved, and counters live in Supabase. *(Changed Oct 5: pre-generated audio now lives in the bucket, not the repo. The rest still holds.)*
- The index is a JSON file, not a vector database. One deck and one notebook produce roughly 60 to 100 vectors, and numpy searches that in under a millisecond. Ben can open the file and read it, which helps when explaining the code. *(Changed Oct 5: two courses produce about 1,200 records. That is still tiny for numpy, so the reasoning holds. Embeddings moved to a separate `embeddings.npy` (about 5 MB at 1,024 dimensions) so the JSON stays readable.)*
- Speech is generated per segment, not per answer. The first clip can start while later ones are still being made, and one failed clip does not sink the whole answer.
- Added Oct 5. **Private bucket, not the repo.** The repo is public and graded. Class transcripts and video are class records, so they stay behind the passcode even after de-identification.
- Added Oct 5. **Signed links, not proxying.** Images and clips go straight from Supabase to the browser through links that expire in an hour. The function never streams large files, which matters because Vercel caps request and response bodies and bills function time. The same reason makes Settings uploads go straight from the browser to the bucket with a signed upload URL: Vercel caps request bodies at 4.5 MB, and a class video is far larger.
- Added Oct 5. **The pipeline runs on the local build machine.** Video work (ffmpeg, frame matching) is too heavy and too slow for a function, and de-identification needs the rosters, which never leave that machine. The worker is a small loop around the same stage scripts I run by hand.
- Added Oct 5. **A small provider interface, not SDKs.** `app/llm.py` has one function per provider, `complete_json(system, user, max_tokens) -> str`, each a plain `httpx` call. Switching providers changes one row in `settings`, not the code. The grounding prompt and the JSON validation are the same for every provider. Embeddings stay on Voyage whatever the narration provider is, because the index was built with Voyage and the question must be embedded the same way.
- Added Oct 5. **Index reload.** The backend caches the index in memory for the life of a warm function. At most every 60 seconds it reads `settings.index_version`; if the pipeline uploaded a new index, it reloads. *Changed Oct 8 (code review):* the version is read before the download, so a bump that lands mid-download is noticed at the next check. A download whose `index.json` says an older `index_version` than the setting (a CDN copy cached before the upload finished) is loaded again at the next check, at most 3 times. A file that cannot be parsed (half uploaded) keeps the loaded index, or answers 503 on a cold start.

**Access and cookies.** Added Oct 5. Everything except `/api/health` and `/api/login` needs the student cookie `ft_session`: signed with `SESSION_SECRET`, httpOnly, SameSite=Lax, Secure in production, 7 days. It carries a random visitor id (used for rate limits) and a passcode version, so rotating the passcode signs everyone out. The Settings API needs a separate `ft_admin` cookie (same signing, 12 hours) issued only for `ADMIN_PASSCODE`. The static HTML, CSS, and JavaScript are public; nothing in them is course content.

**Configuration.** Added Oct 5. `.env.example` lists these names with no values; real values live in Vercel and in my git-ignored local `.env`.

| Variable | Used for |
| --- | --- |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY` | Narration providers. Only the active one is needed. |
| `LLM_PROVIDER`, `LLM_MODEL` | Defaults when the `settings` row is empty: `anthropic` and `claude-sonnet-5-5` |
| `VOYAGE_API_KEY`, `VOYAGE_MODEL` | Embeddings for the index and for questions |
| `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` | Voice; the voice id is the default when `settings.voice_id` is empty |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_BUCKET` | Storage and Postgres, server side only. Bucket defaults to `twin-content` |
| `STUDENT_PASSCODE`, `ADMIN_PASSCODE` | Bootstrap passcodes; a rotated student passcode lives as a hash in `settings` |
| `SESSION_SECRET`, `AUDIO_SIGNING_SECRET` | Cookie signing and audio-link signing |
| `DAILY_VOICE_CHAR_CAP` | Default daily voice cap, overridable in Settings |
| `DAILY_FREE_VOICE_CHAR_CAP` | Added Oct 5. Default daily cap for the free Microsoft voices (200,000 characters), overridable in Settings |
| `EDGE_TTS_RATE`, `EDGE_TTS_PITCH` | Added Oct 5, optional. Speaking rate and pitch for the free voices; defaults `-5%` and `+0Hz` |
| `CONTENT_DIR` | Local folder used instead of Supabase for local development and tests, for example `~/Lecture Archive/_build` |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM`, `ALERT_TO_PHONE`, `ALERT_DAILY_CAP` | Added Oct 8 (instructor alerts), optional. Twilio account and sender (an E.164 number or a Messaging Service SID starting `MG`), Ben's cell in E.164, and the daily text cap (default 10, overridable in Settings). Without them alerts are stored for Settings and no text is sent |

With `CONTENT_DIR` set, `app/storage.py` reads the index and files from that folder and signs links to a local-only route, so the app runs and the tests pass without Supabase.

**Repo layout**

```
faculty-twin/
  README.md            written by Ben
  prompt_log.md        separate file, same folder
  .gitignore           includes .env
  .env.example         key names only, no values
  requirements.txt
  vercel.json          sets the function's max duration
  app/main.py          FastAPI instance named app, routes
  app/retrieval.py     ranking and ordering (hand-written)
  app/narration.py     LLM call and grounding prompt
  app/speech.py        voice call, signed links, daily cap
  public/index.html
  public/app.js        player and state changes
  public/styles.css
  public/slides/       clustering-001.png ...
  public/audio/        pre-generated clips
  content/index.json
  indexer/build_index.py
  tests/test_retrieval.py
```

> **Changed Oct 5: the layout is now this.** `public/slides/`, `public/audio/`, and `content/` leave the repo (they are git-ignored and live in the bucket). New backend modules split out auth, providers, storage, and limits. The indexer becomes one script per pipeline stage plus the worker.

```
faculty-twin/
  README.md               written by Ben
  prompt_log.md           separate file, same folder
  .gitignore              .env, content/, _build/, slide images, video, VTT, .npy
  .env.example            variable names only, no values
  requirements.txt
  vercel.json             sets the function's max duration
  supabase/schema.sql     settings, counters, question_log, courses, sessions, sources
  app/main.py             FastAPI instance named app, routes
  app/auth.py             passcode checks, signed ft_session and ft_admin cookies
  app/retrieval.py        ranking and segment selection (hand-written)
  app/narration.py        grounding prompt, JSON validation, fallback
  app/llm.py              provider interface: Claude, OpenAI, OpenRouter; active choice from settings
  app/speech.py           ElevenLabs call, audio signing, daily cap
  app/voices.py           voice tiers, labels, fallback, which cap a voice uses (added Oct 5)
  app/edge_voice.py       free Microsoft voices through edge-tts, previews (added Oct 5)
  app/storage.py          bucket or CONTENT_DIR access, index load and reload, signed URLs
  app/limits.py           rate limits, counters, question log
  public/index.html       passcode, idle, presenting
  public/app.js           player and state changes
  public/styles.css
  public/admin.html       Settings page, never linked from the student page
  indexer/slides.py       render slide images, extract text and notes
  indexer/deidentify.py   parse VTT, scrub names, mark student turns, leak check
  indexer/align.py        match transcript time to slides
  indexer/clips.py        cut class clips under the clip rules
  indexer/build_index.py  records, code cells, embeddings, index.json and embeddings.npy
  indexer/upload.py       mirror build outputs to the bucket, bump index_version
  indexer/worker.py       poll sources, run the stages for uploaded files
  tests/test_retrieval.py Ben's selection tests (plus failing stubs for his functions)
  tests/                  auth, signing, providers, storage, pipeline tests with fakes (no network)
  tests/test_contract_mock.py  the ?mock=1 dev mock answers in the real API's shapes (added Oct 8)
  tests/e2e/              Playwright browser tests of public/ with ?mock=1, run with --e2e (added Oct 8)
  .github/workflows/tests.yml  CI: the suite with coverage, node --check, browser tests (added Oct 8)
```

## Data

**Content sources, in order of value**

1. The slide deck (.pptx): slide text and speaker notes.
2. The matching notebook (.ipynb): code cells and the markdown cells above them.
3. A cleaned lecture transcript for that session, with student questions removed. This is what makes the narration sound like Ben's explanation instead of a summary of bullet points. Optional for tier 1.

> **Changed Oct 5.** The order is now:
>
> 1. The most current slide PDF per session. It gives the images and the slide numbers students see on Canvas, so citations match.
> 2. The pptx speaker notes, matched to PDF pages by position.
> 3. The Zoom VTT transcript per session, de-identified (for 45-884 Fall 2026, the ASR-corrected cue files in my FacultyTwinContent folder, `*.cleaned.timed.jsonl`, same Zoom timings; for 70-445 session 11, which has no Zoom captions in Drive, a local Whisper transcript until the Zoom file is pulled), with student turns marked and excluded. No longer optional: it is what lets the twin say what I actually said in class, and it drives alignment and clips.
> 4. Notebooks, when a session has them.
> 5. The class video. Used only to align slides and cut clips. Never indexed as text.

**IDs.** Added Oct 5. Course codes are `70445` and `45884` (shown to students as 70-445 and 45-884). A slide id is course, two-digit session, and three-digit slide number (1-based PDF page): `70445-s06-014`. A code id is course, session, notebook number (1-based, in file-name order), and cell number: `45884-s05-nb1-c022`.

**What the indexer writes.** One record per slide and one per code cell, all in `content/index.json`:

```json
{
  "id": "clustering-014",
  "kind": "slide",
  "deck": "clustering",
  "order": 14,
  "title": "Choosing k: the elbow method",
  "text": "slide text, notes, and any transcript passage matched to this slide",
  "image": "/slides/clustering-014.png",
  "related_code": ["clustering-cell-22"],
  "embedding": [0.013, -0.094]
}
```

Code cells use the same shape with `"kind": "code"`, a `source` field holding the code, and no image. `related_code` is filled in by hand for tier 1: a short mapping file that says which cells go with which slides. Ten minutes of manual work beats an hour of tuning automatic matching.

> **Changed Oct 5.** Records carry course and session fields, keep text, notes, and transcript apart, and point at private paths in the bucket. Embeddings moved to `content/embeddings.npy` (float32, row *i* belongs to record *i*), so the JSON has no vectors in it. The hand-written mapping survives as `indexer/code_map.json` (ids only, no content); sessions without a mapping attach no code.

```json
{
  "id": "70445-s06-014",
  "kind": "slide",
  "course": "70445",
  "course_title": "AI for Business Leaders",
  "session": 6,
  "session_title": "Neural Networks and Deep Learning",
  "date": "2026-09-10",
  "slide_number": 14,
  "title": "slide title",
  "text": "slide body text, de-identified",
  "notes": "speaker notes, de-identified",
  "transcript": "what I said in class while this slide was up, instructor turns only, de-identified",
  "image": "slides/70445/s06/70445-s06-014.webp",
  "clip": "clips/70445-s06-014.mp4",
  "related_code": []
}
```

Code records use the same shape with `"kind": "code"`, a `source` field holding the code, a `notebook` field with the notebook's file name, `"image": null`, `"slide_number": null`, and `"clip": null`. The text that gets embedded is title, text, notes, and transcript joined, in that order.

**Build outputs.** Added Oct 5. Everything below is private. The pipeline writes it to `~/Lecture Archive/_build/` and `indexer/upload.py` mirrors the parts the backend needs to the bucket. None of it is ever committed.

| Path | Contents | Uploaded to the bucket |
| --- | --- | --- |
| `slides/<course>/s<NN>/<slide_id>.webp` | One image per PDF page, 1600 px wide | yes |
| `slides/<course>/s<NN>/slides.json` | `[{slide_id, course, session, slide_number, title, text, notes}]`, de-identified | no (folded into the index) |
| `transcripts/<course>/s<NN>.json` | `{course, session, date, cues: [{start, end, text, speaker}]}`, times in seconds, `speaker` is `"instructor"`, `"student"`, or `"unclear"`, every student name replaced by `[student]`. A cue whose text the PG rule changed also carries `"pg": true` (added Oct 5) | no |
| `align/<course>/s<NN>.json` | `[{slide_id, windows: [[start, end], ...], transcript}]`, where `transcript` is de-identified instructor speech in those windows. May also carry `methods`, parallel to `windows`, each `"frame"` or `"text"`; readers ignore fields they do not know | no |
| `clips/<slide_id>.mp4` | H.264 720p, AAC, faststart, 15 to 90 seconds | yes |
| `clips/manifest.json` | `[{slide_id, course, session, start, end, reason_kept}]`, times in the original recording | yes |
| `content/index.json`, `content/embeddings.npy` | The index (see above) | yes |
| `canvas/<course>/items.json` | *Added Oct 7 (course info).* Student-facing Canvas items from `indexer/canvas_import.py`: `[{id, course, module, position, title, kind, canvas_url, source_url, due_at, updated_at, text, chunks}]`, de-identified, PG, access codes removed | no |
| `content/info_index.json`, `content/info_embeddings.npy` | *Added Oct 7 (course info).* The course-info index: `{"records": [{id, course, title, kind, canvas_url, due_at, text}]}`, one record per chunk of about 150 to 400 words, and its Voyage embeddings (input type document), row i for record i. Optional: missing files turn course-info answers off | yes |
| `topics/topics.json` | Suggested questions and their stored playlists | yes |
| `audio/<voice_id>/<hash>.mp3` | Pre-generated narration for suggested questions, named by voice id and a hash of the narration text. *Changed Oct 5 (voice tiers): new files go under `audio/<voice tag>/`, the same 10-character tag the audio links carry, because free voice ids contain a colon. Older `audio/<ElevenLabs id>/` folders are still read for that voice.* | yes |
| `review/<course>-s<NN>.txt` | De-identification review notes | no, never |

The bucket also has `inbox/<course>/s<NN>/<kind>/<filename>` for files uploaded through Settings.

*Added Oct 7 (admin Evals).* Eval data lives in the same private bucket under `evals/`, as plain JSON objects (no new SQL table, no DDL), read and written only by the function with the service role key and never signed into a link (`storage.is_media_path` allows only `slides/`, `clips/`, `audio/`):

| Path | What it holds |
| --- | --- |
| `evals/questions.jsonl` | The de-identified question set (the format in evals/README.md), uploaded by `scripts/upload_eval_questions.py` after `evals/dataset.py`'s checks pass. Re-checked on every read. Question ids are line numbers (`q001`), so they match CLI runs |
| `evals/runs/<run_id>/run.json` | Config (answering models, judges, `top`, categories), the chosen questions, status (`running`, `done`, `cancelled`, `excluded`), progress (`pairs_done` of `pairs_total`), model calls used and the run's cap, the estimate, self-grading pairs, notes, and `summary.by_generator` (the report card numbers per answering model) |
| `evals/runs/<run_id>/rows/<pair>.json` | One result row per question x answering model (`pair` is 4 digits), written once: `qid, pair, category, answerable, question, reference_answer, generator, outcome, response {status, message, segments [{n, slide_id, narration, evidence}], follow_ups, narration_source, top_score, latency_ms}, judgements [{judge, scores (six, 1 to 5 or null), verdict, rationale, issues} or {judge, error}], calls, at` |
| `evals/runs/<run_id>/results.jsonl` | Every row in one file (the union of what it held and every row object), rebuilt after each step; what Analytics reads |
| `evals/runs/<run_id>/finished.json`, `cancelled.json` | Written once when a run finishes or is cancelled |
| `evals/index/<run_id>.json` | One run's entry: id, name, kind (`admin`, `imported`, `baseline`), status, models, judges, counts, notes, self-grading pairs, prompt versions and `by_generator` metrics, with no question text. The runs list and the report card read these |
| `evals/index.json` | `{runs: [...]}`, every entry, newest first, rebuilt from `evals/index/` on each write (for Settings > Analytics and anything else that wants one file) |
| `evals/calibration/<judge>.json`, `evals/calibration/<judge>/<attempt>/<case>.json` | Latest calibration per judge (cases met, which missed, when, and where it came from: Settings, or the CLI results in evals/README.md), and each case's result, written once |

Storage serves object reads through a CDN, and a read just after an overwrite can return the older copy (seen in a live check on Oct 7). So nothing a step depends on is a read-modify-write of one object: which pairs are done, whether a run finished or was cancelled, and which runs exist come from the bucket's list API (a database query, never cached); rows and markers are written once; the aggregate files are rebuilt as a union, so a stale read can never drop a row or a run; and every eval file is written with `cache-control: no-cache, max-age=0`. The spend counted against a run is the sum of its rows' `calls`.

`run_id` is the UTC start time (`20261007T222857Z`, with a short suffix if two start in the same second) or `baseline-YYYYMMDD`. Per-generator metrics (`app/eval_core.py`, `generator_metrics`): pass rate over every judge's valid judgements, the mean of each of the six dimensions with `score_n` (how many judgements each mean covers), decline accuracy (the summary's right-call rate: answered what is answerable, declined the rest; no judge involved), fallback rate (answers whose narration fell back to the notes), and judge agreement (mean, over judge pairs, of how often they gave the same verdict). Transcripts and alignment stay on the local build machine: the backend only needs what is already folded into the index.

**What the backend returns for a question.** A playlist:

```json
{
  "question": "how do I choose k?",
  "covered": true,
  "segments": [
    {
      "n": 1,
      "slide_id": "clustering-014",
      "image": "/slides/clustering-014.png",
      "code": { "source": "inertias = [] ...", "mark_lines": [3, 4, 5] },
      "narration": "Sixty to ninety words in Ben's teaching voice.",
      "audio": "/audio/9f2c1a.mp3"
    }
  ],
  "follow_ups": ["What is the silhouette score?", "When does k-means fail?"]
}
```

`covered` is false when the best match scores below the threshold. In that case `segments` is empty and the frontend shows the not-covered message.

> **Added Oct 7 (answer thresholds).** "The threshold" is the effective slide threshold: the `slide_threshold` Settings override when one is set, otherwise `NOT_COVERED_THRESHOLD` in `app/retrieval.py` (0.52, the value I chose by hand). It is read through the 30-second settings cache on every question (`app/thresholds.py`), never frozen at import, so a change reaches every warm function within 30 seconds. `app/retrieval.py` is unchanged.

> **Added Oct 7 (logistics check).** A question the logistics check routes to Ben comes back as HTTP 200 with `kind: "logistics"`, no segments and no audio:
>
> ```json
> {"question": "...", "covered": false, "kind": "logistics", "segments": [], "sources": [],
>  "message": "That one is for me directly, not my twin. Please email me or come to office hours.",
>  "follow_ups": ["<up to three suggested questions for this course>"]}
> ```
>
> The page shows its own copy of that message (the `logistics` entry in `COPY`) with the suggested chips. Every other reply has no `kind` field.

> **Added Oct 7 (course info).** A question answered from the Canvas index (step 6a) comes back as HTTP 200 with `kind: "course_info"` and no segments or audio:
>
> ```json
> {"question": "...", "covered": true, "kind": "course_info", "label": "From Canvas", "title": "<top Canvas item>",
>  "message": "<the answer>", "answers": [{"course": "70445", "course_label": "70-445", "text": "<the answer>"}],
>  "links": [{"label": "<Canvas item title>", "url": "https://canvas.cmu.edu/..."}],
>  "segments": [], "sources": [], "follow_ups": ["<up to three suggested questions for this course>"]}
> ```
>
> The page shows it on the FAQ card with a small "From Canvas" label; each link opens in a new tab (`noopener`). Only `https://` links are sent.

> **Added Oct 8 (beyond the slides).** A course-adjacent question no slide covers, answered from a web search (step 7b), comes back as HTTP 200 with `kind: "web"`:
>
> ```json
> {"question": "...", "covered": true, "kind": "web", "label": "Beyond my slides: from the web",
>  "title": "Beyond my slides", "message": "<the answer>", "answers": [{"text": "<the answer>"}],
>  "links": [{"label": "<page title>", "url": "https://..."}],
>  "related": [{"slide_id": "70445-s06-014", "title": "...", "course": "70445", "session": 6, "slide_number": 14,
>               "date": "2026-09-15", "image": "<signed URL>"}],
>  "audio": null, "voice": null,
>  "segments": [], "sources": [], "follow_ups": ["<up to three suggested questions for this course>"]}
> ```
>
> `links` holds 2 to 4 sources taken only from the search tool's citation (or result) metadata, never from the model's text; only `https://` links without credentials are sent. `related` is the 2 or 3 best-scoring slides for the course filter, even under the threshold. `audio` is a signed `/api/audio` link and `voice` is `{"kind": "free", "label": "AI voice (a stock voice, not mine)."}` only when "Speak web answers" names a free voice; otherwise both are null.

> **Changed Oct 5.** Each segment now carries the fields for its source card, a signed image link, and an optional clip, and the playlist has a `sources` list. `covered` works the same way, and `sources` is empty when it is false.

```json
{
  "question": "how does a neural network learn?",
  "covered": true,
  "segments": [
    {
      "n": 1,
      "slide_id": "70445-s06-014",
      "course": "70445",
      "course_title": "AI for Business Leaders",
      "session": 6,
      "session_title": "Neural Networks and Deep Learning",
      "date": "2026-09-10",
      "slide_number": 14,
      "image": "https://<project>.supabase.co/storage/v1/object/sign/twin-content/slides/70445/s06/70445-s06-014.webp?token=...",
      "narration": "Sixty to ninety words in my teaching voice.",
      "audio": "/api/audio?t=<base64url narration>&v=<voice tag>&s=<hmac>",
      "voice": { "kind": "clone", "label": "AI voice made from my recordings." },
      "audio_fallback": "/api/audio?t=<base64url narration>&v=<free voice tag>&s=<hmac>",
      "voice_fallback": { "kind": "free", "label": "AI voice (a stock voice, not mine)." },
      "code": null,
      "clip": {
        "url": "https://<project>.supabase.co/storage/v1/object/sign/twin-content/clips/70445-s06-014.mp4?token=...",
        "start": 1394.2,
        "end": 1441.8
      }
    }
  ],
  "sources": [
    { "slide_id": "70445-s06-014", "course": "70445", "session": 6, "date": "2026-09-10", "slide_number": 14, "image": "<signed link>" }
  ],
  "follow_ups": ["What is backpropagation?", "Why do we need activation functions?"]
}
```

`audio` is `null` when the voice is set to captions only. *Added Oct 5 (voice tiers):* `voice` is the label of the voice behind `audio` (`kind` is `clone`, `stock`, `free`, or `unverified` when an ElevenLabs voice's category could not be checked, labeled just "AI voice."), and `null` with no audio. `audio_fallback` and `voice_fallback` are set only when an ElevenLabs voice speaks and the free fallback is on: if `audio` fails to play, the page switches the rest of the answer to `audio_fallback` and shows `voice_fallback.label`. When today's ElevenLabs cap is already spent and the fallback is on, `audio` is the free voice from the start and is labeled as one. For a suggested question it is a signed link to the stored mp3 instead of the audio route. `clip` is `null` when the slide has no clip. `clip.start` and `clip.end` are positions in the original class recording, used for the label; the clip file itself starts at zero. Signed links expire after an hour.

**Segment rules**

- Three to five segments per answer. More than five turns an answer into a lecture.
- Segments play in deck order, even if a later slide scored higher. Slides were written to be seen in sequence. *(Changed Oct 5: with two courses, "deck order" means course, then session, then slide number. "Next to each other" means the same course and session.)*
- Each narration is 60 to 90 words, which is about 25 to 40 seconds of speech.
- Pre-generated clips are static files named by a hash of their narration text, as in the example above. For a live answer, the audio field is a signed link to the audio route instead. *(Changed Oct 5: pre-generated audio is in the bucket under the voice id, served through a signed link.)*
- Added Oct 5. With the course filter set, only that course's records are scored. With "All courses", an answer can draw on both, and the sources list shows which.
- Added Oct 5. Records from sessions I have hidden in Settings are never scored. *Added Oct 8 (code review):* nor replayed from a stored suggested answer; a stored answer with no slide left to show (hidden, or gone from the index) is answered live instead.

**Database tables.** Added Oct 5. All in `supabase/schema.sql`, all reached only by the backend and the worker with the service role key.

| Table | Columns | Notes |
| --- | --- | --- |
| `settings` | `key`, `value` (jsonb), `updated_at` | Keys: `provider`, `model`, `voice_id`, `daily_voice_char_cap`, `student_passcode_hash`, `index_version`. Env vars are the defaults when a key is missing. *Added Oct 5 (voice tiers):* `voice_kind` (`{voice_id, kind}`, written by Settings after checking the voice's category on the ElevenLabs account), `voice_fallback` (`captions` or `free`), `voice_fallback_voice` (`edge:<ShortName>`), `daily_free_voice_char_cap`. *Added Oct 7 (prompt editor):* `prompt:<name>` = `{text, updated_at, note}` for each edited prompt (null or missing means the built-in default). The code reads it through the same 30 second cache as every other setting, so a saved prompt reaches every warm function within 30 seconds. Every saved version is also written to the private bucket at `prompts/history/<name>/<UTC>.json` = `{text, note, saved_at, hash, previous_hash, reset}` (`hash` and `previous_hash` are sha256 of this text and of the text it replaced), so any answer can be traced to the prompt that wrote it. *Added Oct 7 (answer thresholds):* `slide_threshold` and `info_threshold` (floats from 0.30 to 0.90; missing means the default applies: `NOT_COVERED_THRESHOLD` in `app/retrieval.py` for slides, then `INFO_THRESHOLD` or 0.55 for course info), and `threshold_history` (the last 20 changes, newest first: `{at, who, setting, old, new, source}`, where `who` is `admin`, `at` is UTC ISO time, and `old`/`new` are the effective values before and after). *Added Oct 7 (analytics):* `pricing` (the Analytics price table; defaults in `app/pricing.py` apply until it is saved) *Added Oct 8 (instructor alerts):* `alerts_enabled` (default true) and `alert_daily_cap` (0 to 50; missing means `ALERT_DAILY_CAP` or 10) *Added Oct 8 (beyond the slides):* `web_answers_enabled` (boolean, missing means on), `daily_web_answer_cap` (integer, missing means `DAILY_WEB_ANSWER_CAP` or 200), `web_answer_voice` (`edge:<ShortName>` to speak web answers in a free voice; missing or `none` means text only) |
| `counters` | `key`, `day`, `count`, `expires_at` | Rate limits per visitor id, login attempts, daily voice characters, daily question counts. Bumped only through the `ft_increment` function, which adds atomically and refuses an add that would pass a cap. *Added Oct 7 (analytics):* usage counters, kept 400 days, written by `app/usage.py`: `usage:<day>:<purpose>:<provider>:<model>:in\|out\|calls`, `embed:<day>:voyage:<model>:tokens\|calls`, `tts:<day>:<voice tier>:chars\|calls`, `event:<day>:<name>`, `faq:<day>:<entry id>`. Model ids may contain colons, so the metric is read from the right. Written off the request path (a small thread pool, finished after the response is sent); a failed write is logged and never fails a request |
| `question_log` | `id`, `at`, `question`, `course`, `covered`, `top_score`, `provider`, `model`, `latency_ms`, `kind` | Question text and scores only: no names, accounts, cookies, or IP addresses. *Added Oct 7:* `kind` is `course_content`, `logistics`, or null (not covered). Until the column is added, rows are written without it. *Changed Oct 7 (activity explainer):* `kind` is one of `course_content` (narrated slides), `stored_topic` (a suggested question's stored playlist), `faq`, `course_info` (answered from the Canvas index, added Oct 7, covered, with the provider and model and the best info chunk's score as `top_score`), `logistics`, `web` (added Oct 8: answered from a web search, covered, with the provider and model and the best slide score), or `not_covered`; null only on older rows. `provider` and `model` are null when no model was called (stored topic, FAQ, not covered, keyword-routed logistics). Until the column is added, `GET /api/admin/log` reads rows without it and infers the kind. *Added Oct 7 (analytics):* `top_slide_id` (best-scoring slide, or the first slide of a stored answer), `session`, `session_title`, `tokens_in`, `tokens_out` (every model call for this question added up), `voice_chars` (narration characters signed for the live voice), `source` (`chip`, `typed`, `follow_up` from the student page; `smoke`, `eval`, `prompt_test` for test traffic). Added by the migration block in `supabase/schema.sql`; until it runs, rows are written without these columns (then without `kind`), so logging never stops. Still no visitor id, cookie, or address |
| `courses` | `code`, `title`, `term` | Seeded with 70445 and 45884 |
| `sessions` | `id`, `course`, `session`, `date`, `title`, `visible` | `visible` false hides the session from students and from retrieval |
| `sources` | `id`, `course`, `session`, `kind`, `path`, `status`, `message`, `updated_at` | `kind` is `slides` (PDF or pptx), `transcript` (VTT), `video`, or `notebook`, matching the Settings form. `status` is `pending_upload` (link minted, file not confirmed), `uploaded`, `processing`, `ready`, or `error`; the worker only takes `uploaded`. `message` never contains a student name |

## Preprocessing pipeline

Added Oct 5. This is the part that did not exist when the app was one deck. Every stage runs on the local build machine (the computer that holds the private archive) with `uv run python -m indexer.<stage>`, takes `--course` and `--session` (or `--all`), reads from the private archive, writes to `~/Lecture Archive/_build/`, and skips work whose inputs have not changed. A stage that needs a key (only `build_index.py` for Voyage, and `upload.py` for Supabase) caches what it has done, so it can be started before the keys exist and finished the moment they arrive.

| Stage | Script | Reads | Writes | Needs a key |
| --- | --- | --- | --- | --- |
| 1. Slides | `indexer/slides.py` | `slides.pdf` (or `slides.pptx`, converted to PDF with LibreOffice), `slides.pptx` for notes | `slides/` | no |
| 2. De-identify | `indexer/deidentify.py` | `transcript_raw.vtt`, rosters, `slides.json` | `transcripts/`, scrubbed `slides.json`, `review/` | no |
| 3. Align | `indexer/align.py` | `video.mp4`, slide images, transcript | `align/` | no |
| 4. Clips | `indexer/clips.py` | `video.mp4`, alignment, transcript | `clips/` | no |
| 5. Index | `indexer/build_index.py` | everything above, notebooks, `indexer/code_map.json` | `content/` | Voyage |
| 6. Upload | `indexer/upload.py` | `_build/` | the bucket, `settings.index_version` | Supabase |

**1. Slide rendering.** `pdftoppm` renders each PDF page, which is converted to WebP at 1600 px wide. Text comes from the PDF page; the title is the pptx title placeholder when there is one, otherwise the first line. Speaker notes come from the pptx by position (pptx slide *i* is PDF page *i*). If the page counts differ, the stage skips notes for that session and says so, rather than attaching notes to the wrong slide. 45-884 session 10 has only a pptx, so LibreOffice renders its PDF first.

**2. Transcript de-identification.** Zoom labels every cue "Ben Collier" because students speak through the room microphone, so speaker labels are useless and the scrub works on the text.

- Parse the VTT into cues with start and end times in seconds.
- **Roster scrub.** Load the rosters from `~/Lecture Archive/_private/rosters/` into memory only. Build a list of first names, last names, preferred names, and full names, and replace every match in cue text with `[student]`. Names that are also ordinary English words are replaced only in name-like positions (start of a sentence followed by a comma, after "thanks", "yes", "go ahead", before "'s team") and every such case is flagged for review. The same scrub runs on slide text and speaker notes, since a slide can list a team.
- **Student turns.** Detected from content, because the label cannot tell. A cue is `student` when it falls inside a stretch that starts right after I hand the floor to someone (I say a student's name, "go ahead", "yes?", "what do you think?") and ends when I take it back ("great question", "so", "right, so"). Known student segments (the "AI in the News" and "AI Methods in the News" presentations, team presentations) are marked from a small per-session time-range file in `_build/overrides/`, which holds times only. Anything the rules are unsure of is `unclear`. Only `instructor` cues are ever used as narration material or clip audio; `student` and `unclear` are dropped.
- **Everyone else named, too.** Added Oct 5 (later the same evening). I asked for every person named in a transcript to be de-identified except me (Ben, Benjamin, Ben Collier, Professor or Dr. Collier). That includes public figures I talk about, such as researchers and executives. Students and anyone I address in class become `[student]`; everyone else becomes `[person]`. Company, product, and model names are not people and stay. Slide text is not changed by this rule, so a name printed on a slide still shows on the slide. Detection uses capitalized-name patterns and a name list in addition to the roster, and the review file counts `[person]` replacements.
- **Keep it PG.** Added Oct 5. I asked for any cursing to be gently smoothed over with a simple word, since there may be some in class and I want this PG. After de-identification, `indexer/pg_filter.py` swaps each curse word for a mild one that keeps the sentence grammatical and keeps the capitalization: damn becomes darn, hell (only as an expletive: "what the hell", "hell of a") becomes heck, shit becomes stuff, the f-word becomes heck, freaking, or messed up as fits, ass becomes butt, bitch becomes pain, pissed becomes annoyed, bastard becomes jerk, and the Lord's name used as an exclamation ("Oh my God.", "Jesus, that's a lot") becomes goodness. Slurs become `[removed]`. "Crap" stays. Matching is on whole words, so "assess", "class", "Dickens", "Hello", or "Scunthorpe" never change, and legitimate uses stay ("by God's grace", a reading about religion, "Hell's Kitchen"). Asterisked forms (f\*\*\*, s\*\*t) and ASR spellings are handled. It runs on transcript cues and the Drive transcript (a changed cue carries `"pg": true`), and on slide text, titles, speaker notes, and OCR text (the slide gets the flag `pg_language`; the slide image itself is not changed). The review file lists how many of each swap were made, counts only. The word list lives in the code, since it holds nothing private.
- **Leak check.** Before anything is uploaded, every text output (`slides.json`, transcripts, alignment, the index, the clip manifest) is re-scanned against the full roster. One hit fails the stage. This automated check is the gate.
- Added Oct 5 (leak-check review). A roster word can also be a product, a place, or an ordinary word ("Duolingo Max", "IBM Watson", "Austin, Texas"). When a person has looked at such a hit and decided it is not a name, it goes in a private allowlist, `~/Lecture Archive/_private/leak_allowlist.json`, as (record id, field, token, reason). An entry allows only that one-word hit in that one record and field; it never allows a full name, an Andrew ID, or an email, and a changed record id makes the hit fail again. The allowlist holds roster words, so it is never committed. Anything that is a person's name is masked instead, through the de-identification overrides.
- Added Oct 5. The slide stage's text cache is keyed on the roster files too (names, sizes, times), so adding a roster re-runs the name scrub on every deck. Before this, a roster added after extraction left a full student name on a slide.
- Added Oct 5 (late). Roster files do not share a header: the course roster has `Last Name`, `Preferred/First Name`, `Andrew ID`, `Email`; a Canvas group export has `name` ("Last, First") and `login_id` (an email); a gradebook export has `Student` and `SIS Login ID`. One loader, `indexer/roster.py`, reads them all (case, spaces, and underscores ignored; a full-name column is split on its comma, or on the last word) and de-identification, the slide scrub, and the leak check all use it. Canvas placeholder rows ("Points Possible", "Test Student") and numeric SIS ids are not read. Before this, a roster with other headers was skipped without a word.
- **Review.** `review/<course>-s<NN>.txt` lists the number of replacements, the flagged ambiguous cases, the student-turn stretches, and capitalized words that are not in the course vocabulary (possible names the roster cannot know, such as a nickname or a guest). I skim it per session. The review file stays in `_build/review/`, is never printed to the terminal, logged, or uploaded, and nothing in the pipeline ever prints a roster name.

**3. Slide-to-transcript alignment.** For each slide, find the time windows when it was on screen and what I said then.

- **Frame matching first.** Where the recording is a screen share, sample a frame every 2 seconds with ffmpeg, downscale it, and compare it with each slide image of that session (normalized image correlation on grayscale thumbnails; in presenter view, compare the largest slide region, not the next-slide thumbnail). A frame matches a slide when the best score clears a threshold and beats the runner-up by a margin. Runs shorter than 4 seconds are smoothed away. Frames that show something else (a black Zoom tile with only my name card, a Canvas page, a terminal, an embedded third-party video) match nothing.
- **Text similarity as the fallback.** For stretches with no frame match, split the transcript into 30-second chunks and score each against each slide's text and notes (TF-IDF cosine, no key needed). Assign chunks with an in-order constraint: a dynamic program over chunks where the slide number never moves backward, except for a short jump back of one or two slides, which is how I actually teach.
- Each window records which method produced it. The `transcript` for a slide is the de-identified instructor speech inside its windows.

> **Added Oct 5 (Block 2 build).** How `indexer/align.py` does this, after calibration on two sessions:
>
> - Frames are sampled every 2 s at 256 px wide in grayscale and cached in `_build/.cache/align/`, so a re-run does not decode the video again. Comparison is normalized correlation on 64x36 thumbnails. Each frame is tried three ways: the whole frame, the best of up to three bright slide-shaped regions (presenter view, or a slide window next to another display), and the session's usual slide box (for dark slides).
> - Thresholds: best score at least 0.45 and at least 0.04 above the best runner-up that is not a look-alike; below 0.60 the margin must be 0.15. Slides that correlate 0.93 or more with each other (repeated section dividers) count as one group, and the member nearest the previous match wins. A one-frame dropout inside a run is bridged; runs under 4 s are dropped.
> - The text fallback keeps a chunk only when its TF-IDF cosine is at least 0.12, and the dynamic program for each uncovered stretch is bounded by the frame matches on either side (two slides of slack).
> - A recording in two parts (`video.mp4`, `video_part2.mp4`) is one timeline: part 2 starts at part 1's duration, the same rule the transcript stage uses.
> - `align/<course>/s<NN>.meta.json` sits next to each alignment file: coverage, thresholds, and every frame run with its scores, a motion measure, and the times of frames that matched only through bridging. Local only, like the alignment.

**4. Clip cutting rules.** Clips auto-publish behind the passcode, so the rules are strict and a clip is skipped whenever one fails.

- Only frame-matched windows, so the video shows that slide. Text-only alignments never become clips.
- 15 to 90 seconds. Adjacent windows of the same slide merge when the gap is under 5 seconds. Cuts land on cue boundaries; a window over 90 seconds is cut at the last cue boundary before 90.
- Instructor only: every cue overlapping the window, plus 5 seconds on each side, must be `instructor`. Any `student` or `unclear` cue disqualifies it.
- No names in the audio or text: no `[student]` or `[person]` token in the padded window (audio cannot be de-identified), and the slide's own text and notes have no roster hit.
- Added Oct 5. Kept PG: no cue marked `"pg": true` in the padded window. The text was smoothed, but the audio still has the original word.
- No student names on screen: frame matching already guarantees the screen shows my slide, not a Canvas page or a participant list. A slide whose text had a roster hit gets no clip.
- No embedded third-party video playing in the window (the frame match drops while it plays, and the audio is not me).
- Excluded entirely: the student "AI in the News" and "AI Methods in the News" presentations, any student presentation, and the Tesla vs Waymo case sessions (45-884 session 11, and session 12 when it happens). I am keeping that case private for now; its slides are still indexed.
- Encode with ffmpeg: H.264 at 720p, AAC audio, `+faststart`, sized for mostly-static slides (target 3 MB or less per clip).
- `clips/manifest.json` records each kept clip and why (`reason_kept`, for example "frame-matched 42 s, instructor only, no names"). To pull a clip, I add its slide id to the session's override file and re-run the stage.

> **Added Oct 5 (Block 7 build).** Details of `indexer/clips.py`:
>
> - Stricter than the 5-second merge above: every sampled frame inside a clip must match the slide on its own. Runs are split around frames that matched only through bridging, so a 2-second flash of a browser or Canvas page can never sit inside a clip. The 5-second merge is used only when alignment has no frame-level detail.
> - When a whole window fails the speech rules, the longest stretch inside it that passes every rule (whole cues, 15 to 90 seconds, 5 seconds clear of any student, unclear, or masked cue) is used instead. One clip per slide: the longest passing candidate.
> - Slides flagged `student_names_possible`, `in_the_news`, `student_presentation_possible`, or `no_clips_private_case` by the slide stage get no clip, and neither does a slide whose stored text or notes carry a `[student]` mask.
> - PG rule (added Oct 5, later): a window is rejected (`pg_language`) when any cue in it, padding included, is marked `pg: true` by the PG filter, or its text matches the basic profanity list in `indexer/clips.py` (the fallback until every transcript carries the flag). The audio cannot be cleaned.
> - An embedded video is detected from the frames: if more than 35% of consecutive frame pairs in the window change, the window is skipped.
> - Encoding: H.264 CRF 26 with `-tune stillimage`, 720p, AAC 96 kbps mono, `+faststart`; a clip over 3 MB is re-encoded at CRF 30. Only the video and audio streams are kept: Zoom recordings carry an embedded caption text track (raw captions, not de-identified) and metadata, and both are dropped. A clip that would cross from `video.mp4` into `video_part2.mp4` is skipped.
> - The override file is `_build/overrides/<course>/s<NN>.json` with `{"no_clips": ["<slide_id>", ...]}`; a `no_clips` key in the de-identification override file for the session works too.
> - `clips/manifest.json` stays a list. Rejections go to `clips/rejected.json`: per-window reasons (slide ids, times, and reason codes only), counts per reason, and the count of slides left without a clip by reason. It is a local report: upload only the `.mp4` files and `manifest.json`.

**5. Build the index.** Combine `slides.json`, alignment, the clip manifest, notebook code cells (nbformat; code plus the markdown cell above it; outputs dropped), and `indexer/code_map.json` into records. Embed each record's text with Voyage (document input type, batches of 128). Embeddings are cached by a hash of the text in `_build/cache/`, so a re-run only embeds records that changed. Write `content/index.json` and `content/embeddings.npy`, then run the leak check on both.

**6. Upload.** Mirror slide images, clips, the manifest, the index, topics, and audio to `twin-content`, skipping files that have not changed. Then set `settings.index_version` to a hash of the new index. Warm backends pick it up within a minute.

> **Changed Oct 5 (stages 5 and 6 as built).** The index and upload stages always work on the whole build (no `--course`/`--session`), because there is one index. Details that differ from the text above:
>
> - The embedding text is title, slide text, notes, OCR text of image-only slides, then the transcript passage, each truncated (14,000 characters in all). Code cells embed title, the markdown above, and the source. Records also carry `ocr_text`, `thumb`, `flags`, and `hash` (sha256 of the embedding text); code records carry `notebook`, `cell_number`, and `mark_lines`. Slides flagged `student_names_possible` are left out, and so are their images.
> - The embedding cache is `_build/content/embed_cache/<model>/<sha256>.npy`, keyed by model, input type, and text.
> - `index_version` is `<UTC timestamp>-<first 8 hex of the content hash>`, kept unchanged when a re-run produces the same records, so nothing re-uploads. `content/manifest.json` records the version, the sha256 of every source file that went in, and counts per course and session; a copy of each version's manifest stays in `_build/content/versions/` so any answer can be traced back to its inputs.
> - Without `VOYAGE_API_KEY`, `build_index.py` writes `index.json` and the manifest, removes any stale `embeddings.npy`, and exits 3; the same command finishes once the key is in `.env`.
> - The upload is an allowlist built from the index (index, embeddings, manifest, each indexed slide's image and thumbnail, its clip, a clip manifest cut to those clips, topics, and the mp3s topics point to), with a denylist on top (transcripts, alignment, review, code JSON, `slides.json`, `deck.json`, PDFs, anything under `_private`). `content/upload_state.json` in the bucket holds each object's sha256 so unchanged objects are skipped, and objects that left the index are deleted.
> - The leak check has two levels: full roster names, Andrew IDs, and emails are a hit anywhere; a single roster first name or surname (capitalized, not an ordinary English word) is a hit only in transcript passages, because slide text is deliberately not altered and a surname alone often belongs to a cited author. It reports where, never what. Without the rosters the upload refuses to run. *(Added Oct 5, late: a possessive ("Name's") counts as the name; before, it slipped past the single-name check.)*
> - The worker runs the per-session stages for each claimed upload (a notebook re-runs `slides.py`, which extracts notebooks), then `build_index` and `upload` once per poll. A stage whose script is not merged yet fails the row with a clear message; the file is already in the archive, so "Re-run" works later.

**When a new class happens.** Two ways in.

- **On the local build machine:** create the session folder in the archive (`<NN> <date> <title>`), drop in `slides.pdf`, `transcript_raw.vtt`, `video.mp4`, and any notebooks, add the session in Settings (or in the `sessions` table), then run `uv run python -m indexer.run --course 70445 --session 12`, which runs stages 1 to 6 for that session and rebuilds the index. If the Zoom VTT is not in Drive, I download it from the Zoom cloud recording in Chrome.
- **From Settings:** add the session and upload the files. Each lands in `inbox/` with a `sources` row marked `uploaded`. `indexer/worker.py`, running on the local build machine, polls `sources` every 30 seconds, marks a row `processing`, copies the file into the archive folder for that session, runs the stages it affects (a new VTT re-runs de-identify, align, clips, index, upload; a new deck re-runs everything), and marks the row `ready` or `error` with a message. If the worker is not running, rows stay `uploaded` and Settings says "Waiting for the worker." "Re-run" sets a row back to `uploaded`.

A revised deck for an existing session works the same way: replace `slides.pdf` and re-run that session.

## API endpoints

Four routes. The frontend never talks to a model or voice provider directly.

| Route | Input | Returns | Notes |
| --- | --- | --- | --- |
| `GET /api/health` | none | `{"ok": true}` | The frontend calls this on page load to warm the function and to show a clear message if it is down |
| `GET /api/topics` | none | The list of suggested questions | Each one maps to a pre-generated playlist whose audio is already in `public/audio/` |
| `POST /api/ask` | `{"question": "..."}`, 300 characters max | A playlist (see Data) | Retrieval, then one LLM call that writes all segment narrations as JSON. No speech is generated here |
| `GET /api/audio` | the narration text and a signature, both taken from a segment's audio link | An mp3, streamed | Checks the signature, checks the daily cap, calls the voice service, streams the result. Nothing is saved on the server |

> **Changed Oct 5: the routes are now these.** Login, courses, and the Settings API are new; `ask` takes a course filter. Everything except `/api/health` and `/api/login` returns 401 without a valid `ft_session` cookie, and every `/api/admin/*` route except its login returns 401 without `ft_admin`. The frontend still never talks to a model or voice provider directly.

**Student routes**

| Route | Input | Returns | Notes |
| --- | --- | --- | --- |
| `GET /api/health` | none | `{"ok": true}` | No cookie needed. Warms the function on page load |
| `POST /api/login` | `{"passcode": "..."}` | 204 and the `ft_session` cookie, or 401 | Constant-time compare against the current student passcode. Rate-limited per address (salted daily hash): 10 tries per 15 minutes, then 429 |
| `GET /api/courses` | none | `[{course, title, sessions: [{session, date, title}]}]` | Visible sessions only |
| `GET /api/topics` | none | `[{question, course}]` | Read from `topics/topics.json`. Asking a topic's exact question returns its stored playlist, with links signed on the way out |
| `POST /api/ask` | `{"question": "...", "course": "70445" \| "45884" \| null}`, question 300 characters max | A playlist (see Data) | Retrieval, then one LLM call that writes all segment narrations as JSON. No speech is generated here. *Added Oct 7 (analytics):* optional `source` (`chip`, `typed`, `follow_up`; anything else is ignored) and an optional `X-FT-Source: smoke\|eval\|prompt_test` header, which only tags the question-log row and its spend purpose as test traffic and changes nothing else |
| `GET /api/audio?t=&v=&s=` | the narration text (base64url), a short tag of the voice id, and their signature | An mp3, streamed | The voice tag makes every link change when the voice changes, so the browser never replays the old voice; a link from an old voice gets 403. 404 when the voice is set to captions only. 403 on a bad signature, 429 past the daily cap (the frontend falls back to captions). Calls ElevenLabs with the current voice. Nothing is saved on the server. *Changed Oct 5 (voice tiers):* the tag picks the voice: the current voice, or the free fallback voice when the fallback is on. ElevenLabs voices stream from ElevenLabs and spend the ElevenLabs cap; free voices stream from `edge-tts` (MP3 in memory, no temp files; pieces of at most 400 characters, at most 6 at once, each retried) and spend the free cap. A free voice that cannot start answers 502 before any audio |
| `GET /api/voice` | none | `{kind, label, fallback: {kind, label} \| null}` | Added Oct 5. What students are told about the voice right now: `kind` is `clone`, `stock`, `free`, `unverified`, or `none` (captions only, `label` null) |
| `POST /api/event` | `{"name": "..."}`, one of `chip_tap`, `question_typed`, `segment_played` (once per answer, when its first segment starts), `walkthrough_completed`, `clip_played`, `audio_failed`, `follow_up_tapped`, `course_filter_changed` | 204 | Added Oct 7 (analytics). Student cookie. Allowlisted names only: no free text, no ids; other fields are dropped. 60 per minute and 2,000 per day per visitor, then 429. Bumps `event:<day>:<name>`. The page sends these with `fetch(..., {keepalive: true})` and never waits on them |
| `POST /api/links` | `{"slide_ids": [...]}`, at most 10 | `{links: {<slide_id>: {image, clip: {url, start, end} \| null}}}` | *Added Oct 8.* Fresh signed links (1 hour) for slides of the answer on screen, after the old ones expired. Slides that are not in the index or are in a hidden session are left out. No model call, no question-log row. 10 per minute per visitor (429) |

**Settings routes** (all need `ft_admin` except login)

| Route | Input | Returns | Notes |
| --- | --- | --- | --- |
| `POST /api/admin/login` | `{"passcode": "..."}` | 204 and the `ft_admin` cookie, or 401 | Against `ADMIN_PASSCODE`. 5 tries per 15 minutes |
| `GET /api/admin/settings` | none | `{provider, model, voice_id, daily_voice_char_cap, ...}` | Never returns a key or a passcode hash. `voice_id` is null when the server default (`ELEVENLABS_VOICE_ID`) applies, `"none"` for captions only. *Added Oct 5:* also `voice_kind`, `voice_label` (what students see), `voice_costs_money`, `voice_fallback`, `voice_fallback_voice`, `daily_free_voice_char_cap` |
| `PUT /api/admin/settings` | any of `{provider, model, voice_id, daily_voice_char_cap, student_passcode}` | the saved settings | A new student passcode is stored as a hash and bumps the passcode version, signing students out. *Added Oct 5:* also `voice_fallback`, `voice_fallback_voice`, `daily_free_voice_char_cap`. `voice_id` is `eleven:<id>` (checked against the account, which records whether it is my clone), `edge:<ShortName>` (checked against Microsoft's list), `"none"`, or null; an older bare ElevenLabs id is still accepted |
| `GET /api/admin/models?provider=anthropic\|openai\|openrouter` | provider | `{provider, models: [{id, name}], source}` | Curated lists for Anthropic and OpenAI; OpenRouter's live list from `https://openrouter.ai/api/v1/models` |
| `POST /api/admin/test` | `{"question": "...", "provider"?, "model"?}` | a playlist plus latency | Runs the full ask pipeline with the given or saved model; not logged, but counted against limits |
| `GET /api/admin/status` | none | which keys are configured (booleans only), today's counters, index version and record count | |
| `GET /api/admin/voices` | none | `{voices: [{voice_id, name, category, preview_url}]}` | Proxied from ElevenLabs with the server-side key. *Changed Oct 5 (voice tiers):* returns `{groups: [{id: "clone" \| "elevenlabs" \| "free", label, cost, costs_money, student_label, voices: [...]}], voices: [all], elevenlabs_error}`. Without an ElevenLabs key it still returns the free group |
| `GET /api/admin/voice-preview?voice=edge:<ShortName>` | a free voice id | an mp3 | Added Oct 5. Speaks one fixed sentence set on the server (never text from the caller), cached in memory, counted against the free cap. 400 for ElevenLabs voices (they use `preview_url`) and unknown names |
| `GET /api/admin/courses` | none | `{courses: [...]}`, each with sessions and what exists for each (`has: {slides, transcript, video, clips, indexed}`) | Merges the `courses`, `sessions`, and `sources` tables with what the loaded index holds |
| `POST /api/admin/courses` | `{course, title, term}` (`code` also accepted) | the course | |
| `POST /api/admin/sessions` | `{course, session, date, title}` | the session | Starts visible |
| `PATCH /api/admin/sessions/{id}` | any of `{date, title, visible}` | the session | Hiding takes effect on the next question |
| `POST /api/admin/uploads` | `{course, session, kind, filename, size}` | `{upload_url, method, headers, path, source_id}` | Mints a signed upload URL for `inbox/<course>/s<NN>/<kind>/<filename>` and creates a `sources` row with status `pending_upload`. When the PUT finishes, the page calls `rerun` (or `complete`, which first checks the file is in the bucket) to mark it `uploaded`; listing sources also promotes a `pending_upload` row whose file has arrived. Rejects unknown kinds and files over the plan's limit. The browser uploads straight to Supabase |
| `GET /api/admin/sources` | optional `course`, `session` | `{sources: [...]}` | Settings polls this to show status |
| `POST /api/admin/sources/{id}/complete` | none | the row, status `uploaded` | 409 if the file is not in the bucket yet |
| `POST /api/admin/sources/{id}/rerun` | none | the row, status `uploaded` | The worker picks it up again |
| `GET /api/admin/log` | none | `{rows: [...]}`, the last 50 question-log rows | *Changed Oct 7:* each row also has `kind` (recorded, or inferred for older rows) and `kind_inferred`; rows whose kind never calls a model show `provider` and `model` as null. Works before and after the `kind` column exists |
| `GET /api/admin/prompts` | none | `{prompts: [{name, title, description, used_by, testable, variables, required, current, default, is_overridden, updated_at, note}], max_chars}` | Added Oct 7 (prompt editor). `current` is what the code uses now |
| `GET /api/admin/prompts/{name}/history` | none | `{versions: [{version, saved_at, note, text, hash, previous_hash, reset}]}` | Newest first, the latest 30 |
| `PUT /api/admin/prompts/{name}` | `{text, note}` | the prompt, as in the list | 400 when the text is empty, over 12,000 characters, misses a required placeholder or required word, or uses an unknown placeholder. Saves `settings.prompt:<name>` and writes a history file |
| `POST /api/admin/prompts/{name}/reset` | `{note?}` | the prompt | Back to the built-in default (the settings row becomes null); also recorded in history |
| `POST /api/admin/prompts/{name}/restore` | `{version, note?}` | the prompt | Saves that version's text again as a new version |
| `POST /api/admin/prompts/{name}/test` | `{text, question, course?}` | `{ok, latency_ms, output, errors}` | Runs the real path once with the draft text, unsaved, for this request only. App prompts only (400 for the eval prompts). Counts against the per-visitor limits and the daily model-call cap; not logged |
| `GET /api/admin/thresholds` | none | `{slide: {value, default, source, override}, info: {...}, min, max, history}` | Added Oct 7. `source` is `code` (my value in `app/retrieval.py`, or the built-in 0.55 for course info), `env` (`INFO_THRESHOLD`), or `settings` (an override). `history` is the last 20 changes, newest first |
| `PUT /api/admin/thresholds` | any of `{slide_threshold, info_threshold}`: a number from 0.30 to 0.90, or null to reset to the default | the same as GET | Added Oct 7. 400 outside the range or with nothing to change. Values are rounded to 3 decimals. Each real change adds `{at, who: "admin", setting, old, new, source}` to `threshold_history`. Cross-site requests get 403 like every other write |
| `GET /api/admin/analytics?days=7\|30\|90&include_tests=false` | none | everything the Analytics section draws: `{range, test_traffic, kpis, spend, llm, purposes, embeddings, tts, questions_by_day, questions_by_hour, topics, funnel, events, models, log_columns_ready}` | Added Oct 7. Reads `question_log` (falls back when the analytics columns are missing), the usage counters, the price table, and the eval runs. Never returns a visitor id, address, or counter key |
| `GET /api/admin/analytics/pricing`, `PUT` the same `{pricing}` or `{reset: true}` | the price table | `{pricing, defaults, plans}` | Added Oct 7. Validated: known providers, model ids that look right, prices 0 to 1,000 |
| `GET /api/admin/analytics/topics`, `POST` the same `{limit: 10..300}` | none, or the question count | `{runs: [...]}`, or the new run `{id, at, questions, provider, model, latency_ms, themes: [{label, count, examples}]}` | Added Oct 7. "Label topics": see the Settings page section. 400 under 5 questions, 429 past 10 runs a day |
| `GET /api/admin/analytics/export.csv?days=` | none | the question log as CSV | Added Oct 7. Includes a `test` column |

**Settings > Student alerts routes** (added Oct 8; all need `ft_admin`; POST/PUT pass the same-origin check; `app/admin_alerts.py`)

| Route | Input | Returns | Notes |
| --- | --- | --- | --- |
| `GET /api/admin/alerts` | none | `{enabled, daily_cap, sent_today, tests_today, configured: {TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM, ALERT_TO_PHONE}, ready, to_masked, from_kind, alerts: [...]}` | Booleans for the variables, the destination as `***-***-1234`, never a token or a full number. Alerts newest first (50), each with its repeat count |
| `PUT /api/admin/alerts` | any of `{enabled, daily_cap}` | the same as GET | `daily_cap` 0 to 50 |
| `POST /api/admin/alerts/test` | none | `{status, alert}` | Sends a fixed test text. 400 when Twilio is not configured; 429 past 5 tests a day or the daily cap; 502 with Twilio's error code when the send fails (stored either way) |

**Settings > Evals routes** (added Oct 7; all need `ft_admin`, and every POST/PUT passes the same-origin check like every other state-changing route; `app/admin_evals.py`)

| Route | Input | Returns | Notes |
| --- | --- | --- | --- |
| `GET /api/admin/evals/questions` | none | `{count, answerable, categories: [{category, count}], questions: [...], uploaded, privacy}` | Question text for the admin view only. 409 if the uploaded file fails the privacy checks |
| `POST /api/admin/evals/questions` | `{question, category, answerable, reference_answer?, course?, month?}` | the question set | Adds one question after the `evals/dataset.py` checks (400 names the problem, never the text) |
| `PUT /api/admin/evals/questions/{qid}` | same | the question set | Edits one question in place (it keeps its id); same checks |
| `POST /api/admin/evals/runs/estimate` | the run body below | `{questions, estimate: {pairs, calls_typical, calls_max, embeddings, run_call_cap, daily_eval_cap, eval_calls_today, cost_usd, cost_unknown_for, per_model, within_cap}, self_grading}` | Calls no model. Rough cost uses OpenRouter's published price for the same model |
| `POST /api/admin/evals/runs` | `{name, generators: [{provider, model}], judges: [{provider, model}], top, categories?, confirm: true}` | 201 `{run, progress}` | Guards: 1 to 3 answering models, 1 to 3 judges, 1 to 30 questions, keys configured, model ids checked, OpenRouter models over the price ceiling refused (the Model section's check), Jev refused, `confirm` required, the most calls the run could make within `EVAL_MAX_CALLS_PER_RUN`, and one active run at a time (409) |
| `POST /api/admin/evals/runs/{id}/step` | none | `{progress: {done, total, fraction, status, finished, calls_used, call_cap}, row}` or `{progress, waiting: {seconds, reason}}` | Does ONE question x ONE answering model: the real `answer()` path with that model through a per-request override (a context variable in `app/llm.py`; the saved model and student requests are untouched), then every judge in parallel, within about 50 s. Idempotent: the next pair is the first with no result, a pair is written once even if two steps race (a 70 s lease), and a finished run returns its progress without calling anything. A busy embedding service returns `waiting` and records nothing. 429 when a cap would be passed |
| `POST /api/admin/evals/runs/{id}/cancel` | none | `{progress}` | Keeps what was judged |
| `GET /api/admin/evals/runs` | none | `{runs: [index entries], active}` | No question text |
| `GET /api/admin/evals/runs/{id}` | none | `{run, progress, rows, legend}` | Rows without the slide material sent to the judges |
| `GET /api/admin/evals/report-card` | none | `{series: [{generator, label, points: [{run_id, at, judges, notes, self_grading, questions, judgements, score_n, pass_rate, scores, decline_accuracy, fallback_rate, judge_agreement}]}], calibration, legend}` | Oldest first; excluded runs left out |
| `GET /api/admin/evals/calibration` | none | `{cases: [{cid, about}], judges: {...}}` | |
| `POST /api/admin/evals/calibration/step` | `{provider, model, restart?, attempt?}` | `{judge, attempt, result: {met, cases, missed, rows, done}}` | Scores the next calibration case with that judge (the page sends back `attempt` each time); counts against the eval caps |
| `GET /api/admin/evals/limits` | none | caps, today's counts, the live model, which keys are set (booleans) | |

**Inside `/api/ask`, step by step**

> **Added Oct 8 (instructor alerts).** Step 2b, first in `answer()`, before the stored topic and the FAQ: a keyword pre-check for a broken quiz, a broken submission, or an API key out of credits, then one small classifier call (`incident_classifier`). An incident returns the FAQ-style card with `kind: "alert"` and stops; anything else continues unchanged. See "Instructor alerts".

> **Added Oct 7 (course FAQ).** Between step 4 (stored suggested questions) and embedding, check Ben's course FAQ (`app/faq.py`, `app/faq_entries.json`): keyword patterns per entry, first match wins, course-aware. A hit returns `{"kind": "faq", "covered": false, "title", "message", "answers", "links", "contacts", "segments": []}`: Ben's written answer word for word (from his 70-445 and 45-884 FAQ docs, plus his Oct 7 answers on Calendly meetings and TA-handled presentation rescheduling), link buttons (Calendly), and TA contact cards when the answer points to the TA. No embedding, no model call, no audio. TA names and emails come only from the `TA_CONTACTS` environment variable (JSON per course code) and appear only on a contact card the student opens; the twin never says a TA's name. The logistics referral (step 7a) also carries the Calendly button and the TA cards for the course filter.


1. Reject empty or over-length questions with a 400 and a readable message.
2. Check the rate limit for this visitor.
3. If the question matches a suggested question, return its stored playlist and stop.
4. Embed the question.
5. Score every slide by cosine similarity. If the top score is under the threshold, return `covered: false`.
6. Pick the segments (see Code Ben writes by hand).
7. Send the chosen slides' text and code to the LLM with the grounding prompt. Ask for JSON only.
8. Validate the JSON: every `slide_id` must be one that was sent, every narration under 110 words. If validation fails, retry once, then fall back to using each slide's speaker notes as the narration.
9. Sign each narration with a secret key held in an environment variable, build the audio links from the text and signature, and return the playlist.

> **Changed Oct 5.** The steps are now:
>
> 1. Check the `ft_session` cookie (401 if missing, bad, expired, or from an old passcode).
> 2. Reject empty or over-length questions with a 400 and a readable message.
> 3. Check the rate limit for this visitor.
> 4. If the question matches a suggested question (and its course fits the filter), return its stored playlist with fresh signed links and stop.
> 5. Embed the question with Voyage (query input type).
> 6. Keep only rows for the chosen course and visible sessions, then score them by cosine similarity. If the top score is under the threshold, return `covered: false`.
> 6a. *Added Oct 7 (course info).* Order: FAQ (3a), then course info, then slides, then logistics and narration. When the course-info index is loaded (`content/info_index.json` + `content/info_embeddings.npy`, same bucket, reloaded with the slide index on `index_version`), the question vector from step 5 (embedded once) is also scored against the info chunks for the course filter, with the same `rank()`. If the best info chunk scores at least `INFO_THRESHOLD` (env, default 0.55) and higher than the best slide, take the top 3 chunks (with "All courses", only those from the top chunk's course or with no course, *added Oct 8* so one answer never mixes the two courses' policies, dates, or links) and make one call through `app/llm.py` (the active provider and model) with a strict grounding prompt: answer in my first-person voice, at most 120 words, only from the chunks, no names, PG, never reveal an access code; if the chunks do not answer it, reply exactly "Here's where that is on Canvas." and rely on the links. Validate the reply (JSON `{"answer": "..."}`, at most 120 words, no web addresses, no `[student]`, no access-code-like tokens, its content words grounded in the chunks, no long echo of the question). An answer over 120 words keeps its leading whole sentences that fit (*changed Oct 8*: Opus 5.5 wrote 122 to 126 word answers, and each fell back). If the call or the checks fail, use the first sentences of the top chunk instead (skipping any sentence that fails the same checks). *Changed Oct 8 (live O'Reilly bug):* the fallback starts at the chunk's first real sentence (no item title, no Module/Class/Due/Points/Closes/Posted header lines), says the due date as a sentence when the item has one, and ends each heading line with a period. The question log records why it fell back in `fallback_reason` (`provider_credits`, `provider_auth`, `provider_rate_limit`, `provider_unreachable`, `provider_refused`, `provider_error`, `daily_cap`, `not_json`, `no_answer`, `too_long`, `not_grounded`, `unsafe_text`, `error`), shown as a badge in Settings > Activity. Return the course-info reply above (no audio), log kind `course_info` with the provider, model and top info score, and stop: no logistics check, no narration. Due dates go to the model already converted to Pittsburgh time. Missing or mismatched info files (or an info index whose embedding size differs) turn this step off and leave everything else unchanged. Code: `app/course_info.py`.
> 7. Pick the segments (see Code Ben writes by hand). If none are picked, return `covered: false`.
> 7a. *Added Oct 7 (logistics check).* Classify the question as `course_content` or `logistics`. The Oct 7 real-question eval found meeting, missed-class, reschedule, grade and Canvas messages scoring 0.54 to 0.55, just above the 0.52 threshold, so they were getting narrated slides. First a keyword pre-check (`app/logistics.py`: office hours, meet with you, zoom call, missed class, absent, sick, extension, extend the deadline, regrade, my grade, grading, reschedule, swap presentation, on Canvas, can't access, my team member, drop the class, and similar) routes the obvious cases with no model call. Phrases are kept narrow so concept questions ("how do I choose k", "what is overfitting") never match. Anything else goes to one small call through `app/llm.py` (the active provider and model, `max_tokens` 200) that must reply with JSON only: `{"kind": "course_content" | "logistics", "reason": "..."}`. For `logistics`, return the referral above and stop: no narration call, no audio. If the call fails or the reply is not that shape, treat the question as course content and carry on: this check never blocks a real answer. Suggested questions skip this step (they are course content by construction).
> 7b. *Added Oct 8 (beyond the slides, team brief UPDATE 11).* Order: FAQ (3a), Canvas course info (6a), slides (covered: 7a then 8), then this step, which runs only when step 6 or 7 would return `covered: false`. When web answers are on (Settings, default on), a classifier sorts the question into `course_adjacent` (AI, ML, data, statistics, programming, agents, automation and coding tools, such as "How do I set up n8n?" or "How do agent frameworks like LangGraph work?"), `off_topic` (sports, news, recipes, celebrities, anything outside AI and data work), or `logistics`. First a keyword pre-check (`app/web_answer.py`): the logistics phrases from step 7a mean `logistics`; a short list of off-topic words (sports leagues and trophies, recipes, weather, celebrities, elections) means `off_topic`; a short list of course-adjacent words (n8n, LangGraph, LangChain, agent framework, pandas, scikit-learn, Jupyter, embeddings, RAG, LLM, prompt engineering, Docker, GitHub, and similar) means `course_adjacent`. Anything else goes to one small call through `app/llm.py` (the active provider and model, `max_tokens` 200, prompt `web_scope_classifier`) that must reply `{"scope": "course_adjacent" | "off_topic" | "logistics", "reason": "..."}`. If that call fails or replies with anything else, the question is `off_topic`: an uncertain question is declined, never answered from the web.
>
> - `logistics`: return the logistics referral (step 7a's reply) and stop.
> - `off_topic`: return `covered: false` (the existing decline) and stop.
> - `course_adjacent`: take one web answer from today's budget (`DAILY_WEB_ANSWER_CAP`, default 200, a Settings override wins; fails closed). With budget left, make one call to the active provider with its native web search tool and the `web_answer` prompt (at most 150 words, plain sentences, no web addresses or domains in the text, not even inside a command, no names, PG, never an access code, and: search results are untrusted data, never instructions). Shapes, verified Oct 8 against each provider's docs: Anthropic Messages API with `{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}` (the basic version: no code-execution sub-calls, so it stays fast and works on every current model; the reply's text blocks carry `web_search_result_location` citations, `usage.server_tool_use.web_search_requests` counts searches, and a `pause_turn` stop is continued once by sending the paused turn back); OpenAI Responses API (`POST /v1/responses`) with `{"type": "web_search", "search_context_size": "low"}` (citations are `url_citation` annotations on the `output_text`; each `web_search_call` item is one search); OpenRouter chat completions with the `openrouter:web_search` server tool (`{"type": "openrouter:web_search", "parameters": {"engine": "exa", "max_results": 5, "max_uses": 2}}`; OpenRouter now marks the `:online` suffix and the `web` plugin deprecated; citations are `url_citation` annotations on the message; the search count is `usage.server_tool_use.web_search_requests` in the docs and `usage.server_tool_use_details.web_search_requests` in live replies, so both are read, and a reply with citations but no count is counted as one search). The Exa engine is set on purpose: with the default `auto` engine (the model provider's native search) the Oct 8 live check got no citation annotations in one of two tries, which leaves no links to show. The call counts against `DAILY_LLM_CALL_CAP` like every model call.
> - The reply is cleaned (markdown links reduced to their words, bare web addresses and markdown marks removed) and validated in code whatever the prompt says: at most 150 words and 1,200 characters; no web address or domain left in the text; PG; no access code or key-like token; no `[student]` or other name token and nothing `app/privacy.py` recognises as a personal detail (email, handle, phone, a titled name); no long run of the question's own words (the injection echo check from narration); and none of a short list of injection markers ("ignore previous instructions", "system prompt", "as an AI language model", and similar). Links come only from the tool's citations (then its search results), must be `https://` with no user name or password, are de-duplicated, at most 4, and their titles pass the PG and name checks. With no usable link the question gets the existing decline. If the call fails or the text fails a check, the answer is "Here is where to look." with the links (when there are any).
> - The reply above (kind `web`) is logged with the provider, model, best slide score, and tokens; the search count goes to the usage counters (purpose `web_answer`; the classifier call is purpose `web_scope`).
>
> When web answers are off, or the cap is used up, step 6/7's `covered: false` reply is returned exactly as before (no classifier call). Code: `app/web_answer.py`.
> 8. Send the chosen slides' text, notes, transcript passages, and code to the active provider through `app/llm.py`, with the grounding prompt. Ask for JSON only.
> 9. Validate the JSON: every `slide_id` must be one that was sent, every narration under 110 words. If validation fails, retry once, then fall back to each slide's speaker notes (or its transcript passage, then its text) as the narration. *Changed Oct 8 (code review):* a narration over 110 words or 900 characters keeps its leading whole sentences that fit (rejected only when none fit, or what fits is under half the cap), and on the retry a segment whose only problem is that most of its words are not in the material (a thin slide such as "End of class") falls back to that slide's notes on its own while the grounded segments are kept. Repeating the question, a web address, PG, access codes, and name tokens still fail the whole reply. The word matcher treats inflections ("taking" and "take", "embeddings" and "embedding"), contractions, and curly apostrophes as the same words.
> 10. Sign each narration, build the audio links, sign the image and clip links in one batch call to Supabase, build `sources`, write one question-log row, and return the playlist.

**The grounding prompt, in plain terms.** The model is told it is writing narration for Prof. Collier's walkthrough. It may only explain what is in the supplied slide text, notes, transcript passages, and code. It writes in first person, in a conversational teaching voice, one segment per slide, and refers to what is on screen ("on this slide," "in line 4"). It must not add facts, examples, or opinions that are not in the supplied material, and it must not answer questions about anything outside the course content.

Added Oct 5: the transcript passages are what I said in class, so the model may say "as I said in class" but must not mention students, student questions, or anything marked `[student]`. Each slide's course, session, and slide number are in the prompt so the narration can point to them ("this is from session 6"). The same prompt goes to every provider.

Added Oct 5 (PG rule): the prompt also tells the model to keep the language PG and never curse, even if the material or the question does. The material it sees is already smoothed by the pipeline, so this covers the question and the model's own wording. It adds to the grounding rules and the grounding check; it loosens neither.

## Safety, cost and privacy rules

A public page that speaks in a real professor's voice needs firm limits. These are requirements, not polish.

**The voice only says what the materials support**

- Narration is grounded in indexed content. Off-topic questions get the not-covered response, never an improvised answer.
- Added Oct 8 (beyond the slides). Ben asked for one exception: a course-adjacent question no slide covers may get a short web-grounded text answer (step 7b). It is never narration and never in my cloned voice: it is labeled "Beyond my slides: from the web" on screen, shows its sources, and is spoken only when Settings turns on a free stock voice, labeled as such. Off-topic questions still get the not-covered response, an uncertain classification declines, and web content is treated as untrusted data: the prompt says so and the code checks the reply (no web addresses, names, access codes, crude words, injection markers, or question echo; links only from the tool's own citations).
- The audio route only speaks text the backend itself wrote. Every audio link carries a signature made with a secret key, and the route refuses text whose signature does not match, so nobody can send it their own words.
- The page states that the voice is AI-generated from Ben's recordings, on the idle screen and in the README.
- Added Oct 5 (voice tiers). The disclosure always matches the voice that is speaking. My clone: "AI voice made from my recordings." Any ElevenLabs stock voice or free Microsoft voice: "AI voice (a stock voice, not mine)." Captions only: no voice label. A voice is labeled a clone only when the ElevenLabs account says it is one (category `cloned`, or a `professional` clone the account owns); when that cannot be checked the label is "AI voice." and never the clone label. When the fallback voice takes over mid-answer, the label changes with it.
- Added Oct 5 (voice tiers). Free voices follow every rule above: the audio route speaks only signed text, the signature covers the voice tag, and the same length caps and grounding checks apply. Voice previews speak a fixed sentence set on the server.
- Added Oct 5. Switching providers in Settings does not loosen any of this: every provider gets the same grounding prompt and the same validation, and a model that ignores JSON falls back to the speaker notes. "Test this model" exists so I check a model before students get it.
- Added Oct 5. Class clips are labeled as real class recordings, so no one confuses them with the AI voice.
- Added Oct 7 (prompt editor). The prompts can be edited in Settings, so the rules that keep the voice safe live in code, never only in prompt text. Every narration is checked by `narration.validate` whatever the prompt says: the slide id must be one that was sent; at most 110 words and 900 characters; no web address; grounded in the slides sent and not repeating a long run of the question (the injection check); PG (no listed crude word); no quiz, survey, or attendance access code; and no `[student]` or `[person]` mask or other bracketed name token. Follow-ups that fail the PG, access-code, or name check are dropped. Course-info answers (`app/course_info.py`) get the same PG and name-token checks on top of their own grounding, word-cap, and access-code checks. A failing reply is retried once and then falls back to the speaker notes, which the pipeline already de-identified and smoothed. The question log, rate limits, spend caps, and signed audio do not read any prompt. A saved prompt is at most 12,000 characters, and each save is versioned (see Data).

**Access**

Added Oct 5.

- Nothing but `/api/health` works without the student passcode cookie. Course content is reachable only through signed links that expire in an hour.
- The bucket is private. The service role key exists only in Vercel and my local `.env`; the browser never sees it.
- The Settings page needs a separate admin passcode and cookie, and is not linked from the student page. Admin login is rate-limited more tightly than student login.
- The student passcode can be rotated from Settings; rotation signs everyone out.

**Spending caps**

- Per-visitor rate limit: 5 questions per minute, 30 per day.
- Daily cap on characters sent to the voice service, set in an environment variable. Past the cap, answers continue with captions only. *(Changed Oct 5: the env var is the default; Settings can change it.)*
- Question length capped at 300 characters. Narration capped at 110 words per segment.
- Suggested-question clips are pre-generated and served as static files, so replaying them costs nothing. Rate-limit and cap counters live in Supabase, because a function keeps no memory between requests. *(Changed Oct 5: pre-generated audio is served from the bucket by signed link; still costs nothing to replay.)*
- Added Oct 5. The visitor id for rate limits is the random id inside the student cookie. Login attempts are counted per salted hash of the address that rotates daily; that hash lives only in `counters`, never in the question log.
- Added Oct 5. "Test this model" counts against the same limits.
- Added Oct 5 (security review, docs/SECURITY.md). Logging in again mints a new visitor id, so questions are also limited per network address (salted daily hash, kept only in `counters`): 20 per minute, 300 per day. Two global daily caps fail closed when the database is unreachable: model calls (`DAILY_LLM_CALL_CAP`, default 600; past it narration falls back to speaker notes) and question embeddings (`DAILY_EMBED_CAP`, default 1,500; past it `/api/ask` returns a readable 503). One visitor or address may use at most a quarter of the daily voice cap. *Added Oct 8 (code review):* a refused request uses nothing: a question refused by one limit gives back what the earlier limits counted, refused voice characters give back the visitor's and address's share, and characters for a voice request the voice service failed (502, nothing spoken) are given back to the daily cap. Rate limits fall back to an in-memory counter, not to "unlimited", when the database is down; admin login fails closed.
- Added Oct 5 (security review). Every narration is checked for grounding before it is signed for the voice: most of its content words must come from the slides sent, it must not repeat a long run of the question, it must be under 900 characters, and it must not contain a web address. A failing reply is retried once, then falls back to the speaker notes.
- Added Oct 5 (voice tiers). The free Microsoft voices cost nothing but use a free service, so they have their own daily cap (`DAILY_FREE_VOICE_CHAR_CAP`, default 200,000 characters, editable in Settings) with the same quarter-per-visitor and per-address rule, at most 6 requests at once per function instance, and short timeouts. The ElevenLabs cap applies only to ElevenLabs voices. Both caps fail closed.
- Added Oct 5 (security review). OpenRouter models priced above $15 in / $60 out per million tokens are refused in Settings (adjustable with `LLM_MAX_PROMPT_PRICE_PER_MTOK` and `LLM_MAX_COMPLETION_PRICE_PER_MTOK`).
- Added Oct 7 (analytics). Usage metering never slows or blocks an answer: counters are written off the request path and a failure is only logged. Topic labeling is capped (300 questions, 1,500 output tokens, 10 runs a day, failing closed) and counts against `DAILY_LLM_CALL_CAP`. It sends scrubbed question text only, never ids, and test traffic is left out.
- Added Oct 7 (admin Evals). Eval runs started from Settings spend from the same keys, so they have their own guards on top of everything above: at most 30 questions, 3 answering models and 3 judges per run; one active run at a time; the price ceiling applies to every answering model and judge; a confirm click after an estimate of calls and cost. Every model call an eval makes counts against the global `DAILY_LLM_CALL_CAP`, against a daily admin-eval cap (`DAILY_EVAL_LLM_CALL_CAP`, default 300, fails closed) and against the run's own cap (`EVAL_MAX_CALLS_PER_RUN`, default 300). A step waits (429) rather than leave students fewer than `EVAL_STUDENT_RESERVE` (default 100) of the global calls. Embeddings count against `DAILY_EMBED_CAP`; one question's embedding is reused across the run's answering models when the same instance serves them.
- Added Oct 8 (beyond the slides). Web answers have their own daily cap (`DAILY_WEB_ANSWER_CAP`, default 200, editable in Settings, fails closed), an on/off switch (default on), and at most 3 searches per answer on Anthropic and 2 on OpenRouter. Each web answer also costs one classifier call unless the keyword pre-check decides. Both count against `DAILY_LLM_CALL_CAP`.

**Secrets**

- All keys live in the Vercel project's environment variables. `.env` is in `.gitignore` from the first commit. `.env.example` lists the variable names with no values.
- Before the first push, search the repo for key prefixes. The assignment takes a large deduction for a committed secret.
- Added Oct 5. The search runs before every push, over the diff, for the prefixes listed in AGENTS.md (including Voyage, ElevenLabs, Supabase, and JWT-shaped tokens). The Settings page shows only whether each key is configured.
- Added Oct 5 (security review). `SESSION_SECRET` and `AUDIO_SIGNING_SECRET` have no silent fallback: if either is missing the backend refuses to sign, and in production each must be at least 32 random characters. Local dev may opt into fixed dev keys with `FT_LOCAL_DEV=1`, which production ignores.

**Content and privacy**

- No student voices or names. Transcripts are stripped of student questions before indexing.
- Use a deck Ben is comfortable publishing in full: no textbook figures, no licensed images, no unreleased exam material. *(Changed Oct 5: content is no longer published. It sits behind the passcode for my own students, which is how it is already shared on Canvas. Graded material and answer keys still stay out.)*
- The question log stores question text and scores only. No names, no accounts, no IP addresses in the stored rows.
- Added Oct 5 (security review). Before a question is logged, emails, phone numbers, long digit runs, @handles, and names that follow "my name is", a title (Prof., Dr., Ms.), or "classmate"/"partner" are replaced with tokens (`app/privacy.py`). It is a roster-free heuristic, so the question box also asks students not to type names.
- Voice recordings used for cloning are Ben's own. Check the voice provider's current terms and plan requirements before uploading.
- Added Oct 5. Every student name is replaced with `[student]` at import, in transcripts and in slide text. In transcripts, every other person named except me is replaced with `[person]`. Rosters stay in `~/Lecture Archive/_private/rosters/` and are never copied, printed, logged, uploaded, or committed. The leak check is the gate before any upload.
- Added Oct 5. Student turns are marked and never used as narration material or clip audio. Unsure turns are treated as student turns.
- Added Oct 5. Clips follow the clip rules above: instructor only, no names in the audio, text, or on screen, no student presentations, and nothing from the Tesla vs Waymo case sessions.
- Added Oct 5. Everything stays PG: cursing in transcripts and slide text is swapped for a mild word at import, a clip may not contain a smoothed cue, and the narration prompt says never to curse.
- Added Oct 5. Only de-identified text leaves the local build machine: the narration provider (including models reached through OpenRouter) and Voyage see slide text, notes, and de-identified instructor speech, never a roster or a raw transcript.
- Added Oct 5. Never committed: `.env`, rosters, raw or de-identified transcripts, video, clips, slide images, the index, embeddings, review files.
- Added Oct 7 (admin Evals). Eval questions may also live in the private bucket under `evals/`, served only through the admin routes behind the admin cookie: never a signed URL, never anything a student page can reach. They are checked by `evals/dataset.py`'s rules on upload, on every read, and on every question typed or edited in Settings. Judges and answering models see the de-identified question, the twin's answer and the slide material, as in the CLI.
- Added Oct 8 (instructor alerts). A text to Ben's cell carries the course, the problem type, the Canvas item, and at most about 120 characters of the scrubbed question (privacy scrub, plus API-key-like and access-code-like tokens and web addresses removed), never a visitor id, an address, or a name. Texts are capped per day and per visitor, deduped for 2 hours, never sent for test traffic, and the Twilio keys live only in Vercel and the local `.env`.

## Build guide

Eight blocks, about 8 hours. Every block ends the same way: run its check, commit and push, and add an entry to `prompt_log.md` with the prompts used and anything the AI got wrong. The assignment grades the commit history and the log, so doing this as you go is part of the work.

> **Changed Oct 5.** Two courses, private content, clips, and Settings add work that does not fit in 8 hours of my time. A team of AI agents builds the new blocks in parallel, one branch and one pull request per block, following AGENTS.md; my hours go to the four hand-written pieces, reviewing, the voice, and the demo. Every block still ends with its **Check**, a squash-merged PR, and a prompt-log entry. **Deadline: Wednesday October 7, 11:59 PM.** There are now eleven blocks, 0 to 10. The original Blocks 1, 2, and 3 were replaced; their text is kept at the end of this section. The other original blocks are kept with their changes marked.

The original plan, for the record:

| Block | When | Time | Result |
| --- | --- | --- | --- |
| 0. Skeleton and first deploy | Mon evening | 30 min | A public URL that says hello |
| 1. Indexer | Mon evening | 75 min | Slide images and `index.json` |
| 2. Ask endpoint, text only | Mon evening | 75 min | A playlist from a typed question |
| 3. Stage and player | Tue | 90 min | The full walkthrough with captions |
| 4. Voice | Tue | 60 min | Spoken narration, auto-advance |
| 5. Limits and pre-generated answers | Tue | 45 min | Caps on, suggested questions instant |
| 6. Phone pass and error states | Wed | 45 min | Works on a phone, fails gracefully |
| 7. README, log, video, submit | Wed | 60 min | Submitted by 9 PM, three hours early |

The revised plan (Oct 5):

| Block | When | Result | Tier |
| --- | --- | --- | --- |
| 0. Skeleton and first deploy | Mon Oct 5 | A public URL that says hello | 1 |
| 1. Slides and de-identification | Mon Oct 5 | Slide images, scrubbed text and transcripts for 22 sessions, review files | 1 |
| 2. Alignment and index | Mon Oct 5 to Tue Oct 6 | Alignment for every session, `index.json` and `embeddings.npy` | 1 |
| 3. Storage, passcode, ask endpoint (text only) | Tue Oct 6 | A playlist with signed links from a typed question, behind the passcode | 1 |
| 4. Stage, player, source card | Tue Oct 6 | The full walkthrough with captions and citations | 1 |
| 5. Voice (was Block 4) | Tue Oct 6 | Spoken narration, auto-advance | 2 |
| 6. Limits and pre-generated answers (was Block 5) | Tue Oct 6 | Caps on, suggested questions instant, question log | 3 |
| 7. Class clips | Tue Oct 6 | Clips cut, uploaded, and playable from the source card | 3 |
| 8. Settings page and worker | Tue Oct 6 to Wed Oct 7 | Model switch, voice, limits, activity, then courses, uploads, and the worker | 3 |
| 9. Phone pass and error states (was Block 6) | Wed Oct 7 | Works on a phone, fails gracefully | all |
| 10. README, log, video, submit (was Block 7) | Wed Oct 7 | Submitted by 9 PM, three hours early | all |

### Block 0. Skeleton and first deploy

1. Create the `faculty-twin` repo, public, with the folder layout from Architecture.
2. Add `.gitignore` (with `.env`), `.env.example`, `requirements.txt`, a README stub, and an empty `prompt_log.md`.
3. Write `app/main.py` with a FastAPI instance named `app` and one route, `/api/health`. Put a placeholder `index.html` in `public/`.
4. Run it locally with `vercel dev`, following Vercel's FastAPI guide (linked under Open decisions).
5. Connect the GitHub repo to a new Vercel project and deploy. Add the environment variables in the project settings, even if empty.
6. Time box: 30 minutes. If the deploy is still failing at that point, switch to a paid Render instance and move on.

Added Oct 5: the `.gitignore` also covers `content/`, `_build/`, `*.webp`, `*.mp4`, `*.vtt`, and `*.npy`, and `.env.example` lists every variable from Configuration.

**Check:** the Vercel URL loads the page on your phone, and `/api/health` returns ok. Deploying first answers the check-in question about deployment and removes the biggest deadline risk.

### Block 1. Slides and de-identification

New Oct 5 (replaces the slide half of the original Block 1).

1. `indexer/slides.py`: render every session's slide PDF to WebP and write `slides.json` with text and notes (Preprocessing stage 1). Render 45-884 session 10 from its pptx.
2. Pull the missing 70-445 session 11 VTT from the Zoom cloud recording in Chrome into the archive folder.
3. `indexer/deidentify.py`: parse VTTs, run the roster scrub on transcripts and slide text, mark student turns, write the review files, run the leak check (stage 2).
4. Write the per-session override files for the "In the News" presentations.
5. Tests with a made-up roster and a made-up VTT, in `tests/`. No real names in any test.

**Check:** for every session, image count equals PDF page count, and slide 1's image is slide 1. The leak check reports zero roster hits across all outputs. I read three review files and one scrubbed transcript and find no student name and no student turn marked `instructor`.

### Block 2. Alignment and index

New Oct 5 (replaces the index half of the original Block 1).

1. `indexer/align.py`: frame matching where the recording is a screen share, text similarity with the in-order constraint as fallback (stage 3).
2. Write `indexer/code_map.json` by hand for the sessions with notebooks. *(Added Oct 5, late: `indexer/suggest_code_map.py` writes a draft, TF-IDF cosine between cells and slides of the same course with a same-session preference, at most two cells per slide, and a review table for me to strike rows from. The table holds slide titles and code, so it is written to `_build/code_map_review.md` on the local build machine, not the public repo. Keys starting with `_` (source note, method, scores) are ignored by the index stage.)*
3. `indexer/build_index.py`: records, code cells, Voyage embeddings with the cache, `index.json` and `embeddings.npy`, leak check (stage 5). If the Voyage key is not in yet, it writes everything else and embeds when the key arrives.

**Check:** open `index.json` and read three records from different sessions. For each, open the class video at the start of its first window and see that slide on screen. Record count equals slides plus code cells, and `embeddings.npy` has the same number of rows.

### Block 3. Storage, passcode, ask endpoint (text only)

Replaces the original Block 2, with storage and the passcode added.

1. `supabase/schema.sql` and the Supabase project; create the private bucket `twin-content`.
2. `indexer/upload.py`: mirror the build outputs and set `index_version` (stage 6).
3. `app/storage.py`: load the index from the bucket (or `CONTENT_DIR`), reload on a new `index_version`, sign links in batches.
4. `app/auth.py`: `/api/login`, the `ft_session` cookie, the cookie check on every other route.
5. `app/llm.py` with the Claude provider first (OpenAI and OpenRouter can follow in Block 8), and `app/narration.py` with the grounding prompt, validation, and fallback.
6. `/api/courses` and `/api/ask` with the course filter. I write the retrieval and segment-selection bodies by hand (see Code Ben writes by hand) and test them before any LLM call.
7. Add the not-covered threshold. Find its value by trying five on-topic and five off-topic questions per course and looking at the top scores.

**Check:** from a terminal, `/api/ask` without the cookie returns 401. After logging in, a question from each course returns three to five segments in course order with sensible narration and image links that open, `course: "45884"` never returns a 70-445 slide, and "who won the Stanley Cup" returns `covered: false`.

### Block 4. Stage, player, source card

Replaces the original Block 3, with the passcode screen, course filter, and source card added.

1. Build the passcode screen, the idle screen with the course filter, and the docking change to the presenting layout.
2. Render a segment: slide image, source card, code panel with marked lines, caption.
3. Build the player as a small state machine: current segment number, playing or paused, next and previous. In this block, "playing" advances on a timer based on word count.
4. Add the progress dots, the sources list, and the follow-up chips.

**Check:** enter the passcode and ask a question on the deployed site, then click through the whole answer on a computer and a phone. Each card's course, session, date, and slide number match the slide PDF on Canvas.

### Block 5. Voice (was Block 4)

1. Create the voice clone from 2 to 5 minutes of clean solo lecture audio. Listen to a test sentence before wiring anything.
2. Write `/api/audio`: check the signature, call the voice service, stream the mp3 back. Add the signing step to the ask route.
3. Replace the timer in the player with the audio element's ended event. Preload the next clip while the current one plays.
4. Add the mute button and the captions-only fallback.

Changed Oct 5: `/api/audio` also needs the student cookie, and it uses the voice id from `settings` (falling back to `ELEVENLABS_VOICE_ID`).

**Check:** one tap on "ask," then hands off: the answer plays through all segments with sound, and slides change on time.

### Block 6. Limits and pre-generated answers (was Block 5)

1. Create the Supabase table for counters. Add the rate limit and the daily character cap, both reading and writing that table.
2. Write eight to ten suggested questions. Run each once, review the narration by ear, fix anything wrong, and commit the playlists and the mp3 files in `public/audio/`. A small script on your computer does the generating.
3. If time allows: record the 5-second corner clip, and add the question log to the same Supabase project.

Changed Oct 5: `app/limits.py` holds the counters, the login rate limit, and the question log, which is no longer optional. The suggested questions cover both courses (four or five each). Their playlists and mp3 files go to `topics/` and `audio/<voice_id>/` in the bucket, not the repo.

**Check:** a suggested question starts speaking in under 2 seconds. Setting the cap to zero produces a captions-only answer with a note, not an error. Added Oct 5: the eleventh wrong passcode within 15 minutes returns 429, and a question shows up in the log with no cookie or address in the row.

### Block 7. Class clips

New Oct 5.

1. `indexer/clips.py`: cut clips under the clip rules, write the manifest (stage 4). Re-run `build_index.py` so records get their `clip` path, then `upload.py`.
2. Add the clip to each segment in `/api/ask` and the "Watch me explain this in class" button to the source card.

**Check:** the manifest has no entry from 45-884 session 11 and none overlapping an "In the News" window. I watch ten random clips end to end: only my voice, no student name said or shown, and the slide on screen matches the card. The button plays the clip in place of the slide, and the slide comes back, paused, when it ends.

### Block 8. Settings page and worker

New Oct 5. Build in this order and stop where the clock says.

1. Admin login, the `ft_admin` cookie, `/api/admin/status`.
2. Model: OpenAI and OpenRouter providers in `app/llm.py`, `/api/admin/models`, `/api/admin/settings`, `/api/admin/test`.
3. Voice: `/api/admin/voices`, preview, captions-only option. *Added Oct 5:* the three voice tiers (my clone, ElevenLabs voices, free Microsoft voices), `/api/admin/voice-preview`, the fallback voice, the free cap, and `/api/voice` for the label.
4. Limits and access: cap, passcode rotation. Activity: `/api/admin/log`.
5. Courses and source material: the course and session routes, `/api/admin/uploads` with signed upload URLs, `/api/admin/sources` and rerun, `indexer/worker.py`.

**Check:** I switch to an OpenRouter model, "Test this model" returns a valid playlist, and the next student question's log row shows the new provider and model. Rotating the passcode sends an open student tab back to the passcode screen. A test VTT uploaded for a hidden test session goes from `uploaded` to `ready` within a minute of the worker running, and hiding a real session drops its slides from answers.

### Block 8a. Analytics (added Oct 7)

1. `app/usage.py`: provider usage parsing (Anthropic `usage.input_tokens/output_tokens`, OpenAI and OpenRouter `usage.prompt_tokens/completion_tokens`, Voyage `usage.total_tokens`), purposes, counters; hooks in `app/llm.py`, `app/embed.py`, `/api/audio`.
2. `app/pricing.py`: the price table with researched defaults.
3. The question-log columns (migration block in `supabase/schema.sql`) and `source` from the student page; test traffic tagged.
4. `POST /api/event` and the page's events.
5. `app/analytics.py` and the Analytics section (`public/admin-analytics.js`, `public/analytics.css`).

**Check:** after the migration, ask one typed question and tap one chip on the live site; Analytics shows two questions with sources typed and chip, tokens under `narration`, and a nonzero estimated spend; `python -m scripts.live_smoke` adds rows that show only with "Show test traffic"; "Label topics" returns themes and the run appears in its history.

### Block 8b. Test harness (added Oct 8)

1. CI on every pull request and push to `main` (`.github/workflows/tests.yml`): the full suite with coverage, `node --check` on the browser scripts, and the browser tests. No secrets; `tests/conftest.py` fails any test that reaches a non-loopback host.
2. Coverage per module, with tests that bring every `app/` module to at least 80% of lines.
3. Browser tests (Playwright, headless Chromium) of the student page and Settings against `?mock=1`.
4. A contract test that keeps `public/dev/mock.js` in the real API's shapes.
5. The live smoke check also covers a course-info answer, a signed slide image, a stored chip's audio (first kilobyte only), Settings status (only with `ADMIN_PASSCODE`), and `--json`.
6. Property tests (hypothesis) for the PG filter, the access-code filter, de-identification and the narration validators.

Details and commands: [TESTING_AND_SCORES.md](TESTING_AND_SCORES.md), "(a) The automated test suite" and "(d) Live smoke check".

**Check:** the `tests` workflow is green on the pull request; locally the suite passes with no network, and `--e2e tests/e2e` passes.

### Block 9. Phone pass and error states (was Block 6)

1. Walk through every state on a real phone: idle, loading, presenting, not covered, backend asleep, audio failed. *(Added Oct 5: also passcode, wrong passcode, expired cookie, clip playing, clip failed.)*
2. Fix layout problems: long code lines, small tap targets, the caption covering the slide. *(Added Oct 5: and the source card crowding the slide.)*
3. Ask someone who has not seen it to try three questions. Write down what confused them.

**Check:** no state shows a blank screen or a raw error message.

### Block 10. README, log, video, submit (was Block 7)

1. Write the README yourself: what it does, how to use it, proudest features, how to run locally, how secrets are handled, how AI was used.
2. Finish `prompt_log.md`: tools and which job each did, the process, the hand-written code, verbatim prompts, one place AI got it wrong.
3. Record the demo on the deployed URL with sound. Show a question, the walkthrough, a refusal, and the retrieval function on screen while you explain it. *(Added Oct 5: also show the source card, one class clip, and the Settings model switch. Do not show the passcode, the Settings activity list, or any student.)*
4. Add the project to `data/portfolio.json` in the portfolio repo, rebuild, commit.
5. Test the video link in an incognito window. Submit the form. *(Added Oct 5: put the passcode for graders in the form.)*

**Check:** every box in the deliverables checklist below is ticked.

### Original Blocks 1 to 3 (replaced Oct 5)

Kept for the record. They describe the one-deck build.

**Block 1. Indexer**

1. Pick the deck and notebook. Export the deck to PDF, then to one PNG per slide at about 1600 px wide, saved in `public/slides/`.
2. Extract each slide's title, body text, and speaker notes with python-pptx.
3. Extract code cells and their preceding markdown with nbformat.
4. Write the slide-to-code mapping by hand in a small YAML or JSON file.
5. Embed each record's text and write `content/index.json`.

**Check:** open `index.json` and read three records. Slide 1's image file is slide 1. Record count equals slides plus code cells.

**Block 2. Ask endpoint, text only**

1. Load `index.json` into memory at startup.
2. Write the retrieval and segment-selection functions by hand (see next section). Test them with a few questions before any LLM call.
3. Add the narration call with the grounding prompt and JSON validation.
4. Add the not-covered threshold. Find its value by trying five on-topic and five off-topic questions and looking at the top scores.

**Check:** from a terminal, "how do I choose k" returns three to five segments in deck order with sensible narration, and "who won the Stanley Cup" returns `covered: false`.

**Block 3. Stage and player**

1. Build the idle screen and the docking change to the presenting layout.
2. Render a segment: slide image, code panel with marked lines, caption.
3. Build the player as a small state machine: current segment number, playing or paused, next and previous. In this block, "playing" advances on a timer based on word count.
4. Add the progress dots and the follow-up chips.

**Check:** ask a question on the deployed site and click through the whole answer on a computer and a phone.

## Code Ben writes by hand

The graders want to see changes you made yourself and can explain without notes. These four pieces are small, central to how the app behaves, and close to what you teach, so they are the ones to write without an AI tool.

**1. Cosine similarity and ranking** (`app/retrieval.py`). Given the question's embedding and the matrix of slide embeddings, return slides sorted by score. A few lines of numpy, and the same math as the similarity material in your own courses.

**2. Segment selection** (`app/retrieval.py`). This is the design decision that makes answers feel like teaching:

- Take the top 8 slides by score.
- Drop any under the threshold.
- Prefer slides that sit next to each other in the deck: if slides 12, 13, and 15 are in the top 8, include 14 as well so the explanation does not skip a step.
- Keep at most 5, then sort by deck order.
- Attach each slide's related code cell if it has one.

**3. The not-covered threshold.** Choosing the cutoff from your ten test questions, and writing down in the prompt log how you picked it.

*Added Oct 7 (answer thresholds).* The code default stays yours: `NOT_COVERED_THRESHOLD` in `app/retrieval.py` (0.52) and the comment above it are hand-chosen and agents do not edit them. Settings can now override the value at run time (Limits and access, Answer thresholds) without touching that file; removing the override goes back to your number.

**4. The player's advance logic** (`public/app.js`). The function that runs when a clip ends: move to the next segment, start its audio, preload the one after, and handle the last segment and the pause state.

`tests/test_retrieval.py` gets three tests on your selection function: a clear on-topic question returns slides in deck order, a gap between adjacent slides is filled, and an off-topic question returns nothing. Rink Rivals had a regression test that made a good story in the write-up, and these do the same job here.

Added Oct 5: the four items are unchanged. With two courses, "deck" means one course session and "deck order" means course, session, slide number; the course filter and hidden sessions are applied by the code around these functions before ranking, so the functions themselves stay small. The agents left signatures, docstrings, `raise NotImplementedError` stubs, and failing tests for all four (in `app/retrieval.py`, `public/app.js`, and `tests/test_retrieval.py`), with everything around them ready, so I only fill in the bodies. End-to-end tests use a clearly labeled test fake, injected inside `tests/` only.

**For the prompt log.** Likely candidates for "one place AI got it wrong": the slide-to-image export step, autoplay being blocked by the browser, and the model ignoring the JSON-only instruction. Note the first one that happens, with what you did about it.

## Deliverables checklist

From the [Project 2 page](https://www.cs.cmu.edu/~113/project2.html). Due Wednesday October 7 at 11:59 PM, with no extensions into Fall Break.

- [ ] Deployed app at a public URL, verified on a phone
- [ ] Public GitHub repo with several commits across the project days
- [ ] `README.md` at the repo root, written in Ben's own words, covering: what it does, how to use it, proudest features, how to run locally, how secrets are handled, a short summary of AI use with citations
- [ ] Any AI-written documentation sits at the bottom of the README under a heading that labels it as AI-generated
- [ ] `prompt_log.md` as a separate file in the same folder: models and tools used, which tool for which job and why, process from start to finish, which code Ben wrote or changed, important prompts verbatim, one place AI got it wrong
- [ ] No keys or secrets anywhere in the repo or its history
- [ ] Added Oct 5: no rosters, transcripts, video, slide images, or index files anywhere in the repo or its history
- [ ] Added Oct 5: the passcode for graders is in the submission form
- [ ] Project linked from the Projects section of the portfolio site
- [ ] Demo video with sound, recorded on the deployed URL, explaining the architecture and the hand-written code
- [ ] Video link opens in an incognito window
- [ ] Google form submitted with deployed URL, repo URL, and video link
- [ ] Ready to explain the code in person at the final presentation

**Requirement boxes this project ticks:** frontend-backend communication, third-party APIs with secure keys, an ML component (embedding retrieval), and a database if the question log ships. *(Changed Oct 5: the database now ships regardless, for settings, counters, the question log, and courses.)*

## Open decisions

The spec above assumes a default for each of these. Changing one changes the build, so settle them before Block 1.

| Decision | Default assumed | What a different answer changes | Status (Oct 5) |
| --- | --- | --- | --- |
| Which deck and notebook | The clustering session from Data Mining | Block 1 content, the suggested questions | Settled: both Fall 2026 courses, sessions 01 to 11 each, most current slide PDF per session |
| Is there a transcript for that session | No, slides and notes only | With one, narration follows Ben's real explanations | Settled: Zoom VTT for every session; the missing 70-445 session 11 VTT gets pulled from Zoom in Chrome |
| Do the slides have speaker notes | Unknown | Without notes or a transcript, narration is thin and the model has to stretch | Answered: pptx notes for most decks; 45-884 session 6 has none; transcripts cover the gap |
| Voice | A clone of Ben's voice through ElevenLabs | A stock voice removes the consent and labeling work and about 20 minutes | Settled: ElevenLabs clone, switchable to a stock voice or captions only in Settings. Oct 5: also switchable to a free Microsoft voice (edge-tts), with a free-voice fallback option |
| Who can use it | Anyone with the link | A course passcode would cut cost and misuse risk | Settled: content private in a Supabase bucket, course passcode, signed cookies and signed links |
| LLM and embeddings provider | Whichever key Ben already has set up | Only the two API calls | Settled: Claude (`claude-sonnet-5-5`) writes narration, Voyage AI embeds; the narration model is switchable in Settings (Claude, OpenAI, OpenRouter) |
| Hosting | Vercel, one project (decided October 5). Fallback: a paid Render instance | Cloudflare is the candidate for the portfolio integration later; a free build there would mean a JavaScript backend | Settled, unchanged; content and database on Supabase |
| Name | Faculty Twin | Repo name, page title | Settled, unchanged |

**Also settled October 5**

- Class video clips: built, and published automatically behind the passcode, under the clip rules.
- The Tesla vs Waymo case sessions get no clips; their slides are indexed.
- Admin model switch, which grew into the full Settings page: model, voice, courses and source uploads processed by a local worker, limits and access, activity.

**Still open**

| Decision | Default assumed | What a different answer changes |
| --- | --- | --- |
| How students and graders get the passcode | A Canvas announcement in each course, and the submission form for graders | One passcode for both courses is simplest; separate ones would need the cookie to carry a course |
| ElevenLabs plan | The lowest plan that allows an instant voice clone and enough characters for testing and grading | A bigger plan raises the daily cap I can afford; no plan means captions only |
| Supabase project | A new project for Faculty Twin, so its keys and storage are separate from everything else | Plan limits decide whether clips and video uploads fit: the free plan's storage and per-file upload limits may be smaller than a semester of clips or one class video. If so, keep clips small and upload video from the local build machine instead of through Settings |
| Vercel account | My personal account, one project | A team account changes who can see environment variables and the function duration limit |

**Not verified.** ElevenLabs plan requirements and pricing for voice cloning, how long a cold start feels on Vercel's free plan with numpy loaded, and current model names for embeddings. Check each during the block that uses it. Vercel's FastAPI guide: https://vercel.com/docs/frameworks/backend/fastapi

Added Oct 5, also not verified: Supabase's storage, egress, and per-file upload limits on the plan I end up on; the cold start with the index downloaded from storage (about 10 MB); the exact OpenRouter models-list response shape; the current Voyage model name for `VOYAGE_MODEL`; and that `claude-sonnet-5-5` is the right model id. Check each in the block that uses it.

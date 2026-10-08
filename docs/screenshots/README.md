# Screenshots

*Taken by Claude Code (Claude Opus 5.5) on October 8, 2026, from the live site https://faculty-twin.vercel.app.*

## How they were taken

- Headless Google Chrome driven by Playwright. Desktop shots are 1440 x 900; phone shots are 390 x 844 at 2x (780 x 1688 pixels). Light mode, except the one dark-mode shot.
- The browser logged in through the API before any page opened, so the passcode screen never appears in a shot. Every `/api/*` call carried `X-FT-Source: smoke`, so these questions are logged as test traffic and left out of the student numbers in Settings > Analytics. Usage events (`/api/event`) were not sent.
- Live settings at the time: narration model **Anthropic `claude-opus-5-5`**; voice **the server default, Ben's ElevenLabs voice clone** (labeled "AI voice made from my recordings."), with the fallback set to captions only. Audio played muted in the headless browser.
- Saved as WebP, each under 300 KB.

## What was left out on purpose

- Nothing from the passcode field, the Activity question log, the eval question set or run detail rows, `.env`, or any provider dashboard.
- Slides were picked from answers whose slides carry none of the pipeline's caution flags (third-party source, copyright notice, image-only slides, possible student names, "In the News", student presentations, the private Tesla vs Waymo case) and were then checked by eye for names, faces and third-party images. Two shots are cropped so a slide with a textbook figure or clip art does not show (see the notes below).
- Analytics shots show the default view with test traffic hidden and are cropped to spend, tokens and the topic tree: no panel that lists question text.

## The student page

| File | What it shows | Slide shown |
| --- | --- | --- |
| [student-idle-desktop.webp](student-idle-desktop.webp) | The idle screen: course filter, question box, suggested-question chips, the voice disclosure | none |
| [walkthrough-kmeans-desktop.webp](walkthrough-kmeans-desktop.webp) | A suggested question ("How do I choose the number of clusters in k-means?") mid-walkthrough, paused: the slide, the caption (the narration sentence being spoken), the controls, the "Where this is in the course" source card, and the sources list in the chat dock | 70445-s05-042 |
| [class-clip-desktop.webp](class-clip-desktop.webp) | "Watch me explain this in class": a frame of the class clip playing in place of the slide (presenter view of the same slide), with the "Recorded in class ... (my real voice, not the AI voice)" label | 70445-s05-042 (clip frame) |
| [walkthrough-kmeans-dark-desktop.webp](walkthrough-kmeans-dark-desktop.webp) | The same answer in dark mode, segment 3 of 5 | 70445-s05-053 |
| [walkthrough-end-sources-desktop.webp](walkthrough-end-sources-desktop.webp) | The chat dock after the last segment: the sources list (course, session, date, slide) and two follow-up questions. Cropped to the dock because the last slide of this answer has clip art | thumbnails only (70445-s05-042, 044, 053, 054, 056) |
| [code-panel-desktop.webp](code-panel-desktop.webp) | A typed question ("What are frames and semantic networks in knowledge representation?") on a segment with notebook code: the "Code from class" panel, the clip button and the caption. Cropped below the slide, which reproduces a textbook figure | none (segment of 70445-s03-027) |
| [not-covered-desktop.webp](not-covered-desktop.webp) | An off-topic question ("Who won the Stanley Cup last year?") declined with suggested chips | none |
| [faq-office-hours-desktop.webp](faq-office-hours-desktop.webp) | A course FAQ answer ("What are your office hours?"): Ben's written answer and the "Book a 30-minute meeting" Calendly button | none |
| [canvas-course-info-desktop.webp](canvas-course-info-desktop.webp) | A "From Canvas" course-info card (45-884, "How do I get free access to O'Reilly books?") with buttons that open the Canvas pages. This answer is the fallback text (the first sentences of the top Canvas chunk), not a model-written reply; two wordings gave the same result | none |
| [student-idle-phone.webp](student-idle-phone.webp) | The idle screen on a phone | none |
| [walkthrough-tfidf-phone.webp](walkthrough-tfidf-phone.webp) | "What is TF-IDF and why is it useful?" on a phone, segment 2 of 2: slide, source card, caption, controls, and the docked course filter and question bar | 45884-s03-043 |
| [faq-office-hours-phone.webp](faq-office-hours-phone.webp) | The FAQ card on a phone | none |

## The Settings page (admin only)

| File | What it shows |
| --- | --- |
| [settings-model.webp](settings-model.webp) | Model: provider, curated model list, model id, "Test this model" |
| [settings-voice-clone.webp](settings-voice-clone.webp) | Voice: the explainer, what students see now, and the "My voice clone" group (costs money) |
| [settings-voice-free.webp](settings-voice-free.webp) | Voice: the free Microsoft voices, Server default and Captions only, and the fallback setting |
| [settings-thresholds.webp](settings-thresholds.webp) | Limits and access > Answer thresholds: slide 0.52 and course info 0.55, both "my code default", with the explainer and change history |
| [settings-prompts-list.webp](settings-prompts-list.webp) | Prompts: the warning, the five prompts with Default badges, and the narration prompt open in the editor |
| [settings-prompt-editor-diff.webp](settings-prompt-editor-diff.webp) | Prompts: an unsaved one-word edit ("warm,") compared with the built-in default, word by word. Not saved |
| [settings-evals-report-card.webp](settings-evals-report-card.webp) | Evals: the report card chart (pass rate per answering model, run by run, axis from zero) |
| [settings-evals-run-cards.webp](settings-evals-run-cards.webp) | Evals: the three run cards (October 7 twin run, the excluded first attempt, the October 5 baseline), aggregates only |
| [settings-analytics-spend-tokens.webp](settings-analytics-spend-tokens.webp) | Analytics, last 30 days, test traffic hidden: tiles, estimated spend over time, tokens by model and purpose, voice characters, embeddings |
| [settings-analytics-topic-tree.webp](settings-analytics-topic-tree.webp) | Analytics: questions by course, session and slide (from each answered question's top slide) |

## Mock screenshots (placeholder content, `?mock=1`, taken headless)

These come from the dev mock (`public/dev/mock.js`), not the live site: the words are placeholders and no course material is shown.

| File | What it shows |
| --- | --- |
| [web-answer-card-mock.webp](web-answer-card-mock.webp) | "Beyond my slides: from the web": the amber label, the answer, a Listen button in a stock voice, source links, and "Closest material in my course" |
| [helper-slide-diagram-mock.webp](helper-slide-diagram-mock.webp) | An AI-drawn helper slide, diagram kind (cycle layout), drawn by `public/helper-slide.js` from a JSON spec, labeled "AI-drawn slide, not from my course" |
| [helper-slide-code-mock.webp](helper-slide-code-mock.webp) | An AI-drawn helper slide, code kind: Python shown with line numbers and two callouts. The code is only displayed |

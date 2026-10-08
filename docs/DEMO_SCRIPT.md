# Demo video script

A shot list for the Project 2 demo video (spec, Block 10, step 3). It aims for about 5 minutes. Record on the **deployed URL**, with sound, in one take per section, then cut together.

Explain the hand-written code in your own words. This script only says *when* to show it and what the graders need to hear covered. It doesn't put words in your mouth for the parts you wrote.

## Before you record

- [ ] The deployed site loads in a fresh browser profile. No admin cookie, no extensions, notifications off.
- [ ] Retrieval is written, the three tests in `tests/test_retrieval.py` pass, and the threshold is set.
- [ ] Content is uploaded, and `GET /api/health` returns ok.
- [ ] Today's voice cap has room. A full walkthrough is about 5 segments times 500 characters.
- [ ] Ask each demo question once off camera, so the stored or cached answers are warm and you know what they show.
- [ ] Choose one segment that has a class clip, and check the clip contains only your voice and your screen.
- [ ] Screen recorder captures system audio. The browser is at 100% zoom with a window of about 1440 px wide.
- [ ] An editor is open on `app/retrieval.py` and `public/app.js` (at `onClipEnded`), with a font size that's readable in the video.
- [ ] A phone (or the browser's phone view at 390 px) is ready for the 10-second phone shot.

**Never on screen:**
- the passcode, or the passcode field while you type
- the Settings activity list or question log
- `.env`, the Vercel environment variables page, or the Supabase dashboard
- any student's name, face, or voice
- the private eval files in `evals/private/`

Type the passcode before you start recording, or blur that part.

## Shot list

| # | Time | On screen | Cover |
| --- | --- | --- | --- |
| 1 | 0:00 to 0:20 | The idle screen: question box, course filter, suggested chips, the AI-voice label | What Faculty Twin is in one sentence. The voice is an AI made from your recordings, and the page says so. |
| 2 | 0:20 to 1:30 | Ask "How does a neural network learn?" (or your best suggested question) with the course filter on 70-445. Let two or three segments play hands-off. | The chat docking to the side, the slides appearing in deck order, the voice, auto-advance. Point at the **source card** ("Where this is in the course": course, session, date, slide number) and say why it matters: it sends students back to the real slides. |
| 3 | 1:30 to 1:50 | Press "Watch me explain this in class" on the segment with a clip | The clip is your real voice from class, labeled that way. When it ends, the walkthrough stays paused. |
| 4 | 1:50 to 2:10 | Pause, go back one, press play. Show the follow-ups and the source list after the last segment. | The player controls. Follow-up questions come only from the same slides. |
| 5 | 2:10 to 2:35 | Ask something the course doesn't cover. Good choices: "Can I get an extension on lab 2?" (a real kind of email you get) or "Who won the Stanley Cup?" | The twin declines instead of making something up. Mention the evals: real student emails are mostly logistics, and the twin has to decline those, not promise anything for you. |
| 6 | 2:35 to 3:45 | Editor: `app/retrieval.py`, then `tests/test_retrieval.py`, then a terminal running `pytest tests/test_retrieval.py -q` | **Your hand-written code, in your words.** Cover: what `rank` computes, and why cosine similarity rather than a dot product; how `select_segments` picks slides, including filling a gap between neighboring slides and keeping deck order; how you chose `NOT_COVERED_THRESHOLD` from your ten test questions; and the three tests passing. |
| 7 | 3:45 to 4:15 | Editor: `public/app.js` at `onClipEnded` | **Your hand-written player logic, in your words:** what happens when a narration clip ends, preloading the next one, the last segment, the paused state. Keep it separate from the class-clip button. |
| 8 | 4:15 to 4:45 | The architecture diagram from `docs/SPEC.md` (rendered on GitHub), or a slide of it | Browser to FastAPI on Vercel. Embeddings, LLM and voice called only from the backend, so keys never reach the browser. Private content through signed links. Supabase for counters and the question log. The index built on your own computer from the private archive, then uploaded to Supabase. |
| 9 | 4:45 to 5:00 | Settings page, **Model** section only: switch the provider or model, then ask one quick question | The model switch. Don't scroll to Activity. |
| 10 | 5:00 to 5:10 | The same site on a phone | Stacked layout: stage on top, input at the bottom. |

## Talking points that are safe to read

These describe the system, not your hand-written code:

- "Every sentence it speaks has to come from my slides, my speaker notes, or what I said in class over that slide. If the model's narration drifts, the backend throws it out and falls back to my own notes."
- "The voice route only speaks text the backend signed, so nobody can make it say arbitrary words in my voice."
- "There are spend caps on questions per visitor, on model calls per day, and on voice characters per day. Past the voice cap it keeps going with captions."
- "Students' names never reach the index. Transcripts are de-identified on my own computer before anything is uploaded."

## After recording

- [ ] Watch it once with sound, checking for the passcode, the activity list, any student, or a key on screen.
- [ ] Upload, then open the link in an incognito window (spec checklist).
- [ ] Put the link in the README and the submission form, along with the passcode for graders.

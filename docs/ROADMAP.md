# Roadmap

Ideas for after the October 7 submission. Nothing here is in scope for Tier 1 to 3 in [SPEC.md](SPEC.md); each item moves into the spec before it is built.

**v2 design (Oct 8):** the plan for a more conversational twin that blends class and outside material, student feedback, and assignment help is in [V2.md](V2.md).

## Voice: free and open-source models on the local build machine

Added October 5, 2026. These run locally on the build machine (the computer that holds the private archive), so they cost nothing per character. Vercel functions cannot run them, so they fit **pre-generated audio** (the 8 to 10 suggested questions, rendered once by the indexer and uploaded under `audio/<voice tag>/`), not live answers. Live answers keep using the hosted tiers (my ElevenLabs clone, ElevenLabs stock voices, free Microsoft voices through edge-tts). Serving a local model for live answers would mean exposing a voice server on a home computer to the internet, which the security review advises against.

| Option | My voice? | License | Notes |
| --- | --- | --- | --- |
| [Kokoro](https://github.com/hexgrad/kokoro) | No, stock voices | Apache 2.0 | Excellent quality for its size (82M parameters); fast on an ordinary CPU |
| [Chatterbox](https://github.com/resemble-ai/chatterbox) (Resemble AI) | Yes, from about 10 seconds of audio | MIT | Free cloning of my own voice |
| [F5-TTS](https://github.com/SWivid/F5-TTS), [XTTS-v2](https://huggingface.co/coqui/XTTS-v2) | Yes | Weights are non-commercial only | Fine for a class project, not for anything sold |

Not yet verified: current versions, licenses, and quality on my recordings. Check each before building.

*Update Oct 5:* versions and licenses checked (Kokoro 0.9.4, Apache-2.0, Python below 3.13; Chatterbox `chatterbox-tts` 0.1.7, MIT). Steps 1 and 2 are built in [`localvoice/`](../localvoice/README.md): an instructor-only sample cutter and a blind A/B page. Kokoro renders on CPU alone at about 0.16x real time. Quality on my recordings is still unchecked; that needs the A/B run on the local build machine.

**What a first step would look like**

1. Cut 30 to 60 seconds of clean solo speech from my class recordings (instructor-only cues from the de-identified transcripts, so no student voice is in the sample).
2. Render the same three narration segments with Chatterbox (my clone) and Kokoro (stock), next to the ElevenLabs clone, and compare by ear.
3. If one is good enough, add it as a pre-generation option in `indexer/pregenerate.py` and as a voice tier on the Settings page that applies to suggested questions only, with the same disclosure rule: "AI voice made from my recordings" only for a clone of my voice.

## Course FAQ answers (logistics in my own words)

Added October 7, 2026. The first twin evaluation showed that most student email is logistics: meetings, missed classes, late work, rescheduling a presentation. The twin can only decline those today, or worse, narrate slides that happen to score above the threshold. My course FAQs already answer most of them, in my words:
- [45-884 FAQ](https://docs.google.com/document/d/1hl_eLbzUTP53KOaWcKaN-p7lP3ddgDnXXcB-JwJGEaE/edit)
- [70-445 FAQ](https://docs.google.com/document/d/1a8rN0uS7XTKjLnTYY9_UtIDvDOYLWnn7L2IpmhMZWkM/edit)

Two more entries come from me directly:
- **Meetings.** Book a 30-minute meeting through my Calendly link (https://calendly.com/bencollierphd), any weekday 9 AM to 5:30 PM. My calendar is usually up to date, so if Calendly offers a slot, book it, assume it's on my calendar, and I look forward to seeing you.
- **Rescheduling a presentation.** The TA for each course handles presentation schedule changes. The answer names the TA and their email for the student's course.

**Design sketch** (goes into SPEC.md before it's built):
- An FAQ file per course. Each entry holds example phrasings of the question, my answer word for word, and an optional link button (for example, "Book a 30-minute meeting").
- The file lives with the private course content, not in the public repo, because it contains TA emails.
- In `/api/ask`, after the stored suggested questions and before slide retrieval, the question's embedding (already computed) is compared with the FAQ phrasings. Above an FAQ threshold, the twin answers with that entry:
  - my words, spoken in the AI voice (it's backend text, so the signed-audio rule still holds)
  - shown as a caption with the button, and no slides
- The answer depends on the course filter. With "All courses", it gives the matching entry for each course when they differ.
- **Measured by** the eval categories MISSED_CLASS, MEETING_REQUEST, RESCHEDULE_PRESENTATION and LATE_OR_FAILED_SUBMISSION, before and after.

## Presentation rescheduling by talking to the twin (stretch)

Added October 7, 2026. Today, presentation slots live in a publicly editable Google Sheet that teams edit themselves. Instead, I publish the schedule, and a team that wants to move asks the twin. The twin shows the open slots and moves the team on request.

**What it would need:**
- **The schedule as data.** Courses, dates, slots, and which team holds each slot, in a Supabase table I edit from the Settings page. The twin reads it, so it never has to guess.
- **Knowing who is asking.** The course passcode is shared, so anyone could move any team. Options:
  - a per-team code I hand out with the team list
  - a confirmation link sent to an Andrew email on the team roster (the roster stays on the local build machine and is never stored in the app)
- **Rules.** Only open slots, only before a cutoff (for example, 48 hours ahead), one move per team unless the TA approves, and no swapping with another team without both confirming.
- **A record and a notice.** Every move is logged (team, from, to, when). The TA gets an email or a Settings notification for each change, and either of us can undo a move.
- **What the twin says.** It confirms in plain words ("Your team is now presenting Tuesday, Dec 1, slot 3"). It never promises a slot that isn't open, and it hands anything unusual to the TA.

**Why it's worth it:** no more accidental edits or deletions in a shared sheet, a clear history of who moved when, and students get an answer at any hour.

**Not before:** the FAQ answers above. Those come first, and this builds on the same "answer logistics from my own data" path.

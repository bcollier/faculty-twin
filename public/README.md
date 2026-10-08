# public/: the frontend

*AI-generated documentation (Claude Code, Claude Opus 5.5, October 8, 2026). The source of truth is [docs/SPEC.md](../docs/SPEC.md).*

Plain HTML, CSS and JavaScript, with no framework and no build step. Vercel serves this folder from its CDN at the site root. Nothing here is course content and nothing here holds a key: every slide, clip and audio file arrives through a link the backend signed.

## Files

| File | What it does |
| --- | --- |
| `index.html` | The student page: passcode screen, idle screen (course filter, question box, chips), the stage (slide, code panel, clip, caption, controls, source card) and the chat dock |
| `app.js` | The student page: starts the dev mock (local hosts with `?mock=1` only) and `boot()`, and imports the page's sections from `student/` |
| `student/` | The student page, one module per section: `copy.js` (constants and copy), `helpers.js` (`$`, `el`, the API call, usage events), `state.js` (the page's elements and state), `screens.js` (boot, offline, passcode), `voice-label.js`, `chips.js` (course filter, chips, question forms), `stage.js` (not covered, FAQ, From Canvas, logistics, web answers, helper slides, contacts, errors), `chat.js` (chat log, sources list), `asking.js` (`ask()`, fresh links), `player.js` (the player and **`onClipEnded()`**, first written by hand by Ben: when a narration clip ends it shows the next segment, plays it, preloads the one after, and finishes after the last), `narration.js` (read-along), `controls.js` (buttons, dots, keyboard), `clips.js` (the class clip) |
| `readalong.js`, `helper-slide.js`, `prompt-diff.js` | Logic with no DOM, each with its own node test: read-along timing, drawing a checked helper-slide spec, Settings > Prompts' diff |
| `styles.css` | Styles for both pages, light and dark, in the look of collier.phd (grid paper, index cards, a legal-pad caption strip, highlighter tags, sticky-note follow-ups; tokens and fonts copied from the site's `css/site.css`) |
| `fonts.js` | Switches on the Google Fonts stylesheet without blocking the first paint (the CSP allows no inline `onload`) |
| `handoff.css`, `handoff-text.svg`, `handoff.js` | Arriving from collier.phd: `handoff.css` is the shared HANDOFF FRAME and `handoff-text.svg` its text as Kalam glyph outlines (both byte-identical to `css/handoff.css` and `assets/handoff-text.svg` in the ben.collier.phd repo, and inlined in `index.html`; `tests/test_handoff.py` checks the inline copies); `handoff.js` shows it first when the URL has `?from=collier.phd`, then slides it away and draws the question box's border |
| `admin.html` | The Settings page (admin passcode, never linked from the student page): Model, Voice, Courses and source material, Limits and access (with Answer thresholds), Activity, Prompts, Analytics, Evals |
| `admin.js` | Settings: Model, Voice, Courses and uploads, Limits, thresholds, Activity, Prompts |
| `admin-analytics.js`, `analytics.css` | Settings > Analytics: tiles, spend, tokens, topics, engagement and the price table, drawn as inline SVG with a zero baseline |
| `admin-evals.js` | Settings > Evals: question set, judge calibration, starting and stepping a run, run cards, the report card |
| `dev/mock.js`, `dev/mock-evals.js` | Development only: canned API responses, loaded only on localhost with `?mock=1`. No course content |

## How the player works

The player is a small state machine (`player` in `student/player.js`): the current segment, playing or paused, muted, captions only, finished. Every "segment done" signal, the audio `ended` event or the captions-only timer, calls `onClipEnded()` with the segment it belongs to, so there is one advance path. A late signal is ignored: one from a segment no longer on screen, one after Finish, and one while a class clip plays. Watching a class clip pauses the walkthrough, and the clip ending never advances it. When a signed link expires, the page asks `/api/links` for fresh links to the same slides (once per answer) instead of showing a broken image.

## How it connects

- Calls only `/api/*` on the same origin (the Content-Security-Policy in `vercel.json` allows nothing else except signed Supabase media links, and the Google Fonts stylesheet and font files of the collier.phd look).
- Sends `source` (`chip`, `typed`, `follow_up`) with each question and allowlisted usage events to `/api/event` for Settings > Analytics.

## Commands

```bash
# The page with canned responses and no backend (how docs/QA.md tested it):
python3 -m http.server 8080 --directory public
# then open http://localhost:8080/?mock=1

# The page and the real backend together, the way Vercel runs them:
vercel dev
```

FastAPI itself serves only `/api/*`; on Vercel the static files and the function share one origin.

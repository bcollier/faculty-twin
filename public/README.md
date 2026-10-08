# public/: the frontend

*AI-generated documentation (Claude Code, Claude Opus 5.5, October 8, 2026). The source of truth is [docs/SPEC.md](../docs/SPEC.md).*

Plain HTML, CSS and JavaScript, with no framework and no build step. Vercel serves this folder from its CDN at the site root. Nothing here is course content and nothing here holds a key: every slide, clip and audio file arrives through a link the backend signed.

## Files

| File | What it does |
| --- | --- |
| `index.html` | The student page: passcode screen, idle screen (course filter, question box, chips), the stage (slide, code panel, clip, caption, controls, source card) and the chat dock |
| `app.js` | The student page's logic: login, `ask()`, stage messages (not covered, FAQ, From Canvas, logistics), the source card and sources list, and the player. **`onClipEnded()` is Ben's hand-written code**: it runs when a narration clip ends, shows the next segment, plays it, preloads the one after, and finishes after the last |
| `styles.css` | Styles for both pages, light and dark |
| `admin.html` | The Settings page (admin passcode, never linked from the student page): Model, Voice, Courses and source material, Limits and access (with Answer thresholds), Activity, Prompts, Analytics, Evals |
| `admin.js` | Settings: Model, Voice, Courses and uploads, Limits, thresholds, Activity, Prompts |
| `admin-analytics.js`, `analytics.css` | Settings > Analytics: tiles, spend, tokens, topics, engagement and the price table, drawn as inline SVG with a zero baseline |
| `admin-evals.js` | Settings > Evals: question set, judge calibration, starting and stepping a run, run cards, the report card |
| `dev/mock.js`, `dev/mock-evals.js` | Development only: canned API responses, loaded only on localhost with `?mock=1`. No course content |

## How the player works

The player is a small state machine (`player` in `app.js`): the current segment, playing or paused, muted, captions only, finished. Every "segment done" signal, the audio `ended` event or the captions-only timer, calls `onClipEnded()`, so there is one advance path. Watching a class clip pauses the walkthrough, and the clip ending never advances it. When a signed link expires, the page asks the same question again for fresh links instead of showing a broken image.

## How it connects

- Calls only `/api/*` on the same origin (the Content-Security-Policy in `vercel.json` allows nothing else except signed Supabase media links).
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

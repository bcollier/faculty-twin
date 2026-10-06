# Browser QA

## October 5, 2026: WebKit and Firefox pass (mock mode)

The headless Chromium pass from PR #5 covered 1440, 390 and 360 px. This pass adds **WebKit (Safari's engine) and Firefox**. It ran with Playwright against `public/` served locally with `?mock=1`, so no real backend or course content was involved. A pass on a real iPhone is still owed (spec, Block 9) and needs Ben's phone.

**Matrix:**
- 2 browsers (WebKit and Firefox)
- 2 widths: phone 390 x 844 and desktop 1440 x 900
- light mode, with every scenario: a covered answer (including stepping to the class clip), not covered, server unreachable, rate limited, retrieval not finished, cookie expired, no audio, audio failing, fallback voice, clip failing, expired slide links, and server offline at boot
- dark mode, with the covered and not-covered scenarios

56 runs in all.

| Check | Result |
| --- | --- |
| Every state reaches a screen (no blank stage) | Pass, 56 of 56 |
| Readable messages, no raw errors (`undefined`, `[object`, stack traces) | Pass |
| No horizontal scrolling at 390 px | Pass |
| Expired cookie returns to the passcode screen | Pass |
| Expired slide links: the page asks again and recovers | Pass (slides shown 3.5 to 6.5 s after asking) |
| Audio failing mid-answer: captions continue | Pass |
| Script errors | None. The only console errors are the 404s the bad-audio, bad-clip and stale-link scenarios cause on purpose. |
| Tap targets at least 44 px | **Fixed in this PR.** WebKit drew the side panel's course picker (`#dock-course`) 23 px tall, because Safari ignores `min-height` on a native `<select>`. Every select now uses `appearance: none` with a drawn chevron, and measures 44 px in WebKit, Firefox and Chromium, light and dark. |

Still owed: a real iPhone in Safari (including the audio unlock on the first tap, which a headless browser can't show), and someone who hasn't seen the app trying three questions.

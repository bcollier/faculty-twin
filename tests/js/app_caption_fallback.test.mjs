// Student page: when the voice fails and the page falls back to captions only, every segment's audio
// element is paused and its source dropped (releaseMedia), so nothing keeps downloading in the background.
import assert from 'node:assert/strict';
import { loadPage, makeBrowser, reply, tick } from './fakedom.mjs';

const seg = (n) => ({
  n, slide_id: `70445-s01-00${n}`, course: '70445', course_title: 'Fake Course A', session: 1, session_title: 'Fruit',
  date: '2026-09-01', slide_number: n, image: `/img/${n}.webp`, narration: 'A short fake narration.',
  audio: `/api/audio?t=a${n}&v=v&s=s`, voice: { kind: 'free', label: 'AI voice.' },
  audio_fallback: null, voice_fallback: null, code: null, clip: null, timings: null, timings_fallback: null, boxes: null,
});
const fetch = async (path) => {
  if (path === '/api/topics') return reply(200, []);
  if (path === '/api/voice') return reply(200, { kind: 'free', label: 'AI voice.', fallback: null });
  if (path === '/api/ask') return reply(200, { question: 'q', covered: true, segments: [seg(1), seg(2)], sources: [], follow_ups: [] });
  return reply(200, {});
};

const page = await loadPage('public/app.js', makeBrowser({ fetch }), ['ask', 'player', 'fallBackToCaptions']);
for (let i = 0; i < 5; i++) await tick();
await page.ask('What is a fruit?');
for (let i = 0; i < 5; i++) await tick();

const audio = [...page.player.audio.values()];
assert.ok(audio.length >= 1, 'the narration audio was created');
assert.ok(audio.every(a => a.src), 'and has a source before the fallback');
page.fallBackToCaptions();
assert.equal(page.player.captionsOnly, true, 'captions only from here on');
assert.equal(page.player.audio.size, 0, 'no audio elements are kept');
assert.ok(audio.every(a => !a.src), 'every old element had its source dropped, not just paused');
console.log('ok app_caption_fallback');

// Student page: a slide image whose signed link expired (Oct 8 code review).
// Before the fix the page re-asked the whole question: a second model call and question-log row, a
// different answer if the course filter had changed, and a 429 could replace the paused walkthrough.
// Now it asks /api/links for fresh links to the same slides and keeps playing the same answer.
import assert from 'node:assert/strict';
import { loadPage, makeBrowser, reply, tick } from './fakedom.mjs';

const seg = (n, id) => ({
  n, slide_id: id, course: '70445', course_title: 'Fake Course A', session: 1, session_title: 'Fruit basics',
  date: '2026-09-01', slide_number: n + 1, image: `/old/${id}.webp`, narration: `Narration ${n}.`,
  audio: `/api/audio?t=x${n}&v=v&s=s`, voice: { kind: 'free', label: 'AI voice.' }, code: null,
  clip: n === 1 ? { url: `/old/${id}.mp4`, start: 1, end: 20 } : null,
});
const answer = {
  question: 'What is an apple?', covered: true,
  segments: [seg(1, '70445-s01-002'), seg(2, '70445-s01-003')],
  sources: [{ slide_id: '70445-s01-002', course: '70445', session: 1, date: '2026-09-01', slide_number: 2, image: '/old/70445-s01-002.webp' }],
  follow_ups: [],
};
const calls = [];
const fetch = async (path, opts = {}) => {
  calls.push({ path, body: opts.body ? JSON.parse(opts.body) : null });
  if (path === '/api/topics') return reply(200, []);
  if (path === '/api/voice') return reply(200, { kind: 'free', label: 'AI voice.', fallback: null });
  if (path === '/api/ask') return reply(200, structuredClone(answer));
  if (path === '/api/links') {
    return reply(200, { links: {
      '70445-s01-002': { image: '/new/70445-s01-002.webp', clip: { url: '/new/70445-s01-002.mp4', start: 1, end: 20 } },
      '70445-s01-003': { image: '/new/70445-s01-003.webp', clip: null },
    } });
  }
  return reply(200, {});
};
const browser = makeBrowser({ fetch });
const page = await loadPage('public/app.js', browser, ['ask', 'player']);
for (let i = 0; i < 5; i++) await tick(); // boot: health, topics, voice

await page.ask('What is an apple?');
const img = browser.element('#slide-img');
assert.equal(img.src, '/old/70445-s01-002.webp');

await img.fire('error'); // the hour-old link no longer loads
for (let i = 0; i < 5; i++) await tick();

const asks = calls.filter(c => c.path === '/api/ask');
const refresh = calls.filter(c => c.path === '/api/links');
assert.equal(asks.length, 1, 'the question is not asked again');
assert.equal(refresh.length, 1, 'fresh links are fetched once');
assert.deepEqual(refresh[0].body, { slide_ids: ['70445-s01-002', '70445-s01-003'] });
assert.equal(img.src, '/new/70445-s01-002.webp', 'the slide on screen reloads with the fresh link');
assert.equal(page.player.segments[1].image, '/new/70445-s01-003.webp');
assert.equal(page.player.segments[0].clip.url, '/new/70445-s01-002.mp4');
assert.equal(page.player.segments[0].narration, 'Narration 1.', 'the same answer keeps playing');

await img.fire('error'); // still failing: do not loop
for (let i = 0; i < 5; i++) await tick();
assert.equal(calls.filter(c => c.path === '/api/links').length, 1, 'only one refresh per answer');
console.log('ok');
process.exit(0);

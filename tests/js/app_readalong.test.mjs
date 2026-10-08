// Student page read-along (docs/SPEC.md, "Read-along narration and slide spotlight"): the narration box
// follows the voice word by word, starts on the syllable estimate and switches to the real timings when
// they arrive (after a "pending" reply, as live audio gives), tells screen readers once per sentence,
// lights up the slide words the narrator says, and in captions only follows the caption timer.
import assert from 'node:assert/strict';
import { loadPage, makeBrowser, reply, tick } from './fakedom.mjs';

const NARRATION = 'Gradient descent moves downhill. Then the learning rate sets the step size.';
const box = (t, x, y, line) => [t, x, y, x + 0.08, y + 0.05, line];
const BOXES = { v: 1, src: 'pdf', words: [box('Gradient', 0.1, 0.1, 0), box('Descent', 0.2, 0.1, 0),
  box('Learning', 0.1, 0.4, 1), box('rate', 0.2, 0.4, 1)] };
// "Gradient" at 0 s, "descent" at 0.5 s ... one word every half second.
const WORDS = [...NARRATION.matchAll(/\S+/g)].map((m, k) => [k * 0.5, m.index]);

const seg = (n, extra = {}) => ({
  n, slide_id: `70445-s01-00${n}`, course: '70445', course_title: 'Fake Course A', session: 1, session_title: 'Fruit',
  date: '2026-09-01', slide_number: n, image: `/img/${n}.webp`, narration: NARRATION,
  audio: `/api/audio?t=a${n}&v=v&s=s`, voice: { kind: 'free', label: 'AI voice (a stock voice, not mine).' },
  audio_fallback: null, voice_fallback: null, code: null, clip: null,
  timings: `/api/audio/timings?t=a${n}&v=v&s=s`, timings_fallback: null, boxes: `/boxes/${n}.json`, ...extra,
});

function setup({ answer, reduce = false }) {
  const calls = [];
  let timingsAsked = 0;
  const fetch = async (path) => {
    calls.push(path);
    if (path === '/api/topics') return reply(200, []);
    if (path === '/api/voice') return reply(200, { kind: 'free', label: 'AI voice (a stock voice, not mine).', fallback: null });
    if (path === '/api/ask') return reply(200, structuredClone(answer));
    if (path.startsWith('/api/audio/timings')) {
      timingsAsked += 1;
      return reply(200, timingsAsked === 1 ? { words: null, source: 'pending' } : { words: WORDS, source: 'edge' });
    }
    if (path.startsWith('/boxes/')) return reply(200, BOXES);
    return reply(200, {});
  };
  const browser = makeBrowser({ fetch });
  browser.globals.matchMedia = (q) => ({ matches: reduce && /reduce/.test(q), addEventListener() {} });
  return { browser, calls };
}

const expose = ['ask', 'player', 'reading', 'followNarration'];
const nowWord = (page) => page.reading.spans.findIndex(s => s.classList.contains('now'));

// ---------------------------------------------------------------- with audio
{
  const { browser, calls } = setup({ answer: { question: 'q', covered: true, segments: [seg(1), seg(2)], sources: [], follow_ups: [] } });
  const page = await loadPage('public/app.js', browser, expose);
  for (let i = 0; i < 5; i++) await tick();
  await page.ask('How does gradient descent work?');
  for (let i = 0; i < 5; i++) await tick();
  const { player, reading } = page;

  assert.equal(reading.spans.length, 12, 'one span per narration word');
  assert.equal(browser.element('#narration-voice').textContent, 'AI voice (a stock voice, not mine).');
  assert.ok(calls.includes('/api/audio/timings?t=a1&v=v&s=s'), 'the timings for the voice speaking are fetched');
  assert.ok(calls.includes('/boxes/1.json'), 'and the slide word boxes');
  assert.equal(reading.words, null, 'live timings still pending: the page runs on the estimate');
  assert.equal(reading.plan.size, 2, 'two slide phrases to light up');
  assert.deepEqual([...reading.plan.values()].map(m => m.words), ['Gradient Descent', 'Learning rate']);

  // The estimate: 6 seconds of audio, 2.5 s in is in the first sentence's last words.
  const audio = player.current;
  audio.duration = 6;
  audio.currentTime = 0.1;
  const img = browser.element('#slide-img');
  img.clientHeight = 360; img.naturalWidth = 1600; img.naturalHeight = 900; img.offsetLeft = 0; img.offsetTop = 0;
  await img.fire('load');
  page.followNarration();
  assert.equal(nowWord(page), 0);
  assert.equal(reading.marks.length, 1, '"Gradient descent" lights up on the slide as it is said');
  assert.equal(browser.element('#narration-live').textContent, 'Gradient descent moves downhill.', 'screen readers get the sentence');

  // The real timings arrive on the retry (live audio saves them when the stream ends).
  await new Promise(r => setTimeout(r, 1400));
  for (let i = 0; i < 5; i++) await tick();
  assert.deepEqual(reading.words, WORDS, 'real timings replace the estimate');
  audio.currentTime = 2.6; // word 5: "the" (0-based), in the second sentence
  page.followNarration();
  assert.equal(nowWord(page), 5);
  assert.ok(reading.spans.slice(0, 5).every(s => s.classList.contains('said')), 'words before it are said');
  assert.ok(!reading.spans[6].classList.contains('said'), 'words after it are not');
  assert.equal(browser.element('#narration-live').textContent, 'Then the learning rate sets the step size.');
  audio.currentTime = 3.1; // "learning": the second phrase lights up
  page.followNarration();
  assert.equal(reading.marks.length, 2);
  const before = reading.marks.length;
  audio.currentTime = 3.6; // "rate" is part of the phrase already lit: nothing more
  page.followNarration();
  assert.equal(reading.marks.length, before);

  // Seeking back moves the box back.
  audio.currentTime = 0.6;
  page.followNarration();
  assert.equal(nowWord(page), 1);
  assert.ok(!reading.spans[4].classList.contains('said'));

  // The spotlight follows play and pause.
  const frame = browser.element('#slide-frame');
  assert.ok(frame.classList.contains('is-speaking'));
  await browser.element('#btn-play').fire('click');
  assert.ok(!frame.classList.contains('is-speaking'), 'paused: the slide sits back');
  await browser.element('#btn-play').fire('click');
  assert.ok(frame.classList.contains('is-speaking'));

  // Next slide: a fresh box, the old marks are gone.
  await browser.element('#btn-next').fire('click');
  for (let i = 0; i < 5; i++) await tick();
  assert.equal(player.index, 1);
  assert.ok(reading.current <= 0, 'the new narration starts at its first word');
  assert.ok(!reading.spans.some(sp => sp.classList.contains('said')));
  assert.equal(reading.marks.length, 0);
  assert.ok(calls.includes('/boxes/2.json'));
}

// ---------------------------------------------------------------- captions only, reduced motion
{
  const s = seg(1, { audio: null, voice: null, timings: null });
  const { browser, calls } = setup({ answer: { question: 'q', covered: true, segments: [s], sources: [], follow_ups: [] }, reduce: true });
  const page = await loadPage('public/app.js', browser, expose);
  for (let i = 0; i < 5; i++) await tick();
  await page.ask('How does gradient descent work?');
  for (let i = 0; i < 5; i++) await tick();
  const { player, reading } = page;
  assert.ok(player.captionsOnly);
  assert.equal(browser.element('#narration-voice').textContent, 'Captions only');
  assert.ok(!calls.some(c => c.startsWith('/api/audio/timings')), 'nothing to fetch without audio');
  assert.ok(player.timer, 'the caption timer paces the segment');
  // Half way through the timer: the estimate puts the box in the second sentence.
  player.timer.remainingMs = player.timer.totalMs / 2;
  player.timer.startedAt = Date.now();
  page.followNarration();
  const i = nowWord(page);
  assert.ok(i >= 4 && i <= 8, `half way is mid-narration (word ${i})`);
  assert.ok(browser.element('#caption').classList.contains('reduce'), 'reduced motion: sentence highlight');
  const inSentence = reading.spans.filter(sp => sp.classList.contains('in-sentence')).length;
  assert.equal(inSentence, 8, 'the whole current sentence is marked');
}

console.log('ok');
process.exit(0);

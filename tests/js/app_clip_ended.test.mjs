// Student page: onClipEnded(), the one place the walkthrough advances on its own. A segment's `ended`
// signal advances to the next segment, and after the last one finishes the answer. A late signal is
// ignored: one from a segment no longer on screen, one after Finish, and one while the class clip plays.
import assert from 'node:assert/strict';
import { loadPage, makeBrowser, reply, tick } from './fakedom.mjs';

const seg = (n, clip = null) => ({
  n, slide_id: `70445-s01-00${n}`, course: '70445', course_title: 'Fake Course A', session: 1, session_title: 'Fruit',
  date: '2026-09-01', slide_number: n, image: `/img/${n}.webp`, narration: 'A short fake narration.',
  audio: `/api/audio?t=a${n}&v=v&s=s`, voice: { kind: 'free', label: 'AI voice.' },
  audio_fallback: null, voice_fallback: null, code: null, clip, timings: null, timings_fallback: null, boxes: null,
});
const answer = {
  question: 'q', covered: true, sources: [], follow_ups: ['What is a pear?'],
  segments: [seg(1), seg(2, { url: '/clip/2.mp4', start: 1, end: 20 }), seg(3)],
};
const events = [];
const fetch = async (path, opts = {}) => {
  if (path === '/api/topics') return reply(200, []);
  if (path === '/api/voice') return reply(200, { kind: 'free', label: 'AI voice.', fallback: null });
  if (path === '/api/ask') return reply(200, structuredClone(answer));
  if (path === '/api/event') events.push(JSON.parse(opts.body).name);
  return reply(200, {});
};
const settle = async () => { for (let i = 0; i < 5; i++) await tick(); };

const page = await loadPage('public/app.js', makeBrowser({ fetch }),
  ['ask', 'player', 'onClipEnded', 'enterClip', 'resumePlayback']);
await settle();
await page.ask('What is a fruit?');
await settle();
const { player } = page;
const audioOf = (i) => player.audio.get(i);
const completed = () => events.filter(e => e === 'walkthrough_completed').length;

// The current segment's `ended` advances to the next one.
assert.equal(player.index, 0);
assert.equal(player.playing, true);
const first = audioOf(0);
await first.fire('ended');
assert.equal(player.index, 1, 'segment 1 ended: on to segment 2');

// A late `ended` from segment 1's audio, or a signal naming a segment no longer on screen, is ignored.
await first.fire('ended');
page.onClipEnded(0);
assert.equal(player.index, 1, 'a late signal from segment 1 does not skip segment 2');

// While the class clip plays, a late `ended` from the narration does not move the walkthrough.
const second = audioOf(1);
page.enterClip();
assert.equal(player.inClip, true);
await second.fire('ended');
player.playing = true; // even if something set playing again, the clip keeps the walkthrough where it is
page.onClipEnded();
assert.equal(player.index, 1, 'the clip keeps the walkthrough on its slide');
assert.equal(player.inClip, true, 'and the clip keeps playing');
player.playing = false;

// Play again leaves the clip; the next `ended` advances, and the last one finishes the answer.
page.resumePlayback();
assert.equal(player.inClip, false);
await audioOf(1).fire('ended');
assert.equal(player.index, 2);
const last = audioOf(2);
await last.fire('ended');
assert.equal(player.finished, true, 'the last segment ended: the answer is finished');
assert.equal(completed(), 1);

// After Finish, a late `ended` (or a stray call) changes nothing.
await last.fire('ended');
player.playing = true;
page.onClipEnded();
player.playing = false;
assert.equal(player.index, 2, 'still on the last segment');
assert.equal(player.finished, true, 'still finished');
assert.equal(completed(), 1, 'walkthrough_completed is not sent twice');
console.log('ok app_clip_ended');
process.exit(0);

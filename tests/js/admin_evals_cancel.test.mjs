// Settings > Evals: cancelling a run while a step request is in flight (Oct 8 code review).
// Before the fix, the step's reply wrote to E.active after Cancel had set it to null: drive() threw,
// E.driving stayed true, and every later Start or Resume returned at once until a reload.
import assert from 'node:assert/strict';
import { deferred, loadPage, makeBrowser, reply, tick } from './fakedom.mjs';

let stepReply = deferred();
const steps = [];
const fetch = async (path, opts = {}) => {
  if (path.endsWith('/step')) { steps.push(path); return stepReply.promise; }
  if (path.endsWith('/cancel')) return reply(200, { progress: { done: 0, total: 4, finished: true, status: 'cancelled' } });
  return reply(200, { runs: [], series: [], calibration: [] });
};
const browser = makeBrowser({ fetch });
browser.element('#a-app').hidden = true; // signed out: the page does not start loading on its own
const page = await loadPage('public/admin-evals.js', browser, ['E', 'drive']);

page.E.active = { id: '20261008T000000Z', name: 'Test run', progress: { done: 0, total: 4 } };
const driving = page.drive();
await tick();
assert.equal(page.E.driving, true);

await browser.element('#ev-cancel').fire('click');
assert.equal(page.E.active, null);

stepReply.resolve(reply(200, { progress: { done: 1, total: 4, finished: false }, row: null }));
await driving; // must not throw
assert.equal(page.E.driving, false, 'the page can start or resume another run');
assert.equal(steps.length, 1, 'no further steps after Cancel');

// The next run is driven (before the fix drive() returned at once because E.driving was stuck).
stepReply = deferred();
page.E.active = { id: '20261008T000001Z', name: 'Next run', progress: { done: 0, total: 1 } };
const next = page.drive();
await tick();
assert.equal(steps.length, 2, 'the next run sends its first step');
stepReply.resolve(reply(200, { progress: { done: 1, total: 1, finished: true, status: 'done' }, row: null }));
await next;
assert.equal(page.E.driving, false);
console.log('ok');

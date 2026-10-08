// Settings > Analytics: changing the range while a load is running (Oct 8 code review).
// Before the fix, load() returned at once while a load was in flight, so the 90-day click was dropped
// and the 30-day numbers stayed on screen under a 90-day selection.
import assert from 'node:assert/strict';
import { deferred, loadPage, makeBrowser, reply, tick } from './fakedom.mjs';

const pending = [];
const asked = [];
const fetch = async (path) => {
  if (path.startsWith('/api/admin/analytics?')) {
    asked.push(path);
    const d = deferred();
    pending.push(d);
    return d.promise;
  }
  return reply(200, {});
};
const browser = makeBrowser({ fetch });
const page = await loadPage('public/admin-analytics.js', browser, ['A', 'load']);

const first = page.load();
await tick();
assert.equal(asked.length, 1);
assert.match(asked[0], /days=30/);

page.A.days = 90; // what the 90-day button does before calling load()
const second = page.load();
await tick();

pending[0].resolve(reply(500, { detail: 'slow 30-day reply' }));
await first;
await second;
for (let i = 0; i < 5 && asked.length < 2; i++) await tick();
assert.equal(asked.length, 2, 'the newer selection is loaded once the first load finishes');
assert.match(asked[1], /days=90/);
pending[1].resolve(reply(500, { detail: 'done' }));
for (let i = 0; i < 5; i++) await tick();
assert.equal(page.A.loading, false);
assert.equal(asked.length, 2, 'no extra loads');
console.log('ok');

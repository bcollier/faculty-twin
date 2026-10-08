// Settings > 4. Limits, web answers, the passcode, and 4a. open access (no passcode until a set time).
// Part of the Settings page; admin.js has the map.

import { $, api, detail, el, fmtNum, humanize, keepSettings, say, sayError } from './helpers.js';
import { S } from './state.js';
import { freeVoiceOptions } from './voice.js';

/* ---------------- 4. limits and access ---------------- */

/** A <dl> of name and number pairs, or one `emptyName` row when there are none. */
export function renderKv(node, values, emptyName) {
  const entries = Object.entries(values);
  node.replaceChildren(...(entries.length ? entries : [[emptyName, '']]).map(([k, v]) =>
    el('div', {}, el('dt', { text: humanize(k) }), el('dd', { text: fmtNum(v) }))));
}

/** The caps the server enforces, as it reports them. */
export function renderLimits() {
  renderKv($('#limits-kv'), S.status?.limits || {}, 'not reported');
}

$('#cap-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const v = Number($('#cap').value);
  const fv = Number($('#free-cap').value);
  const st = $('#cap-status');
  if (!Number.isInteger(v) || v < 0 || !Number.isInteger(fv) || fv < 0) { say(st, 'Use whole numbers, 0 or more.', 'err'); return; }
  try {
    const r = await api('/api/admin/settings', { method: 'PUT', body: { daily_voice_char_cap: v, daily_free_voice_char_cap: fv } });
    if (!r.ok) { say(st, detail(r), 'err'); return; }
    keepSettings(r.data || {});
    const eleven = v === 0 ? 'ElevenLabs is off' : `ElevenLabs ${fmtNum(v)}`;
    const free = fv === 0 ? 'free voices off' : `free voices ${fmtNum(fv)}`;
    say(st, `Saved. ${eleven}, ${free} characters a day.`, 'ok');
  } catch (ex) { sayError(st, ex); }
});

/** Web answers (beyond my slides): on/off, the daily cap, and an optional free voice. Never the clone. */
export function renderWebAnswers() {
  const s = S.settings || {};
  $('#web-on').checked = s.web_answers_enabled !== false;
  $('#web-cap').value = s.daily_web_answer_cap ?? '';
  const today = s.web_answers_today ?? S.status?.today?.web_answers;
  $('#web-cap-hint').textContent = `Web answers per day across all students (DAILY_WEB_ANSWER_CAP, default 200). Each one costs a few cents in searches and tokens. Past the cap, those questions get the usual "not covered" reply. Zero turns them off.${today != null ? ` Today so far: ${fmtNum(today)}.` : ''}`;
  const chosen = s.web_answer_voice || 'none';
  const off = el('option', { value: 'none', selected: chosen === 'none' }, 'Off (text only)');
  $('#web-voice').replaceChildren(off, ...freeVoiceOptions(chosen, chosen !== 'none' ? chosen.replace(/^edge:/, '') : null));
}

$('#web-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const st = $('#web-status');
  const cap = Number($('#web-cap').value);
  if (!Number.isInteger(cap) || cap < 0 || cap > 100000) { say(st, 'Use a whole number from 0 to 100,000.', 'err'); return; }
  const body = { web_answers_enabled: $('#web-on').checked, daily_web_answer_cap: cap, web_answer_voice: $('#web-voice').value || 'none' };
  try {
    const r = await api('/api/admin/settings', { method: 'PUT', body });
    if (!r.ok) { say(st, detail(r), 'err'); return; }
    keepSettings(r.data || {});
    renderWebAnswers();
    const on = body.web_answers_enabled && cap > 0;
    const spoken = body.web_answer_voice === 'none' ? 'text only' : `read by ${body.web_answer_voice.replace(/^edge:/, '')} (a stock voice)`;
    say(st, on ? `Saved. Web answers are on, up to ${fmtNum(cap)} a day, ${spoken}.` : 'Saved. Web answers are off.', 'ok');
  } catch (ex) { sayError(st, ex); }
});

$('#pass-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const a = $('#pass-new').value, b = $('#pass-confirm').value;
  const st = $('#pass-status');
  if (a.length < 6) { say(st, 'Use at least 6 characters.', 'err'); return; }
  if (a !== b) { say(st, 'The two entries don\'t match.', 'err'); return; }
  try {
    const r = await api('/api/admin/settings', { method: 'PUT', body: { student_passcode: a } });
    if (!r.ok) { say(st, detail(r), 'err'); return; }
    e.target.reset();
    say(st, 'Passcode changed. Share the new one with the class.', 'ok');
  } catch (ex) { sayError(st, ex); }
});

/* ---------------- 4a. open access (no passcode until a set time) ---------------- */

/** Whether the student page is open to everyone, and until when (in this browser's time). */
export function renderOpenAccess() {
  const until = S.settings.open_access_until;
  const open = typeof until === 'number' && until * 1000 > Date.now();
  $('#open-now').textContent = open
    ? `Open to everyone until ${new Date(until * 1000).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' })}. No passcode needed.`
    : 'Closed: students need the passcode.';
  $('#open-close').disabled = !open;
}

/** Save the open-until time (epoch seconds; 0 closes now) and say what changed. */
async function saveOpenAccess(until, done) {
  const st = $('#open-status');
  try {
    const r = await api('/api/admin/settings', { method: 'PUT', body: { open_access_until: until } });
    if (!r.ok) { say(st, detail(r), 'err'); return; }
    keepSettings(r.data || {});
    renderOpenAccess();
    say(st, done, 'ok');
  } catch (ex) { sayError(st, ex); }
}

$('#open-midnight').addEventListener('click', () => {
  const midnight = new Date();
  midnight.setHours(24, 0, 0, 0);
  saveOpenAccess(Math.floor(midnight.getTime() / 1000), 'Saved. Anyone with the link can use the student page until midnight.');
});
$('#open-close').addEventListener('click', () => saveOpenAccess(0, 'Closed. Students need the passcode again.'));

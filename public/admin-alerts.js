// Faculty Twin: Settings > Student alerts. Loaded by admin.html next to admin.js; talks only to
// /api/admin/alerts* with the ft_admin cookie (admin.js handles sign-in and tells us with ft-admin-enter).
// The server never sends a Twilio token or the full phone number: the destination is its last 4 digits.

const $ = (s, r = document) => r.querySelector(s);
/** Build an element: `class`, `text`, `on<event>` listeners, other keys as attributes. Never takes HTML. */
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k === 'text') n.textContent = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids) if (kid != null) n.append(kid);
  return n;
};
/** "70-445" for "70445"; anything else is "unclear" (the classifier could not tell). */
const courseCode = (c) => (/^\d{5}$/.test(String(c)) ? `${String(c).slice(0, 2)}-${String(c).slice(2)}` : 'unclear');
/** Date and time in Eastern time (when the texts went out, as Ben reads them). */
const fmtWhen = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
};
const TYPE_TEXT = { api_credits: 'API key', submission: 'Submission', quiz: 'Quiz', other_course_tech: 'Course tech', test: 'Test text' };
const STATUS = {
  sent: { text: 'Texted', cls: 'ok', title: 'Twilio accepted the text.' },
  not_configured: { text: 'Stored, not texted', cls: 'warn', title: 'Twilio is not set up yet, so it waits here.' },
  failed: { text: 'Text failed', cls: 'err', title: 'Twilio refused or did not answer. The error code is in the tooltip.' },
  over_cap: { text: 'Over daily cap', cls: 'off', title: 'Past today\'s text cap: stored here, not texted.' },
};

/** fetch wrapper that never throws: no connection comes back as status 0 with a message. */
async function call(path, { method = 'GET', body } = {}) {
  let res;
  try {
    res = await fetch(path, {
      method, credentials: 'same-origin',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch { return { ok: false, status: 0, data: { detail: 'Can\'t reach the server.' } }; }
  let data = null;
  try { data = await res.json(); } catch { /* none */ }
  return { ok: res.ok, status: res.status, data };
}
/** The server's error message, else `fallback`. */
const detail = (r, fallback) => (typeof r?.data?.detail === 'string' ? r.data.detail : fallback || `Request failed (HTTP ${r?.status}).`);
/** Write a status line; `kind` is '', 'ok', 'warn' or 'err'. */
function say(node, text, kind = '') { node.textContent = text || ''; node.className = `status-line ${kind}`.trim(); }

let state = null;

/** Whether the text went out, with Twilio's error code in the tooltip when it failed. */
function statusPill(a) {
  const s = STATUS[a.status] || { text: a.status || 'unknown', cls: 'off', title: '' };
  const t = a.twilio || {};
  const extra = a.status === 'failed' ? ` Code ${t.error_code ?? 'none'}: ${t.error || 'no detail'}` : (a.status === 'not_configured' && t.error ? ` ${t.error}` : '');
  return el('span', { class: `pill ${s.cls}`, text: s.text, title: `${s.title}${extra}`.trim() });
}

/** The switch, the cap, whether Twilio is ready, and every alert. */
function render() {
  const d = state;
  $('#al-enabled').checked = !!d.enabled;
  $('#al-cap').value = d.daily_cap;
  const set = Object.values(d.configured || {}).filter(Boolean).length;
  const rows = [
    ['Destination', d.to_masked || 'not set (ALERT_TO_PHONE)'],
    ['Sender', d.from_kind === 'messaging_service' ? 'Messaging Service' : d.from_kind === 'number' ? 'Twilio number' : 'not set (TWILIO_FROM)'],
    ['Twilio variables set', `${set} of ${Object.keys(d.configured || {}).length}`],
    ['Ready to text', d.ready ? 'yes' : 'no'],
    ['Texts today', `${d.sent_today} of ${d.daily_cap}`],
    ['Test texts today', `${d.tests_today} of ${d.max_tests_per_day}`],
  ];
  $('#al-kv').replaceChildren(...rows.map(([k, v]) => el('div', {}, el('dt', { text: k }), el('dd', { text: String(v) }))));
  if (!d.ready && (d.problems || []).length) say($('#al-test-status'), `Not ready: ${d.problems.join('; ')}.`, 'warn');
  $('#al-test').disabled = !d.ready;
  const list = d.alerts || [];
  $('#al-body').replaceChildren(...(list.length ? list.map(a => el('tr', {},
    el('td', { 'data-label': 'When (ET)', text: fmtWhen(a.at) }),
    el('td', { 'data-label': 'Course', text: a.kind === 'test' ? '' : courseCode(a.course) }),
    el('td', { 'data-label': 'Type', text: TYPE_TEXT[a.type] || a.type || '' }),
    el('td', { 'data-label': 'Item' }, a.item_url && /^https:\/\//.test(a.item_url)
      ? el('a', { href: a.item_url, target: '_blank', rel: 'noopener', text: a.item || 'Canvas' })
      : (a.item || '')),
    el('td', { 'data-label': 'Reports', class: 'num', text: String((a.reports || 1) + (a.repeats || 0)), title: a.repeats ? `${a.reports || 1} in the text, ${a.repeats} more within 2 hours (counted into the next text)` : '' }),
    el('td', { 'data-label': 'Delivery' }, statusPill(a)),
    el('td', { 'data-label': 'Student said', class: 'small', text: a.quote || (a.kind === 'test' ? 'Test text from Settings' : '') }),
  )) : [el('tr', {}, el('td', { colspan: '7', class: 'muted', text: d.error || 'No alerts yet.' }))]));
}

/** Fetch the alert settings and the alerts. */
async function load() {
  const r = await call('/api/admin/alerts');
  if (!r.ok) { say($('#al-status'), detail(r, 'Couldn\'t load the alerts.'), 'err'); return; }
  state = r.data;
  render();
}

/** Save the on/off switch and the daily text cap. */
async function save(e) {
  e.preventDefault();
  const cap = Number($('#al-cap').value);
  if (!Number.isInteger(cap) || cap < 0 || cap > (state?.max_daily_cap ?? 50)) {
    say($('#al-status'), `Texts per day must be a whole number from 0 to ${state?.max_daily_cap ?? 50}.`, 'err');
    return;
  }
  const r = await call('/api/admin/alerts', { method: 'PUT', body: { enabled: $('#al-enabled').checked, daily_cap: cap } });
  if (!r.ok) { say($('#al-status'), detail(r), 'err'); return; }
  state = r.data;
  render();
  say($('#al-status'), 'Saved.', 'ok');
}

/** Send one test text to the configured phone. */
async function sendTest() {
  const btn = $('#al-test');
  btn.disabled = true;
  say($('#al-test-status'), 'Sending...');
  const r = await call('/api/admin/alerts/test', { method: 'POST' });
  say($('#al-test-status'), r.ok ? `Sent to ${state?.to_masked || 'your phone'}. Twilio status: ${r.data?.alert?.twilio?.status || 'queued'}.` : detail(r), r.ok ? 'ok' : 'err');
  await load();
}

$('#al-form').addEventListener('submit', save);
$('#al-test').addEventListener('click', sendTest);
$('#al-refresh').addEventListener('click', load);
document.addEventListener('ft-admin-enter', load);

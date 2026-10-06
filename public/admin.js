// Faculty Twin: Settings page (admin). Not linked from the student page.
// Talks only to /api/admin/* with the ft_admin cookie. Keys never reach the browser.

const DEV_HOSTS = ['localhost', '127.0.0.1', '[::1]'];
if (DEV_HOSTS.includes(location.hostname) && new URLSearchParams(location.search).get('mock') === '1') {
  // Development only, and only on a local host: on the live site a crafted ?mock=1 link
  // would otherwise show fake "saved" results while nothing is saved.
  await import('./dev/mock.js');
}

const $ = (s, r = document) => r.querySelector(s);
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
const asList = (d, ...keys) => {
  if (Array.isArray(d)) return d;
  for (const k of keys) if (Array.isArray(d?.[k])) return d[k];
  return [];
};
const pad2 = (n) => String(n).padStart(2, '0');
const courseCode = (c) => (/^\d{5}$/.test(String(c)) ? `${String(c).slice(0, 2)}-${String(c).slice(2)}` : String(c ?? ''));
const fmtWhen = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
};
const fmtNum = (n) => (typeof n === 'number' ? n.toLocaleString('en-US') : String(n ?? ''));
const humanize = (k) => String(k).replace(/_/g, ' ').replace(/\b(api|id)\b/gi, s => s.toUpperCase()).replace(/^./, c => c.toUpperCase());

class NetworkError extends Error {}
class AuthError extends Error {}

async function api(path, { method = 'GET', body, timeout = 30000 } = {}) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeout);
  let res;
  try {
    res = await fetch(path, {
      method, credentials: 'same-origin', signal: ctrl.signal,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (e) { throw new NetworkError(e?.message); } finally { clearTimeout(t); }
  let data = null;
  try { data = await res.json(); } catch { /* no body */ }
  if (res.status === 401 && !path.endsWith('/login')) { showLogin('Your admin session ran out. Sign in again.'); throw new AuthError(); }
  return { status: res.status, ok: res.ok, data };
}
const detail = (r, fallback) => {
  const d = r?.data?.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d) && d[0]?.msg) return d.map(x => x.msg).join('; ');
  return fallback || `Request failed (HTTP ${r?.status}).`;
};
function say(node, text, kind = '') {
  node.textContent = text || '';
  node.className = `status-line ${kind}`.trim();
}
const errText = (e) => (e instanceof NetworkError ? 'Can\'t reach the server.' : (e?.message || 'Something went wrong.'));

/* ---------------- screens ---------------- */

const screens = { boot: $('#a-boot'), offline: $('#a-offline'), login: $('#a-login'), app: $('#a-app') };
function show(name) { for (const [k, n] of Object.entries(screens)) n.hidden = k !== name; }

function showLogin(note) {
  show('login');
  $('#a-login-note').hidden = !note;
  $('#a-login-note').textContent = note || '';
  $('#a-passcode').value = '';
  $('#a-passcode').focus();
}

async function boot() {
  show('boot');
  try {
    const r = await api('/api/admin/status');
    if (!r.ok) throw new NetworkError();
    enter(r.data);
  } catch (e) {
    if (e instanceof AuthError) return; // login already shown
    show('offline');
  }
}
$('#a-retry').addEventListener('click', boot);

$('#a-login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const passcode = $('#a-passcode').value.trim();
  const err = $('#a-login-error');
  if (!passcode) { err.textContent = 'Enter the admin passcode.'; return; }
  err.textContent = '';
  try {
    const r = await api('/api/admin/login', { method: 'POST', body: { passcode } });
    if (r.status === 401) { err.textContent = 'That passcode didn\'t work.'; return; }
    if (r.status === 429) { err.textContent = 'Too many tries. Wait a minute.'; return; }
    if (!r.ok) { err.textContent = detail(r); return; }
    boot();
  } catch (ex) { err.textContent = errText(ex); }
});

/* ---------------- state ---------------- */

const S = {
  status: null,
  settings: { provider: 'anthropic', model: '' },
  models: [],
  voices: [],
  courses: [],
  sources: [],
  pollTimer: null,
};

async function enter(status) {
  show('app');
  renderStatus(status);
  await loadSettings();
  loadModels();
  loadVoices();
  loadCourses();
  loadActivity();
}

/* ---------------- keys / status ---------------- */

function renderStatus(st) {
  S.status = st || {};
  const keys = S.status.keys || {};
  $('#keys-list').replaceChildren(...Object.entries(keys).map(([k, v]) =>
    el('li', {}, el('span', { class: `pill ${v ? 'ok' : 'off'}` }, `${k} ${v ? 'set' : 'not set'}`))));
  renderLimits();
  renderToday();
  updateProviderWarning();
}

function providerKeyName(p) {
  return { anthropic: 'ANTHROPIC_API_KEY', openai: 'OPENAI_API_KEY', openrouter: 'OPENROUTER_API_KEY' }[p];
}
function updateProviderWarning() {
  const name = providerKeyName($('#provider').value);
  const keys = S.status?.keys || {};
  const warn = $('#provider-key-warn');
  const missing = name && name in keys && !keys[name];
  warn.hidden = !missing;
  warn.textContent = missing ? `${name} isn't set on the server, so this provider will fail until it is.` : '';
}

/* ---------------- 1. model ---------------- */

async function loadSettings() {
  try {
    const r = await api('/api/admin/settings');
    if (r.ok && r.data) S.settings = r.data;
  } catch (e) { if (e instanceof AuthError) throw e; }
  $('#provider').value = S.settings.provider || 'anthropic';
  $('#model-id').value = S.settings.model || '';
  $('#current-model').textContent = liveModelText();
  $('#cap').value = S.settings.daily_voice_char_cap ?? S.status?.today?.voice_char_cap ?? '';
  $('#a-index-version').textContent = S.settings.index_version != null ? `Index version ${S.settings.index_version}` : '';
  updateProviderWarning();
}

function liveModelText() {
  if (!S.settings.model) return '';
  const warn = S.settings.model_warning ? ` (${S.settings.model_warning})` : '';
  return `Live now: ${S.settings.provider} / ${S.settings.model}${warn}`;
}

let modelsReq = 0;
async function loadModels() {
  const provider = $('#provider').value;
  const my = ++modelsReq;
  say($('#models-status'), 'Loading models...');
  $('#model-list').replaceChildren();
  try {
    const r = await api(`/api/admin/models?provider=${encodeURIComponent(provider)}`);
    if (my !== modelsReq) return;
    if (!r.ok) { say($('#models-status'), detail(r, 'Couldn\'t load the model list. You can still type a model id.'), 'err'); S.models = []; return; }
    S.models = asList(r.data, 'models', 'data').map(m => (typeof m === 'string' ? { id: m } : m)).filter(m => m.id);
    renderModels();
  } catch (e) {
    if (my === modelsReq && !(e instanceof AuthError)) say($('#models-status'), `${errText(e)} You can still type a model id.`, 'err');
  }
}

function perMillion(x) {
  const n = Number(x);
  if (!isFinite(n)) return null;
  if (n === 0) return 'free';
  return `$${(n * 1e6).toFixed(2)}`;
}
function modelMeta(m) {
  const bits = [];
  if (m.name && m.name !== m.id) bits.push(m.name);
  if (m.context_length) bits.push(`${Math.round(m.context_length / 1000).toLocaleString()}k context`);
  if (m.pricing) {
    const pin = perMillion(m.pricing.prompt), pout = perMillion(m.pricing.completion);
    if (pin === 'free' && pout === 'free') bits.push('free');
    else if (pin && pout) bits.push(`${pin} in / ${pout} out per 1M tokens`);
  }
  return bits.join(' · ');
}

function renderModels() {
  const q = $('#model-search').value.trim().toLowerCase();
  const current = $('#model-id').value.trim();
  const list = S.models.filter(m => !q || m.id.toLowerCase().includes(q) || (m.name || '').toLowerCase().includes(q));
  const shown = list.slice(0, 200);
  $('#model-list').replaceChildren(...shown.map(m => el('li', {},
    el('button', {
      type: 'button', class: 'model-opt', 'aria-pressed': String(m.id === current),
      onclick: () => { $('#model-id').value = m.id; renderModels(); say($('#save-model-status'), 'Not saved yet.'); },
    }, el('span', { class: 'm-id', text: m.id }), el('span', { class: 'm-meta', text: modelMeta(m) })))));
  say($('#models-status'), S.models.length
    ? `${list.length} of ${S.models.length} models${list.length > shown.length ? ` (showing the first ${shown.length}; search to narrow)` : ''}`
    : 'No models listed. Type a model id below.');
}

$('#provider').addEventListener('change', () => {
  $('#model-search').value = '';
  if ($('#provider').value !== S.settings.provider) $('#model-id').value = '';
  else $('#model-id').value = S.settings.model || '';
  updateProviderWarning();
  loadModels();
});
$('#model-search').addEventListener('input', renderModels);
$('#model-id').addEventListener('input', () => { renderModels(); say($('#save-model-status'), ''); });

$('#save-model').addEventListener('click', async () => {
  const provider = $('#provider').value, model = $('#model-id').value.trim();
  if (!model) { say($('#save-model-status'), 'Pick or type a model id first.', 'err'); return; }
  say($('#save-model-status'), 'Saving...');
  try {
    const r = await api('/api/admin/settings', { method: 'PUT', body: { provider, model } });
    if (!r.ok) { say($('#save-model-status'), detail(r), 'err'); return; }
    S.settings = { ...S.settings, ...(r.data || { provider, model }) };
    $('#current-model').textContent = liveModelText();
    const warn = S.settings.model_warning;
    say($('#save-model-status'), warn ? `Saved. ${warn}` : 'Saved. New questions use this model.', warn ? 'err' : 'ok');
  } catch (e) { if (!(e instanceof AuthError)) say($('#save-model-status'), errText(e), 'err'); }
});

$('#test-model').addEventListener('click', async () => {
  const provider = $('#provider').value, model = $('#model-id').value.trim();
  const question = $('#test-q').value.trim() || 'What is the main idea of the first session?';
  const out = $('#test-output');
  const btn = $('#test-model');
  btn.disabled = true;
  say($('#test-status'), `Asking ${model || 'the saved model'}...`);
  out.hidden = true;
  const t0 = performance.now();
  try {
    const r = await api('/api/admin/test', { method: 'POST', body: { question, provider, model: model || undefined }, timeout: 90000 });
    const ms = r.data?.latency_ms ?? Math.round(performance.now() - t0);
    if (!r.ok) { say($('#test-status'), `${detail(r)} (${fmtNum(ms)} ms)`, 'err'); }
    else say($('#test-status'), `Worked in ${fmtNum(ms)} ms.`, 'ok');
    let payload = r.data?.narration ?? r.data?.output ?? r.data;
    if (typeof payload === 'string') { try { payload = JSON.parse(payload); } catch { /* show raw */ } }
    out.textContent = typeof payload === 'string' ? payload : JSON.stringify(payload, null, 2);
    out.hidden = false;
  } catch (e) {
    if (!(e instanceof AuthError)) say($('#test-status'), errText(e), 'err');
  } finally { btn.disabled = false; }
});

/* ---------------- 2. voice ---------------- */

const previewAudio = new Audio();
let previewBtn = null;
previewAudio.addEventListener('ended', () => { if (previewBtn) previewBtn.textContent = 'Play preview'; previewBtn = null; });

async function loadVoices() {
  say($('#voices-status'), 'Loading voices...');
  try {
    const r = await api('/api/admin/voices');
    if (!r.ok) { say($('#voices-status'), detail(r, 'Couldn\'t load voices. Is ELEVENLABS_API_KEY set?'), 'err'); S.voices = []; }
    else { S.voices = asList(r.data, 'voices'); say($('#voices-status'), ''); }
  } catch (e) { if (!(e instanceof AuthError)) say($('#voices-status'), errText(e), 'err'); }
  renderVoices();
}

function renderVoices() {
  const current = S.settings.voice_id ?? '';
  const options = [
    { voice_id: '', name: 'Default from the server', category: 'ELEVENLABS_VOICE_ID env var' },
    ...S.voices,
    { voice_id: 'none', name: 'Captions only', category: 'No voice. Answers show captions.' },
  ];
  $('#voice-list').replaceChildren(...options.map(v => {
    const id = `voice-${v.voice_id || 'default'}`;
    const li = el('li', { class: `voice${v.voice_id === current ? ' selected' : ''}` },
      el('label', { for: id },
        el('input', { type: 'radio', name: 'voice', id, value: v.voice_id, checked: v.voice_id === current }),
        el('span', {}, el('span', { class: 'v-name', text: v.name || v.voice_id }), el('span', { class: 'v-cat', text: v.category || '' }))));
    if (v.preview_url) {
      li.append(el('button', {
        type: 'button', class: 'btn btn-small btn-ghost', 'aria-label': `Play preview of ${v.name}`,
        onclick: (e) => togglePreview(e.currentTarget, v.preview_url),
      }, 'Play preview'));
    }
    return li;
  }));
}
function togglePreview(btn, url) {
  if (previewBtn === btn) { previewAudio.pause(); btn.textContent = 'Play preview'; previewBtn = null; return; }
  if (previewBtn) previewBtn.textContent = 'Play preview';
  previewBtn = btn;
  btn.textContent = 'Stop';
  previewAudio.src = url;
  previewAudio.play().catch(() => { btn.textContent = 'Preview failed'; previewBtn = null; });
}
$('#voice-list').addEventListener('change', (e) => {
  document.querySelectorAll('.voice').forEach(li => li.classList.toggle('selected', li.contains(e.target) && e.target.checked));
  say($('#save-voice-status'), 'Not saved yet.');
});
$('#save-voice').addEventListener('click', async () => {
  const picked = document.querySelector('input[name="voice"]:checked');
  if (!picked) return;
  const voice_id = picked.value || null;
  say($('#save-voice-status'), 'Saving...');
  try {
    const r = await api('/api/admin/settings', { method: 'PUT', body: { voice_id } });
    if (!r.ok) { say($('#save-voice-status'), detail(r), 'err'); return; }
    S.settings = { ...S.settings, voice_id: voice_id ?? '' , ...(r.data || {}) };
    say($('#save-voice-status'), 'Saved.', 'ok');
  } catch (e) { if (!(e instanceof AuthError)) say($('#save-voice-status'), errText(e), 'err'); }
});

/* ---------------- 3. courses and source material ---------------- */

const KIND_ACCEPT = { slides: '.pdf,.pptx', transcript: '.vtt', video: '.mp4', notebook: '.ipynb' };
const HAS_KEYS = [['slides', 'Slides'], ['transcript', 'Transcript'], ['video', 'Video'], ['clips', 'Clips'], ['indexed', 'Indexed']];
const STATUS_PILL = { ready: 'ok', processing: 'info', uploaded: 'warn', uploading: 'warn', error: 'err' };

async function loadCourses() {
  say($('#courses-status'), 'Loading...');
  try {
    const [c, s] = await Promise.all([api('/api/admin/courses'), api('/api/admin/sources')]);
    S.courses = c.ok ? asList(c.data, 'courses') : [];
    S.sources = s.ok ? asList(s.data, 'sources') : [];
    say($('#courses-status'), c.ok ? '' : detail(c, 'Couldn\'t load courses.'), c.ok ? '' : 'err');
  } catch (e) { if (!(e instanceof AuthError)) say($('#courses-status'), errText(e), 'err'); }
  renderCourses();
  renderSources();
  fillCourseSelects();
  schedulePoll();
}
$('#refresh-courses').addEventListener('click', loadCourses);

function sessionId(s, course) { return s.id ?? `${course}-s${pad2(s.session)}`; }

function renderCourses() {
  const wrap = $('#course-blocks');
  if (!S.courses.length) { wrap.replaceChildren(el('p', { class: 'muted', text: 'No courses yet. Add one below.' })); return; }
  wrap.replaceChildren(...S.courses.map(c => {
    const rows = (c.sessions || []).map(s => {
      const has = s.has || s;
      const visible = s.visible !== false;
      const srcFor = S.sources.filter(x => String(x.course) === String(c.course) && Number(x.session) === Number(s.session));
      const worst = ['error', 'processing', 'uploaded'].find(st => srcFor.some(x => x.status === st));
      return el('tr', { class: visible ? '' : 'is-hidden' },
        el('td', { class: 'num', text: pad2(s.session) }),
        el('td', { text: s.date || '' }),
        el('td', { class: 'full', text: s.title || '' }),
        el('td', { class: 'full' }, el('div', { class: 'pills' },
          ...HAS_KEYS.map(([k, label]) => el('span', { class: `pill ${has[k] ? 'ok' : 'off'}`, title: `${label}: ${has[k] ? 'yes' : 'no'}` }, `${has[k] ? '✓' : '·'} ${label}`)),
          worst ? el('span', { class: `pill ${STATUS_PILL[worst]}` }, worst === 'error' ? 'Has an error' : humanize(worst)) : null,
          visible ? null : el('span', { class: 'pill off' }, 'Hidden'))),
        el('td', { class: 'full' }, el('div', { class: 'actions' },
          el('button', { type: 'button', class: 'btn btn-small', 'aria-label': `Upload files for session ${s.session}`, onclick: () => pickUpload(c.course, s.session) }, 'Upload'),
          el('button', {
            type: 'button', class: 'btn btn-small btn-ghost', 'aria-label': `${visible ? 'Hide' : 'Show'} session ${s.session} ${visible ? 'from' : 'to'} students`,
            onclick: (e) => toggleVisible(e.currentTarget, c.course, s),
          }, visible ? 'Hide' : 'Show'))));
    });
    return el('div', { class: 'course-block' },
      el('h3', {}, `${courseCode(c.course)} ${c.title || ''}`, el('span', { class: 'muted small', text: c.term || '' })),
      el('div', { class: 'table-wrap' }, el('table', { class: 'data stack' },
        el('thead', {}, el('tr', {}, ...['#', 'Date', 'Title', 'What exists'].map(h => el('th', { scope: 'col', text: h })),
          el('th', { scope: 'col' }, el('span', { class: 'visually-hidden', text: 'Actions' })))),
        el('tbody', {}, ...rows.length ? rows : [el('tr', {}, el('td', { colspan: '5', class: 'muted', text: 'No sessions yet.' }))]))));
  }));
}

async function toggleVisible(btn, course, s) {
  const next = s.visible === false;
  btn.disabled = true;
  try {
    const r = await api(`/api/admin/sessions/${encodeURIComponent(sessionId(s, course))}`, { method: 'PATCH', body: { visible: next } });
    if (!r.ok) { say($('#courses-status'), detail(r), 'err'); return; }
    s.visible = next;
    say($('#courses-status'), `Session ${s.session} is now ${next ? 'visible to students' : 'hidden from students'}.`, 'ok');
    renderCourses();
  } catch (e) { if (!(e instanceof AuthError)) say($('#courses-status'), errText(e), 'err'); }
  finally { btn.disabled = false; }
}

function renderSources() {
  const body = $('#sources-body');
  if (!S.sources.length) { body.replaceChildren(el('tr', {}, el('td', { colspan: '7', class: 'muted', text: 'Nothing uploaded yet.' }))); return; }
  const sorted = S.sources.slice().sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')));
  body.replaceChildren(...sorted.map(x => el('tr', {},
    el('td', { text: courseCode(x.course) }),
    el('td', { 'data-label': 'Session', text: pad2(x.session) }),
    el('td', { text: humanize(x.kind) }),
    el('td', { class: 'full' }, el('span', { class: 'src-path', text: String(x.path || '').split('/').pop() || '' })),
    el('td', { class: 'full' }, el('span', { class: `pill ${STATUS_PILL[x.status] || ''}`, text: humanize(x.status || 'unknown') }),
      x.message ? el('div', { class: 'small muted', text: x.message }) : null),
    el('td', { class: 'full', 'data-label': 'Updated', text: fmtWhen(x.updated_at) }),
    el('td', { class: 'full' }, el('button', {
      type: 'button', class: 'btn btn-small', disabled: x.status === 'uploaded' || x.status === 'processing',
      'aria-label': `Re-run ${x.kind} for ${x.course} session ${x.session}`, onclick: () => rerun(x.id),
    }, 'Re-run')))));
}

async function rerun(id) {
  try {
    const r = await api(`/api/admin/sources/${encodeURIComponent(id)}/rerun`, { method: 'POST' });
    if (!r.ok) { say($('#courses-status'), detail(r), 'err'); return; }
    const s = S.sources.find(x => x.id === id);
    if (s) Object.assign(s, r.data && typeof r.data === 'object' ? r.data : { status: 'uploaded' });
    renderSources(); renderCourses(); schedulePoll();
    say($('#courses-status'), 'Queued. The worker picks it up on its next check.', 'ok');
  } catch (e) { if (!(e instanceof AuthError)) say($('#courses-status'), errText(e), 'err'); }
}

function schedulePoll() {
  clearTimeout(S.pollTimer);
  const busy = S.sources.some(x => ['uploaded', 'processing', 'uploading'].includes(x.status));
  if (!busy) return;
  S.pollTimer = setTimeout(async () => {
    try {
      const s = await api('/api/admin/sources');
      if (s.ok) { S.sources = asList(s.data, 'sources'); renderSources(); renderCourses(); }
    } catch { /* try again next tick */ }
    schedulePoll();
  }, 4000);
}

function fillCourseSelects() {
  for (const sel of [$('#up-course'), $('#ns-course')]) {
    const prev = sel.value;
    sel.replaceChildren(...S.courses.map(c => el('option', { value: c.course, text: `${courseCode(c.course)} ${c.title || ''}` })));
    if (prev && S.courses.some(c => c.course === prev)) sel.value = prev;
  }
  fillSessionSelect();
}
function fillSessionSelect() {
  const c = S.courses.find(x => x.course === $('#up-course').value);
  const sel = $('#up-session');
  const prev = sel.value;
  sel.replaceChildren(...(c?.sessions || []).map(s => el('option', { value: String(s.session), text: `${pad2(s.session)} ${s.title || ''}` })));
  if (prev) sel.value = prev;
}
$('#up-course').addEventListener('change', fillSessionSelect);
$('#up-kind').addEventListener('change', () => { $('#up-file').accept = KIND_ACCEPT[$('#up-kind').value]; $('#up-file').value = ''; });

function pickUpload(course, session) {
  $('#up-course').value = course;
  fillSessionSelect();
  $('#up-session').value = String(session);
  $('#upload-form').scrollIntoView({ behavior: 'smooth', block: 'center' });
  $('#up-kind').focus({ preventScroll: true });
}

$('#upload-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const course = $('#up-course').value, session = Number($('#up-session').value), kind = $('#up-kind').value;
  const file = $('#up-file').files[0];
  const status = $('#upload-status');
  if (!course || !session) { say(status, 'Pick a course and session.', 'err'); return; }
  if (!file) { say(status, 'Choose a file.', 'err'); return; }
  const okExt = KIND_ACCEPT[kind].split(',').some(ext => file.name.toLowerCase().endsWith(ext));
  if (!okExt) { say(status, `That doesn't look like ${KIND_ACCEPT[kind].replace(/,/g, ' or ')}.`, 'err'); return; }
  say(status, '');

  const bar = el('span');
  const line = el('span', { text: `${file.name}: asking for an upload link...` });
  const item = el('li', { class: 'upload-item' }, line, el('div', { class: 'progress', role: 'progressbar', 'aria-label': `Upload of ${file.name}`, 'aria-valuemin': '0', 'aria-valuemax': '100', 'aria-valuenow': '0' }, bar));
  $('#upload-list').prepend(item);
  const prog = item.querySelector('.progress');

  try {
    const r = await api('/api/admin/uploads', { method: 'POST', body: { course, session, kind, filename: file.name, size: file.size } });
    if (!r.ok) { line.textContent = `${file.name}: ${detail(r)}`; item.classList.add('error-text'); return; }
    const d = r.data || {};
    const url = d.upload_url || d.signed_url || d.signedUrl || d.url;
    if (!url) { line.textContent = `${file.name}: the server didn't return an upload link.`; return; }
    line.textContent = `${file.name}: uploading...`;
    await putWithProgress(url, file, d.method || 'PUT', d.headers || {}, (p) => {
      bar.style.width = `${p}%`;
      prog.setAttribute('aria-valuenow', String(p));
      line.textContent = `${file.name}: ${p}%`;
    });
    // Ask the server to confirm the file is in storage before queueing it for the worker.
    // (/rerun would queue it without checking.) A 409 means storage has not shown it yet;
    // listing sources promotes it once it appears.
    const id = d.source_id ?? d.id;
    let note = 'uploaded. Waiting for the worker.';
    if (id != null) {
      const c = await api(`/api/admin/sources/${encodeURIComponent(id)}/complete`, { method: 'POST' });
      if (c.status === 409) note = 'uploaded, but storage has not confirmed it yet. It will be queued when it appears.';
      else if (!c.ok) { line.textContent = `${file.name}: ${detail(c)}`; item.classList.add('error-text'); return; }
    }
    line.textContent = `${file.name}: ${note}`;
    $('#up-file').value = '';
    loadCourses();
  } catch (ex) {
    if (ex instanceof AuthError) return;
    line.textContent = `${file.name}: ${ex.message || 'upload failed'}`;
    item.classList.add('error-text');
  }
});

function putWithProgress(url, file, method, headers, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(method, url);
    xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
    for (const [k, v] of Object.entries(headers)) xhr.setRequestHeader(k, v);
    xhr.upload.addEventListener('progress', (e) => { if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100)); });
    xhr.addEventListener('load', () => (xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(new Error(`storage said HTTP ${xhr.status}`))));
    xhr.addEventListener('error', () => reject(new Error('network error during upload')));
    xhr.addEventListener('abort', () => reject(new Error('upload cancelled')));
    xhr.send(file);
  });
}

$('#course-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const course = $('#nc-code').value.trim(), title = $('#nc-title').value.trim(), term = $('#nc-term').value.trim();
  const st = $('#course-status');
  if (!/^\d{5}$/.test(course)) { say(st, 'Course code is 5 digits, like 70445.', 'err'); return; }
  if (!title || !term) { say(st, 'Add a title and a term.', 'err'); return; }
  try {
    const r = await api('/api/admin/courses', { method: 'POST', body: { course, title, term } });
    if (!r.ok) { say(st, detail(r), 'err'); return; }
    say(st, `Added ${course}.`, 'ok');
    e.target.reset();
    loadCourses();
  } catch (ex) { if (!(ex instanceof AuthError)) say(st, errText(ex), 'err'); }
});

$('#session-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const course = $('#ns-course').value, session = Number($('#ns-number').value), date = $('#ns-date').value, title = $('#ns-title').value.trim();
  const st = $('#session-status');
  if (!course || !session || !date || !title) { say(st, 'Fill in all four fields.', 'err'); return; }
  try {
    const r = await api('/api/admin/sessions', { method: 'POST', body: { course, session, date, title } });
    if (!r.ok) { say(st, detail(r), 'err'); return; }
    say(st, `Added session ${session}. Upload its files on the left.`, 'ok');
    e.target.reset();
    await loadCourses();
    pickUpload(course, session);
  } catch (ex) { if (!(ex instanceof AuthError)) say(st, errText(ex), 'err'); }
});

/* ---------------- 4. limits and access ---------------- */

function renderLimits() {
  const limits = S.status?.limits || {};
  const entries = Object.entries(limits);
  $('#limits-kv').replaceChildren(...(entries.length ? entries : [['not reported', '']]).map(([k, v]) =>
    el('div', {}, el('dt', { text: humanize(k) }), el('dd', { text: fmtNum(v) }))));
}

$('#cap-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const v = Number($('#cap').value);
  const st = $('#cap-status');
  if (!Number.isInteger(v) || v < 0) { say(st, 'Use a whole number, 0 or more.', 'err'); return; }
  try {
    const r = await api('/api/admin/settings', { method: 'PUT', body: { daily_voice_char_cap: v } });
    if (!r.ok) { say(st, detail(r), 'err'); return; }
    S.settings.daily_voice_char_cap = v;
    say(st, v === 0 ? 'Saved. The voice is off; answers use captions.' : `Saved. Cap is ${fmtNum(v)} characters a day.`, 'ok');
  } catch (ex) { if (!(ex instanceof AuthError)) say(st, errText(ex), 'err'); }
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
  } catch (ex) { if (!(ex instanceof AuthError)) say(st, errText(ex), 'err'); }
});

/* ---------------- 5. activity ---------------- */

function renderToday() {
  const today = S.status?.today || {};
  const entries = Object.entries(today);
  $('#today-kv').replaceChildren(...(entries.length ? entries : [['nothing yet', '']]).map(([k, v]) =>
    el('div', {}, el('dt', { text: humanize(k) }), el('dd', { text: fmtNum(v) }))));
}

async function loadActivity() {
  try {
    const [st, lg] = await Promise.all([api('/api/admin/status'), api('/api/admin/log')]);
    if (st.ok) renderStatus(st.data);
    const rows = lg.ok ? asList(lg.data, 'rows', 'log', 'items').slice(0, 50) : [];
    $('#log-body').replaceChildren(...(rows.length ? rows.map(x => el('tr', {},
      el('td', { class: 'small muted', text: fmtWhen(x.created_at || x.at || x.time) }),
      el('td', {}, el('span', { class: `pill ${x.covered ? 'ok' : 'warn'}`, text: x.covered ? 'Covered' : 'Not covered' })),
      el('td', { class: 'full', text: x.question || '' }),
      el('td', { class: 'num', 'data-label': 'Top score', text: x.top_score != null ? Number(x.top_score).toFixed(3) : '' }),
      el('td', { class: 'num', 'data-label': 'Latency', text: x.latency_ms != null ? `${fmtNum(x.latency_ms)} ms` : '' }),
      el('td', { class: 'small full', text: [x.provider, x.model].filter(Boolean).join(' / ') }),
    )) : [el('tr', {}, el('td', { colspan: '6', class: 'muted', text: lg.ok ? 'No questions logged yet.' : detail(lg, 'Couldn\'t load the log.') }))]));
  } catch (e) { /* auth handled in api(); network shows on next refresh */ }
}
$('#refresh-activity').addEventListener('click', loadActivity);

/* ---------------- nav highlight ---------------- */

const navLinks = [...document.querySelectorAll('.admin-nav a')];
const io = new IntersectionObserver((entries) => {
  for (const en of entries) if (en.isIntersecting) {
    navLinks.forEach(a => a.setAttribute('aria-current', String(a.getAttribute('href') === `#${en.target.id}`)));
  }
}, { rootMargin: '-30% 0px -60% 0px' });
document.querySelectorAll('section.panel[id]').forEach(s => io.observe(s));

boot();

// Faculty Twin: Settings page (admin). Not linked from the student page.
// Talks only to /api/admin/* with the ft_admin cookie. Keys never reach the browser.
//
// This file signs in and runs the numbered sections: 1 model, 2 voice, 3 courses and source material,
// 4 limits, web answers and access, 4b answer thresholds, 5 activity, 6 prompts. The other sections
// are their own scripts (admin-analytics.js, admin-evals.js, admin-alerts.js, admin-drafts.js); they
// start when this file dispatches `ft-admin-enter`. Prompts' "Run an eval" dispatches `ft:run-eval`.
//
// Each page script defines its own small helpers ($, el, api) on purpose: the scripts load
// independently (no bundler), and the node tests (tests/js/fakedom.mjs) run one file at a time.

const DEV_HOSTS = ['localhost', '127.0.0.1', '[::1]'];
if (DEV_HOSTS.includes(location.hostname) && new URLSearchParams(location.search).get('mock') === '1') {
  // Development only, and only on a local host: on the live site a crafted ?mock=1 link
  // would otherwise show fake "saved" results while nothing is saved.
  await import('./dev/mock.js');
}
// Settings > Prompts' word-level diff (pure functions).
const DIFF = await import('./prompt-diff.js');

/** The first element matching `s`. */
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
/** The list in a reply: the reply itself when it is an array, else the first array under one of `keys`. */
const asList = (d, ...keys) => {
  if (Array.isArray(d)) return d;
  for (const k of keys) if (Array.isArray(d?.[k])) return d[k];
  return [];
};
const pad2 = (n) => String(n).padStart(2, '0');
/** "70-445" for "70445". */
const courseCode = (c) => (/^\d{5}$/.test(String(c)) ? `${String(c).slice(0, 2)}-${String(c).slice(2)}` : String(c ?? ''));
const fmtWhen = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
};
const fmtNum = (n) => (typeof n === 'number' ? n.toLocaleString('en-US') : String(n ?? ''));
/** "daily_api_calls" as "Daily API calls". */
const humanize = (k) => String(k).replace(/_/g, ' ').replace(/\b(api|id)\b/gi, s => s.toUpperCase()).replace(/^./, c => c.toUpperCase());

/** The server could not be reached. */
class NetworkError extends Error {}
/** A 401: api() has already shown the sign-in screen, so callers show nothing more. */
class AuthError extends Error {}
/* True once this page has been inside the app. Only then does a 401 mean the session ran out;
   on a fresh visit with no admin cookie, a 401 just means "not signed in yet". */
let hadSession = false;

/** fetch wrapper: {status, ok, data}. Throws NetworkError (no reply) or AuthError (signed out, login shown). */
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
  if (res.status === 401 && !path.endsWith('/login')) {
    showLogin(hadSession ? 'Your admin session ran out. Sign in again.' : '');
    hadSession = false;
    throw new AuthError();
  }
  return { status: res.status, ok: res.ok, data };
}
/** The server's error message (FastAPI's `detail`, a string or a list of field errors), else `fallback`. */
const detail = (r, fallback) => {
  const d = r?.data?.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d) && d[0]?.msg) return d.map(x => x.msg).join('; ');
  return fallback || `Request failed (HTTP ${r?.status}).`;
};
/** Write a status line; `kind` is '', 'ok' or 'err'. */
function say(node, text, kind = '') {
  node.textContent = text || '';
  node.className = `status-line ${kind}`.trim();
}
const errText = (e) => (e instanceof NetworkError ? 'Can\'t reach the server.' : (e?.message || 'Something went wrong.'));
/** Show a failed request on a status line. A sign-out shows nothing: the login screen is already up. */
function sayError(node, e) {
  if (!(e instanceof AuthError)) say(node, errText(e), 'err');
}
/** Keep what the server says was saved (it returns the full settings after a PUT). */
function keepSettings(saved) {
  S.settings = { ...S.settings, ...saved };
}

/* ---------------- screens ---------------- */

const screens = { boot: $('#a-boot'), offline: $('#a-offline'), login: $('#a-login'), app: $('#a-app') };
/** Show one of boot, offline, login, app. */
function show(name) { for (const [k, n] of Object.entries(screens)) n.hidden = k !== name; }

/** The sign-in screen, with an optional note (for example "your session ran out"). */
function showLogin(note) {
  show('login');
  $('#a-login-note').hidden = !note;
  $('#a-login-note').textContent = note || '';
  $('#a-passcode').value = '';
  $('#a-passcode').focus();
}

/** First load: /api/admin/status decides between the app, the login screen (401) and offline. */
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
  voiceGroups: [],
  freeDefault: '',
  courses: [],
  sources: [],
  pollTimer: null,
};

/** Signed in: render the status and load every section. */
async function enter(status) {
  hadSession = true;
  show('app');
  renderStatus(status);
  await loadSettings();
  loadModels();
  loadVoices();
  loadCourses();
  loadActivity();
  loadPrompts();
  loadThresholds();
  // Settings > Evals lives in admin-evals.js; tell it the admin is signed in.
  document.dispatchEvent(new CustomEvent('ft-admin-enter', { detail: { status } }));
}

/* ---------------- keys / status ---------------- */

/** Which keys are set on the server, the limits, and today's counts. */
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
/** Warn when the chosen provider's key is not set on the server. */
function updateProviderWarning() {
  const name = providerKeyName($('#provider').value);
  const keys = S.status?.keys || {};
  const warn = $('#provider-key-warn');
  const missing = name && name in keys && !keys[name];
  warn.hidden = !missing;
  warn.textContent = missing ? `${name} isn't set on the server, so this provider will fail until it is.` : '';
}

/* ---------------- 1. model ---------------- */

/** The saved settings into the model, cap and web answer fields. */
async function loadSettings() {
  try {
    const r = await api('/api/admin/settings');
    if (r.ok && r.data) S.settings = r.data;
  } catch (e) { if (e instanceof AuthError) throw e; }
  $('#provider').value = S.settings.provider || 'anthropic';
  $('#model-id').value = S.settings.model || '';
  $('#current-model').textContent = liveModelText();
  renderOpenAccess();
  $('#cap').value = S.settings.daily_voice_char_cap ?? S.status?.today?.voice_char_cap ?? '';
  $('#free-cap').value = S.settings.daily_free_voice_char_cap ?? S.status?.today?.free_voice_char_cap ?? '';
  renderWebAnswers();
  $('#a-index-version').textContent = S.settings.index_version != null ? `Index version ${S.settings.index_version}` : '';
  updateProviderWarning();
}

/** "Live now: provider / model", with the server's warning about it. */
function liveModelText() {
  if (!S.settings.model) return '';
  const warn = S.settings.model_warning ? ` (${S.settings.model_warning})` : '';
  return `Live now: ${S.settings.provider} / ${S.settings.model}${warn}`;
}

let modelsReq = 0;   // only the newest model-list request may render
/** The provider's model list (a failure still lets the admin type a model id). */
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

/** A per-token price as dollars per million tokens, "free", or null when it is not a number. */
function perMillion(x) {
  const n = Number(x);
  if (!isFinite(n)) return null;
  if (n === 0) return 'free';
  return `$${(n * 1e6).toFixed(2)}`;
}
/** "Name · 200k context · $3.00 in / $15.00 out per 1M tokens". */
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

/** The model list filtered by the search box (the first 200 shown). */
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
    keepSettings(r.data || { provider, model });
    $('#current-model').textContent = liveModelText();
    const warn = S.settings.model_warning;
    say($('#save-model-status'), warn ? `Saved. ${warn}` : 'Saved. New questions use this model.', warn ? 'err' : 'ok');
  } catch (e) { sayError($('#save-model-status'), e); }
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
    sayError($('#test-status'), e);
  } finally { btn.disabled = false; }
});

/* ---------------- 2. voice ---------------- */
// Three groups from /api/admin/voices: My voice clone, ElevenLabs voices, Free Microsoft voices.
// Plus "Server default" (null) and "Captions only" ("none"). Ids: eleven:<id>, edge:<ShortName>.

const previewAudio = new Audio();
let previewBtn = null;
let previewBlobUrl = null;
previewAudio.addEventListener('ended', () => { if (previewBtn) previewBtn.textContent = 'Preview'; previewBtn = null; });

const KIND_TEXT = {
  clone: 'my voice clone (ElevenLabs, costs credits)',
  stock: 'an ElevenLabs stock voice (costs credits)',
  free: 'a free Microsoft voice (no cost)',
  unverified: 'an ElevenLabs voice that could not be checked',
  none: 'captions only',
};

/** The voice groups from the server, then every voice-dependent control. */
async function loadVoices() {
  say($('#voices-status'), 'Loading voices...');
  try {
    const r = await api('/api/admin/voices');
    if (!r.ok) { say($('#voices-status'), detail(r, 'Couldn\'t load voices.'), 'err'); S.voiceGroups = []; }
    else {
      S.voiceGroups = asList(r.data, 'groups');
      S.freeDefault = r.data?.free_voice_default || '';
      say($('#voices-status'), r.data?.elevenlabs_error || '', r.data?.elevenlabs_error ? 'err' : '');
    }
  } catch (e) { sayError($('#voices-status'), e); }
  renderVoices();
}

/** One voice as a radio button, with a Preview button when it has a sample. */
function voiceItem(v, current, previewKind) {
  const id = `voice-${String(v.voice_id || 'default').replace(/[^A-Za-z0-9_-]/g, '_')}`;
  const sub = [v.description, v.is_default ? 'server default (ELEVENLABS_VOICE_ID)' : null].filter(Boolean).join(' · ');
  const li = el('li', { class: `voice${v.voice_id === current ? ' selected' : ''}` },
    el('label', { for: id },
      el('input', { type: 'radio', name: 'voice', id, value: v.voice_id, checked: v.voice_id === current }),
      el('span', {}, el('span', { class: 'v-name', text: v.name || v.voice_id }), el('span', { class: 'v-cat', text: sub || v.category || '' }))));
  if (v.preview_url) {
    li.append(el('button', {
      type: 'button', class: 'btn btn-small btn-ghost', 'aria-label': `Preview ${v.name}`,
      onclick: (e) => togglePreview(e.currentTarget, v.preview_url, previewKind),
    }, 'Preview'));
  }
  return li;
}

/** The voice groups, the "Other" group, and the custom free voice; then the fallback and web voice menus. */
function renderVoices() {
  const current = S.settings.voice_id ?? '';
  const known = new Set();
  const blocks = S.voiceGroups.map(g => {
    const items = asList(g.voices).map(v => { known.add(v.voice_id); return voiceItem(v, current, g.id === 'free' ? 'server' : 'remote'); });
    if (g.id === 'free') items.push(customFreeItem(current));
    if (!items.length) items.push(el('li', { class: 'v-cat', text: g.id === 'clone' ? 'No cloned voice on the ElevenLabs account.' : 'None available.' }));
    return el('div', { class: 'voice-group' },
      el('h3', { class: 'h-sub' }, `${g.label} `, el('span', { class: `pill ${g.costs_money ? 'warn' : 'ok'}`, text: g.costs_money ? 'Costs money' : 'Free' })),
      el('p', { class: 'hint', text: `${g.cost} Students see: "${g.student_label}"` }),
      el('ul', { class: 'voice-list' }, ...items));
  });
  const other = [
    { voice_id: '', name: 'Server default', description: 'ELEVENLABS_VOICE_ID from Vercel' },
    { voice_id: 'none', name: 'Captions only', description: 'No voice. Answers show captions, with no voice label.' },
  ];
  blocks.push(el('div', { class: 'voice-group' },
    el('h3', { class: 'h-sub', text: 'Other' }),
    el('ul', { class: 'voice-list' }, ...other.map(v => voiceItem(v, current)))));
  $('#voice-groups').replaceChildren(...blocks);
  // A saved free voice that is not on the curated list shows in the custom field.
  if (current.startsWith('edge:') && !known.has(current)) {
    $('#voice-custom-name').value = current.slice(5);
    $('#voice-custom').checked = true;
    $('#voice-custom').closest('.voice').classList.add('selected');
  }
  renderFallback();
  renderWebAnswers();
  renderCurrentVoice();
}

/** "Another Microsoft voice": a radio plus a ShortName field and its own Preview. */
function customFreeItem(current) {
  const li = el('li', { class: 'voice voice-custom' },
    el('label', { for: 'voice-custom' },
      el('input', { type: 'radio', name: 'voice', id: 'voice-custom', value: 'edge-custom' }),
      el('span', {}, el('span', { class: 'v-name', text: 'Another Microsoft voice' }), el('span', { class: 'v-cat', text: 'Type its ShortName' }))),
    el('input', { type: 'text', id: 'voice-custom-name', placeholder: 'en-AU-NatashaNeural', 'aria-label': 'Microsoft voice ShortName', maxlength: '80', spellcheck: 'false', autocomplete: 'off' }),
    el('button', {
      type: 'button', class: 'btn btn-small btn-ghost', 'aria-label': 'Preview the typed voice',
      onclick: (e) => {
        const name = $('#voice-custom-name').value.trim();
        if (!name) { say($('#save-voice-status'), 'Type a ShortName first.', 'err'); return; }
        togglePreview(e.currentTarget, `/api/admin/voice-preview?voice=${encodeURIComponent('edge:' + name)}`, 'server');
      },
    }, 'Preview'));
  li.querySelector('#voice-custom-name').addEventListener('input', () => {
    $('#voice-custom').checked = true;
    document.querySelectorAll('.voice').forEach(n => n.classList.toggle('selected', n === li));
    say($('#save-voice-status'), 'Not saved yet.');
  });
  return li;
}

/** The free Microsoft voices from the voice list. */
function freeVoices() {
  return asList(S.voiceGroups.find(g => g.id === 'free')?.voices);
}

/**
 * <option>s for the free voices. A saved voice that is not on the list gets its own option labeled
 * `savedLabel` (pass null for none), so saving the form again keeps it.
 */
function freeVoiceOptions(chosen, savedLabel) {
  const options = freeVoices().map(v => el('option', { value: v.voice_id, selected: v.voice_id === chosen }, `${v.name} (${v.description || 'free'})`));
  if (savedLabel != null && !freeVoices().some(v => v.voice_id === chosen)) options.push(el('option', { value: chosen, selected: true }, savedLabel));
  return options;
}

/** The fallback when the first voice fails: captions, or a free voice (the menu shows only then). */
function renderFallback() {
  const mode = S.settings.voice_fallback || 'captions';
  $('#voice-fallback').value = mode;
  const chosen = S.settings.voice_fallback_voice || S.freeDefault;
  $('#voice-fallback-voice').replaceChildren(...freeVoiceOptions(chosen, chosen ? chosen.slice(5) : null));
  $('#voice-fallback-voice-field').hidden = mode !== 'free';
}
$('#voice-fallback').addEventListener('change', () => {
  $('#voice-fallback-voice-field').hidden = $('#voice-fallback').value !== 'free';
  say($('#save-voice-status'), 'Not saved yet.');
});
$('#voice-fallback-voice').addEventListener('change', () => say($('#save-voice-status'), 'Not saved yet.'));

/** "Live now: ..." with the label students see and the fallback. */
function renderCurrentVoice() {
  const s = S.settings;
  const kind = s.voice_kind || 'none';
  const label = s.voice_label ? ` Students see: "${s.voice_label}"` : ' Students see no voice label.';
  const fb = s.voice_fallback === 'free' && s.voice_fallback_label ? ` Fallback: ${s.voice_fallback_voice.slice(5)}.` : '';
  say($('#voice-current'), `Live now: ${KIND_TEXT[kind] || kind}.${label}${fb}`);
}

/**
 * Play or stop a voice sample. `kind` 'remote' plays the provider's sample URL; 'server' fetches our
 * admin-only preview route first, so an error message can be shown instead of a silent failure.
 */
async function togglePreview(btn, url, kind) {
  if (previewBtn === btn) { previewAudio.pause(); btn.textContent = 'Preview'; previewBtn = null; return; }
  if (previewBtn) previewBtn.textContent = 'Preview';
  previewAudio.pause();
  previewBtn = btn;
  btn.textContent = kind === 'server' ? 'Loading...' : 'Stop';
  let src = url;
  if (kind === 'server') {
    // Free voices: our own admin-only route speaks one fixed sentence. Fetch first so an error can be shown.
    try {
      const res = await fetch(url, { credentials: 'same-origin' });
      if (!res.ok) {
        let msg = `Preview failed (HTTP ${res.status}).`;
        try { msg = (await res.json()).detail || msg; } catch { /* not JSON */ }
        if (previewBtn === btn) { btn.textContent = 'Preview'; previewBtn = null; }
        say($('#save-voice-status'), msg, 'err');
        return;
      }
      if (previewBlobUrl) URL.revokeObjectURL(previewBlobUrl);
      previewBlobUrl = URL.createObjectURL(await res.blob());
      src = previewBlobUrl;
    } catch {
      if (previewBtn === btn) { btn.textContent = 'Preview'; previewBtn = null; }
      say($('#save-voice-status'), 'Can\'t reach the server.', 'err');
      return;
    }
    if (previewBtn !== btn) return; // another preview started meanwhile
    btn.textContent = 'Stop';
  }
  previewAudio.src = src;
  previewAudio.play().catch(() => { btn.textContent = 'Preview failed'; previewBtn = null; });
}

$('#voice-groups').addEventListener('change', (e) => {
  if (e.target.name !== 'voice') return;
  document.querySelectorAll('.voice').forEach(li => li.classList.toggle('selected', li.contains(e.target) && e.target.checked));
  say($('#save-voice-status'), 'Not saved yet.');
});
$('#save-voice').addEventListener('click', async () => {
  const picked = document.querySelector('input[name="voice"]:checked');
  if (!picked) { say($('#save-voice-status'), 'Pick a voice first.', 'err'); return; }
  let voice_id = picked.value || null;
  if (voice_id === 'edge-custom') {
    const name = $('#voice-custom-name').value.trim();
    if (!name) { say($('#save-voice-status'), 'Type the Microsoft voice ShortName.', 'err'); return; }
    voice_id = `edge:${name}`;
  }
  const body = { voice_id, voice_fallback: $('#voice-fallback').value };
  if (body.voice_fallback === 'free' && $('#voice-fallback-voice').value) body.voice_fallback_voice = $('#voice-fallback-voice').value;
  say($('#save-voice-status'), 'Saving...');
  try {
    const r = await api('/api/admin/settings', { method: 'PUT', body });
    if (!r.ok) { say($('#save-voice-status'), detail(r), 'err'); return; }
    keepSettings(r.data || {});
    renderCurrentVoice();
    say($('#save-voice-status'), 'Saved.', 'ok');
  } catch (e) { sayError($('#save-voice-status'), e); }
});

/* ---------------- 3. courses and source material ---------------- */

const KIND_ACCEPT = { slides: '.pdf,.pptx', transcript: '.vtt', video: '.mp4', notebook: '.ipynb' };
const HAS_KEYS = [['slides', 'Slides'], ['transcript', 'Transcript'], ['video', 'Video'], ['clips', 'Clips'], ['indexed', 'Indexed']];
const STATUS_PILL = { ready: 'ok', processing: 'info', uploaded: 'warn', uploading: 'warn', error: 'err' };

/** Courses, sessions and uploaded sources, then the tables, the upload menus and the status poll. */
async function loadCourses() {
  say($('#courses-status'), 'Loading...');
  try {
    const [c, s] = await Promise.all([api('/api/admin/courses'), api('/api/admin/sources')]);
    S.courses = c.ok ? asList(c.data, 'courses') : [];
    S.sources = s.ok ? asList(s.data, 'sources') : [];
    say($('#courses-status'), c.ok ? '' : detail(c, 'Couldn\'t load courses.'), c.ok ? '' : 'err');
  } catch (e) { sayError($('#courses-status'), e); }
  renderCourses();
  renderSources();
  fillCourseSelects();
  schedulePoll();
}
$('#refresh-courses').addEventListener('click', loadCourses);

/** The session's id, or "70445-s03" when the server did not send one. */
function sessionId(s, course) { return s.id ?? `${course}-s${pad2(s.session)}`; }

/** One table per course: each session, what exists for it, its worst source status, Upload and Hide. */
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

/** Hide a session from students, or show it again. */
async function toggleVisible(btn, course, s) {
  const next = s.visible === false;
  btn.disabled = true;
  try {
    const r = await api(`/api/admin/sessions/${encodeURIComponent(sessionId(s, course))}`, { method: 'PATCH', body: { visible: next } });
    if (!r.ok) { say($('#courses-status'), detail(r), 'err'); return; }
    s.visible = next;
    say($('#courses-status'), `Session ${s.session} is now ${next ? 'visible to students' : 'hidden from students'}.`, 'ok');
    renderCourses();
  } catch (e) { sayError($('#courses-status'), e); }
  finally { btn.disabled = false; }
}

/** Every uploaded file, newest first, with its status and a Re-run button. */
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

/** Queue a source for the worker again. */
async function rerun(id) {
  try {
    const r = await api(`/api/admin/sources/${encodeURIComponent(id)}/rerun`, { method: 'POST' });
    if (!r.ok) { say($('#courses-status'), detail(r), 'err'); return; }
    const s = S.sources.find(x => x.id === id);
    if (s) Object.assign(s, r.data && typeof r.data === 'object' ? r.data : { status: 'uploaded' });
    renderSources(); renderCourses(); schedulePoll();
    say($('#courses-status'), 'Queued. The worker picks it up on its next check.', 'ok');
  } catch (e) { sayError($('#courses-status'), e); }
}

/** While anything is queued or processing, check the sources every 4 seconds. */
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

/** The course menus in the upload and new-session forms (keeping the current choice). */
function fillCourseSelects() {
  for (const sel of [$('#up-course'), $('#ns-course')]) {
    const prev = sel.value;
    sel.replaceChildren(...S.courses.map(c => el('option', { value: c.course, text: `${courseCode(c.course)} ${c.title || ''}` })));
    if (prev && S.courses.some(c => c.course === prev)) sel.value = prev;
  }
  fillSessionSelect();
}
/** The session menu for the course picked in the upload form. */
function fillSessionSelect() {
  const c = S.courses.find(x => x.course === $('#up-course').value);
  const sel = $('#up-session');
  const prev = sel.value;
  sel.replaceChildren(...(c?.sessions || []).map(s => el('option', { value: String(s.session), text: `${pad2(s.session)} ${s.title || ''}` })));
  if (prev) sel.value = prev;
}
$('#up-course').addEventListener('change', fillSessionSelect);
$('#up-kind').addEventListener('change', () => { $('#up-file').accept = KIND_ACCEPT[$('#up-kind').value]; $('#up-file').value = ''; });

/** Point the upload form at a session and move to it. */
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

/** Upload straight to storage with the signed link (XHR, because fetch reports no upload progress). */
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
  } catch (ex) { sayError(st, ex); }
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
  } catch (ex) { sayError(st, ex); }
});

/* ---------------- 4. limits and access ---------------- */

/** A <dl> of name and number pairs, or one `emptyName` row when there are none. */
function renderKv(node, values, emptyName) {
  const entries = Object.entries(values);
  node.replaceChildren(...(entries.length ? entries : [[emptyName, '']]).map(([k, v]) =>
    el('div', {}, el('dt', { text: humanize(k) }), el('dd', { text: fmtNum(v) }))));
}

function renderLimits() {
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

/* Web answers (beyond my slides): on/off, the daily cap, and an optional free voice. Never the clone. */
function renderWebAnswers() {
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
function renderOpenAccess() {
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

/* ---------------- 4b. answer thresholds ---------------- */

const TH = {
  slide: { key: 'slide_threshold', input: '#slide-th', now: '#slide-th-now', status: '#slide-th-status', name: 'Slide threshold' },
  info: { key: 'info_threshold', input: '#info-th', now: '#info-th-now', status: '#info-th-status', name: 'Course-info threshold' },
  margin: { key: 'info_margin', input: '#info-margin', now: '#info-margin-now', status: '#info-margin-status', name: 'Course-info margin', form: '#info-margin-form', reset: '#info-margin-reset', min: 0, max: 0.3 },
};
const TH_SOURCE = { code: 'my code default', env: 'the INFO_THRESHOLD environment variable', settings: 'a Settings override' };
const TH_LABEL = { slide_threshold: 'slide threshold', info_threshold: 'course-info threshold', info_margin: 'course-info margin' };
const fmtTh3 = (v) => (typeof v === 'number' ? String(Math.round(v * 1000) / 1000) : 'none');

/** The three threshold fields, where each value comes from, and the last five changes. */
function renderThresholds(d) {
  if (!d) return;
  const s = d.slide || {}, i = d.info || {}, m = d.margin || {};
  $('#slide-th').value = s.value ?? '';
  $('#info-th').value = i.value ?? '';
  $('#info-margin').value = m.value ?? '';
  $('#slide-th-now').textContent = `Slides scoring ${fmtTh3(s.value)} or higher are used. Source: ${TH_SOURCE[s.source] || s.source}` +
    (s.source === 'settings' ? ` (default ${fmtTh3(s.default)}).` : '.');
  $('#info-th-now').textContent = `A Canvas answer needs ${fmtTh3(i.value)} or higher and must beat the best slide by the margin. Source: ${TH_SOURCE[i.source] || i.source}` +
    (i.source === 'settings' ? ` (default ${fmtTh3(i.default)}).` : '.');
  $('#info-margin-now').textContent = `The best Canvas page must score at least ${fmtTh3(m.value)} more than the best slide, so close calls go to the slides. Source: ${m.source === 'env' ? 'the INFO_MARGIN environment variable' : (TH_SOURCE[m.source] || m.source)}` +
    (m.source === 'settings' ? ` (default ${fmtTh3(m.default)}).` : '.');
  $('#slide-th-reset').disabled = s.source !== 'settings';
  $('#info-th-reset').disabled = i.source !== 'settings';
  $('#info-margin-reset').disabled = m.source !== 'settings';
  const hist = asList(d.history).slice(0, 5);
  $('#th-history').replaceChildren(...(hist.length ? hist.map(h => el('li', {
    text: `${fmtWhen(h.at)}, ${h.who || 'admin'}: ${TH_LABEL[h.setting] || h.setting} ${fmtTh3(h.old)} to ${fmtTh3(h.new)}` +
      (h.source && h.source !== 'settings' ? ` (reset to ${TH_SOURCE[h.source] || h.source})` : ''),
  })) : [el('li', { class: 'muted', text: 'No changes yet.' })]));
}

/** Fetch the thresholds and their history. */
async function loadThresholds() {
  try {
    const r = await api('/api/admin/thresholds');
    if (r.ok) renderThresholds(r.data);
  } catch (e) { /* auth handled in api() */ }
}

/** Save one threshold (null resets it to the default). The range check here matches the server's. */
async function saveThreshold(which, value) {
  const t = TH[which];
  const st = $(t.status);
  const lo = t.min ?? 0.3, hi = t.max ?? 0.9;
  if (value !== null && !(Number.isFinite(value) && value >= lo && value <= hi)) { say(st, `Use a number from ${lo.toFixed(2)} to ${hi.toFixed(2)}.`, 'err'); return; }
  try {
    const r = await api('/api/admin/thresholds', { method: 'PUT', body: { [t.key]: value } });
    if (!r.ok) { say(st, detail(r), 'err'); return; }
    renderThresholds(r.data);
    say(st, value === null ? `${t.name} reset to the default. Run an eval to check it.` : `Saved. Run an eval to check the new value.`, 'ok');
  } catch (ex) { sayError(st, ex); }
}

for (const which of Object.keys(TH)) {
  $(TH[which].form || `#${which}-th-form`).addEventListener('submit', (e) => {
    e.preventDefault();
    const raw = $(TH[which].input).value.trim();
    saveThreshold(which, raw === '' ? NaN : Number(raw));
  });
  $(TH[which].reset || `#${which}-th-reset`).addEventListener('click', () => saveThreshold(which, null));
}

/* ---------------- 5. activity ---------------- */

function renderToday() {
  renderKv($('#today-kv'), S.status?.today || {}, 'nothing yet');
}

/* What answered each question (question_log.kind). docs/TESTING_AND_SCORES.md explains each one. */
const KIND_BADGES = {
  course_content: { text: 'Covered', cls: 'ok', title: 'Slides found at or above the threshold, narrated by the model.' },
  stored_topic: { text: 'Stored answer', cls: 'ok', title: 'A suggested question: its stored answer was replayed. No search, no model.' },
  faq: { text: 'FAQ', cls: 'info', title: 'Answered from my course FAQ, word for word. No search, no model.' },
  course_info: { text: 'From Canvas', cls: 'info', title: 'Answered from my Canvas pages (syllabus, policies, assignments), written by the model from those pages only.' },
  web: { text: 'From the web', cls: 'info', title: 'No slide covered it, but it was about AI, data or coding tools: answered from a web search, with source links. Never in my voice.' },
  logistics: { text: 'Referred to Ben', cls: 'info', title: 'A logistics question: the student was sent to me.' },
  not_covered: { text: 'Not covered', cls: 'warn', title: 'No slide scored at or above the threshold.' },
  alert: { text: 'Student alert', cls: 'warn', title: 'A student reported a broken quiz, submission or API key. See Student alerts for whether a text went out.' },
};
/** What answered the question (an older row without a kind is inferred from `covered`). */
function kindBadge(x) {
  const fallback = x.covered ? KIND_BADGES.course_content : KIND_BADGES.not_covered;
  const b = KIND_BADGES[x.kind] || fallback;
  const title = x.kind_inferred ? `${b.title} (Inferred from the score: logged before kinds were recorded.)` : b.title;
  return el('span', { class: `pill ${b.cls}`, text: b.text, title });
}
/* Smoke checks, evals and model tests (question_log.source); "likely" when only the question text matched. */
function testBadge(x) {
  if (!x.test) return null;
  const title = x.test_inferred
    ? 'Likely test traffic: the question is one of the smoke-check questions (logged before sources were recorded).'
    : `Test traffic (${x.source}): left out of student analytics.`;
  return el('span', { class: 'pill off', text: x.test_inferred ? 'Test?' : 'Test', title, style: 'margin-left:.3rem' });
}
/* Why a course-info or web answer fell back (question_log.fallback_reason; app/course_info.py, app/web_answer.py). */
const FALLBACK_REASONS = {
  provider_credits: 'Model account out of credits',
  provider_auth: 'Model key rejected',
  provider_rate_limit: 'Model rate-limited',
  provider_unreachable: 'Model unreachable',
  provider_refused: 'Model declined',
  provider_error: 'Model error',
  daily_cap: 'Daily model-call cap reached',
  not_json: 'Reply was not JSON',
  no_answer: 'Reply had no answer',
  too_long: 'Answer too long',
  not_grounded: 'Answer not grounded in Canvas',
  unsafe_text: 'Answer failed a safety check',
  no_links: 'Search gave no usable link',
  error: 'Unexpected error',
};
/** "Fell back: ..." when a course-info or web answer could not be written. */
function fallbackBadge(x) {
  if (!x.fallback_reason) return null;
  const text = FALLBACK_REASONS[x.fallback_reason] || x.fallback_reason;
  const saw = x.kind === 'web' ? '"Here is where to look." with links and my closest slides'
    : x.kind === 'course_info' ? 'the Canvas text' : 'my fallback';
  return el('span', { class: 'pill warn', text: `Fell back: ${text}`, title: `The student saw ${saw}, not a written answer (${x.fallback_reason}).`, style: 'margin-left:.3rem' });
}
/** "provider / model", or "none" when no model was called. */
function modelText(x) {
  return [x.provider, x.model].filter(Boolean).join(' / ') || 'none';
}

/** Refresh the status counts and the last 50 questions. */
async function loadActivity() {
  try {
    const [st, lg] = await Promise.all([api('/api/admin/status'), api('/api/admin/log')]);
    if (st.ok) renderStatus(st.data);
    const rows = lg.ok ? asList(lg.data, 'rows', 'log', 'items').slice(0, 50) : [];
    $('#log-body').replaceChildren(...(rows.length ? rows.map(x => el('tr', {},
      el('td', { class: 'small muted', text: fmtWhen(x.created_at || x.at || x.time) }),
      el('td', {}, kindBadge(x), testBadge(x), fallbackBadge(x)),
      el('td', { class: 'full', text: x.question || '' }),
      el('td', { class: 'num', 'data-label': 'Top score', title: x.top_score != null ? null : 'No search ran', text: x.top_score != null ? Number(x.top_score).toFixed(3) : '' }),
      el('td', { class: 'num', 'data-label': 'Latency', text: x.latency_ms != null ? `${fmtNum(x.latency_ms)} ms` : '' }),
      el('td', { class: `small full${x.provider || x.model ? '' : ' muted'}`, 'data-label': 'Model', title: x.provider || x.model ? null : 'No model was called for this question', text: modelText(x) }),
    )) : [el('tr', {}, el('td', { colspan: '6', class: 'muted', text: lg.ok ? 'No questions logged yet.' : detail(lg, 'Couldn\'t load the log.') }))]));
  } catch (e) { /* auth handled in api(); network shows on next refresh */ }
}
$('#refresh-activity').addEventListener('click', loadActivity);

/* ---------------- 6. prompts ---------------- */
// Every model-facing prompt (app/prompts.py). Edits are checked on the server; safety checks stay in code.

const SCORES_DOC = 'https://github.com/bcollier/faculty-twin/blob/main/docs/TESTING_AND_SCORES.md#prompt-changes';
const P = { list: [], maxChars: 12000, sel: null, history: [], review: null };

/** Draw the diff in the review panel (screen readers hear "added" and "removed") and summarize it. */
function renderDiff(before, after) {
  const parts = DIFF.diffParts(before, after);
  const added = DIFF.countWords(parts, '+'), removed = DIFF.countWords(parts, '-');
  const nodes = parts.map(([op, t]) => {
    if (op === '=') return document.createTextNode(t);
    const tag = op === '+' ? 'ins' : 'del';
    return el(tag, {}, el('span', { class: 'visually-hidden', text: op === '+' ? ' [added: ' : ' [removed: ' }), t,
      el('span', { class: 'visually-hidden', text: '] ' }));
  });
  $('#pe-diff').replaceChildren(el('pre', { class: 'diff-text' }, ...nodes));
  const same = before === after;
  const summary = same ? 'No differences.'
    : `${fmtNum(added)} word${added === 1 ? '' : 's'} added, ${fmtNum(removed)} removed. Added text is underlined in green, removed text is struck through in red.`;
  return { added, removed, summary, same };
}

/** The prompt list, then select `keep` (or the one selected, or the first). */
async function loadPrompts(keep) {
  say($('#prompts-status'), 'Loading prompts...');
  try {
    const r = await api('/api/admin/prompts');
    if (!r.ok) { say($('#prompts-status'), detail(r, 'Couldn\'t load the prompts.'), 'err'); return; }
    P.list = asList(r.data, 'prompts');
    P.maxChars = r.data?.max_chars || 12000;
    say($('#prompts-status'), '');
  } catch (e) { sayError($('#prompts-status'), e); return; }
  const name = keep || P.sel?.name || P.list[0]?.name;
  if (name) selectPrompt(name, { quiet: true });
  else renderPromptList();
}

/** Unsaved edits, Edited, or Default. */
function promptBadge(p) {
  if (P.sel?.name === p.name && isDirty()) return el('span', { class: 'pill warn', text: 'Unsaved edits' });
  return p.is_overridden ? el('span', { class: 'pill info', text: 'Edited' }) : el('span', { class: 'pill off', text: 'Default' });
}

/** The prompt picker, with each prompt's state. */
function renderPromptList() {
  $('#prompt-list').replaceChildren(...P.list.map(p => el('li', {},
    el('button', {
      type: 'button', class: 'prompt-opt', 'aria-pressed': String(P.sel?.name === p.name),
      onclick: () => selectPrompt(p.name),
    },
    el('span', { class: 'm-id', text: p.title }),
    el('span', { class: 'm-meta' }, promptBadge(p), ' ',
      p.used_by === 'evals' ? 'Used in eval runs' : 'Students hear its effect',
      p.updated_at ? ` · saved ${fmtWhen(p.updated_at)}` : '')))));
}

/** The editor holds text that is not saved. */
function isDirty() { return !!P.sel && $('#pe-text').value !== P.sel.current; }

/** Open a prompt in the editor (asks first when that would discard unsaved edits, unless `quiet`). */
function selectPrompt(name, { quiet = false } = {}) {
  if (!quiet && P.sel && P.sel.name !== name && isDirty()
      && !window.confirm('Discard your unsaved edits to this prompt?')) return;
  const p = P.list.find(x => x.name === name);
  if (!p) return;
  P.sel = p;
  $('#prompt-editor').hidden = false;
  $('#pe-title').textContent = p.title;
  $('#pe-desc').textContent = p.description;
  $('#pe-meta').textContent = p.is_overridden
    ? `Edited. Saved ${fmtWhen(p.updated_at)}${p.note ? `: "${p.note}"` : ''}.`
    : 'Using the built-in default.';
  const vars = Object.entries(p.variables || {});
  let help = vars.length
    ? `Placeholders the code fills in: ${vars.map(([k, v]) => `{${k}}${(p.required || []).includes(k) ? ' (required)' : ''} is ${v}`).join('; ')}.`
    : 'This prompt has no placeholders.';
  const words = p.must_mention || [];
  if (words.length) help += ` Keep the word${words.length > 1 ? 's' : ''} ${words.map(w => `"${w}"`).join(', ')}: the code reads the reply by ${words.length > 1 ? 'them' : 'it'}.`;
  $('#pe-vars').textContent = help;
  $('#pe-text').value = p.current;
  $('#pe-test').disabled = !p.testable;
  $('#pe-test-q').disabled = !p.testable;
  $('#pe-test-hint').textContent = p.testable
    ? 'Runs one question through the real path with the text in the editor. Nothing is saved, and the same safety checks apply. Counts against your question limits.'
    : 'This prompt is used in eval runs, not for students. Test it by running an eval after you save.';
  $('#pe-reset').disabled = !p.is_overridden;
  say($('#pe-status'), '');
  say($('#pe-test-status'), '');
  $('#pe-test-output').hidden = true;
  $('#pe-after').hidden = true;
  closeReview();
  updateCount();
  renderPromptList();
  loadHistory();
}

/** "1,234 of 12,000 characters", red when over. */
function updateCount() {
  const n = $('#pe-text').value.length;
  const over = n > P.maxChars;
  const node = $('#pe-count');
  node.textContent = `${fmtNum(n)} of ${fmtNum(P.maxChars)} characters${over ? '. Too long to save.' : ''}`;
  node.classList.toggle('error-text', over);
}

$('#pe-text').addEventListener('input', () => {
  updateCount();
  say($('#pe-status'), isDirty() ? 'Not saved yet.' : '');
  if (P.review?.mode === 'save') closeReview();
  renderPromptList();
});

/* One review panel for every change: it always shows the diff before anything is written. */
function openReview(mode, { before, after, title, confirm, version } = {}) {
  P.review = { mode, before, after, version };
  const d = renderDiff(before, after);
  const writes = mode !== 'compare';
  const nothing = writes && d.same;
  $('#pe-review').hidden = false;
  $('#pe-review-h').textContent = title;
  $('#pe-review-summary').textContent = d.summary;
  $('#pe-note-field').hidden = !writes;
  $('#pe-confirm').hidden = !writes;
  $('#pe-confirm').textContent = confirm || 'Confirm';
  $('#pe-confirm').disabled = nothing;
  $('#pe-cancel').textContent = writes ? 'Cancel' : 'Close';
  $('#pe-note').value = '';
  say($('#pe-review-status'), nothing ? 'Nothing would change, so there is nothing to save.' : '');
  $('#pe-review-h').focus();
  $('#pe-review').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
/** Close the review panel without writing anything. */
function closeReview() { P.review = null; $('#pe-review').hidden = true; }
$('#pe-cancel').addEventListener('click', closeReview);

$('#pe-save').addEventListener('click', () => {
  if (!P.sel) return;
  const text = $('#pe-text').value;
  if (!text.trim()) { say($('#pe-status'), 'The prompt is empty.', 'err'); return; }
  if (text.length > P.maxChars) { say($('#pe-status'), `Keep it under ${fmtNum(P.maxChars)} characters.`, 'err'); return; }
  openReview('save', {
    before: P.sel.current, after: text, confirm: 'Confirm save',
    title: 'Review your changes against the saved version',
  });
});
$('#pe-compare-default').addEventListener('click', () => {
  if (!P.sel) return;
  openReview('compare', { before: P.sel.default, after: $('#pe-text').value, title: 'The editor text compared with the built-in default' });
});
$('#pe-discard').addEventListener('click', () => {
  if (!P.sel) return;
  $('#pe-text').value = P.sel.current;
  updateCount(); closeReview(); renderPromptList();
  say($('#pe-status'), 'Edits discarded.');
});
$('#pe-reset').addEventListener('click', () => {
  if (!P.sel) return;
  openReview('reset', {
    before: P.sel.current, after: P.sel.default, confirm: 'Confirm reset to default',
    title: 'Reset to the built-in default: changes from the saved version',
  });
});

$('#pe-confirm').addEventListener('click', async () => {
  const rv = P.review;
  if (!rv || !P.sel || rv.mode === 'compare') return;
  const name = P.sel.name;
  const note = $('#pe-note').value.trim();
  if (rv.mode === 'save' && !note) { say($('#pe-review-status'), 'Add a short note so the history says why.', 'err'); $('#pe-note').focus(); return; }
  const path = `/api/admin/prompts/${encodeURIComponent(name)}`;
  const req = {
    save: () => api(path, { method: 'PUT', body: { text: rv.after, note } }),
    reset: () => api(`${path}/reset`, { method: 'POST', body: { note: note || undefined } }),
    restore: () => api(`${path}/restore`, { method: 'POST', body: { version: rv.version, note: note || undefined } }),
  }[rv.mode];
  $('#pe-confirm').disabled = true;
  say($('#pe-review-status'), 'Saving...');
  try {
    const r = await req();
    if (!r.ok) { say($('#pe-review-status'), detail(r), 'err'); return; }
    const i = P.list.findIndex(x => x.name === name);
    if (i >= 0 && r.data) P.list[i] = r.data;
    selectPrompt(name, { quiet: true });
    showAfterSave(rv.mode);
  } catch (e) { sayError($('#pe-review-status'), e); }
  finally { $('#pe-confirm').disabled = false; }
});

/** After a save, reset or restore: when it takes effect, and a link to run an eval. */
function showAfterSave(mode) {
  const p = P.sel;
  const what = { save: 'Saved', reset: 'Reset to the default', restore: 'Restored' }[mode] || 'Saved';
  const when = p.used_by === 'evals' ? 'The next eval run uses it.' : 'New questions use it within 30 seconds.';
  $('#pe-after-text').textContent = `${what}. ${when} Run an eval to check that answers still score well.`;
  const evals = document.getElementById('sec-evals');
  const link = $('#pe-run-eval');
  if (evals) { link.href = '#sec-evals'; link.removeAttribute('target'); link.removeAttribute('rel'); }
  else { link.href = SCORES_DOC; link.target = '_blank'; link.rel = 'noopener'; }
  $('#pe-after').hidden = false;
  say($('#pe-status'), '');
}
$('#pe-run-eval').addEventListener('click', (e) => {
  const evals = document.getElementById('sec-evals');
  if (!evals) return; // the link opens Testing and scores in a new tab instead
  e.preventDefault();
  // The Evals section can listen for this to preselect the prompt that just changed.
  document.dispatchEvent(new CustomEvent('ft:run-eval', { detail: { prompt: P.sel?.name, hash: P.sel?.hash } }));
  evals.scrollIntoView({ behavior: 'smooth', block: 'start' });
  if (!evals.hasAttribute('tabindex')) evals.setAttribute('tabindex', '-1');
  evals.focus({ preventScroll: true });
});

$('#pe-test').addEventListener('click', async () => {
  if (!P.sel?.testable) return;
  const text = $('#pe-text').value;
  const question = $('#pe-test-q').value.trim();
  const out = $('#pe-test-output');
  if (!question) { say($('#pe-test-status'), 'Type a test question.', 'err'); return; }
  const btn = $('#pe-test');
  btn.disabled = true;
  out.hidden = true;
  say($('#pe-test-status'), 'Testing the draft...');
  const t0 = performance.now();
  try {
    const r = await api(`/api/admin/prompts/${encodeURIComponent(P.sel.name)}/test`, { method: 'POST', body: { text, question }, timeout: 90000 });
    const ms = r.data?.latency_ms ?? Math.round(performance.now() - t0);
    if (!r.ok) { say($('#pe-test-status'), `${detail(r)} (${fmtNum(ms)} ms)`, 'err'); return; }
    const errs = r.data?.errors || [];
    say($('#pe-test-status'), r.data?.ok
      ? `Worked in ${fmtNum(ms)} ms with ${r.data.provider} / ${r.data.model}.${errs.length ? ' One reply was rejected by the safety checks first.' : ''}`
      : `The safety checks rejected the model's replies, so students would get the fallback. ${fmtNum(ms)} ms.`, r.data?.ok ? 'ok' : 'err');
    out.textContent = JSON.stringify({ ...(r.data?.output || {}), ...(errs.length ? { rejected_replies: errs } : {}) }, null, 2);
    out.hidden = false;
  } catch (e) { sayError($('#pe-test-status'), e); }
  finally { btn.disabled = !P.sel?.testable; }
});

/** The selected prompt's saved versions, newest first. */
async function loadHistory() {
  const name = P.sel?.name;
  if (!name) return;
  say($('#pe-history-status'), 'Loading history...');
  $('#pe-history').replaceChildren();
  try {
    const r = await api(`/api/admin/prompts/${encodeURIComponent(name)}/history`);
    if (P.sel?.name !== name) return;
    if (!r.ok) { say($('#pe-history-status'), detail(r, 'Couldn\'t load the history.'), 'err'); return; }
    P.history = asList(r.data, 'versions');
    say($('#pe-history-status'), P.history.length ? '' : 'No saved versions yet. The built-in default is in use until the first save.');
    renderHistory();
  } catch (e) { sayError($('#pe-history-status'), e); }
}

/** Each saved version with Compare and Restore (both open the review panel first). */
function renderHistory() {
  const live = P.sel?.hash;
  $('#pe-history').replaceChildren(...P.history.map((v, i) => {
    const isLive = i === 0 && v.hash === live;
    const when = fmtWhen(v.saved_at || '');
    return el('li', { class: `history-item${isLive ? ' live' : ''}` },
      el('div', {},
        el('span', { class: 'v-name', text: when }), ' ',
        isLive ? el('span', { class: 'pill ok', text: 'Live now' }) : null, ' ',
        v.reset ? el('span', { class: 'pill off', text: 'Default' }) : null,
        el('div', { class: 'small muted', text: v.note || 'No note.' })),
      el('div', { class: 'actions' },
        el('button', {
          type: 'button', class: 'btn btn-small', 'aria-label': `Compare the version saved ${when} with the saved prompt`,
          onclick: () => openReview('compare', { before: P.sel.current, after: v.text, title: `The version saved ${when} compared with the saved prompt` }),
        }, 'Compare'),
        el('button', {
          type: 'button', class: 'btn btn-small btn-ghost', disabled: isLive, 'aria-label': `Restore the version saved ${when}`,
          onclick: () => openReview('restore', {
            before: P.sel.current, after: v.text, version: v.version, confirm: 'Confirm restore',
            title: `Restore the version saved ${when}: changes from the saved prompt`,
          }),
        }, 'Restore')));
  }));
}

/* ---------------- nav highlight ---------------- */

const navLinks = [...document.querySelectorAll('.admin-nav a')];
const io = new IntersectionObserver((entries) => {
  for (const en of entries) if (en.isIntersecting) {
    navLinks.forEach(a => a.setAttribute('aria-current', String(a.getAttribute('href') === `#${en.target.id}`)));
  }
}, { rootMargin: '-30% 0px -60% 0px' });
document.querySelectorAll('section.panel[id]').forEach(s => io.observe(s));

boot();

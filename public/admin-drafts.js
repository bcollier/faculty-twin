// Faculty Twin: Settings > Draft slides. Loaded by admin.html next to admin.js; talks only to
// /api/admin/helper-slides and /api/admin/drafts* with the ft_admin cookie.
// Slides are drawn by public/helper-slide.js from a checked JSON spec: no markup ever comes from a model.

import { LABEL, renderHelperFigure, toMarkdown, toSvgString } from './helper-slide.js';

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
const fmtWhen = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
};
const STATUS = {
  draft: { text: 'Draft', cls: 'off' },
  approved: { text: 'Approved', cls: 'ok' },
  hidden: { text: 'Hidden', cls: 'warn' },
};

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
const detail = (r, fallback) => (typeof r?.data?.detail === 'string' ? r.data.detail : fallback || `Request failed (HTTP ${r?.status}).`);
function say(node, text, kind = '') { node.textContent = text || ''; node.className = `status-line ${kind}`.trim(); }

let pending = null; // the generated, unsaved draft: { topic, spec }

function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = el('a', { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function slug(text) {
  return String(text || 'slide').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) || 'slide';
}

function exportButtons(spec, status) {
  return [
    el('button', { type: 'button', class: 'btn btn-small', text: 'Download SVG',
      onclick: () => download(`${slug(spec.title)}.svg`, toSvgString(spec), 'image/svg+xml') }),
    el('button', { type: 'button', class: 'btn btn-small', text: 'Copy as Markdown', onclick: async () => {
      try { await navigator.clipboard.writeText(toMarkdown(spec)); say(status, 'Copied as Markdown.', 'ok'); }
      catch { say(status, 'The browser blocked the clipboard. Use Download SVG instead.', 'err'); }
    } }),
  ];
}

/* ---------------- settings ---------------- */

function renderSettings(d) {
  $('#hs-enabled').checked = !!d.helper_slides_enabled;
  $('#hs-approved').checked = !!d.helper_slides_use_approved;
  $('#hs-cap').value = d.daily_helper_slide_cap;
  $('#hs-cap-hint').textContent = `Model calls for helper slides per day (DAILY_HELPER_SLIDE_CAP, default 150). Each is about a cent. Past the cap, answers go out without one. Zero turns them off. Today so far: ${d.helper_slides_today ?? 0}.`;
}

async function loadSettings() {
  const r = await call('/api/admin/helper-slides');
  if (r.ok) renderSettings(r.data);
  else say($('#hs-settings-status'), detail(r), 'err');
}

$('#hs-settings').addEventListener('submit', async (e) => {
  e.preventDefault();
  const cap = Number($('#hs-cap').value);
  const st = $('#hs-settings-status');
  if (!Number.isInteger(cap) || cap < 0 || cap > 100000) { say(st, 'Use a whole number from 0 to 100,000.', 'err'); return; }
  const r = await call('/api/admin/helper-slides', { method: 'PUT', body: {
    helper_slides_enabled: $('#hs-enabled').checked, helper_slides_use_approved: $('#hs-approved').checked, daily_helper_slide_cap: cap } });
  if (!r.ok) { say(st, detail(r), 'err'); return; }
  renderSettings(r.data);
  say(st, 'Saved.', 'ok');
});

/* ---------------- generate ---------------- */

async function loadGaps() {
  const r = await call('/api/admin/drafts/gaps');
  const gaps = r.ok ? (r.data.gaps || []) : [];
  $('#hs-gap').replaceChildren(
    el('option', { value: '', text: gaps.length ? 'Content gaps from the question log' : 'No declined questions yet' }),
    ...gaps.map(g => el('option', { value: g.question, text: `${g.question} (${g.count})` })));
}
$('#hs-gap').addEventListener('change', () => { if ($('#hs-gap').value) $('#hs-topic').value = $('#hs-gap').value; });

$('#hs-gen').addEventListener('submit', async (e) => {
  e.preventDefault();
  const topic = $('#hs-topic').value.trim();
  const st = $('#hs-gen-status');
  if (!topic) { say(st, 'Type a topic or pick a declined question.', 'err'); return; }
  say(st, 'Drafting...');
  $('#hs-save').disabled = true;
  const r = await call('/api/admin/drafts/generate', { method: 'POST', body: { topic } });
  if (!r.ok) { say(st, detail(r), 'err'); $('#hs-preview').replaceChildren(); pending = null; return; }
  pending = { topic: r.data.topic, spec: r.data.spec };
  $('#hs-preview').replaceChildren(renderHelperFigure(pending.spec), el('div', { class: 'actions' }, ...exportButtons(pending.spec, st)));
  $('#hs-save').disabled = false;
  say(st, 'Not saved yet. Save it as a draft to review it below.');
});

$('#hs-save').addEventListener('click', async () => {
  if (!pending) return;
  const st = $('#hs-gen-status');
  const r = await call('/api/admin/drafts', { method: 'POST', body: pending });
  if (!r.ok) { say(st, detail(r), 'err'); return; }
  pending = null;
  $('#hs-save').disabled = true;
  $('#hs-preview').replaceChildren();
  say(st, 'Saved as a draft.', 'ok');
  loadDrafts();
});

/* ---------------- review ---------------- */

function draftItem(d) {
  const status = el('p', { class: 'status-line', role: 'status' });
  const pill = STATUS[d.status] || STATUS.draft;
  const editor = el('textarea', { class: 'hs-edit', rows: '12', spellcheck: 'false', 'aria-label': 'Slide spec (JSON)', hidden: true });
  editor.value = JSON.stringify(d.spec, null, 2);
  const saveEdit = el('button', { type: 'button', class: 'btn btn-small btn-primary', text: 'Save text', hidden: true });
  const setStatus = (next) => async () => {
    const r = await call(`/api/admin/drafts/${encodeURIComponent(d.id)}`, { method: 'PATCH', body: { status: next } });
    if (!r.ok) { say(status, detail(r), 'err'); return; }
    loadDrafts();
  };
  saveEdit.addEventListener('click', async () => {
    let spec;
    try { spec = JSON.parse(editor.value); } catch { say(status, 'That is not valid JSON.', 'err'); return; }
    const r = await call(`/api/admin/drafts/${encodeURIComponent(d.id)}`, { method: 'PATCH', body: { spec } });
    if (!r.ok) { say(status, detail(r), 'err'); return; }
    loadDrafts();
  });
  return el('li', { class: 'hs-item' },
    el('div', { class: 'hs-head' },
      el('span', { class: `pill ${pill.cls}`, text: pill.text }),
      el('strong', { text: d.topic }),
      el('span', { class: 'muted small', text: `saved ${fmtWhen(d.created_at)}` })),
    renderHelperFigure(d.spec),
    el('div', { class: 'actions' },
      d.status !== 'approved' ? el('button', { type: 'button', class: 'btn btn-small', text: 'Approve', onclick: setStatus('approved') }) : null,
      d.status !== 'hidden' ? el('button', { type: 'button', class: 'btn btn-small', text: 'Hide', onclick: setStatus('hidden') }) : null,
      d.status !== 'draft' ? el('button', { type: 'button', class: 'btn btn-small', text: 'Back to draft', onclick: setStatus('draft') }) : null,
      el('button', { type: 'button', class: 'btn btn-small', text: 'Edit text', onclick: () => {
        editor.hidden = !editor.hidden; saveEdit.hidden = editor.hidden;
        say(status, editor.hidden ? '' : 'Edit the words in the JSON, then Save text. The server checks it again.');
      } }),
      ...exportButtons(d.spec, status),
      el('button', { type: 'button', class: 'btn btn-small', text: 'Delete', onclick: async () => {
        if (!window.confirm(`Delete the draft "${d.topic}"? This cannot be undone.`)) return;
        const r = await call(`/api/admin/drafts/${encodeURIComponent(d.id)}`, { method: 'DELETE' });
        if (!r.ok) { say(status, detail(r), 'err'); return; }
        loadDrafts();
      } })),
    editor, saveEdit, status);
}

async function loadDrafts() {
  const r = await call('/api/admin/drafts');
  if (!r.ok) { say($('#hs-list-status'), detail(r, 'Couldn\'t load the drafts.'), 'err'); return; }
  const list = r.data.drafts || [];
  say($('#hs-list-status'), list.length ? `${list.length} saved. Each is labeled "${LABEL}".` : 'No drafts yet.');
  $('#hs-list').replaceChildren(...list.map(draftItem));
}

$('#hs-refresh').addEventListener('click', loadDrafts);
document.addEventListener('ft-admin-enter', () => { loadSettings(); loadGaps(); loadDrafts(); });

// Settings > 1. Model: provider, the model picker, the price check, and a test question.
// Part of the Settings page; admin.js has the map.

import { $, api, asList, AuthError, detail, el, errText, fmtNum, keepSettings, say, sayError } from './helpers.js';
import { S } from './state.js';
import { updateProviderWarning } from './status.js';
import { renderOpenAccess, renderWebAnswers } from './limits.js';

/* ---------------- 1. model ---------------- */

/** The saved settings into the model, cap and web answer fields. */
export async function loadSettings() {
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
export async function loadModels() {
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

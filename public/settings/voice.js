// Settings > 2. Voice: the voice groups, previews, and the fallback voice.
// Part of the Settings page; admin.js has the map.

import { $, api, asList, detail, el, keepSettings, say, sayError } from './helpers.js';
import { S } from './state.js';
import { renderWebAnswers } from './limits.js';

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
export async function loadVoices() {
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
export function freeVoiceOptions(chosen, savedLabel) {
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

// Student page: which voice speaks and how it is labeled; entering the app after the passcode.
// Part of the student page; app.js has the map.

import { COPY } from './copy.js';
import { api } from './helpers.js';
import { app, loadCourseChoice, ui } from './state.js';
import { showScreen } from './screens.js';
import { setCourse } from './chips.js';
import { ask } from './asking.js';
import { player } from './player.js';

/* =====================================================================
   Voice label
   The label must name the voice that actually speaks: my clone ("made from my
   recordings"), a stock or free voice ("a stock voice, not mine"), or nothing
   for captions only. The server decides the kind; the page never assumes the clone.
   ===================================================================== */

const IDLE_VOICE_TEXT = {
  clone: 'The voice is AI-generated from recordings of me, Ben Collier. It only explains what is on my slides and what I said in class.',
  stock: 'The voice is AI-generated (a stock voice, not mine). It only explains what is on my slides and what I said in class.',
  // A free voice reads the same as a stock one: neither is mine.
  free: 'The voice is AI-generated (a stock voice, not mine). It only explains what is on my slides and what I said in class.',
  unverified: 'The voice is AI-generated. It only explains what is on my slides and what I said in class.',
};

/** Ask the server which voice will speak, then label it. A failure keeps the neutral label. */
export async function loadVoice() {
  try {
    const res = await api('/api/voice');
    if (res.ok && res.data) app.voice = res.data;
  } catch { /* keep the neutral label already on the page */ }
  renderIdleVoice();
  updateVoiceLabel();
}

/** The voice note on the idle screen; hidden when answers are captions only. */
function renderIdleVoice() {
  const kind = app.voice?.kind;
  if (kind === 'none') { ui.idleVoiceLabel.hidden = true; return; }
  ui.idleVoiceLabel.hidden = false;
  ui.idleVoiceText.textContent = IDLE_VOICE_TEXT[kind] || IDLE_VOICE_TEXT.unverified;
}

/** The dock label follows the voice speaking the current segment (it changes if the fallback voice takes over). */
export function updateVoiceLabel() {
  let label = app.voice ? app.voice.label : 'AI voice.';
  const seg = player.segments[player.index];
  if (player.answer && seg) {
    const v = player.useFallback ? seg.voice_fallback : seg.voice;
    label = player.captionsOnly ? null : (v?.label || null);
  }
  ui.voiceLabel.textContent = label || '';
  ui.voiceLabel.hidden = !label;
  // The narration box says which voice reads it, or that this answer is captions only.
  if (player.answer) ui.narrationVoice.textContent = player.captionsOnly ? COPY.captionsOnly : (label || '');
}

/** Into the app: restore the course filter, and ask the question that was waiting on the passcode. */
export function enterApp() {
  showScreen('app');
  setCourse(loadCourseChoice());
  if (app.pendingQuestion) {
    const q = app.pendingQuestion;
    app.pendingQuestion = null;
    ask(q);
    return;
  }
  if (ui.screens.app.dataset.view === 'idle') ui.idleQ.focus();
}

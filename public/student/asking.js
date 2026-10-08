// Student page: asking a question, routing the reply to the stage or the player, and fresh links when a
// signed link expires. Part of the student page; app.js has the map.

import { ASK_TIMEOUT_MS, COPY } from './copy.js';
import { api } from './helpers.js';
import { app, ui } from './state.js';
import { setView, showLogin } from './screens.js';
import { showFaqAnswer, showHelperSlide, showStageError, showStageMessage, showWebAnswer } from './stage.js';
import { addTwinMessage, addUserMessage, renderSourcesList, summarize } from './chat.js';
import { loadAnswer, player, stopPlayback } from './player.js';

/* =====================================================================
   Asking
   ===================================================================== */

let silentUnlockUrl = null;
/** Called inside the click/tap that asks, so the browser lets us play audio later. */
function unlockAudio() {
  if (!silentUnlockUrl) silentUnlockUrl = URL.createObjectURL(makeSilentWav(0.05));
  const a = new Audio(silentUnlockUrl);
  a.play().catch(() => {});
}
/** A tiny silent 8-bit WAV, built in memory: playing it inside the tap unlocks audio on phones. */
function makeSilentWav(seconds) {
  const rate = 8000, n = Math.max(1, Math.floor(rate * seconds));
  const buf = new ArrayBuffer(44 + n), v = new DataView(buf);
  const str = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  str(0, 'RIFF'); v.setUint32(4, 36 + n, true); str(8, 'WAVE'); str(12, 'fmt ');
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate, true); v.setUint16(32, 1, true); v.setUint16(34, 8, true);
  str(36, 'data'); v.setUint32(40, n, true);
  for (let i = 0; i < n; i++) v.setUint8(44 + i, 128);
  return new Blob([buf], { type: 'audio/wav' });
}

/**
 * Ask a question: clear the stage, show the spinner, POST /api/ask, then show what came back.
 * `source` ("chip", "follow_up", "typed") is logged with the question for Analytics.
 * Only the newest question's reply is shown (app.requestId).
 */
export async function ask(raw, { source = null } = {}) {
  const question = String(raw || '').trim().slice(0, 300);
  if (!question) {
    (ui.screens.app.dataset.view === 'idle' ? ui.idleQ : ui.dockQ).focus();
    return;
  }
  unlockAudio();
  stopPlayback();
  const myId = ++app.requestId;
  ui.idleQ.value = ''; ui.idleCount.textContent = '0 / 300'; ui.dockQ.value = '';
  ui.followups.hidden = true;
  setView('presenting');
  addUserMessage(question);
  showStageMessage({ title: COPY.loading[0], text: COPY.loading[1], spinner: true });

  let res;
  try {
    res = await api('/api/ask', { method: 'POST', body: { question, course: app.course, source }, timeout: ASK_TIMEOUT_MS });
  } catch {
    if (myId === app.requestId) showStageError('unreachable', question);
    return;
  }
  if (myId !== app.requestId) return; // a newer question took over

  if (res.status === 401) { app.pendingQuestion = question; return showLogin(COPY.sessionExpired); }
  const errorKind = askErrorKind(res);
  if (errorKind) return showStageError(errorKind, question);
  showAnswer(res.data, question);
}

/** The COPY key for an /api/ask reply that is not an answer, or null when it is one. */
function askErrorKind(res) {
  if (res.status === 429) return 'rateLimited';
  if (res.status === 400 || res.status === 422) return 'badQuestion';
  if (res.status === 503 && /not loaded/i.test(String(res.data?.detail || ''))) return 'contentLoading';
  if (res.status === 501 || res.status === 503) return 'notReady';
  if (!res.ok || !res.data) return 'generic';
  return null;
}

/** Show an answer by its kind: one card for each kind that is not a walkthrough, else the slides. */
function showAnswer(answer, question) {
  // My own FAQ answers (meetings, missed class, late work...): my written words, never narrated slides.
  if (answer.kind === 'faq') return showFaqAnswer(answer);
  // Syllabus, policies, assignments and due dates, answered from my Canvas pages, with links to them.
  if (answer.kind === 'course_info') return showFaqAnswer(answer, 'From Canvas');
  // A broken quiz, submission or API key: flagged for me (or "please email"), then stop. No slides.
  if (answer.kind === 'alert') return showFaqAnswer(answer, answer.label || 'Tech problem');
  // Not on my slides but about AI, data or coding tools: a labeled answer from a web search, with sources.
  if (answer.kind === 'web') return showWebAnswer(answer);
  // Meetings, absences, grades, deadlines: a referral to me, never narrated slides.
  if (answer.kind === 'logistics') return showStageError('logistics', question, answer);
  if (!answer.covered || !Array.isArray(answer.segments) || answer.segments.length === 0) {
    return showStageError('notCovered', question);
  }
  const msg = addTwinMessage(summarize(answer));
  renderSourcesList(msg, answer);
  loadAnswer(answer);
  showHelperSlide(answer.generated_slide || null);
}

/** Signed slide and clip links expire after an hour. If the slide image fails to load, fetch fresh
   links for this answer's slides (once per answer) and swap them in: same answer, same segment,
   no second model call. (Changed Oct 8: it used to ask the whole question again.) */
export async function refreshExpiredLinks() {
  const answer = player.answer;
  if (!answer || answer.linksRefreshed) return;
  answer.linksRefreshed = true;
  let res;
  try {
    res = await api('/api/links', { method: 'POST', body: { slide_ids: answer.segments.map(s => s.slide_id) } });
  } catch { return; }
  if (player.answer !== answer || !res.ok || !res.data?.links) return; // a newer answer, or no luck: keep what is shown
  const fresh = res.data.links;
  for (const s of answer.segments) {
    const f = fresh[s.slide_id];
    if (!f) continue;
    if (f.image) s.image = f.image;
    if (s.clip && f.clip?.url) s.clip.url = f.clip.url;
    if (f.boxes) s.boxes = f.boxes;
  }
  for (const src of answer.sources || []) if (fresh[src.slide_id]?.image) src.image = fresh[src.slide_id].image;
  const seg = player.segments[player.index];
  if (seg?.image && ui.slideImg.getAttribute('src') !== seg.image) {
    ui.slideImg.src = seg.image;
    ui.srcThumb.src = seg.image;
  }
  if (app.sourcesBlock) {
    const parent = app.sourcesBlock.parentElement;
    app.sourcesBlock.remove();
    renderSourcesList(parent, answer);
  }
}

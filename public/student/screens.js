// Student page: the boot, offline and passcode screens, and the idle/presenting views.
// Part of the student page; app.js has the map.

import { COPY } from './copy.js';
import { $, api, NetworkError, normalizeTopics } from './helpers.js';
import { app, ui } from './state.js';
import { enterApp, loadVoice } from './voice-label.js';
import { stopPlayback } from './player.js';

/* =====================================================================
   Screens
   ===================================================================== */

/** Show one of boot, offline, login, app. */
export function showScreen(name) {
  for (const [k, node] of Object.entries(ui.screens)) node.hidden = k !== name;
}

/** Switch the app between "idle" (ask a question) and "presenting" (the stage and the dock). */
export function setView(view) {
  const node = ui.screens.app;
  if (node.dataset.view === view) return;
  const apply = () => {
    node.dataset.view = view;
    const presenting = view === 'presenting';
    $('#main').hidden = presenting;
    $('#stage').hidden = !presenting;
    ui.dock.hidden = !presenting;
    $('.skip-link').setAttribute('href', presenting ? '#stage' : '#main');
  };
  // The idle -> presenting change is the signature moment: animate it where the browser supports it.
  if (document.startViewTransition && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
    document.startViewTransition(apply);
  } else {
    apply();
  }
}

/* =====================================================================
   Boot and login
   ===================================================================== */

/** First load: a 401 from /api/topics means "log in"; no reply at all means the offline screen. */
export async function boot() {
  showScreen('boot');
  try {
    api('/api/health').catch(() => {}); // warm the function; topics below is the real check
    const res = await api('/api/topics');
    if (res.status === 401) return showLogin();
    if (!res.ok) throw new NetworkError(`HTTP ${res.status}`);
    app.topics = normalizeTopics(res.data);
    loadVoice();
    enterApp();
  } catch (err) {
    showScreen('offline');
    $('[data-action="boot-retry"]').focus();
  }
}

/** The passcode screen, with an optional note (for example "your session ran out"). */
export function showLogin(note) {
  stopPlayback();
  ui.loginNote.hidden = !note;
  ui.loginNote.textContent = note || '';
  ui.loginError.textContent = '';
  showScreen('login');
  ui.passcode.value = '';
  ui.passcode.focus();
}

ui.loginForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const passcode = ui.passcode.value.trim();
  if (!passcode) { ui.loginError.textContent = 'Enter the passcode first.'; return; }
  const btn = ui.loginForm.querySelector('button[type=submit]');
  btn.disabled = true;
  ui.loginError.textContent = '';
  try {
    const res = await api('/api/login', { method: 'POST', body: { passcode } });
    if (res.status === 401) { ui.loginError.textContent = 'That passcode did not work. Check the course announcement and try again.'; ui.passcode.select(); return; }
    if (res.status === 429) { ui.loginError.textContent = 'Too many tries. Wait a few minutes and try again.'; return; }
    if (!res.ok) { ui.loginError.textContent = COPY.generic[0] + ' ' + COPY.generic[1]; return; }
    const topics = await api('/api/topics');
    app.topics = topics.ok ? normalizeTopics(topics.data) : [];
    loadVoice();
    enterApp();
  } catch {
    ui.loginError.textContent = COPY.unreachable.join(' ');
  } finally {
    btn.disabled = false;
  }
});

document.addEventListener('click', (e) => {
  if (e.target.closest('[data-action="boot-retry"]')) boot();
});

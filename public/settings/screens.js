// Settings: boot, the sign-in screen, entering the app, and the section nav highlight.
// Part of the Settings page; admin.js has the map.

import { $, api, AuthError, detail, errText, markSignedIn, NetworkError } from './helpers.js';
import { renderStatus } from './status.js';
import { loadModels, loadSettings } from './model.js';
import { loadVoices } from './voice.js';
import { loadCourses } from './courses.js';
import { loadThresholds } from './thresholds.js';
import { loadActivity } from './activity.js';
import { loadPrompts } from './prompts.js';

/* ---------------- screens ---------------- */

const screens = { boot: $('#a-boot'), offline: $('#a-offline'), login: $('#a-login'), app: $('#a-app') };
/** Show one of boot, offline, login, app. */
function show(name) { for (const [k, n] of Object.entries(screens)) n.hidden = k !== name; }

/** The sign-in screen, with an optional note (for example "your session ran out"). */
export function showLogin(note) {
  show('login');
  $('#a-login-note').hidden = !note;
  $('#a-login-note').textContent = note || '';
  $('#a-passcode').value = '';
  $('#a-passcode').focus();
}

/** First load: /api/admin/status decides between the app, the login screen (401) and offline. */
export async function boot() {
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

/** Signed in: render the status and load every section. */
async function enter(status) {
  markSignedIn();
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

/* ---------------- nav highlight ---------------- */

const navLinks = [...document.querySelectorAll('.admin-nav a')];
const io = new IntersectionObserver((entries) => {
  for (const en of entries) if (en.isIntersecting) {
    navLinks.forEach(a => a.setAttribute('aria-current', String(a.getAttribute('href') === `#${en.target.id}`)));
  }
}, { rootMargin: '-30% 0px -60% 0px' });
document.querySelectorAll('section.panel[id]').forEach(s => io.observe(s));

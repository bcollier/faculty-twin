// Faculty Twin: student page.
// Plain ES module, no framework. Talks only to our own backend (/api/...).
// Screens: boot -> (offline | login | app). The app has two views: idle and presenting.
// The player is a small state machine; see the "Player" section below.

if (new URLSearchParams(location.search).get('mock') === '1') {
  // Development only: canned responses that match the API contract. Never loaded otherwise.
  await import('./dev/mock.js');
}

/* =====================================================================
   Constants and copy (Ben's voice, no em dashes)
   ===================================================================== */

const COURSES = {
  '70445': { code: '70-445', title: 'AI for Business Leaders' },
  '45884': { code: '45-884', title: 'AI Methods for Social and Visual Data' },
};
const ASK_TIMEOUT_MS = 45000;
const MAX_CHIPS = 8;
const CAPTION_WORDS_PER_SEC = 2.6; // pace for captions-only mode
const CAPTION_MIN_SEC = 4;

const COPY = {
  loading: ['Finding where I cover this in class...', 'Pulling up the slides and writing the walkthrough.'],
  notCovered: ['I don\'t have course material on that.',
    'I only answer from my slides and what I said in class for 70\u2011445 and 45\u2011884. Try one of these instead:'],
  unreachable: ['I can\'t reach the server right now.', 'It may be waking up. That usually takes a few seconds.'],
  rateLimited: ['That\'s a lot of questions in a short time.',
    'I cap questions per minute and per day to keep costs down. Give it a minute and try again.'],
  notReady: ['This part isn\'t finished yet.',
    'I\'m still writing the code that picks the slides for an answer. Check back soon, or try one of these.'],
  badQuestion: ['I couldn\'t use that question.', 'Keep it under 300 characters and about the course.'],
  generic: ['Something went wrong on my end.', 'Try again, or ask a different question.'],
  sessionExpired: 'Your session ran out. Enter the passcode again to keep going.',
  finished: 'That\'s the end of this answer. Ask a follow-up or a new question.',
  tapToPlay: 'Tap play to start the audio.',
};

/* =====================================================================
   Small helpers
   ===================================================================== */

const $ = (sel, root = document) => root.querySelector(sel);
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

class NetworkError extends Error {}

/** fetch wrapper: returns {status, ok, data}; throws NetworkError when the server can't be reached. */
async function api(path, { method = 'GET', body, timeout = 15000 } = {}) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeout);
  let res;
  try {
    res = await fetch(path, {
      method,
      credentials: 'same-origin',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
    });
  } catch (err) {
    throw new NetworkError(err && err.message);
  } finally {
    clearTimeout(t);
  }
  let data = null;
  try { data = await res.json(); } catch { /* empty or non-JSON body */ }
  // Vercel and proxies answer 502/504 when the function is cold or down: treat like unreachable.
  if (res.status === 502 || res.status === 504) throw new NetworkError(`HTTP ${res.status}`);
  return { status: res.status, ok: res.ok, data };
}

function courseLabel(code, title) {
  const c = COURSES[String(code)];
  if (c) return `${c.code} ${title || c.title}`;
  return title || String(code || '');
}
function courseCode(code) {
  return COURSES[String(code)]?.code || String(code || '');
}
function pad2(n) { return String(n).padStart(2, '0'); }
function fmtDate(iso) {
  if (!iso) return '';
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}
function splitSentences(text) {
  const parts = String(text || '').match(/[^.!?]+(?:[.!?]+["')\]]*|$)\s*/g) || [];
  const out = parts.map(p => p.trim()).filter(Boolean);
  return out.length ? out : [String(text || '')];
}
function wordCount(text) { return (String(text || '').match(/\S+/g) || []).length; }

/** /api/topics may return strings or objects; normalize to [{question, course}]. */
function normalizeTopics(data) {
  const list = Array.isArray(data) ? data : (data?.topics || data?.questions || []);
  return list.map(t => {
    if (typeof t === 'string') return { question: t, course: null };
    return { question: t.question || t.q || t.title || t.text || '', course: t.course ? String(t.course) : null };
  }).filter(t => t.question);
}

/* =====================================================================
   Elements and app state
   ===================================================================== */

const ui = {
  screens: { boot: $('#screen-boot'), offline: $('#screen-offline'), login: $('#screen-login'), app: $('#screen-app') },
  loginForm: $('#login-form'), passcode: $('#passcode'), loginError: $('#login-error'), loginNote: $('#login-note'),
  idleForm: $('#idle-form'), idleQ: $('#idle-q'), idleCount: $('#idle-count'), idleChips: $('#idle-chips'),
  idleCourse: $('#idle-course'), dockCourse: $('#dock-course'),
  stageMsg: $('#stage-message'), stageSpinner: $('#stage-spinner'), stageTitle: $('#stage-message-title'),
  stageText: $('#stage-message-text'), stageActions: $('#stage-message-actions'), stageChips: $('#stage-message-chips'),
  player: $('#player'), media: $('#media'), slideImg: $('#slide-img'), slideAlt: $('#slide-alt'), clipVideo: $('#clip-video'),
  clipBtn: $('#clip-btn'), clipBack: $('#clip-back'), clipNote: $('#clip-note'),
  caption: $('#caption'), btnPrev: $('#btn-prev'), btnPlay: $('#btn-play'), btnNext: $('#btn-next'),
  btnMute: $('#btn-mute'), dots: $('#dots'), audioNote: $('#audio-note'),
  codePanel: $('#code-panel'), codeBody: $('#code-body'), codeMarked: $('#code-marked'),
  srcThumb: $('#source-thumb'), srcCourse: $('#src-course'), srcSession: $('#src-session'), srcDate: $('#src-date'), srcSlide: $('#src-slide'),
  dock: $('#dock'), dockToggle: $('#dock-toggle'), log: $('#log'), dockLog: $('#dock-log'),
  followups: $('#followups'), followupChips: $('#followup-chips'), dockForm: $('#dock-form'), dockQ: $('#dock-q'),
  dialog: $('#slide-dialog'), dialogTitle: $('#slide-dialog-h'), dialogImg: $('#slide-dialog-img'),
};

const app = {
  course: null,          // '70445' | '45884' | null (all)
  topics: [],
  lastQuestion: '',
  pendingQuestion: null, // asked when the session ran out; asked again after the passcode
  requestId: 0,          // ignores stale /api/ask responses
  sourceCount: 0,
  sourcesBlock: null,    // the newest answer's "Slides used in this answer" list
  refreshedFor: 0,       // requestId whose expired links were already refreshed once
};

/* The course filter is remembered on this device (spec). Storage can be blocked, so never rely on it. */
const COURSE_KEY = 'ft.course';
function loadCourseChoice() {
  try { const v = localStorage.getItem(COURSE_KEY); return v && COURSES[v] ? v : ''; } catch { return ''; }
}
function saveCourseChoice(v) {
  try { if (v) localStorage.setItem(COURSE_KEY, v); else localStorage.removeItem(COURSE_KEY); } catch { /* ignore */ }
}

/* =====================================================================
   Screens
   ===================================================================== */

function showScreen(name) {
  for (const [k, node] of Object.entries(ui.screens)) node.hidden = k !== name;
}

function setView(view) {
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

async function boot() {
  showScreen('boot');
  try {
    api('/api/health').catch(() => {}); // warm the function; topics below is the real check
    const res = await api('/api/topics');
    if (res.status === 401) return showLogin();
    if (!res.ok) throw new NetworkError(`HTTP ${res.status}`);
    app.topics = normalizeTopics(res.data);
    enterApp();
  } catch (err) {
    showScreen('offline');
    $('[data-action="boot-retry"]').focus();
  }
}

function showLogin(note) {
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

function enterApp() {
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

/* =====================================================================
   Course filter, chips, question forms
   ===================================================================== */

function setCourse(value) {
  app.course = value || null;
  saveCourseChoice(value || '');
  ui.dockCourse.value = value || '';
  const radio = ui.idleCourse.querySelector(`input[value="${value || ''}"]`);
  if (radio) radio.checked = true;
  renderIdleChips();
}
ui.idleCourse.addEventListener('change', (e) => setCourse(e.target.value));
ui.dockCourse.addEventListener('change', (e) => setCourse(e.target.value));

function topicsForCourse() {
  const list = app.topics.filter(t => !app.course || !t.course || t.course === app.course);
  return list.slice(0, MAX_CHIPS);
}

function chip(text, courseCodeStr) {
  const b = el('button', { type: 'button', class: 'chip', onclick: () => ask(text) }, text);
  if (courseCodeStr && !app.course) b.append(el('span', { class: 'chip-course', text: courseCode(courseCodeStr) }));
  return el('li', {}, b);
}

function renderChips(listNode, topics) {
  listNode.replaceChildren(...topics.map(t => chip(t.question, t.course)));
}

function renderIdleChips() {
  const topics = topicsForCourse();
  renderChips(ui.idleChips, topics);
  ui.idleChips.closest('.chips-block').hidden = topics.length === 0;
}

ui.idleQ.addEventListener('input', () => { ui.idleCount.textContent = `${ui.idleQ.value.length} / 300`; });
ui.idleQ.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ui.idleForm.requestSubmit(); }
});
ui.idleForm.addEventListener('submit', (e) => { e.preventDefault(); ask(ui.idleQ.value); });
ui.dockForm.addEventListener('submit', (e) => { e.preventDefault(); ask(ui.dockQ.value); });

ui.dockToggle.addEventListener('click', () => {
  const open = !ui.dock.classList.contains('expanded');
  ui.dock.classList.toggle('expanded', open);
  ui.dockToggle.setAttribute('aria-expanded', String(open));
  ui.dockToggle.textContent = open ? 'Hide' : (app.sourceCount ? `Sources (${app.sourceCount})` : 'Sources');
});

/* =====================================================================
   Stage messages (loading, not covered, errors)
   ===================================================================== */

function showStageMessage({ title, text, spinner = false, actions = [], chips = [] }) {
  ui.player.hidden = true;
  ui.stageMsg.hidden = false;
  ui.stageSpinner.hidden = !spinner;
  ui.stageTitle.textContent = title || '';
  ui.stageText.textContent = text || '';
  ui.stageActions.replaceChildren(...actions.map(a =>
    el('button', { type: 'button', class: `btn ${a.primary ? 'btn-primary' : ''}`, onclick: a.onClick }, a.label)));
  renderChips(ui.stageChips, chips);
  ui.stageMsg.setAttribute('role', spinner ? 'status' : 'alert');
}

function showStageError(kind, question) {
  const [title, text] = COPY[kind] || COPY.generic;
  const actions = [];
  if (kind === 'unreachable' || kind === 'generic' || kind === 'rateLimited') {
    actions.push({ label: 'Try again', primary: true, onClick: () => ask(question) });
  }
  const chips = (kind === 'notCovered' || kind === 'notReady' || kind === 'badQuestion') ? topicsForCourse().slice(0, 6) : [];
  showStageMessage({ title, text, actions, chips });
  addTwinMessage(title, 'msg-error');
  ui.followups.hidden = true;
  const firstBtn = ui.stageActions.querySelector('button') || ui.stageChips.querySelector('button');
  if (firstBtn && !ui.dockQ.matches(':focus')) firstBtn.focus({ preventScroll: true });
}

/* =====================================================================
   Chat log
   ===================================================================== */

function addUserMessage(q) {
  ui.log.append(el('li', { class: 'msg msg-user' }, q));
  scrollLog();
}
function addTwinMessage(text, extra = '') {
  const li = el('li', { class: `msg msg-twin ${extra}` }, el('p', {}, text));
  ui.log.append(li);
  scrollLog();
  return li;
}
function scrollLog() { ui.dockLog.scrollTop = ui.dockLog.scrollHeight; }

function summarize(answer) {
  const segs = answer.segments;
  const sessions = new Set(segs.map(s => `${s.course}-${s.session}`));
  const n = segs.length;
  const slides = `${n} slide${n === 1 ? '' : 's'}`;
  if (sessions.size === 1) {
    const s = segs[0];
    return `Here are ${slides} from ${courseCode(s.course)}, session ${s.session} (${s.session_title}). I'll walk you through them.`;
  }
  const courses = new Set(segs.map(s => s.course));
  if (courses.size === 1) return `Here are ${slides} from ${sessions.size} sessions of ${courseCode(segs[0].course)}. I'll walk you through them in order.`;
  return `Here are ${slides} from both courses. I'll walk you through them in order.`;
}

function renderSourcesList(container, answer) {
  // Only the newest answer's list jumps within the walkthrough; older lists open the slide in a dialog.
  const sources = (answer.sources && answer.sources.length) ? answer.sources : answer.segments;
  const list = el('ol', { class: 'sources' });
  for (const src of sources) {
    const segIndex = answer.segments.findIndex(s => s.slide_id === src.slide_id);
    const l1 = `${courseCode(src.course)} · Session ${src.session} · Slide ${src.slide_number}`;
    const l2 = [fmtDate(src.date), segIndex >= 0 ? `Part ${segIndex + 1} of this answer` : 'Also relevant'].filter(Boolean).join(' · ');
    const btn = el('button', {
      type: 'button', class: 'source-btn', 'data-slide': src.slide_id,
      'aria-label': `${l1}. ${l2}. ${segIndex >= 0 ? 'Jump to it' : 'Open the slide'}`,
      onclick: () => (segIndex >= 0 && player.answer === answer && !ui.player.hidden ? jumpTo(segIndex) : openSlideDialog(src)),
    },
    el('img', { src: src.image, alt: '', loading: 'lazy' }),
    el('span', {}, el('span', { class: 'src-l1', text: l1 }), el('span', { class: 'src-l2', text: l2 })));
    list.append(el('li', {}, btn));
  }
  const block = el('div', { class: 'sources-block' },
    el('h3', { class: 'sources-h', text: 'Slides used in this answer' }), list);
  container.append(block);
  app.sourcesBlock = block;
  app.sourceCount = sources.length;
  if (!ui.dock.classList.contains('expanded')) ui.dockToggle.textContent = `Sources (${sources.length})`;
}

function openSlideDialog(src) {
  ui.dialogTitle.textContent = `${courseCode(src.course)}, session ${src.session}, slide ${src.slide_number}`;
  ui.dialogImg.src = src.image;
  ui.dialogImg.alt = `Slide ${src.slide_number} from session ${src.session}`;
  ui.dialog.showModal();
}

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

async function ask(raw, { resumeAt = 0, quiet = false } = {}) {
  const question = String(raw || '').trim().slice(0, 300);
  if (!question) {
    (ui.screens.app.dataset.view === 'idle' ? ui.idleQ : ui.dockQ).focus();
    return;
  }
  unlockAudio();
  stopPlayback();
  const myId = ++app.requestId;
  app.lastQuestion = question;
  ui.idleQ.value = ''; ui.idleCount.textContent = '0 / 300'; ui.dockQ.value = '';
  ui.followups.hidden = true;
  setView('presenting');
  if (!quiet) addUserMessage(question);
  showStageMessage({ title: COPY.loading[0], text: COPY.loading[1], spinner: true });

  let res;
  try {
    res = await api('/api/ask', { method: 'POST', body: { question, course: app.course }, timeout: ASK_TIMEOUT_MS });
  } catch {
    if (myId === app.requestId) showStageError('unreachable', question);
    return;
  }
  if (myId !== app.requestId) return; // a newer question took over

  if (res.status === 401) { app.pendingQuestion = question; return showLogin(COPY.sessionExpired); }
  if (res.status === 429) return showStageError('rateLimited', question);
  if (res.status === 400 || res.status === 422) return showStageError('badQuestion', question);
  if (res.status === 501 || res.status === 503) return showStageError('notReady', question);
  if (!res.ok || !res.data) return showStageError('generic', question);

  const answer = res.data;
  if (!answer.covered || !Array.isArray(answer.segments) || answer.segments.length === 0) {
    return showStageError('notCovered', question);
  }
  if (quiet && app.sourcesBlock) {
    // Fresh links for the same answer: swap the old list instead of adding another message.
    const parent = app.sourcesBlock.parentElement;
    app.sourcesBlock.remove();
    renderSourcesList(parent, answer);
  } else {
    const msg = addTwinMessage(summarize(answer));
    renderSourcesList(msg, answer);
  }
  loadAnswer(answer, resumeAt);
}

/* Signed slide and clip links expire after an hour. If the slide image fails to load, ask the
   same question again (once per answer) for fresh links and pick up at the same segment. */
function refreshExpiredLinks() {
  if (!player.answer || app.refreshedFor === app.requestId) return;
  app.refreshedFor = app.requestId + 1; // the id the refresh request will get
  ask(app.lastQuestion, { resumeAt: player.index, quiet: true });
}

/* =====================================================================
   Player
   A small state machine:
     player.index      current segment (0-based)
     player.playing    true while narration should be running (audio or caption timer)
     player.muted      audio muted (narration still runs, so timing is unchanged)
     player.captionsOnly  no usable audio: a timer paces each segment instead
     player.finished   the last segment has been played through
   Every "segment is done" signal (audio `ended` OR the captions-only timer)
   calls onClipEnded(). That is the single advance path.
   ===================================================================== */

const player = {
  answer: null,
  segments: [],
  index: 0,
  playing: false,
  muted: false,
  captionsOnly: false,
  finished: false,
  audio: new Map(),     // index -> HTMLAudioElement (current and preloaded)
  current: null,        // HTMLAudioElement playing now
  timer: null,          // captions-only: { id, startedAt, remainingMs, totalMs, raf }
  sentences: [],
  sentenceIdx: -1,
  inClip: false,
  clipFailed: new Set(), // segment indexes whose class clip would not load: the button stays hidden
};

/** Load a new answer and start at segment 1 (or `start`, after refreshing expired links). */
function loadAnswer(answer, start = 0) {
  stopPlayback();
  player.answer = answer;
  player.segments = answer.segments.slice().sort((a, b) => (a.n ?? 0) - (b.n ?? 0));
  player.clipFailed.clear();
  start = Math.min(Math.max(0, start), player.segments.length - 1);
  player.captionsOnly = player.segments.every(s => !s.audio);
  player.finished = false;
  ui.audioNote.hidden = !player.captionsOnly;
  buildDots();
  ui.stageMsg.hidden = true;
  ui.player.hidden = false;
  showSegment(start);
  player.playing = true;
  playCurrent();
  preloadAudio(start + 1);
}

/** Render segment i on the stage. Does not start or stop narration. */
function showSegment(i) {
  const seg = player.segments[i];
  if (!seg) return;
  exitClip(false);
  stopNarration();
  player.index = i;

  ui.slideImg.src = seg.image;
  ui.slideImg.alt = `Slide ${seg.slide_number} from ${courseCode(seg.course)} session ${seg.session}, ${seg.session_title}`;
  ui.slideAlt.textContent = ui.slideImg.alt;

  renderCode(seg.code);

  ui.clipBtn.hidden = !(seg.clip && seg.clip.url) || player.clipFailed.has(i);
  ui.clipBack.hidden = true;
  ui.clipNote.hidden = true;

  player.sentences = splitSentences(seg.narration);
  player.sentenceIdx = -1;
  setCaptionSentence(0);

  ui.srcThumb.src = seg.image;
  ui.srcThumb.alt = '';
  ui.srcCourse.textContent = courseLabel(seg.course, seg.course_title);
  ui.srcSession.textContent = `Session ${pad2(seg.session)}: ${seg.session_title || ''}`.replace(/: $/, '');
  ui.srcDate.textContent = fmtDate(seg.date);
  ui.srcSlide.textContent = `Slide ${seg.slide_number}`;

  updateDots();
  updateControls();
  app.sourcesBlock?.querySelectorAll('.source-btn').forEach(b => b.setAttribute('aria-current', String(b.dataset.slide === seg.slide_id)));
}

function renderCode(code) {
  const hasCode = !!(code && code.source);
  ui.codePanel.hidden = !hasCode;
  ui.media.classList.toggle('has-code', hasCode);
  if (!hasCode) { ui.codeBody.replaceChildren(); return; }
  const marks = new Set((code.mark_lines || []).map(Number));
  const lines = String(code.source).replace(/\n$/, '').split('\n');
  ui.codeBody.replaceChildren(...lines.map((line, k) =>
    el('span', { class: `ln${marks.has(k + 1) ? ' marked' : ''}`, 'data-n': String(k + 1) }, line || ' ')));
  ui.codeMarked.textContent = marks.size ? `Lines ${[...marks].sort((a, b) => a - b).join(', ')} marked` : '';
  const first = ui.codeBody.querySelector('.marked');
  const pre = ui.codeBody.parentElement;
  pre.scrollTop = first ? Math.max(0, first.offsetTop - pre.clientHeight / 3) : 0;
  pre.scrollLeft = 0;
}

function setCaptionSentence(k) {
  k = Math.min(Math.max(0, k), player.sentences.length - 1);
  if (k === player.sentenceIdx) return;
  player.sentenceIdx = k;
  ui.caption.textContent = player.sentences[k] || '';
}

/** Move the caption along as narration progresses (fraction 0..1 of the segment). */
function syncCaption(fraction) {
  const s = player.sentences;
  if (s.length <= 1) return;
  const total = s.reduce((a, x) => a + x.length, 0);
  let acc = 0;
  for (let k = 0; k < s.length; k++) {
    acc += s[k].length;
    if (fraction * total < acc) { setCaptionSentence(k); return; }
  }
  setCaptionSentence(s.length - 1);
}

/** The audio element for segment i, created once and reused (this is also the preload). */
function audioFor(i) {
  const seg = player.segments[i];
  if (!seg || !seg.audio || player.captionsOnly) return null;
  if (player.audio.has(i)) return player.audio.get(i);
  const a = new Audio();
  a.preload = 'auto';
  a.muted = player.muted;
  a.addEventListener('ended', () => { if (a === player.current) onClipEnded(); });
  a.addEventListener('timeupdate', () => {
    if (a === player.current && a.duration) syncCaption(a.currentTime / a.duration);
  });
  a.addEventListener('error', () => { if (a === player.current) fallBackToCaptions(); });
  a.src = seg.audio;
  player.audio.set(i, a);
  return a;
}

/** Start loading segment i's audio in the background. */
function preloadAudio(i) {
  const a = audioFor(i);
  if (a) a.load();
}

/** Start narration for the current segment: its audio, or the captions-only timer. */
function playCurrent() {
  if (player.inClip) return;
  player.playing = true;
  player.finished = false;
  const a = audioFor(player.index);
  if (!a) {
    startCaptionTimer();
  } else {
    player.current = a;
    a.muted = player.muted;
    const p = a.play();
    if (p && p.catch) {
      p.catch((err) => {
        if (a !== player.current) return;
        if (err && err.name === 'NotAllowedError') {
          // Autoplay blocked: wait for the student to press play.
          player.playing = false;
          ui.caption.textContent = COPY.tapToPlay;
          player.sentenceIdx = -1;
          updateControls();
        } else if (err && err.name !== 'AbortError') {
          fallBackToCaptions();
        }
      });
    }
  }
  updateControls();
}

/** Stop narration for the current segment without changing the playing flag. */
function stopNarration() {
  if (player.current) { player.current.pause(); player.current = null; }
  clearCaptionTimer();
}

/** Stop everything (new question, logout). */
function stopPlayback() {
  stopNarration();
  exitClip(false);
  for (const a of player.audio.values()) { a.pause(); a.removeAttribute('src'); a.load(); }
  player.audio.clear();
  player.playing = false;
}

function pausePlayback() {
  player.playing = false;
  if (player.current) player.current.pause();
  if (player.timer) pauseCaptionTimer();
  updateControls();
}

function resumePlayback() {
  if (player.inClip) exitClip(false);
  if (player.finished) {           // replay from the top
    showSegment(0);
    playCurrent();
    preloadAudio(1);
    return;
  }
  player.playing = true;
  if (player.current) {
    player.current.play().catch(() => fallBackToCaptions());
  } else if (player.timer) {
    resumeCaptionTimer();
  } else {
    playCurrent();
  }
  updateControls();
}

function togglePlay() {
  if (!player.segments.length) return;
  if (player.playing) pausePlayback(); else resumePlayback();
}

/** Prev/next and the dots: show segment i, and keep narrating if we were playing. */
function jumpTo(i) {
  if (i < 0 || i >= player.segments.length) return;
  const wasPlaying = player.playing || player.finished;
  player.finished = false;
  showSegment(i);
  if (wasPlaying) { playCurrent(); preloadAudio(i + 1); }
  else { player.playing = false; updateControls(); }
}
function goPrev() { jumpTo(player.index - 1); }
function goNext() {
  if (player.index >= player.segments.length - 1) finishAnswer();
  else jumpTo(player.index + 1);
}

/** After the last segment: stop, show follow-up chips. */
function finishAnswer() {
  stopNarration();
  player.playing = false;
  player.finished = true;
  ui.caption.textContent = COPY.finished;
  player.sentenceIdx = -1;
  const ups = (player.answer?.follow_ups || []).filter(Boolean);
  ui.followupChips.replaceChildren(...ups.map(q => chip(q)));
  ui.followups.hidden = ups.length === 0;
  updateDots();
  updateControls();
}

/** Audio failed or the daily voice cap was hit: keep going with captions and a timer. */
function fallBackToCaptions() {
  if (player.captionsOnly) return;
  player.captionsOnly = true;
  ui.audioNote.hidden = false;
  for (const a of player.audio.values()) { a.pause(); }
  player.audio.clear();
  player.current = null;
  if (player.playing) startCaptionTimer();
  updateControls();
}

/* ---- captions-only timer: paces a segment by word count, then calls onClipEnded() ---- */

function captionDurationMs(seg) {
  return Math.max(CAPTION_MIN_SEC, wordCount(seg.narration) / CAPTION_WORDS_PER_SEC) * 1000;
}
function startCaptionTimer() {
  clearCaptionTimer();
  const total = captionDurationMs(player.segments[player.index]);
  player.timer = { id: null, raf: null, startedAt: 0, remainingMs: total, totalMs: total };
  resumeCaptionTimer();
}
function resumeCaptionTimer() {
  const t = player.timer;
  if (!t) return;
  t.startedAt = performance.now();
  t.id = setTimeout(() => {
    cancelAnimationFrame(t.raf);
    player.timer = null;
    onClipEnded();
  }, t.remainingMs);
  const tick = () => {
    const elapsed = t.totalMs - t.remainingMs + (performance.now() - t.startedAt);
    syncCaption(Math.min(1, elapsed / t.totalMs));
    t.raf = requestAnimationFrame(tick);
  };
  t.raf = requestAnimationFrame(tick);
}
function pauseCaptionTimer() {
  const t = player.timer;
  if (!t || t.id == null) return;
  clearTimeout(t.id); cancelAnimationFrame(t.raf);
  t.id = null;
  t.remainingMs = Math.max(0, t.remainingMs - (performance.now() - t.startedAt));
}
function clearCaptionTimer() {
  const t = player.timer;
  if (t) { clearTimeout(t.id); cancelAnimationFrame(t.raf); }
  player.timer = null;
}

/**
 * Runs when the current segment's narration finishes: the audio element's
 * `ended` event, or the captions-only timer running out. This is the only
 * place the walkthrough advances on its own.
 *
 * Written by hand by Ben (see AGENTS.md, "Code Ben writes by hand").
 *
 * State it can read: player.index, player.segments, player.playing,
 * player.captionsOnly, player.finished.
 * Helpers it can call: showSegment(i), playCurrent(), preloadAudio(i),
 * finishAnswer().
 */
function onClipEnded() { /* Ben writes this by hand: move to next segment, start its audio, preload the one after, handle last segment and pause state. */ }

/* ---- controls ---- */

function buildDots() {
  ui.dots.replaceChildren(...player.segments.map((s, i) => el('li', {},
    el('button', { type: 'button', 'aria-label': `Slide ${i + 1} of ${player.segments.length}`, onclick: () => jumpTo(i) }))));
}
function updateDots() {
  [...ui.dots.querySelectorAll('button')].forEach((b, i) => {
    if (i === player.index && !player.finished) b.setAttribute('aria-current', 'step'); else b.removeAttribute('aria-current');
    b.classList.toggle('done', i < player.index || player.finished);
  });
}
function updateControls() {
  ui.player.classList.toggle('is-paused', !player.playing);
  ui.btnPlay.setAttribute('aria-label', player.playing ? 'Pause' : (player.finished ? 'Play again from the start' : 'Play'));
  ui.btnPrev.disabled = player.index <= 0;
  ui.btnNext.disabled = player.finished;
  ui.btnNext.setAttribute('aria-label', player.index >= player.segments.length - 1 ? 'Finish' : 'Next slide');
  ui.btnMute.setAttribute('aria-pressed', String(player.muted));
  ui.btnMute.setAttribute('aria-label', player.muted ? 'Unmute' : 'Mute');
  ui.btnMute.disabled = player.captionsOnly;
}

function toggleMute() {
  player.muted = !player.muted;
  for (const a of player.audio.values()) a.muted = player.muted;
  ui.clipVideo.muted = player.muted;
  updateControls();
}

ui.btnPlay.addEventListener('click', togglePlay);
ui.btnPrev.addEventListener('click', goPrev);
ui.btnNext.addEventListener('click', goNext);
ui.btnMute.addEventListener('click', toggleMute);

/* ---- "Watch me explain this in class" ---- */

function enterClip() {
  const seg = player.segments[player.index];
  if (!seg?.clip?.url) return;
  if (player.playing) pausePlayback();
  player.inClip = true;
  ui.slideImg.hidden = true;
  ui.clipVideo.hidden = false;
  ui.clipVideo.muted = player.muted;
  ui.clipVideo.src = seg.clip.url;
  ui.clipBtn.hidden = true;
  ui.clipBack.hidden = false;
  ui.clipNote.textContent = `Recorded in class on ${fmtDate(seg.date)} (my real voice, not the AI voice).`;
  ui.clipNote.hidden = false;
  ui.clipVideo.play().catch(() => {});
  ui.clipBack.focus();
}

/** Back to the slide. The walkthrough stays paused until the student presses play (spec). */
function exitClip(returnFocus = true) {
  if (!player.inClip) return;
  player.inClip = false;
  ui.clipVideo.pause();
  ui.clipVideo.removeAttribute('src');
  ui.clipVideo.load();
  ui.clipVideo.hidden = true;
  ui.slideImg.hidden = false;
  const seg = player.segments[player.index];
  ui.clipBtn.hidden = !(seg?.clip?.url) || player.clipFailed.has(player.index);
  ui.clipBack.hidden = true;
  ui.clipNote.hidden = true;
  updateControls();
  if (returnFocus) (ui.clipBtn.hidden ? ui.btnPlay : ui.clipBtn).focus();
}

ui.clipBtn.addEventListener('click', enterClip);
ui.clipBack.addEventListener('click', () => exitClip(true));
ui.clipVideo.addEventListener('ended', () => exitClip(true));
ui.clipVideo.addEventListener('error', () => {
  if (!player.inClip || !ui.clipVideo.getAttribute('src')) return;
  player.clipFailed.add(player.index); // spec: the button disappears for that segment, the slide stays
  exitClip(true);
  ui.clipNote.textContent = 'That class clip won\'t load right now, so here is the slide.';
  ui.clipNote.hidden = false;
});

ui.slideImg.addEventListener('error', () => {
  if (ui.slideImg.getAttribute('src')) refreshExpiredLinks();
});

/* ---- phone: keep the stage clear of the fixed bottom bar, whatever its height ---- */
if ('ResizeObserver' in window) {
  new ResizeObserver(([entry]) => {
    const h = Math.ceil(entry.borderBoxSize?.[0]?.blockSize ?? entry.target.offsetHeight);
    document.documentElement.style.setProperty('--dock-h', `${h}px`);
  }).observe(ui.dock);
}

/* ---- keyboard: space = play/pause, arrows = prev/next, m = mute ---- */

document.addEventListener('keydown', (e) => {
  if (ui.screens.app.hidden || ui.screens.app.dataset.view !== 'presenting' || ui.player.hidden) return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const t = e.target;
  // Let form fields, the video and the scrollable code panel keep their own keys.
  if (t.closest('input, textarea, select, video, [contenteditable], dialog, pre')) return;
  const isSpace = e.key === ' ' || e.code === 'Space';
  // Space on a focused button or link already activates it; don't toggle twice.
  if (isSpace && t.closest('button, a')) return;
  if (isSpace) { e.preventDefault(); togglePlay(); }
  else if (e.key === 'ArrowRight') { e.preventDefault(); if (!player.finished) goNext(); }
  else if (e.key === 'ArrowLeft') { e.preventDefault(); goPrev(); }
  else if (e.key === 'm' || e.key === 'M') { toggleMute(); }
});

/* =====================================================================
   Go
   ===================================================================== */

boot();

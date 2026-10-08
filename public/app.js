// Faculty Twin: student page.
// Plain ES module, no framework. Talks only to our own backend (/api/...).
// Screens: boot -> (offline | login | app). The app has two views: idle and presenting.
// The player is a small state machine; see the "Player" section below.
//
// Sections, in order: constants and copy, small helpers, elements and state, screens, boot and login,
// voice label, course filter and chips, stage messages (FAQ, web and error cards), chat log, asking,
// player, read-along, onClipEnded (the one advance path), controls, clips, keyboard.
//
// Why one file: the page has no bundler, and the node tests (tests/js/fakedom.mjs) run this file's
// source as one function body, where a static `import` is not allowed. Code that can stand alone
// moves to its own file and is loaded with `await import()`, as readalong.js and helper-slide.js are.

const DEV_HOSTS = ['localhost', '127.0.0.1', '[::1]'];
if (DEV_HOSTS.includes(location.hostname) && new URLSearchParams(location.search).get('mock') === '1') {
  // Development only (local hosts only): canned responses that match the API contract. Never loaded otherwise.
  await import('./dev/mock.js');
}
// Read-along: word timings, the syllable estimate, and narration-to-slide word matching (pure functions).
const RA = await import('./readalong.js');

/* =====================================================================
   Constants and copy (Ben's voice, no em dashes)
   ===================================================================== */

const COURSES = {
  '70445': { code: '70-445', title: 'AI for Business Leaders' },
  '45884': { code: '45-884', title: 'AI Methods for Social and Visual Data' },
};
const ASK_TIMEOUT_MS = 45000;
const MAX_CHIPS = 8;        // suggested questions on the idle screen
const STAGE_CHIPS = 6;      // suggestions under a stage message (fewer: they share the stage with a card)
const CAPTION_WORDS_PER_SEC = 2.6; // pace for captions-only mode
const CAPTION_MIN_SEC = 4;

const COPY = {
  loading: ['Finding where I cover this in class...', 'Pulling up the slides and writing the walkthrough.'],
  notCovered: ['I don\'t have course material on that.',
    'I only answer from my slides and what I said in class for 70\u2011445 and 45\u2011884.'],
  logistics: ['That one is for me directly.',
    'My twin only explains course material. For meetings, absences, grades or deadlines, please email me or come to office hours.'],
  unreachable: ['I can\'t reach the server right now.', 'It may be waking up. That usually takes a few seconds.'],
  rateLimited: ['That\'s a lot of questions in a short time.',
    'I cap questions per minute and per day to keep costs down. Give it a minute and try again.'],
  notReady: ['This part isn\'t finished yet.',
    'I\'m still writing the code that picks the slides for an answer. Check back soon.'],
  contentLoading: ['My course material is still loading.',
    'The slides and class transcripts are being uploaded. Try again in a few minutes.'],
  badQuestion: ['I couldn\'t use that question.', 'Keep it under 300 characters and about the course.'],
  generic: ['Something went wrong on my end.', 'Try again, or ask a different question.'],
  sessionExpired: 'Your session ran out. Enter the passcode again to keep going.',
  finished: 'That\'s the end of this answer. Ask a follow-up or a new question.',
  tapToPlay: 'Tap play to start the audio.',
  captionsOnly: 'Captions only',
  webLabel: 'Beyond my slides: from the web',
  webSources: 'Sources (open in a new tab)',
  webRelated: 'Closest material in my courses',
};

/* =====================================================================
   Small helpers
   ===================================================================== */

/** The first element matching `sel`. */
const $ = (sel, root = document) => root.querySelector(sel);
/**
 * Build an element. `attrs`: `class`, `text` (textContent), `on<event>` (a listener), anything else an
 * attribute (`true` is an empty attribute; `null`/`false` is left out). Never takes HTML.
 */
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

/** Stop a media element and drop its source, so the browser stops downloading it. */
function releaseMedia(media) {
  media.pause();
  media.removeAttribute('src');
  media.load();
}

/** The server could not be reached (or a proxy answered for it): the page shows "unreachable". */
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

/** One usage event for Settings > Analytics: an allowlisted name only (no text, no ids).
    Fire and forget: it never waits, never retries, and never shows an error. */
const EVENTS = new Set(['chip_tap', 'question_typed', 'segment_played', 'walkthrough_completed', 'clip_played',
  'audio_failed', 'follow_up_tapped', 'course_filter_changed']);
/** One usage event (name only), fire and forget. */
function track(name) {
  if (!EVENTS.has(name)) return;
  try {
    fetch('/api/event', {
      method: 'POST', credentials: 'same-origin', keepalive: true,
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }),
    }).catch(() => {});
  } catch { /* analytics must never break the page */ }
}

/** "70-445 AI for Business Leaders": the course number and its title (the answer's title wins). */
function courseLabel(code, title) {
  const c = COURSES[String(code)];
  if (c) return `${c.code} ${title || c.title}`;
  return title || String(code || '');
}
/** "70-445" for "70445"; an unknown course is shown as given. */
function courseCode(code) {
  return COURSES[String(code)]?.code || String(code || '');
}
function pad2(n) { return String(n).padStart(2, '0'); }
/** "Sep 1, 2026" for "2026-09-01" (read as a local date, so it never shifts a day). */
function fmtDate(iso) {
  if (!iso) return '';
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}
/** "70-445 · Session 3 · Slide 12": the first line of a slide card. */
function slideLine(src) {
  return `${courseCode(src.course)} · Session ${src.session} · Slide ${src.slide_number}`;
}
/** Words in a narration (for the captions-only pace). */
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
  stageMsg: $('#stage-message'), stageSpinner: $('#stage-spinner'), stageTitle: $('#stage-message-title'), stageLabel: $('#stage-message-label'),
  stageText: $('#stage-message-text'), stageActions: $('#stage-message-actions'), stageChips: $('#stage-message-chips'),
  stageExtra: $('#stage-message-extra'), helperSlot: $('#helper-slot'),
  player: $('#player'), media: $('#media'), slideImg: $('#slide-img'), slideAlt: $('#slide-alt'), clipVideo: $('#clip-video'),
  clipBtn: $('#clip-btn'), clipBack: $('#clip-back'), clipNote: $('#clip-note'),
  caption: $('#caption'), btnPrev: $('#btn-prev'), btnPlay: $('#btn-play'), btnNext: $('#btn-next'),
  btnMute: $('#btn-mute'), dots: $('#dots'), audioNote: $('#audio-note'), crossNote: $('#cross-note'),
  codePanel: $('#code-panel'), codeBody: $('#code-body'), codeMarked: $('#code-marked'),
  srcThumb: $('#source-thumb'), srcCourse: $('#src-course'), srcSession: $('#src-session'), srcDate: $('#src-date'), srcSlide: $('#src-slide'),
  dock: $('#dock'), dockToggle: $('#dock-toggle'), log: $('#log'), dockLog: $('#dock-log'),
  followups: $('#followups'), followupChips: $('#followup-chips'), dockForm: $('#dock-form'), dockQ: $('#dock-q'),
  dialog: $('#slide-dialog'), dialogTitle: $('#slide-dialog-h'), dialogImg: $('#slide-dialog-img'),
  contactDialog: $('#contact-dialog'), contactTitle: $('#contact-dialog-h'), contactBody: $('#contact-dialog-body'),
  idleVoiceLabel: $('#idle-voice-label'), idleVoiceText: $('#idle-voice-text'), voiceLabel: $('#voice-label'),
  slideFrame: $('#slide-frame'), slideMarks: $('#slide-marks'),
  narrationVoice: $('#narration-voice'), narrationNote: $('#narration-note'), narrationLive: $('#narration-live'),
};

const app = {
  course: null,          // '70445' | '45884' | null (all)
  topics: [],
  pendingQuestion: null, // asked when the session ran out; asked again after the passcode
  requestId: 0,          // ignores stale /api/ask responses
  sourceCount: 0,
  sourcesBlock: null,    // the newest answer's "Slides used in this answer" list
  voice: null,           // /api/voice: { kind, label, fallback } for the voice that will speak
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

/** Show one of boot, offline, login, app. */
function showScreen(name) {
  for (const [k, node] of Object.entries(ui.screens)) node.hidden = k !== name;
}

/** Switch the app between "idle" (ask a question) and "presenting" (the stage and the dock). */
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

/** First load: a 401 from /api/topics means "log in"; no reply at all means the offline screen. */
async function boot() {
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
async function loadVoice() {
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
function updateVoiceLabel() {
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

/** Filter by course ('' or null is both). Remembered on this device and reflected in both pickers. */
function setCourse(value) {
  app.course = value || null;
  saveCourseChoice(value || '');
  ui.dockCourse.value = value || '';
  const radio = ui.idleCourse.querySelector(`input[value="${value || ''}"]`);
  if (radio) radio.checked = true;
  renderIdleChips();
}
function onCourseFilter(e) {
  setCourse(e.target.value);
  track('course_filter_changed');
}
ui.idleCourse.addEventListener('change', onCourseFilter);
ui.dockCourse.addEventListener('change', onCourseFilter);

/** Suggested questions for the chosen course (and those for both courses). */
function topicsForCourse() {
  const list = app.topics.filter(t => !app.course || !t.course || t.course === app.course);
  return list.slice(0, MAX_CHIPS);
}

/** A question chip. `source` is "chip" (suggested questions) or "follow_up" (after an answer). */
function chip(text, courseCodeStr, source = 'chip') {
  const onclick = () => { track(source === 'follow_up' ? 'follow_up_tapped' : 'chip_tap'); ask(text, { source }); };
  const b = el('button', { type: 'button', class: 'chip', onclick }, text);
  if (courseCodeStr && !app.course) b.append(el('span', { class: 'chip-course', text: courseCode(courseCodeStr) }));
  return el('li', {}, b);
}

/** The fewer suggestions shown under a stage message. */
function stageChips() {
  return topicsForCourse().slice(0, STAGE_CHIPS);
}

/** Fill a chip list with question chips. */
function renderChips(listNode, topics) {
  listNode.replaceChildren(...topics.map(t => chip(t.question, t.course)));
}

/** The idle screen's suggestions for the chosen course (the block hides when there are none). */
function renderIdleChips() {
  const topics = topicsForCourse();
  renderChips(ui.idleChips, topics);
  ui.idleChips.closest('.chips-block').hidden = topics.length === 0;
}

ui.idleQ.addEventListener('input', () => { ui.idleCount.textContent = `${ui.idleQ.value.length} / 300`; });
ui.idleQ.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ui.idleForm.requestSubmit(); }
});
/** Ask what was typed; only a non-empty question counts as "typed" for Analytics. */
function askTyped(value) {
  if (String(value || '').trim()) track('question_typed');
  ask(value, { source: 'typed' });
}
ui.idleForm.addEventListener('submit', (e) => { e.preventDefault(); askTyped(ui.idleQ.value); });
ui.dockForm.addEventListener('submit', (e) => { e.preventDefault(); askTyped(ui.dockQ.value); });

ui.dockToggle.addEventListener('click', () => {
  const open = !ui.dock.classList.contains('expanded');
  ui.dock.classList.toggle('expanded', open);
  ui.dockToggle.setAttribute('aria-expanded', String(open));
  ui.dockToggle.textContent = open ? 'Hide' : (app.sourceCount ? `Sources (${app.sourceCount})` : 'Sources');
});

/* =====================================================================
   Stage messages (loading, not covered, errors)
   ===================================================================== */

/**
 * Replace the player with a message card: the spinner while asking, an error, or an FAQ, Canvas or web answer.
 * `actions` are buttons ({label, onClick, primary}), `chips` suggested questions, `extra` nodes under the text.
 */
function showStageMessage({ title, text, spinner = false, actions = [], chips = [], label = '', labelClass = '', extra = [] }) {
  ui.player.hidden = true;
  stopWebAudio();
  ui.stageLabel.textContent = label || '';
  ui.stageLabel.hidden = !label;
  ui.stageLabel.className = `eyebrow stage-label${labelClass ? ` ${labelClass}` : ''}`;
  ui.stageExtra.replaceChildren(...extra);
  ui.stageExtra.hidden = !extra.length;
  ui.stageMsg.hidden = false;
  ui.stageSpinner.hidden = !spinner;
  ui.stageTitle.textContent = title || '';
  ui.stageText.textContent = text || '';
  ui.stageActions.replaceChildren(...actions.map(a =>
    el('button', { type: 'button', class: `btn ${a.primary ? 'btn-primary' : ''}`, onclick: a.onClick }, a.label)));
  renderChips(ui.stageChips, chips);
  ui.stageMsg.setAttribute('role', spinner ? 'status' : 'alert');
}

/** Move focus to the card's first button, unless the student is typing the next question. */
function focusFirstStageButton() {
  const firstBtn = ui.stageActions.querySelector('button') || ui.stageChips.querySelector('button');
  if (firstBtn && !ui.dockQ.matches(':focus')) firstBtn.focus({ preventScroll: true });
}

/** An error or referral card from COPY[kind], with "Try again" where trying again can help. */
function showStageError(kind, question, answer = null) {
  const [title, text] = COPY[kind] || COPY.generic;
  const actions = [];
  if (kind === 'unreachable' || kind === 'generic' || kind === 'rateLimited' || kind === 'contentLoading') {
    actions.push({ label: 'Try again', primary: true, onClick: () => ask(question) });
  }
  if (kind === 'logistics' && answer) actions.push(...answerActions(answer));
  const withChips = kind === 'notCovered' || kind === 'notReady' || kind === 'badQuestion' || kind === 'logistics';
  const chips = withChips ? stageChips() : [];
  // Only invite the visitor to pick a chip when there are chips to pick.
  const invite = kind === 'logistics' ? 'Or ask me about the course:' : 'Try one of these instead:';
  if (kind === 'notCovered' || kind === 'logistics') clearSourcesToggle();
  showStageMessage({ title, text: chips.length ? `${text} ${invite}` : text, actions, chips });
  addTwinMessage(title, kind === 'logistics' ? '' : 'msg-error');
  ui.followups.hidden = true;
  focusFirstStageButton();
}

/* Link buttons (e.g. my Calendly) and TA contact cards that come with an FAQ or logistics answer. */
function answerActions(answer) {
  const actions = [];
  for (const link of answer.links || []) {
    if (!/^https:\/\//.test(String(link.url || ''))) continue;
    actions.push({ label: link.label, primary: true, onClick: () => window.open(link.url, '_blank', 'noopener') });
  }
  const contacts = answer.contacts || [];
  for (const c of contacts) {
    const label = contacts.length > 1 ? `Contact the ${c.course_label} TA` : 'Contact the TA';
    actions.push({ label, onClick: () => showContact(c) });
  }
  return actions;
}

/* FAQ answers and course-info answers from Canvas share this card: my words, link buttons, chips. */
function showFaqAnswer(answer, label = '') {
  clearSourcesToggle();
  const chips = stageChips();
  showStageMessage({ title: answer.title || 'From my course FAQ', text: answer.message || '', actions: answerActions(answer), chips, label });
  addTwinMessage(answer.message || '');
  ui.followups.hidden = true;
  focusFirstStageButton();
}

/* Beyond my slides: an answer from a web search when no slide covers a course-adjacent question.
   Always labeled, with its sources and the closest slides in my course. It is text; when Settings
   turns it on, a Listen button reads it in a stock voice (never my clone), labeled as such. */
let webAudio = null;
/** Stop a web answer's Listen audio (a new question or card replaces it). */
function stopWebAudio() {
  if (webAudio) { releaseMedia(webAudio); webAudio = null; }
}

/** A titled list under a stage card (web sources, related slides), or null when it would be empty. */
function listSection(title, listClass, items) {
  return items.length ? el('section', { 'aria-label': title }, el('h3', { text: title }), el('ul', { class: listClass }, ...items)) : null;
}

/** The web answer's sources: https links only, each with its host, opening in a new tab. */
function webSourceList(links) {
  const items = [];
  for (const link of links || []) {
    const url = String(link.url || '');
    if (!/^https:\/\//.test(url)) continue;
    let host = '';
    try { host = new URL(url).hostname.replace(/^www\./, ''); } catch { continue; }
    items.push(el('li', {}, el('a', { href: url, target: '_blank', rel: 'noopener noreferrer' },
      String(link.label || host), el('span', { class: 'host', text: host }))));
  }
  return listSection(COPY.webSources, 'web-sources', items);
}

/** The closest slides in my course under a web answer; each opens in the slide dialog. */
function relatedSlides(related) {
  const items = (related || []).filter(r => r && r.image).map(r => {
    const l1 = slideLine(r);
    return el('li', {}, el('button', {
      type: 'button', class: 'source-btn', 'aria-label': `${l1}${r.title ? `. ${r.title}` : ''}. Open the slide`,
      onclick: () => openSlideDialog(r),
    }, el('img', { src: r.image, alt: '', loading: 'lazy' }),
    el('span', {}, el('span', { class: 'src-l1', text: l1 }), el('span', { class: 'src-l2', text: r.title || fmtDate(r.date) || '' }))));
  });
  return listSection(COPY.webRelated, 'related-slides', items);
}

/** A Listen button for a web answer, only for a signed /api/audio link in a voice that is not my clone. */
function webListen(answer) {
  const url = String(answer.audio || '');
  if (!url.startsWith('/api/audio?') || !answer.voice || answer.voice.kind === 'clone') return null;
  const btn = el('button', { type: 'button', class: 'btn btn-small' }, 'Listen');
  btn.addEventListener('click', () => {
    if (webAudio && !webAudio.paused) { webAudio.pause(); btn.textContent = 'Listen'; return; }
    if (!webAudio) {
      webAudio = new Audio(url);
      webAudio.addEventListener('ended', () => { btn.textContent = 'Listen'; });
      webAudio.addEventListener('error', () => { btn.textContent = 'Audio unavailable'; btn.disabled = true; });
    }
    webAudio.play().then(() => { btn.textContent = 'Pause'; }).catch(() => { btn.textContent = 'Listen'; });
  });
  return el('div', { class: 'web-listen' }, btn, el('span', { class: 'small', text: answer.voice.label || 'AI voice (a stock voice, not mine).' }));
}

/** The labeled "Beyond my slides" card: the answer, Listen, sources, closest slides, then any helper slide. */
function showWebAnswer(answer) {
  clearSourcesToggle();
  const chips = stageChips();
  const extra = [webListen(answer), webSourceList(answer.links), relatedSlides(answer.related)].filter(Boolean);
  showStageMessage({ title: answer.title || 'Beyond my slides', text: answer.message || '', chips,
    label: COPY.webLabel, labelClass: 'web-label', extra });
  addTwinMessage(`${COPY.webLabel}. ${answer.message || ''}`);
  ui.followups.hidden = true;
  // A web answer is never in my voice: the dock names the stock voice when there is one, else nothing.
  ui.voiceLabel.textContent = answer.voice?.label || '';
  ui.voiceLabel.hidden = !answer.voice;
  // An AI-drawn helper slide goes last, after the sources and my closest slides.
  if (answer.generated_slide) {
    const myId = app.requestId;
    helperFigure(answer.generated_slide).then((fig) => {
      if (!fig || myId !== app.requestId) return;
      ui.stageExtra.append(fig);
      ui.stageExtra.hidden = false;
    });
  }
}

/* AI-drawn helper slides (public/helper-slide.js draws a checked spec; never markup from a model).
   Loaded only when an answer has one. */
let helperModule = null;
/** The drawn helper slide, or null when it cannot be drawn (the answer shows without it). */
async function helperFigure(slide) {
  try {
    helperModule = helperModule || await import('./helper-slide.js');
    return helperModule.renderHelperFigure(slide);
  } catch { return null; }
}

/* An AI-drawn helper slide under a walkthrough: labeled, dashed border, after the real slides. */
function showHelperSlide(slide) {
  ui.helperSlot.replaceChildren();
  ui.helperSlot.hidden = true;
  if (!slide) return;
  const myId = app.requestId;
  helperFigure(slide).then((fig) => {
    if (!fig || myId !== app.requestId) return;
    ui.helperSlot.replaceChildren(fig);
    ui.helperSlot.hidden = false;
  });
}

/* The TA's contact details in a small card. The twin never says a TA's name aloud. */
function showContact(c) {
  ui.contactTitle.textContent = `TA for ${c.course_label}`;
  const email = el('a', { href: `mailto:${c.email}` }, c.email);
  ui.contactBody.replaceChildren(
    ...(c.name ? [el('p', { class: 'contact-name' }, c.name)] : []),
    el('p', {}, email),
    el('p', { class: 'contact-note' }, 'Presentation schedule changes and Canvas problems with participation points go to the TA.'),
  );
  ui.contactDialog.showModal();
}

/* =====================================================================
   Chat log
   ===================================================================== */

/** The student's question in the dock's log. */
function addUserMessage(q) {
  ui.log.append(el('li', { class: 'msg msg-user' }, q));
  scrollLog();
}
/** The twin's reply in the dock's log; returns the item so a sources list can go under it. */
function addTwinMessage(text, extra = '') {
  const li = el('li', { class: `msg msg-twin ${extra}` }, el('p', {}, text));
  ui.log.append(li);
  scrollLog();
  return li;
}
function scrollLog() { ui.dockLog.scrollTop = ui.dockLog.scrollHeight; }

/** One sentence for the log: how many slides, and from which session, course, or both courses. */
function summarize(answer) {
  // The course filter had nothing and the other course answered: the server's intro says so plainly.
  if (crossCourseIntro(answer)) return crossCourseIntro(answer);
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

/** The intro of an answer from the other course ("My 45-884 slides don't cover that, but ..."), or ''. */
function crossCourseIntro(answer) {
  return answer && answer.kind === 'cross_course' && typeof answer.message === 'string' ? answer.message : '';
}

/** "Slides used in this answer" under the log message; the newest list also feeds the phone's Sources toggle. */
function renderSourcesList(container, answer) {
  // Only the newest answer's list jumps within the walkthrough; older lists open the slide in a dialog.
  const sources = (answer.sources && answer.sources.length) ? answer.sources : answer.segments;
  const list = el('ol', { class: 'sources' });
  for (const src of sources) {
    const segIndex = answer.segments.findIndex(s => s.slide_id === src.slide_id);
    const l1 = slideLine(src);
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

/** This answer used no slides: the phone's Sources toggle must not keep the previous answer's count. */
function clearSourcesToggle() {
  app.sourcesBlock = null;
  app.sourceCount = 0;
  if (!ui.dock.classList.contains('expanded')) ui.dockToggle.textContent = 'Sources';
}

/** A slide full size in a dialog (older answers' sources, and related slides). */
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
async function ask(raw, { source = null } = {}) {
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

/* Signed slide and clip links expire after an hour. If the slide image fails to load, fetch fresh
   links for this answer's slides (once per answer) and swap them in: same answer, same segment,
   no second model call. (Changed Oct 8: it used to ask the whole question again.) */
async function refreshExpiredLinks() {
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
  useFallback: false,   // the first voice failed: segments play their audio_fallback (a free voice) instead
  finished: false,
  audio: new Map(),     // index -> HTMLAudioElement (current and preloaded)
  current: null,        // HTMLAudioElement playing now
  timer: null,          // captions-only: { id, startedAt, remainingMs, totalMs, raf, index }
  inClip: false,
  clipFailed: new Set(), // segment indexes whose class clip would not load: the button stays hidden
};

/** Load a new answer and start at segment 1 (or `start`). */
function loadAnswer(answer, start = 0) {
  stopPlayback();
  player.answer = answer;
  player.segments = answer.segments.slice().sort((a, b) => (a.n ?? 0) - (b.n ?? 0));
  player.clipFailed.clear();
  start = Math.min(Math.max(0, start), player.segments.length - 1);
  player.captionsOnly = player.segments.every(s => !s.audio);
  player.useFallback = false;
  player.finished = false;
  player.sentPlayed = false; // usage events: once per answer
  player.sentCompleted = false;
  ui.audioNote.hidden = !player.captionsOnly;
  ui.crossNote.textContent = crossCourseIntro(answer);
  ui.crossNote.hidden = !ui.crossNote.textContent;
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

  if (ui.slideImg.getAttribute('src') !== seg.image) {
    ui.slideImg.classList.add('is-loading');
    ui.slideImg.src = seg.image;
  }
  ui.slideImg.alt = `Slide ${seg.slide_number} from ${courseCode(seg.course)} session ${seg.session}, ${seg.session_title}`;
  ui.slideAlt.textContent = ui.slideImg.alt;

  renderCode(seg.code);

  ui.clipBtn.hidden = clipButtonHidden(seg, i);
  ui.clipBack.hidden = true;
  ui.clipNote.hidden = true;

  renderNarration(seg);
  renderSourceCard(seg);

  updateDots();
  updateControls();
  updateVoiceLabel();
  app.sourcesBlock?.querySelectorAll('.source-btn').forEach(b => b.setAttribute('aria-current', String(b.dataset.slide === seg.slide_id)));
}

/** The "where this is from" card next to the slide. */
function renderSourceCard(seg) {
  ui.srcThumb.src = seg.image;
  ui.srcThumb.alt = '';
  ui.srcCourse.textContent = courseLabel(seg.course, seg.course_title);
  ui.srcSession.textContent = `Session ${pad2(seg.session)}: ${seg.session_title || ''}`.replace(/: $/, '');
  ui.srcDate.textContent = fmtDate(seg.date);
  ui.srcSlide.textContent = `Slide ${seg.slide_number}`;
}

/** No class clip for this segment, or its clip already failed to load (the button then stays hidden). */
function clipButtonHidden(seg, i) {
  return !seg?.clip?.url || player.clipFailed.has(i);
}

/** The code panel: the notebook source with the marked lines, scrolled to the first marked line. */
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

/** The audio element for segment i, created once and reused (this is also the preload). */
function audioFor(i) {
  const seg = player.segments[i];
  const url = seg && (player.useFallback ? seg.audio_fallback : seg.audio);
  if (!url || player.captionsOnly) return null;
  if (player.audio.has(i)) return player.audio.get(i);
  const a = new Audio();
  a.preload = 'auto';
  a.muted = player.muted;
  a.addEventListener('ended', () => { if (a === player.current) onClipEnded(i); });
  a.addEventListener('timeupdate', () => { if (a === player.current) followNarration(); });
  a.addEventListener('playing', () => { if (a === player.current) followLoop(); });
  a.addEventListener('seeked', () => { if (a === player.current) followNarration(); });
  a.addEventListener('error', () => { if (a === player.current) voiceFailed(); });
  a.src = url;
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
  if (!player.sentPlayed) { player.sentPlayed = true; track('segment_played'); }
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
          showNarrationNote(COPY.tapToPlay);
          updateControls();
        } else if (err && err.name !== 'AbortError') {
          voiceFailed();
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

/** Drop every segment's audio element (current and preloaded). */
function releaseSegmentAudio() {
  for (const a of player.audio.values()) releaseMedia(a);
  player.audio.clear();
}

/** Stop everything (new question, logout). */
function stopPlayback() {
  stopNarration();
  exitClip(false);
  releaseSegmentAudio();
  player.playing = false;
}

/** Pause the narration (audio or caption timer) where it is. */
function pausePlayback() {
  player.playing = false;
  if (player.current) player.current.pause();
  if (player.timer) pauseCaptionTimer();
  updateControls();
}

/** Play again from where it paused; after the last segment, from the top. */
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
    player.current.play().catch(() => voiceFailed());
  } else if (player.timer) {
    resumeCaptionTimer();
  } else {
    playCurrent();
  }
  updateControls();
}

/** The play/pause button and the space bar. */
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
/** Next segment; after the last one, finish the answer. */
function goNext() {
  if (player.index >= player.segments.length - 1) finishAnswer();
  else jumpTo(player.index + 1);
}

/** After the last segment: stop, show follow-up chips. */
function finishAnswer() {
  stopNarration();
  player.playing = false;
  player.finished = true;
  finishNarration();
  const ups = (player.answer?.follow_ups || []).filter(Boolean);
  ui.followupChips.replaceChildren(...ups.map(q => chip(q, undefined, 'follow_up')));
  if (!player.sentCompleted) { player.sentCompleted = true; track('walkthrough_completed'); }
  ui.followups.hidden = ups.length === 0;
  updateDots();
  updateControls();
}

/** The current voice failed (service down, or today's cap). Switch to the free fallback voice
    if the answer has one, otherwise keep going with captions. */
function voiceFailed() {
  track('audio_failed');
  const seg = player.segments[player.index];
  if (!player.useFallback && seg && seg.audio_fallback) switchToFallbackVoice();
  else fallBackToCaptions();
}

/** Play the rest of this answer with the fallback voice, and change the label to match it. */
function switchToFallbackVoice() {
  player.useFallback = true;
  releaseSegmentAudio();
  player.current = null;
  updateVoiceLabel();
  loadTimings(player.segments[player.index]);
  if (player.playing) { playCurrent(); preloadAudio(player.index + 1); }
  updateControls();
}

/** Audio failed or the daily voice cap was hit: keep going with captions and a timer. */
function fallBackToCaptions() {
  if (player.captionsOnly) return;
  player.captionsOnly = true;
  ui.audioNote.hidden = false;
  releaseSegmentAudio(); // pause every element and drop its source, so nothing keeps downloading
  player.current = null;
  reading.words = null; // the caption timer starts the segment again on the estimate
  if (player.playing) startCaptionTimer();
  updateControls();
  updateVoiceLabel();
}

/* ---- captions-only timer: paces a segment by word count, then calls onClipEnded() ---- */

/** How long a segment shows in captions only: its words at reading pace, at least CAPTION_MIN_SEC. */
function captionDurationMs(seg) {
  return Math.max(CAPTION_MIN_SEC, wordCount(seg.narration) / CAPTION_WORDS_PER_SEC) * 1000;
}
/** Start pacing the current segment from its beginning. */
function startCaptionTimer() {
  clearCaptionTimer();
  const total = captionDurationMs(player.segments[player.index]);
  player.timer = { id: null, raf: null, startedAt: 0, remainingMs: total, totalMs: total, index: player.index };
  resumeCaptionTimer();
}
/** Run the timer for what is left of the segment, and follow the narration on every frame meanwhile. */
function resumeCaptionTimer() {
  const t = player.timer;
  if (!t) return;
  t.startedAt = performance.now();
  t.id = setTimeout(() => {
    cancelAnimationFrame(t.raf);
    player.timer = null;
    onClipEnded(t.index);
  }, t.remainingMs);
  const tick = () => {
    const elapsed = t.totalMs - t.remainingMs + (performance.now() - t.startedAt);
    if (elapsed <= t.totalMs) followNarration();
    t.raf = requestAnimationFrame(tick);
  };
  t.raf = requestAnimationFrame(tick);
}
/** Stop the clock and remember how much of the segment is left. */
function pauseCaptionTimer() {
  const t = player.timer;
  if (!t || t.id == null) return;
  clearTimeout(t.id); cancelAnimationFrame(t.raf);
  t.id = null;
  t.remainingMs = Math.max(0, t.remainingMs - (performance.now() - t.startedAt));
}
/** Drop the timer entirely (a new segment, or audio took over). */
function clearCaptionTimer() {
  const t = player.timer;
  if (t) { clearTimeout(t.id); cancelAnimationFrame(t.raf); }
  player.timer = null;
}

/* =====================================================================
   Read-along (docs/SPEC.md, "Read-along narration and slide spotlight")
   The narration box shows the whole narration, one span per word. On every animation frame
   while narration runs, the word being spoken is found from the segment's word timings
   (`timings` from the voice service, fetched when the segment shows) or, until they arrive and
   in captions only, from the syllable estimate over the segment's duration. Narration words
   that are also on the slide light up those words on the slide image (`boxes`).
   ===================================================================== */

const reading = {
  seg: null,           // the segment the box shows
  tokens: [],          // RA.tokenize(narration)
  spans: [],           // one span per token
  sents: [],           // [{first, last}] token ranges
  words: null,         // real timings for the voice speaking now, or null (estimate)
  est: { dur: 0, words: [] },
  current: -1,         // token being spoken
  sentence: -1,
  plan: new Map(),     // token index -> slide region to light up
  fired: -1,           // last token whose highlight was considered
  raf: 0,
  marks: [],           // [{node, key, timer}] lit regions, oldest first
  recent: new Map(),   // region key -> time it was last lit
};
const MAX_MARKS = 2;
const MARK_HOLD_MS = 1500;
const MARK_AGAIN_MS = 3000;
const TIMING_RETRIES = [1200, 2500, 5000, 9000];
const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : 0);

/** Draw the narration for a segment and start loading its timings and the slide's word boxes. */
function renderNarration(seg) {
  reading.seg = seg;
  reading.tokens = RA.tokenize(seg.narration);
  reading.sents = RA.sentences(reading.tokens);
  reading.spans = reading.tokens.map((t, k) =>
    el('span', { class: 'w', 'data-s': String(t.sentence) }, k < reading.tokens.length - 1 ? `${t.text} ` : t.text));
  ui.caption.replaceChildren(...reading.spans);
  ui.caption.classList.toggle('reduce', reducedMotion());
  ui.caption.scrollTop = 0;
  reading.current = -1;
  reading.sentence = -1;
  reading.words = null;
  reading.est = { dur: 0, words: [] };
  reading.plan = new Map();
  reading.fired = -1;
  hideNarrationNote();
  clearSlideMarks();
  loadTimings(seg);
  loadBoxes(seg);
}

/** Fetch the word timings for the voice speaking this segment (cached on the segment). */
async function loadTimings(seg, attempt = 0) {
  if (!seg || player.captionsOnly) return;
  const url = player.useFallback ? seg.timings_fallback : seg.timings;
  if (!url) return;
  seg._timings ||= {};
  if (seg._timings[url]) { if (reading.seg === seg) reading.words = seg._timings[url]; return; }
  let data = null;
  try {
    const res = await fetch(url, { credentials: 'same-origin' });
    if (res.ok) data = await res.json();
  } catch { /* the estimate keeps the box moving */ }
  const words = RA.validTimings(data?.words);
  if (words) {
    seg._timings[url] = words;
    const now = player.useFallback ? seg.timings_fallback : seg.timings;
    if (reading.seg === seg && now === url) { reading.words = words; followNarration(); }
    return;
  }
  // Live audio saves its timings when the stream finishes: ask again a little later.
  if (data?.source === 'pending' && attempt < TIMING_RETRIES.length) {
    setTimeout(() => { if (reading.seg === seg) loadTimings(seg, attempt + 1); }, TIMING_RETRIES[attempt]);
  }
}

/** Fetch the slide's word boxes once per segment and plan which narration words light up which slide words. */
async function loadBoxes(seg) {
  if (!seg?.boxes) return;
  if (!seg._boxes) {
    seg._boxes = fetch(seg.boxes).then(r => (r.ok ? r.json() : null)).catch(() => null);
  }
  const boxes = await seg._boxes;
  if (reading.seg !== seg || !boxes) return;
  reading.plan = RA.planHighlights(reading.tokens, boxes);
  reading.fired = reading.current; // words already said do not fire late
}

/** The time and length of the narration now: the audio element, or the captions-only timer. */
function narrationClock() {
  const a = player.current;
  const seg = player.segments[player.index];
  const fallback = seg ? captionDurationMs(seg) / 1000 : 0;
  if (a) {
    const d = num(a.duration);
    return { t: num(a.currentTime), dur: d > 0 ? d : fallback };
  }
  const tm = player.timer;
  if (tm) {
    const running = tm.id != null ? performance.now() - tm.startedAt : 0;
    return { t: (tm.totalMs - tm.remainingMs + running) / 1000, dur: tm.totalMs / 1000 };
  }
  return null;
}

/** Move the narration box (and the slide highlights) to the word being spoken. */
function followNarration() {
  const seg = player.segments[player.index];
  if (!seg || reading.seg !== seg || player.finished) return;
  const clock = narrationClock();
  if (!clock) return;
  let words = reading.words;
  if (!words) {
    if (Math.abs(reading.est.dur - clock.dur) > 0.05) reading.est = { dur: clock.dur, words: RA.estimateTimings(seg.narration, clock.dur) };
    words = reading.est.words;
  }
  const i = RA.tokenAt(reading.tokens, RA.charAt(words, clock.t));
  if (i !== reading.current) setCurrentWord(i);
}

/** Keep following on every frame while the audio plays (a few times a second is not smooth enough). */
function followLoop() {
  if (reading.raf) return;
  const step = () => {
    reading.raf = 0;
    followNarration();
    if (player.current && !player.current.paused && player.playing) reading.raf = requestAnimationFrame(step);
  };
  reading.raf = requestAnimationFrame(step);
}

/** Mark word i as being said (the ones before it as said), and light up the slide words it passed. */
function setCurrentWord(i) {
  const { spans } = reading;
  const prev = reading.current;
  const lo = Math.max(0, Math.min(prev, i)), hi = Math.min(spans.length - 1, Math.max(prev, i));
  for (let k = lo; k <= hi; k++) {
    spans[k].classList.toggle('said', k < i);
    spans[k].classList.remove('now');
  }
  reading.current = i;
  if (i >= 0 && spans[i]) spans[i].classList.add('now');
  const sentence = i >= 0 ? reading.tokens[i].sentence : -1;
  if (sentence !== reading.sentence) setSentence(sentence);
  if (i >= 0) keepInView(spans[i]);
  // Light up the slide for every planned word passed since the last frame (a seek backwards resets).
  if (i < reading.fired) reading.fired = i;
  for (let k = reading.fired + 1; k <= i; k++) {
    const match = reading.plan.get(k);
    if (match) lightUp(match);
  }
  reading.fired = Math.max(reading.fired, i);
}

/** A new sentence: tell screen readers (once per sentence), and with reduced motion, mark the sentence. */
function setSentence(n) {
  const prev = reading.sentence;
  reading.sentence = n;
  for (const span of reading.spans) {
    const s = Number(span.getAttribute('data-s'));
    if (s === prev || s === n) span.classList.toggle('in-sentence', s === n);
  }
  const range = reading.sents[n];
  if (range) ui.narrationLive.textContent = reading.tokens.slice(range.first, range.last + 1).map(t => t.text).join(' ');
}

/** Keep the spoken line in the upper middle of the box when the narration is longer than the box. */
function keepInView(span) {
  const box = ui.caption;
  const boxH = num(box.clientHeight), top = num(span.offsetTop) - num(box.offsetTop);
  if (!boxH || num(box.scrollHeight) <= boxH + 2) return;
  const y = top - num(box.scrollTop);
  if (y < boxH * 0.15 || y > boxH * 0.6) {
    box.scrollTo({ top: Math.max(0, top - boxH * 0.3), behavior: reducedMotion() ? 'auto' : 'smooth' });
  }
}

/** A short note in the narration box ("Tap play...", "That's the end..."), also read to screen readers. */
function showNarrationNote(text) {
  ui.narrationNote.textContent = text;
  ui.narrationNote.hidden = false;
  ui.narrationLive.textContent = text;
}
/** Clear the narration box's note. */
function hideNarrationNote() {
  if (!ui.narrationNote.hidden) { ui.narrationNote.hidden = true; ui.narrationNote.textContent = ''; }
}

/** After the last segment: every word said, the box says the answer is over. */
function finishNarration() {
  for (const span of reading.spans) { span.classList.add('said'); span.classList.remove('now', 'in-sentence'); }
  reading.current = reading.spans.length;
  reading.sentence = -1;
  showNarrationNote(COPY.finished);
}

/* ---- the slide lights up: highlighter marks over the words being said ---- */

/** Fit the marks layer to the picture inside the frame (the image keeps its aspect ratio). */
function layoutMarks() {
  const img = ui.slideImg;
  const fw = num(img.clientWidth), fh = num(img.clientHeight);
  const nw = num(img.naturalWidth) || 16, nh = num(img.naturalHeight) || 9;
  if (!fw || !fh) return false;
  const k = Math.min(fw / nw, fh / nh);
  const w = nw * k, h = nh * k;
  const st = ui.slideMarks.style;
  st.left = `${num(img.offsetLeft) + (fw - w) / 2}px`;
  st.top = `${num(img.offsetTop) + (fh - h) / 2}px`;
  st.width = `${w}px`;
  st.height = `${h}px`;
  return true;
}

/**
 * Highlight one planned slide region, unless a clip is showing, the slide is still loading, or the same
 * region was lit in the last MARK_AGAIN_MS. At most MAX_MARKS are lit; the oldest fades first.
 */
function lightUp(match) {
  if (player.inClip || ui.slideImg.hidden || ui.slideImg.classList.contains('is-loading')) return;
  const now = performance.now();
  if (now - (reading.recent.get(match.key) || -Infinity) < MARK_AGAIN_MS) return;
  if (!layoutMarks()) return;
  reading.recent.set(match.key, now);
  while (reading.marks.length >= MAX_MARKS) fadeMark(reading.marks[0]);
  const node = el('div', { class: 'mark-group' });
  for (const r of match.rects) {
    const padX = 0.004, padY = (r.y1 - r.y0) * 0.18;
    const box = el('span', { class: 'slide-mark' });
    box.style.left = `${Math.max(0, r.x0 - padX) * 100}%`;
    box.style.top = `${Math.max(0, r.y0 - padY) * 100}%`;
    box.style.width = `${(Math.min(1, r.x1 + padX) - Math.max(0, r.x0 - padX)) * 100}%`;
    box.style.height = `${(Math.min(1, r.y1 + padY) - Math.max(0, r.y0 - padY)) * 100}%`;
    node.append(box);
  }
  ui.slideMarks.append(node);
  const mark = { node, key: match.key, timer: 0 };
  // A longer phrase stays lit a little longer, so it is still there while the narrator finishes it.
  const hold = Math.min(3500, MARK_HOLD_MS + 300 * Math.max(0, (match.length || 1) - 1));
  mark.timer = setTimeout(() => fadeMark(mark), hold);
  reading.marks.push(mark);
}

/** Fade one highlight out and remove it once the fade is done. */
function fadeMark(mark) {
  clearTimeout(mark.timer);
  reading.marks = reading.marks.filter(m => m !== mark);
  mark.node.classList.add('out');
  setTimeout(() => mark.node.remove(), reducedMotion() ? 0 : 450);
}

/** Remove every highlight now (a new segment, or a clip). */
function clearSlideMarks() {
  for (const m of reading.marks) clearTimeout(m.timer);
  reading.marks = [];
  reading.recent.clear();
  ui.slideMarks.replaceChildren();
}

/**
 * Runs when a segment's narration finishes: the audio element's `ended`
 * event, or the captions-only timer running out. This is the only place
 * the walkthrough advances on its own: to the next segment, or after the
 * last one to finishAnswer().
 *
 * `endedIndex` is the segment whose narration ended. A late signal is
 * ignored: one for a segment that is no longer on screen, one after the
 * answer finished, and one while the class clip plays (the walkthrough
 * stays paused until the student presses play).
 *
 * First written by hand by Ben for the course assignment.
 */
function onClipEnded(endedIndex = player.index) {
  if (!player.playing || player.finished || player.inClip) return;
  if (endedIndex !== player.index) return;

  const next = player.index + 1;

  if (next >= player.segments.length) {
    finishAnswer();
    return;
  }

  showSegment(next);
  playCurrent();
  preloadAudio(next + 1);
}
/* ---- controls ---- */

/** One progress dot per segment; each jumps to its segment. */
function buildDots() {
  ui.dots.replaceChildren(...player.segments.map((s, i) => el('li', {},
    el('button', { type: 'button', 'aria-label': `Slide ${i + 1} of ${player.segments.length}`, onclick: () => jumpTo(i) }))));
}
/** Mark the current dot and the ones already played. */
function updateDots() {
  [...ui.dots.querySelectorAll('button')].forEach((b, i) => {
    if (i === player.index && !player.finished) b.setAttribute('aria-current', 'step'); else b.removeAttribute('aria-current');
    b.classList.toggle('done', i < player.index || player.finished);
  });
}
/** Bring the buttons, their labels and the speaking spotlight in line with the player state. */
function updateControls() {
  ui.player.classList.toggle('is-paused', !player.playing);
  // The slide being talked about gets a soft spotlight while the narration runs.
  ui.slideFrame.classList.toggle('is-speaking', player.playing && !player.inClip && !player.finished);
  if (player.playing) hideNarrationNote();
  ui.btnPlay.setAttribute('aria-label', player.playing ? 'Pause' : (player.finished ? 'Play again from the start' : 'Play'));
  ui.btnPrev.disabled = player.index <= 0;
  ui.btnNext.disabled = player.finished;
  ui.btnNext.setAttribute('aria-label', player.index >= player.segments.length - 1 ? 'Finish' : 'Next slide');
  ui.btnMute.setAttribute('aria-pressed', String(player.muted));
  ui.btnMute.setAttribute('aria-label', player.muted ? 'Unmute' : 'Mute');
  ui.btnMute.disabled = player.captionsOnly;
}

/** Mute or unmute the narration and the class clip; the walkthrough keeps its timing. */
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

/** Show the class recording for this slide in place of the slide; the narration pauses. */
function enterClip() {
  const seg = player.segments[player.index];
  if (!seg?.clip?.url) return;
  track('clip_played');
  if (player.playing) pausePlayback();
  player.inClip = true;
  clearSlideMarks();
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
  releaseMedia(ui.clipVideo);
  ui.clipVideo.hidden = true;
  ui.slideImg.hidden = false;
  ui.clipBtn.hidden = clipButtonHidden(player.segments[player.index], player.index);
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

ui.slideImg.addEventListener('load', () => ui.slideImg.classList.remove('is-loading'));
ui.slideImg.addEventListener('error', () => {
  ui.slideImg.classList.remove('is-loading');
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

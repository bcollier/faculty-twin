// Settings > 3. Courses and source material: the catalog, sessions, uploads and their status.
// Part of the Settings page; admin.js has the map.

import {
  $, api, asList, AuthError, courseCode, detail, el, fmtWhen, humanize, pad2, say, sayError,
} from './helpers.js';
import { S } from './state.js';

/* ---------------- 3. courses and source material ---------------- */

const KIND_ACCEPT = { slides: '.pdf,.pptx', transcript: '.vtt', video: '.mp4', notebook: '.ipynb' };
const HAS_KEYS = [['slides', 'Slides'], ['transcript', 'Transcript'], ['video', 'Video'], ['clips', 'Clips'], ['indexed', 'Indexed']];
const STATUS_PILL = { ready: 'ok', processing: 'info', uploaded: 'warn', uploading: 'warn', error: 'err' };

/** Courses, sessions and uploaded sources, then the tables, the upload menus and the status poll. */
export async function loadCourses() {
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

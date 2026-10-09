// Student page: the chat log in the dock and the "Slides used in this answer" list.
// Part of the student page; app.js has the map.

import { courseCode, el, fmtDate, slideLine } from './helpers.js';
import { app, ui } from './state.js';
import { jumpTo, player } from './player.js';

/* =====================================================================
   Chat log
   ===================================================================== */

/** The student's question in the dock's log. */
export function addUserMessage(q) {
  ui.log.append(el('li', { class: 'msg msg-user' }, q));
  scrollLog();
}
/** The twin's reply in the dock's log; returns the item so a sources list can go under it. */
export function addTwinMessage(text, extra = '') {
  const li = el('li', { class: `msg msg-twin ${extra}` }, el('p', {}, text));
  ui.log.append(li);
  scrollLog();
  return li;
}
function scrollLog() { ui.dockLog.scrollTop = ui.dockLog.scrollHeight; }

/** One sentence for the log: how many slides, and from which session, course, or both courses. */
export function summarize(answer) {
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
export function crossCourseIntro(answer) {
  return answer && answer.kind === 'cross_course' && typeof answer.message === 'string' ? answer.message : '';
}

/** "Slides used in this answer" under the log message; the newest list also feeds the phone's Sources toggle. */
export function renderSourcesList(container, answer) {
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
export function clearSourcesToggle() {
  app.sourcesBlock = null;
  app.sourceCount = 0;
  if (!ui.dock.classList.contains('expanded')) ui.dockToggle.textContent = 'Sources';
}

/** A slide full size in a dialog (older answers' sources, and related slides). */
export function openSlideDialog(src) {
  ui.dialogTitle.textContent = `${courseCode(src.course)}, session ${src.session}, slide ${src.slide_number}`;
  ui.dialogImg.src = src.image;
  ui.dialogImg.alt = `Slide ${src.slide_number} from session ${src.session}`;
  ui.dialog.showModal();
}

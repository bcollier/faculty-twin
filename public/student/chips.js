// Student page: the course filter, the suggested-question chips, and the two question forms.
// Part of the student page; app.js has the map.

import { MAX_CHIPS, STAGE_CHIPS } from './copy.js';
import { courseCode, el, track } from './helpers.js';
import { app, saveCourseChoice, ui } from './state.js';
import { ask } from './asking.js';

/* =====================================================================
   Course filter, chips, question forms
   ===================================================================== */

/** Filter by course ('' or null is both). Remembered on this device and reflected in both pickers. */
export function setCourse(value) {
  app.course = value || null;
  saveCourseChoice(value || '');
  ui.dockCourse.value = value || '';
  const radio = ui.idleCourse.querySelector(`input[value="${value || ''}"]`);
  if (radio) radio.checked = true;
  renderIdleChips();
}
/** Either copy of the course filter changed: apply it to the page and count the change. */
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
export function chip(text, courseCodeStr, source = 'chip') {
  const onclick = () => { track(source === 'follow_up' ? 'follow_up_tapped' : 'chip_tap'); ask(text, { source }); };
  const b = el('button', { type: 'button', class: 'chip', onclick }, text);
  if (courseCodeStr && !app.course) b.append(el('span', { class: 'chip-course', text: courseCode(courseCodeStr) }));
  return el('li', {}, b);
}

/** The fewer suggestions shown under a stage message. */
export function stageChips() {
  return topicsForCourse().slice(0, STAGE_CHIPS);
}

/** Fill a chip list with question chips. */
export function renderChips(listNode, topics) {
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

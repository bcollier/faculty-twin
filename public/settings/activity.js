// Settings > 5. Activity: the question log with each answer's kind, score, model and fallback reason.
// Part of the Settings page; admin.js has the map.

import { $, api, asList, detail, el, fmtNum, fmtWhen } from './helpers.js';
import { S } from './state.js';
import { renderStatus } from './status.js';
import { renderKv } from './limits.js';

/* ---------------- 5. activity ---------------- */

/** Today's counters, as the server reports them, in the Activity panel. */
export function renderToday() {
  renderKv($('#today-kv'), S.status?.today || {}, 'nothing yet');
}

/* What answered each question (question_log.kind). docs/TESTING_AND_SCORES.md explains each one. */
const KIND_BADGES = {
  course_content: { text: 'Covered', cls: 'ok', title: 'Slides found at or above the threshold, narrated by the model.' },
  stored_topic: { text: 'Stored answer', cls: 'ok', title: 'A suggested question: its stored answer was replayed. No search, no model.' },
  faq: { text: 'FAQ', cls: 'info', title: 'Answered from my course FAQ, word for word. No search, no model.' },
  course_info: { text: 'From Canvas', cls: 'info', title: 'Answered from my Canvas pages (syllabus, policies, assignments), written by the model from those pages only.' },
  cross_course: { text: 'Other course', cls: 'ok', title: 'The course filter had no slide at or above the threshold, but the other course did: answered from those slides, and the student was told they are from the other course.' },
  web: { text: 'From the web', cls: 'info', title: 'No slide covered it, but it was about AI, data or coding tools: answered from a web search, with source links. Never in my voice.' },
  logistics: { text: 'Referred to Ben', cls: 'info', title: 'A logistics question: the student was sent to me.' },
  not_covered: { text: 'Not covered', cls: 'warn', title: 'No slide scored at or above the threshold.' },
  alert: { text: 'Student alert', cls: 'warn', title: 'A student reported a broken quiz, submission or API key. See Student alerts for whether a text went out.' },
};
/** What answered the question (an older row without a kind is inferred from `covered`). */
function kindBadge(x) {
  const fallback = x.covered ? KIND_BADGES.course_content : KIND_BADGES.not_covered;
  const b = KIND_BADGES[x.kind] || fallback;
  const title = x.kind_inferred ? `${b.title} (Inferred from the score: logged before kinds were recorded.)` : b.title;
  return el('span', { class: `pill ${b.cls}`, text: b.text, title });
}
/** Smoke checks, evals and model tests (question_log.source); "likely" when only the question text matched. */
function testBadge(x) {
  if (!x.test) return null;
  const title = x.test_inferred
    ? 'Likely test traffic: the question is one of the smoke-check questions (logged before sources were recorded).'
    : `Test traffic (${x.source}): left out of student analytics.`;
  return el('span', { class: 'pill off', text: x.test_inferred ? 'Test?' : 'Test', title, style: 'margin-left:.3rem' });
}
/* Why a course-info or web answer fell back (question_log.fallback_reason; app/course_info.py, app/web_answer.py). */
const FALLBACK_REASONS = {
  provider_credits: 'Model account out of credits',
  provider_auth: 'Model key rejected',
  provider_rate_limit: 'Model rate-limited',
  provider_unreachable: 'Model unreachable',
  provider_refused: 'Model declined',
  provider_error: 'Model error',
  daily_cap: 'Daily model-call cap reached',
  not_json: 'Reply was not JSON',
  no_answer: 'Reply had no answer',
  too_long: 'Answer too long',
  not_grounded: 'Answer not grounded in Canvas',
  unsafe_text: 'Answer failed a safety check',
  no_links: 'Search gave no usable link',
  error: 'Unexpected error',
};
/** "Fell back: ..." when a course-info or web answer could not be written. */
function fallbackBadge(x) {
  if (!x.fallback_reason) return null;
  const text = FALLBACK_REASONS[x.fallback_reason] || x.fallback_reason;
  const saw = x.kind === 'web' ? '"Here is where to look." with links and my closest slides'
    : x.kind === 'course_info' ? 'the Canvas text' : 'my fallback';
  return el('span', { class: 'pill warn', text: `Fell back: ${text}`, title: `The student saw ${saw}, not a written answer (${x.fallback_reason}).`, style: 'margin-left:.3rem' });
}
/** "provider / model", or "none" when no model was called. */
function modelText(x) {
  return [x.provider, x.model].filter(Boolean).join(' / ') || 'none';
}

/** Refresh the status counts and the last 50 questions. */
export async function loadActivity() {
  try {
    const [st, lg] = await Promise.all([api('/api/admin/status'), api('/api/admin/log')]);
    if (st.ok) renderStatus(st.data);
    const rows = lg.ok ? asList(lg.data, 'rows', 'log', 'items').slice(0, 50) : [];
    $('#log-body').replaceChildren(...(rows.length ? rows.map(x => el('tr', {},
      el('td', { class: 'small muted', text: fmtWhen(x.created_at || x.at || x.time) }),
      el('td', {}, kindBadge(x), testBadge(x), fallbackBadge(x)),
      el('td', { class: 'full', text: x.question || '' }),
      el('td', { class: 'num', 'data-label': 'Top score', title: x.top_score != null ? null : 'No search ran', text: x.top_score != null ? Number(x.top_score).toFixed(3) : '' }),
      el('td', { class: 'num', 'data-label': 'Latency', text: x.latency_ms != null ? `${fmtNum(x.latency_ms)} ms` : '' }),
      el('td', { class: `small full${x.provider || x.model ? '' : ' muted'}`, 'data-label': 'Model', title: x.provider || x.model ? null : 'No model was called for this question', text: modelText(x) }),
    )) : [el('tr', {}, el('td', { colspan: '6', class: 'muted', text: lg.ok ? 'No questions logged yet.' : detail(lg, 'Couldn\'t load the log.') }))]));
  } catch (e) { /* auth handled in api(); network shows on next refresh */ }
}
$('#refresh-activity').addEventListener('click', loadActivity);

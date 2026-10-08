// Faculty Twin Settings > Evals (admin only). Talks only to /api/admin/evals/* with the ft_admin cookie.
// A run is driven from this page one step at a time (one question x one answering model per request),
// so it can be paused, and a reload picks up where the server says it is.

const $ = (s, r = document) => r.querySelector(s);
/** Build an element: `class`, `text`, `on<event>` listeners, other keys as attributes. Never takes HTML. */
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k === 'text') n.textContent = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids) if (kid != null && kid !== false) n.append(kid);
  return n;
};
const SVG = 'http://www.w3.org/2000/svg';
/** Build an SVG element with attributes and children. */
const svg = (tag, attrs = {}, ...kids) => {
  const n = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) n.setAttribute(k, v);
  for (const kid of kids) if (kid != null) n.append(kid);
  return n;
};
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const say = (node, text, kind = '') => { node.textContent = text || ''; node.className = `status-line ${kind}`.trim(); };
const pct = (x) => (x == null ? 'n/a' : `${Math.round(Number(x) * 100)}%`);
const num = (x) => (x == null ? 'n/a' : Number(x).toFixed(2));
const fmtDate = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  return isNaN(d) ? String(iso) : d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
};
const fmtDay = (iso) => {
  const d = new Date(iso);
  return isNaN(d) ? String(iso || '') : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
};
/** "off_topic" as "Off topic". */
const humanCat = (c) => String(c || '').toLowerCase().replace(/_/g, ' ').replace(/^./, s => s.toUpperCase());
/** A model id without its provider prefix, dots as dashes ("anthropic/claude-sonnet-5.5" as "claude-sonnet-5-5"). */
const baseModel = (m) => String(m || '').split('/').pop().toLowerCase().replace(/\./g, '-');

const DIMS = {
  grounded: 'Grounded', answers_question: 'Answers the question', correct_scope: 'Right scope',
  matches_reference: 'Matches the real reply', speech_quality: 'Speech quality', safety_tone: 'Safety and tone',
  // Teaching quality (added Oct 8): their own group in the rubric (app/eval_core.py, DIMENSION_GROUPS).
  good_teaching: 'Good teaching', explains_concept_effectively: 'Explains the concept effectively', accurate: 'Accurate',
  engaging_voice: 'Engaging voice', appropriate_depth: 'Appropriate depth',
};
const DIM_SHORT = {
  grounded: 'Grounded', answers_question: 'Answers', correct_scope: 'Scope', matches_reference: 'Real reply', speech_quality: 'Speech', safety_tone: 'Safety',
  good_teaching: 'Teaching', explains_concept_effectively: 'Explains', accurate: 'Accurate', engaging_voice: 'Voice', appropriate_depth: 'Depth',
};
/** A score dimension's column title, with its scale. */
const dimHeader = (d) => `${DIMS[d]} (1–5, 5 best)`;
const METRICS = {
  pass_rate: { label: 'Pass rate (% of answers judged pass)', short: 'Pass rate', rate: true, n: 'judgements' },
  ...Object.fromEntries(Object.entries(DIMS).map(([k, v]) => [k, { label: `${v} (mean, 1–5, 5 best)`, short: v, rate: false, dim: true }])),
  decline_accuracy: { label: 'Right call: answer versus decline (% of questions)', short: 'Right call', rate: true, n: 'questions' },
  fallback_rate: { label: 'Fell back to speaker notes (% of answers with slides)', short: 'Fell back to notes', rate: true, n: 'answered' },
  judge_agreement: { label: 'Judges agree on the verdict (% of answers both judged)', short: 'Judges agree', rate: true },
  // Added Oct 8 (docs/SPEC.md, Block 8c). Runs before then show n/a.
  pass_rate_excluding_same_family: { label: 'Pass rate without same-family judges (% of answers judged pass)', short: 'Pass rate, other families', rate: true, n: 'judgements_excluding_same_family' },
  route_accuracy: { label: 'Right route: slides, Canvas, FAQ, referral, web or decline (% of answers)', short: 'Right route', rate: true, n: 'route_n' },
  retrieval_hit_rate: { label: 'Retrieval hit: an expected slide was used (% of questions with expected slides)', short: 'Retrieval hit', rate: true, n: 'hit_n' },
  cost_per_answer: { label: 'Cost per answer (USD, estimated from tokens)', short: 'Cost per answer', unit: 'usd', n: 'cost_n' },
  mean_latency_ms: { label: 'Mean time to answer (seconds, inside answer())', short: 'Time to answer', unit: 's', n: 'latency_n' },
};
// Shown under every table when the server's copy (app/eval_core.py) has not loaded yet.
const LEGEND_FALLBACK = {
  dimensions: Object.entries(DIMS).map(([key, label]) => ({ key, label, description: '' })),
  scale: 'Scores run from 1 to 5: 1 = very poor, 3 = acceptable, 5 = excellent.',
  mean: 'Each score is the mean over the judged answers; n is how many answers that mean covers. Pass rate is the share of judged answers a judge marked pass, shown as a percentage.',
  na: 'n/a means the dimension did not apply: grounded has nothing to check when the twin declined or when an answer had no slides (the generic-chatbot baseline).',
  verdict: 'Pass or fail is each judge\'s overall verdict, given separately from the six scores: it is not computed from them.',
};
/** A mean score with its n, or "n/a" (grounded says why: no slides to check). */
const scoreCell = (dim, mean, n) => (mean == null ? (dim === 'grounded' ? 'n/a (no slides)' : 'n/a')
  : `${Number(mean).toFixed(2)}${n != null ? ` (n=${n})` : ''}`);
/** A rate as a percentage with its n, or n/a. */
const rateCell = (rate, n) => (rate == null ? 'n/a' : `${pct(rate)}${n != null ? ` (n=${n})` : ''}`);
/** A cost in dollars or a time in seconds (from milliseconds), or n/a. */
const unitText = (unit, v) => (v == null ? 'n/a' : unit === 'usd' ? `$${Number(v).toFixed(4)}` : `${(Number(v) / 1000).toFixed(1)} s`);
/** One measure for one answering model, in its own form (score, rate, cost or time) with its n. */
function metricCell(m, key) {
  const meta = METRICS[key];
  if (meta.dim) return scoreCell(key, m.scores?.[key], m.score_n ? m.score_n[key] : null);
  if (meta.unit) return `${unitText(meta.unit, m[key])}${m[key] != null && meta.n && m[meta.n] != null ? ` (n=${m[meta.n]})` : ''}`;
  return rateCell(m[key], meta.n ? m[meta.n] : null);
}
const legendCalls = new Map();
/** The server's legend text arrived: redraw every legend already on the page with it. */
function setLegend(data) {
  if (!data) return;
  E.legend = data;
  for (const [target, args] of legendCalls) legend(target, ...args);
}
/** The notes under a table: `extra` lines first, then (with `scores`) what each score means. */
function legend(target, extra = [], { scores = true } = {}) {
  legendCalls.set(target, [extra, { scores }]);
  const L = E.legend || LEGEND_FALLBACK;
  const lines = [...extra];
  if (scores) {
    for (const d of L.dimensions) lines.push([`${d.label}: `, d.description || '']);
    lines.push(L.scale, L.mean, L.na, L.verdict);
  }
  $(target).replaceChildren(...lines.map(t => (Array.isArray(t)
    ? el('li', {}, el('strong', { text: t[0] }), t[1])
    : el('li', { text: t }))));
}
const OUTCOMES = {
  course_content: ['Covered', 'ok'], stored_topic: ['Stored answer', 'ok'], faq: ['FAQ', 'info'],
  logistics: ['Referred to Ben', 'info'], not_covered: ['Not covered', 'warn'], course_info: ['Course info', 'info'], web: ['From the web', 'info'],
  cross_course: ['Other course', 'ok'], // added Oct 8: the course filter had nothing, so the other course's slides answered
};
const STATUS = {
  running: ['In progress', 'info'], done: ['Done', 'ok'], cancelled: ['Cancelled', 'off'], stopped: ['Stopped', 'warn'],
  excluded: ['Excluded', 'off'],
};
const PROVIDERS = { anthropic: 'Claude', openai: 'OpenAI', openrouter: 'OpenRouter' };

const E = {
  legend: null,
  started: false, limits: null, questions: null, runs: [], active: null, card: null, cal: null,
  gens: [], judges: [], estimateOk: false, driving: false, paused: true, detail: null, editing: null,
};

/* ---------------- api ---------------- */

/** A 401: the admin session ran out (this section asks for a reload; admin.js shows the sign-in). */
class EvalAuthError extends Error {}
/** fetch wrapper: {status, ok, data}. Throws EvalAuthError on a 401 and lets network errors through. */
async function api(path, { method = 'GET', body, timeout = 30000 } = {}) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeout);
  let res;
  try {
    res = await fetch(path, {
      method, credentials: 'same-origin', signal: ctrl.signal,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } finally { clearTimeout(t); }
  let data = null;
  try { data = await res.json(); } catch { /* no body */ }
  if (res.status === 401) throw new EvalAuthError('Your admin session ran out. Reload the page and sign in again.');
  return { status: res.status, ok: res.ok, data };
}
/** The server's error message, else `fallback`. */
const detail = (r, fallback) => (typeof r?.data?.detail === 'string' ? r.data.detail : (fallback || `Request failed (HTTP ${r?.status}).`));
/** What to say about a request that threw: signed out, timed out, or no connection. */
const errText = (e) => (e instanceof EvalAuthError ? e.message : e?.name === 'AbortError' ? 'The request timed out.' : 'Can\'t reach the server.');

/* ---------------- start ---------------- */

/** Set up the section once, on sign-in (or right away when the page is already signed in). */
function start() {
  if (E.started) return;
  E.started = true;
  for (const pfx of ['gen', 'judge', 'cal']) {
    const sel = $(`#ev-${pfx}-provider`);
    sel.addEventListener('change', () => { $(`#ev-${pfx}-model`).setAttribute('list', `ev-models-${sel.value}`); });
  }
  $('#ev-metric').replaceChildren(...Object.entries(METRICS).map(([k, m]) => el('option', { value: k, text: m.label })));
  $('#ev-metric').addEventListener('change', renderCard);
  let resizeTimer = null;
  window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (E.card) renderCard(); }, 150); });
  $('#ev-q-cat').replaceChildren(el('option', { value: '', text: 'Pick a category' }));
  loadAll();
}
document.addEventListener('ft-admin-enter', start);
// Settings > Prompts: "Run an eval with this prompt" lands here with the prompt that just changed.
document.addEventListener('ft:run-eval', (e) => {
  const name = e.detail?.prompt;
  if (name) { $('#ev-name').value = `After editing the ${name} prompt`; invalidateEstimate(); }
  setTimeout(() => $('#ev-name').focus({ preventScroll: true }), 400);
});
if ($('#a-app') && !$('#a-app').hidden) start();

/** Everything the section shows: limits, the question set, runs and the report card. */
async function loadAll() {
  loadModelLists();
  await Promise.all([loadLimits(), loadQuestions(), loadRuns(), loadCard()]);
}

/** Today's eval limits; the live model becomes the first model that answers. */
async function loadLimits() {
  try {
    const r = await api('/api/admin/evals/limits');
    if (r.ok) E.limits = r.data;
    if (E.limits?.live_model && !E.gens.length) {
      E.gens = [{ provider: E.limits.live_model.provider, model: E.limits.live_model.model }];
      renderChips();
    }
  } catch { /* shown elsewhere */ }
}

/** Each provider's model ids as <datalist> suggestions (typing any id still works). */
async function loadModelLists() {
  for (const p of Object.keys(PROVIDERS)) {
    try {
      const r = await api(`/api/admin/models?provider=${p}`);
      const models = (r.ok && Array.isArray(r.data?.models)) ? r.data.models : [];
      $(`#ev-models-${p}`).replaceChildren(...models.slice(0, 500).map(m => el('option', { value: m.id, text: m.name && m.name !== m.id ? m.name : null })));
    } catch { /* free text still works */ }
  }
}

/* ---------------- question set ---------------- */

/** The private question set's summary (served only behind the admin cookie). */
async function loadQuestions() {
  const st = $('#ev-q-status');
  try {
    const r = await api('/api/admin/evals/questions');
    if (!r.ok) { say(st, detail(r, 'Couldn\'t load the question set.'), 'err'); E.questions = null; renderQuestions(); return; }
    E.questions = r.data;
    say(st, r.data.uploaded ? '' : r.data.note || 'No question set uploaded yet.', r.data.uploaded ? '' : 'err');
  } catch (e) { say(st, errText(e), 'err'); }
  renderQuestions();
}

/** The question counts, the question table, the category menu and the run's category picker. */
function renderQuestions() {
  const q = E.questions || { count: 0, categories: [], questions: [], all_categories: [] };
  $('#ev-q-kv').replaceChildren(
    el('div', {}, el('dt', { text: 'Questions' }), el('dd', { text: String(q.count || 0) })),
    el('div', {}, el('dt', { text: 'Answerable from course material' }), el('dd', { text: String(q.answerable ?? 0) })),
    el('div', {}, el('dt', { text: 'Categories' }), el('dd', { text: String((q.categories || []).length) })),
  );
  $('#ev-q-cats').replaceChildren(...(q.categories || []).map(c => el('li', {}, el('span', { class: 'pill', text: `${humanCat(c.category)} ${c.count}` }))));
  $('#ev-q-body').replaceChildren(...(q.questions || []).map(x => el('tr', {},
    el('td', { class: 'small', 'data-label': 'Id', text: x.qid }),
    el('td', { class: 'small', 'data-label': 'Category', text: humanCat(x.category) }),
    el('td', { class: 'small', 'data-label': 'Expected', text: x.answerable ? 'Answer' : 'Decline' }),
    el('td', { class: 'full', text: x.question }),
    el('td', { class: 'full small muted', text: x.reference_answer || '' }),
    el('td', {}, el('button', { type: 'button', class: 'btn btn-small btn-ghost', 'aria-label': `Edit ${x.qid}`, onclick: () => editQuestion(x) }, 'Edit')),
  )));
  legend('#ev-q-legend', ['Expected: answer means the course material covers it; decline means the twin should send the student to Ben (logistics, grades, meetings).',
    'How Ben or the TA replied: a paraphrase of the real reply, used for the "Matches the real reply" score. Empty means there was none.'], { scores: false });
  const cats = q.all_categories?.length ? q.all_categories : (q.categories || []).map(c => c.category);
  const keep = $('#ev-q-cat').value;
  $('#ev-q-cat').replaceChildren(el('option', { value: '', text: 'Pick a category' }), ...cats.map(c => el('option', { value: c, text: humanCat(c) })));
  $('#ev-q-cat').value = keep;
  const counts = Object.fromEntries((q.categories || []).map(c => [c.category, c.count]));
  const checked = new Set([...document.querySelectorAll('#ev-cat-pick input:checked')].map(i => i.value));
  const first = !$('#ev-cat-pick').children.length;
  $('#ev-cat-pick').replaceChildren(...(q.categories || []).map(c => el('label', {},
    el('input', { type: 'checkbox', value: c.category, checked: first || checked.has(c.category), onchange: invalidateEstimate }),
    `${humanCat(c.category)} (${counts[c.category]})`)));
  const top = $('#ev-top');
  if (q.count && Number(top.value) > q.count) top.value = Math.min(q.count, 30);
}

/** Load a question into the form to edit it. */
function editQuestion(x) {
  E.editing = x.qid;
  $('#ev-q-text').value = x.question;
  $('#ev-q-cat').value = x.category;
  $('#ev-q-ans').checked = !!x.answerable;
  $('#ev-q-ref').value = x.reference_answer || '';
  $('#ev-q-form-legend').textContent = `Edit ${x.qid}`;
  $('#ev-q-save').textContent = 'Save changes';
  $('#ev-q-cancel').hidden = false;
  $('#ev-q-text').focus();
}
/** Back to "Add a question". */
function resetQuestionForm() {
  E.editing = null;
  $('#ev-q-form').reset();
  $('#ev-q-form-legend').textContent = 'Add a question';
  $('#ev-q-save').textContent = 'Add question';
  $('#ev-q-cancel').hidden = true;
}
$('#ev-q-cancel').addEventListener('click', resetQuestionForm);
$('#ev-q-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const st = $('#ev-q-form-status');
  const body = { question: $('#ev-q-text').value.trim(), category: $('#ev-q-cat').value, answerable: $('#ev-q-ans').checked, reference_answer: $('#ev-q-ref').value.trim() || null };
  if (!body.question || !body.category) { say(st, 'Type the question and pick a category.', 'err'); return; }
  say(st, 'Checking for personal details...');
  try {
    const r = E.editing
      ? await api(`/api/admin/evals/questions/${encodeURIComponent(E.editing)}`, { method: 'PUT', body })
      : await api('/api/admin/evals/questions', { method: 'POST', body });
    if (!r.ok) { say(st, detail(r), 'err'); return; }
    say(st, E.editing ? `Saved ${E.editing}.` : 'Added. It passed the privacy checks.', 'ok');
    resetQuestionForm();
    E.questions = r.data;
    renderQuestions();
  } catch (ex) { say(st, errText(ex), 'err'); }
});

/* ---------------- model chips ---------------- */

/** The chosen answering models and judges, each with a remove button. */
function renderChips() {
  const draw = (list, key) => $(`#ev-${key}-list`).replaceChildren(...list.map((m, i) => el('li', {},
    el('span', { class: 'pill info' }, `${PROVIDERS[m.provider] || m.provider}: ${m.model}`,
      el('button', { type: 'button', 'aria-label': `Remove ${m.model}`, onclick: () => { list.splice(i, 1); renderChips(); invalidateEstimate(); } }, '×')))));
  draw(E.gens, 'gen');
  draw(E.judges, 'judge');
}
/** Add an answering model (`key` 'gen') or a judge ('judge') after checking the caps and duplicates. */
function addModel(key, list) {
  const provider = $(`#ev-${key}-provider`).value;
  const model = $(`#ev-${key}-model`).value.trim();
  const st = $('#ev-run-status');
  if (!model) { say(st, 'Type or pick a model id first.', 'err'); return; }
  if (/^jev/i.test(model)) { say(st, 'Jev runs from the command line only (it needs deepeval, which is not in the Vercel bundle).', 'err'); return; }
  const cap = key === 'gen' ? (E.limits?.max_generators || 6) : (E.limits?.max_judges || 6);
  if (list.length >= cap) { say(st, key === 'gen' ? `Up to ${cap} models that answer.` : `Up to ${cap} judges.`, 'err'); return; }
  if (list.some(m => m.provider === provider && m.model === model)) { say(st, 'That one is already in the list.', 'err'); return; }
  list.push({ provider, model });
  $(`#ev-${key}-model`).value = '';
  say(st, '');
  renderChips();
  invalidateEstimate();
}
$('#ev-gen-add').addEventListener('click', () => addModel('gen', E.gens));
$('#ev-judge-add').addEventListener('click', () => addModel('judge', E.judges));
for (const key of ['gen', 'judge']) {
  $(`#ev-${key}-model`).addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addModel(key, key === 'gen' ? E.gens : E.judges); } });
}

/* ---------------- estimate and start ---------------- */

/** The run request from the form; `categories` is null when every category is picked. */
function runBody(confirm = false) {
  const cats = [...document.querySelectorAll('#ev-cat-pick input:checked')].map(i => i.value);
  const all = document.querySelectorAll('#ev-cat-pick input').length;
  return {
    name: $('#ev-name').value.trim() || null,
    generators: E.gens, judges: E.judges,
    top: Number($('#ev-top').value) || 0,
    categories: cats.length && cats.length < all ? cats : null,
    confirm,
  };
}
/** The form changed: the estimate is stale, so Start hides until it is checked again. */
function invalidateEstimate() {
  E.estimateOk = false;
  $('#ev-start-row').hidden = true;
  $('#ev-estimate-box').hidden = true;
}
['#ev-name', '#ev-top'].forEach(s => $(s).addEventListener('input', invalidateEstimate));

$('#ev-run-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const st = $('#ev-run-status');
  const body = runBody();
  if (!E.gens.length || !E.judges.length) { say(st, 'Add at least one model that answers and one judge.', 'err'); return; }
  if (!(body.top >= 1 && body.top <= 30)) { say(st, 'Pick 1 to 30 questions.', 'err'); return; }
  if (!document.querySelectorAll('#ev-cat-pick input:checked').length) { say(st, 'Pick at least one category.', 'err'); return; }
  say(st, 'Working out the estimate...');
  try {
    const r = await api('/api/admin/evals/runs/estimate', { method: 'POST', body });
    if (!r.ok) { say(st, detail(r), 'err'); invalidateEstimate(); return; }
    renderEstimate(r.data);
    say(st, '');
  } catch (ex) { say(st, errText(ex), 'err'); }
});

/** What the run will cost in calls, embeddings and dollars, and whether it fits the caps. */
function renderEstimate(d) {
  const est = d.estimate;
  const cost = est.cost_usd != null ? `about $${est.cost_usd.toFixed(2)}`
    : `at least $${(est.cost_usd_known_part || 0).toFixed(2)}, plus ${est.cost_unknown_for.join(', ')} (no published price found)`;
  const left = Math.max(0, (est.daily_eval_cap || 0) - (est.eval_calls_today || 0));
  const box = $('#ev-estimate-box');
  const items = [
    `${d.questions} questions × ${E.gens.length} answering model${E.gens.length > 1 ? 's' : ''} = ${est.pairs} answers, each judged by ${E.judges.length} judge${E.judges.length > 1 ? 's' : ''}.`,
    `Model calls: about ${est.calls_typical}, at most ${est.calls_max} (this run's cap is ${est.run_call_cap}; ${left} of today's ${est.daily_eval_cap} eval calls are left).`,
    `Voyage embeddings: up to ${est.embeddings}.`,
    `Rough cost: ${cost}. ${est.cost_note}`,
  ];
  box.replaceChildren(el('strong', { text: 'Before you start' }), el('ul', {}, ...items.map(t => el('li', { text: t }))));
  if (!est.within_cap) box.append(el('p', { class: 'ev-warn', text: `Too big: up to ${est.calls_max} calls is over the per-run cap of ${est.run_call_cap}. Use fewer questions, models or judges.` }));
  for (const s of d.self_grading || []) {
    box.append(el('p', { class: 'ev-warn', text: `Self-grading: ${s.judge} would grade answers from ${s.generator}, the same model. ${d.self_grading_note || ''}` }));
  }
  box.hidden = false;
  E.estimateOk = est.within_cap;
  $('#ev-start-row').hidden = !est.within_cap || !!E.active;
  if (E.active) box.append(el('p', { class: 'ev-warn', text: 'Another run is still in progress. Finish or cancel it before starting a new one.' }));
}

$('#ev-start').addEventListener('click', async () => {
  const st = $('#ev-run-status');
  if (!E.estimateOk) { say(st, 'Check the estimate first.', 'err'); return; }
  $('#ev-start').disabled = true;
  say(st, 'Starting...');
  try {
    const r = await api('/api/admin/evals/runs', { method: 'POST', body: runBody(true), timeout: 45000 });
    if (!r.ok) { say(st, detail(r), 'err'); return; }
    say(st, `Started ${r.data.run.name}.`, 'ok');
    invalidateEstimate();
    E.active = { ...r.data.run, progress: r.data.progress };
    renderActive();
    loadRuns();
    drive();
  } catch (ex) { say(st, errText(ex), 'err'); } finally { $('#ev-start').disabled = false; }
});

/* ---------------- driving a run ---------------- */

/** The progress bar of the run in progress, with Pause or Resume. */
function renderActive() {
  const box = $('#ev-active');
  if (!E.active) { box.hidden = true; return; }
  box.hidden = false;
  const p = E.active.progress || { done: E.active.pairs_done || 0, total: E.active.pairs_total || 0 };
  const frac = p.total ? p.done / p.total : 0;
  $('#ev-active-title').textContent = `${E.active.name}: ${p.done} of ${p.total} answers judged`;
  $('#ev-active-bar').setAttribute('aria-valuenow', String(Math.round(frac * 100)));
  $('#ev-active-bar > span').style.width = `${(frac * 100).toFixed(1)}%`;
  $('#ev-resume').hidden = E.driving;
  $('#ev-pause').hidden = !E.driving;
}

/* Drives one run. Cancel (or a finished run) can clear E.active while a step is in flight, so the loop
   holds its own `run` and stops as soon as E.active is no longer that run; E.driving is always reset. */
async function drive() {
  if (!E.active || E.driving) return;
  const run = E.active;
  E.driving = true;
  E.paused = false;
  renderActive();
  try {
    await driveRun(run);
  } finally {
    E.driving = false;
    if (E.active === run) E.paused = true;
    renderActive();
  }
}

/** Ask the server for one answer at a time until the run finishes, is paused or cancelled; network errors retry with a growing wait. */
async function driveRun(run) {
  const st = $('#ev-active-status');
  let failures = 0;
  say(st, 'Running. Keep this page open; you can pause at any time and resume later.');
  while (!E.paused && E.active === run) {
    let r;
    try {
      r = await api(`/api/admin/evals/runs/${encodeURIComponent(run.id)}/step`, { method: 'POST', timeout: 75000 });
      if (E.active !== run) return; // cancelled (or replaced) while the step was running
    } catch (ex) {
      if (ex instanceof EvalAuthError) { say(st, ex.message, 'err'); break; }
      failures += 1;
      if (failures >= 4) { say(st, `${errText(ex)} Paused. Resume when the connection is back.`, 'err'); break; }
      say(st, `${errText(ex)} Trying again...`, 'err');
      await sleep(4000 * failures);
      continue;
    }
    if (!r.ok) {
      if (r.status >= 500 && r.status !== 503 && failures < 3) { failures += 1; say(st, `${detail(r)} Trying again...`, 'err'); await sleep(5000); continue; }
      say(st, `${detail(r)} Paused.`, 'err');
      break;
    }
    failures = 0;
    run.progress = r.data.progress;
    renderActive();
    if (r.data.waiting) {
      say(st, `${r.data.waiting.reason} Waiting ${r.data.waiting.seconds} s.`);
      await sleep(r.data.waiting.seconds * 1000);
      continue;
    }
    const row = r.data.row;
    if (row) say(st, `Answered ${row.qid} with ${row.generator} (${outcomeText(row)}) in ${row.seconds} s.`);
    if (r.data.progress.finished) {
      say(st, `Finished: ${r.data.progress.status}.`, 'ok');
      E.active = null;
      E.driving = false;
      renderActive();
      await Promise.all([loadRuns(), loadCard()]);
      openRun(run.id);
      return;
    }
  }
}
$('#ev-resume').addEventListener('click', drive);
$('#ev-pause').addEventListener('click', () => { E.paused = true; say($('#ev-active-status'), 'Pausing after this answer...'); });
$('#ev-cancel').addEventListener('click', async () => {
  if (!E.active || !window.confirm('Cancel this run? Answers judged so far are kept.')) return;
  E.paused = true;
  try {
    const r = await api(`/api/admin/evals/runs/${encodeURIComponent(E.active.id)}/cancel`, { method: 'POST' });
    if (!r.ok) { say($('#ev-active-status'), detail(r), 'err'); return; }
    E.active = null;
    renderActive();
    loadRuns();
    loadCard();
  } catch (ex) { say($('#ev-active-status'), errText(ex), 'err'); }
});
window.addEventListener('beforeunload', (e) => { if (E.driving) { e.preventDefault(); e.returnValue = ''; } });

/* ---------------- runs list ---------------- */

/** Every run, newest first; picks up a run still in progress (paused until Resume). */
async function loadRuns() {
  const st = $('#ev-runs-status');
  try {
    const r = await api('/api/admin/evals/runs');
    if (!r.ok) { say(st, detail(r, 'Couldn\'t load runs.'), 'err'); return; }
    E.runs = r.data.runs || [];
    const active = E.runs.find(x => x.status === 'running');
    if (active && (!E.active || E.active.id !== active.id)) {
      E.active = { ...active, progress: { done: active.pairs_done, total: active.pairs_total } };
      say($('#ev-active-status'), E.driving ? '' : 'This run is paused. Resume picks up at the next unanswered question.');
    } else if (!active && !E.driving) {
      E.active = null;
    }
    renderActive();
    say(st, E.runs.length ? '' : 'No runs yet.');
    renderRuns();
    renderCompareOptions();
  } catch (ex) { say(st, errText(ex), 'err'); }
}
$('#ev-refresh').addEventListener('click', () => { loadRuns(); loadCard(); });

/** An answering model's display name in a run. */
function labelFor(run, key) { return (run.generator_labels || {})[key] || key; }

/** One card per run: status, judges, and each answering model's measures. */
function renderRuns() {
  legend('#ev-runs-legend', ['Pass rate and the six scores pool every judge of that run; the judges are named on each card. n is how many judgements (or questions) a number covers.',
    'Right call: did the twin answer what the course covers and decline the rest (no judge involved).']);
  $('#ev-runs').replaceChildren(...E.runs.map(run => {
    const [stText, stCls] = run.kind === 'baseline' ? ['Baseline', 'info'] : (STATUS[run.status] || [run.status, '']);
    const card = el('article', { class: `ev-card${run.excluded ? ' is-excluded' : ''}`, 'aria-label': run.name },
      el('h4', { text: run.name || run.id }),
      el('p', { class: 'meta', text: `${fmtDate(run.created_at)} · ${run.questions ?? '?'} questions · judges: ${(run.judges || []).join(', ') || 'none'}` }),
      el('p', {}, el('span', { class: `pill ${stCls}`, text: stText }), run.kind === 'imported' ? ' ' : null,
        run.kind === 'imported' ? el('span', { class: 'pill', text: 'Command line, imported' }) : null,
        run.status === 'running' ? ` ${run.pairs_done} of ${run.pairs_total}` : null),
    );
    for (const [key, m] of Object.entries(run.by_generator || {})) {
      card.append(el('p', { class: 'gen', text: labelFor(run, key) }));
      card.append(el('dl', {}, ...Object.keys(METRICS).flatMap(k => [
        el('dt', { text: METRICS[k].dim ? dimHeader(k) : METRICS[k].label }), el('dd', { text: metricCell(m, k) })])));
    }
    for (const n of run.notes || []) card.append(el('p', { class: 'ev-note', text: n }));
    if (run.excluded && run.status_note && !(run.notes || []).includes(run.status_note)) card.append(el('p', { class: 'ev-note', text: run.status_note }));
    for (const s of run.self_grading || []) card.append(el('p', { class: 'ev-warn', text: `Self-grading: ${s.judge} graded its own model's answers. Read its verdicts with care.` }));
    if (run.kind !== 'baseline') {
      card.append(el('div', { class: 'actions' }, el('button', { type: 'button', class: 'btn btn-small', onclick: () => openRun(run.id) }, 'Open results')));
    }
    return card;
  }));
}

/* ---------------- run detail ---------------- */

/** One run's results: its details and a row per answer, with filters. */
async function openRun(id) {
  const box = $('#ev-detail');
  box.hidden = false;
  $('#ev-detail-h').textContent = 'Loading results...';
  $('#ev-detail-body').replaceChildren();
  try {
    const r = await api(`/api/admin/evals/runs/${encodeURIComponent(id)}`);
    if (!r.ok) { $('#ev-detail-h').textContent = detail(r, 'Couldn\'t load that run.'); return; }
    E.detail = r.data;
    if (r.data.legend) setLegend(r.data.legend);
    const run = r.data.run;
    $('#ev-detail-h').textContent = `Results: ${run.name}`;
    $('#ev-detail-meta').textContent = `${fmtDate(run.created_at)} · status ${run.status}${run.status_note ? ` (${run.status_note})` : ''} · answering: ${(run.generators || []).map(g => `${g.provider}:${g.model}`).join(', ')} · judges: ${(run.judges || []).map(j => `${j.provider}:${j.model}`).join(', ')}${run.calls_used != null ? ` · ${run.calls_used} model calls` : ''}`;
    const pv = Object.entries(run.prompt_versions || {});
    if (pv.length) $('#ev-detail-meta').textContent += ` · prompts: ${pv.map(([n, v]) => `${n} ${v.hash}${v.edited ? ' (edited)' : ''}`).join(', ')}`;
    $('#ev-detail-notes').replaceChildren(...(run.notes || []).map(n => el('p', { class: 'ev-note', text: n })),
      ...(run.self_grading || []).map(s => el('p', { class: 'ev-warn', text: `Self-grading: ${s.judge} graded answers from ${s.generator}. ${r.data.self_grading_note || ''}` })));
    const rows = r.data.rows || [];
    const fill = (sel, values, label) => {
      const keep = $(sel).value;
      $(sel).replaceChildren(el('option', { value: '', text: label }), ...values.map(v => el('option', { value: v, text: sel === '#ev-f-cat' ? humanCat(v) : v })));
      $(sel).value = values.includes(keep) ? keep : '';
    };
    fill('#ev-f-cat', [...new Set(rows.map(x => x.category))].sort(), 'All categories');
    fill('#ev-f-gen', [...new Set(rows.map(x => x.generator))], 'All models');
    renderDetail();
    box.focus();
  } catch (ex) { $('#ev-detail-h').textContent = errText(ex); }
}
['#ev-f-cat', '#ev-f-gen', '#ev-f-verdict'].forEach(s => $(s).addEventListener('change', renderDetail));

/** The judges' verdict on one answer: pass, fail, split, error, or '' when not judged. */
function verdictKind(row) {
  const js = row.judgements || [];
  if (js.some(j => j.error)) return 'error';
  const v = new Set(js.map(j => j.verdict));
  if (v.size > 1) return 'split';
  return v.has('fail') ? 'fail' : v.has('pass') ? 'pass' : '';
}
/** What answered the question, in words. */
function outcomeText(row) {
  const o = row.outcome || row.response?.outcome;
  if (OUTCOMES[o]) return OUTCOMES[o][0];
  const s = row.response?.status;
  return s === 'ok' ? 'Answered' : s === 'not_covered' ? 'Declined' : s === 'error' ? 'Error' : s || 'n/a';
}
/** The outcome as a pill, with what the question set expected. */
function outcomeBadge(row) {
  const o = row.outcome || row.response?.outcome;
  const [text, cls] = OUTCOMES[o] || [outcomeText(row), row.response?.status === 'error' ? 'err' : ''];
  const expected = row.answerable ? 'should answer' : 'should decline';
  return el('span', {}, el('span', { class: `pill ${cls}`, text }), el('span', { class: 'small muted', text: ` (${expected})` }));
}

/** The run's answers that match the filters: the question, the twin's reply and each judge's scores and reasons. */
function renderDetail() {
  if (!E.detail) return;
  const cat = $('#ev-f-cat').value, gen = $('#ev-f-gen').value, verdict = $('#ev-f-verdict').value;
  const rows = (E.detail.rows || []).filter(x => (!cat || x.category === cat) && (!gen || x.generator === gen)
    && (!verdict || verdictKind(x) === verdict || (verdict === 'fail' && (x.judgements || []).some(j => j.verdict === 'fail'))));
  $('#ev-detail-count').textContent = `${rows.length} of ${(E.detail.rows || []).length} answers shown.`;
  legend('#ev-detail-legend', ['Short score names: Grounded, Answers (the question), Scope (right scope), Real reply (matches it), Speech (quality), Safety (and tone). Each is one judge\'s score for one answer, 1 to 5.',
    'Top score: how close the best slide was to the question (similarity, 0 to 1). Blank when no search ran (FAQ, stored answer).',
    'Outcome: what answered the question. "Should answer" or "should decline" is what the question set expects.']);
  $('#ev-detail-body').replaceChildren(...rows.map(x => {
    const resp = x.response || {};
    const more = el('details', {}, el('summary', { text: 'What the twin said' }),
      x.reference_answer ? el('p', { class: 'small' }, el('strong', { text: 'Real reply: ' }), x.reference_answer) : null,
      resp.message ? el('p', { class: 'small narr', text: resp.message }) : null,
      ...(resp.segments || []).map(s => el('p', { class: 'small narr', text: `${s.slide_id}: ${s.narration}` })),
      resp.status === 'not_covered' && !resp.message ? el('p', { class: 'small muted', text: 'Declined: not covered by the course material.' }) : null,
      resp.narration_source === 'fallback' ? el('p', { class: 'small muted', text: 'Narration fell back to the speaker notes.' }) : null,
      (x.narration_errors || []).length ? el('p', { class: 'small muted', text: `Narration errors: ${x.narration_errors.join('; ')}` }) : null);
    const judges = el('div', { class: 'ev-jv' }, ...(x.judgements || []).map(j => {
      if (j.error) return el('div', {}, el('span', { class: 'pill err', text: 'Error' }), el('span', { class: 'small', text: ` ${j.judge}: ${j.error}` }));
      const scores = Object.keys(DIMS).map(d => `${DIM_SHORT[d]} ${j.scores?.[d] ?? 'n/a'}`).join(' · ');
      return el('div', {},
        el('span', { class: `pill ${j.verdict === 'pass' ? 'ok' : 'warn'}`, text: j.verdict === 'pass' ? 'Pass' : 'Fail' }),
        el('span', { class: 'small', text: ` ${j.judge}` }),
        el('div', { class: 'scores', title: Object.entries(DIMS).map(([d, l]) => `${l}: ${j.scores?.[d] ?? 'n/a'}`).join('\n'), text: scores }),
        el('details', {}, el('summary', { text: 'Why' }), el('p', { class: 'small', text: j.rationale || '' }),
          (j.issues || []).length ? el('ul', { class: 'small' }, ...j.issues.map(i => el('li', { text: i }))) : null));
    }));
    return el('tr', {},
      el('td', { class: 'full q' }, el('span', { class: 'small muted', text: `${x.qid} ` }), x.question, more),
      el('td', { class: 'small', 'data-label': 'Category', text: humanCat(x.category) }),
      el('td', { class: 'small', 'data-label': 'Model', text: x.generator }),
      el('td', { class: 'num', 'data-label': 'Top score', text: typeof resp.top_score === 'number' ? resp.top_score.toFixed(3) : '' }),
      el('td', { 'data-label': 'Outcome' }, outcomeBadge(x)),
      el('td', { class: 'full' }, judges));
  }));
}

/* ---------------- compare two runs ---------------- */

/** Runs that can be compared: not excluded, with results. */
function comparable() { return E.runs.filter(r => !r.excluded && Object.keys(r.by_generator || {}).length); }
/** The two run menus (the newest two by default), then the comparison. */
function renderCompareOptions() {
  const runs = comparable();
  for (const [sel, idx] of [['#ev-cmp-a', 1], ['#ev-cmp-b', 0]]) {
    const keep = $(sel).value;
    $(sel).replaceChildren(...runs.map(r => el('option', { value: r.id, text: `${r.name} (${fmtDay(r.created_at)})${r.excluded ? ', excluded' : ''}` })));
    $(sel).value = runs.some(r => r.id === keep) ? keep : (runs[Math.min(idx, runs.length - 1)]?.id || '');
  }
  renderCompare();
}
['#ev-cmp-a', '#ev-cmp-b'].forEach(s => $(s).addEventListener('change', renderCompare));
/** Run A against run B, measure by measure, one column per answering model. */
function renderCompare() {
  const a = E.runs.find(r => r.id === $('#ev-cmp-a').value), b = E.runs.find(r => r.id === $('#ev-cmp-b').value);
  const table = $('#ev-cmp-table');
  if (!a || !b) { table.replaceChildren(el('tbody', {}, el('tr', {}, el('td', { class: 'muted', text: 'Two runs with results are needed to compare.' })))); return; }
  const cols = [];
  for (const run of [a, b]) for (const [key, m] of Object.entries(run.by_generator || {})) cols.push({ run, key, m });
  const rows = [
    ['Questions (n)', c => String(c.m.questions ?? '')],
    ['Judges', c => (c.run.judges || []).join(', ')],
    ['Judgements (n)', c => String(c.m.judgements ?? 'n/a')],
    ...Object.keys(METRICS).map(k => [METRICS[k].label, c => metricCell(c.m, k)]),
  ];
  legend('#ev-cmp-legend', ['Compare runs judged by the same judges: a different judge moves the numbers as much as a different model does.']);
  table.replaceChildren(
    el('thead', {}, el('tr', {}, el('th', { scope: 'col', text: 'Measure' }), ...cols.map(c => el('th', { scope: 'col', text: `${c.run === a ? 'A' : 'B'}: ${labelFor(c.run, c.key)}` })))),
    el('tbody', {}, ...rows.map(([label, f]) => el('tr', {}, el('th', { scope: 'row', text: label }), ...cols.map(c => el('td', { class: 'num', text: f(c) }))))),
  );
}

/* ---------------- report card ---------------- */

/** The report card data, then the chart and the calibration table. */
async function loadCard() {
  try {
    const r = await api('/api/admin/evals/report-card');
    if (r.ok) { E.card = r.data; setLegend(r.data.legend); }
  } catch { /* chart shows empty */ }
  renderCard();
  renderCalibration();
}

/** A point's value for the measure (scores live under `scores`), or null. */
function valueOf(p, metric) { return METRICS[metric]?.dim ? p.scores?.[metric] ?? null : p[metric] ?? null; }

/** The report card chart: one line per answering model, run by run, for the measure picked above it. */
function renderCard() {
  const metric = $('#ev-metric').value || 'pass_rate';
  const meta = METRICS[metric];
  const series = (E.card?.series || []).map((s, i) => ({ ...s, color: `var(--series-${(i % 8) + 1})` }));
  const plot = $('#ev-plot');
  const tip = $('#ev-tip');
  tip.hidden = true;
  renderCardCaption(series, meta, metric);
  const runs = runsOnAxis(series);
  const f = chartFrame(plot, runs.length, cardScale(series, meta, metric));
  const root = svg('svg', { viewBox: `0 0 ${f.W} ${f.H}`, role: 'img', 'aria-label': `${meta.label} over time. Values are in the table below.` });
  drawCardAxes(root, f, meta, runs);
  const ends = [];
  for (const s of series) {
    const end = drawCardSeries(root, f, s, runs, meta, metric, { plot, tip });
    if (end) ends.push(end);
  }
  drawEndLabels(root, f, ends, meta);
  plot.replaceChildren(root);
  renderCardTable(series, runs);
}

/** The caption under the chart (who judged, what is n/a) and the legend when there are two or more lines. */
function renderCardCaption(series, meta, metric) {
  const judgeNames = [...new Set(series.flatMap(x => x.points.flatMap(p => p.judges || [])))];
  const missing = series.filter(x => x.points.some(p => valueOf(p, metric) == null)).map(x => x.label.split(' (')[0]);
  $('#ev-chart-cap').textContent = `${meta.label}, per answering model, run by run. Judged by ${judgeNames.join(', ') || 'no judge yet'} (each point's judges are in its tooltip and the table). Excluded runs are left out. The axis starts at zero.`
    + (missing.length ? ` No point where this measure is n/a (did not apply): ${missing.join(', ')}.` : '');
  $('#ev-legend').replaceChildren(...(series.length > 1 ? series : []).map(s => el('span', {},
    el('i', { class: 'ev-key', style: `background:${s.color}` }), s.label)));
}

/** The x positions: every run that has a point, oldest first. */
function runsOnAxis(series) {
  const runs = [];
  for (const s of series) for (const p of s.points) if (!runs.some(r => r.run_id === p.run_id)) runs.push(p);
  runs.sort((a, b) => String(a.at).localeCompare(String(b.at)) || String(a.run_id).localeCompare(String(b.run_id)));
  return runs;
}

/** The value axis, always from zero: rates to 100%, scores to 5, units (cost, seconds) to just above the largest value. */
function cardScale(series, meta, metric) {
  if (meta.rate) return { yMax: 1, ticks: [0, 0.25, 0.5, 0.75, 1] };
  if (!meta.unit) return { yMax: 5, ticks: [0, 1, 2, 3, 4, 5] };
  const vals = series.flatMap(x => x.points.map(p => valueOf(p, metric))).filter(v => v != null);
  const top = Math.max(...vals, 0) * (meta.unit === 's' ? 1 / 1000 : 1) || 1;
  const step = 10 ** Math.floor(Math.log10(top));
  const unitMax = Math.ceil(top / step) * step * (meta.unit === 's' ? 1000 : 1);
  return { yMax: unitMax, ticks: [0, 0.25, 0.5, 0.75, 1].map(t => t * unitMax) };
}

/** The chart's size, margins and value-to-pixel maps. Drawn at the container's own width so text stays 11 px on a phone. */
function chartFrame(plot, runCount, scale) {
  const W = Math.max(320, Math.round(plot.clientWidth || 760)), H = W < 560 ? 280 : 320;
  const L = 60, R = W < 560 ? 96 : 190, T = 14, B = 62;
  const xOf = (i) => (runCount < 2 ? L + (W - L - R) / 2 : L + (i * (W - L - R)) / (runCount - 1));
  const yOf = (v) => T + (H - T - B) * (1 - v / scale.yMax);
  return { W, H, L, R, T, B, xOf, yOf, ...scale };
}

/** Value text in the measure's own form: 75%, $0.0123, 2.4 s, or 4.20. */
function cardValueText(meta, v) {
  return meta.rate ? pct(v) : meta.unit ? unitText(meta.unit, v) : num(v);
}

/** Grid lines and their labels, both axis titles, a date (and run number when needed) under each run. */
function drawCardAxes(root, f, meta, runs) {
  const { W, H, L, R, T, B, xOf, yOf } = f;
  for (const t of f.ticks) {
    root.append(svg('line', { class: t === 0 ? 'axis' : 'grid', x1: L, x2: W - R + 12, y1: yOf(t), y2: yOf(t) }));
    root.append(svg('text', { x: L - 8, y: yOf(t) + 4, 'text-anchor': 'end' }, meta.rate ? `${Math.round(t * 100)}%` : meta.unit ? unitText(meta.unit, t) : String(t)));
  }
  const yTitle = meta.rate ? `${meta.short} (%)` : meta.unit ? `${meta.short} (${meta.unit === 'usd' ? 'USD' : 'seconds'})` : `${meta.short} (mean, 1–5; 5 best)`;
  root.append(svg('text', { class: 'axis-title', x: 14, y: T + (H - T - B) / 2, 'text-anchor': 'middle', transform: `rotate(-90 14 ${T + (H - T - B) / 2})` }, yTitle));
  root.append(svg('text', { class: 'axis-title', x: L + (W - L - R) / 2, y: H - 6, 'text-anchor': 'middle' }, 'Run date (oldest on the left)'));
  runs.forEach((r, i) => {
    const sameDay = runs.filter(x => fmtDay(x.at) === fmtDay(r.at)).length > 1;
    root.append(svg('text', { x: xOf(i), y: H - B + 18, 'text-anchor': 'middle' }, fmtDay(r.at)));
    if (sameDay || r.kind === 'baseline') {
      root.append(svg('text', { x: xOf(i), y: H - B + 32, 'text-anchor': 'middle' }, r.kind === 'baseline' ? 'baseline' : `run ${i + 1}`));
    }
  });
  if (!runs.length) root.append(svg('text', { class: 'empty', x: (W - R + L) / 2, y: H / 2, 'text-anchor': 'middle' }, 'No finished runs yet.'));
}

/**
 * One answering model's line (broken where the measure is n/a, with an "n/a" note there instead of a
 * silent gap) and a focusable point per run with a tooltip. Returns where its last point is, for the end label.
 */
function drawCardSeries(root, f, s, runs, meta, metric, { plot, tip }) {
  const { W, xOf, yOf } = f;
  const pts = s.points.map(p => ({ p, i: runs.findIndex(r => r.run_id === p.run_id), v: valueOf(p, metric) }))
    .filter(x => x.i >= 0).sort((a, b) => a.i - b.i);
  let seg = [];
  const flush = () => {
    if (seg.length > 1) root.append(svg('polyline', { points: seg.map(x => `${xOf(x.i)},${yOf(x.v)}`).join(' '), fill: 'none', stroke: s.color, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
    seg = [];
  };
  for (const x of pts) { if (x.v == null) flush(); else seg.push(x); }
  flush();
  for (const x of pts) {
    if (x.v == null) {
      const na = metric === 'grounded' ? 'n/a (no slides)' : 'n/a';
      const left = xOf(x.i) < W / 2;
      root.append(svg('text', { x: xOf(x.i) + (left ? -4 : 4), y: yOf(0) - 8, 'text-anchor': left ? 'start' : 'end' },
        `${s.label.split(' (')[0].slice(0, W < 560 ? 10 : 22)}: ${na}`));
      continue;
    }
    const value = cardValueText(meta, x.v);
    const label = `${s.label}: ${meta.short} ${value}, ${x.p.run_name || x.p.run_id}, ${fmtDay(x.p.at)}`;
    const g = svg('g', {});
    const hit = svg('circle', { class: 'hit', cx: xOf(x.i), cy: yOf(x.v), r: 12, tabindex: 0, 'aria-label': label });
    const dot = svg('circle', { class: 'dot', cx: xOf(x.i), cy: yOf(x.v), r: 4.5, fill: s.color, stroke: 'var(--surface)', 'stroke-width': 2 });
    const show = () => {
      tip.replaceChildren(el('strong', { text: value }), ...pointTipLines(x.p, s, meta, metric).map(t => el('span', { text: t })));
      tip.hidden = false;
      const box = plot.getBoundingClientRect(), fig = $('#ev-chart').getBoundingClientRect();
      const px = box.left - fig.left + (xOf(x.i) / f.W) * box.width, py = box.top - fig.top + (yOf(x.v) / f.H) * box.height;
      tip.style.left = `${Math.max(0, Math.min(px + 14, fig.width - 270))}px`;
      tip.style.top = `${Math.max(0, py - 10)}px`;
    };
    const hide = () => { tip.hidden = true; };
    hit.addEventListener('pointerenter', show); hit.addEventListener('focus', show);
    hit.addEventListener('pointerleave', hide); hit.addEventListener('blur', hide);
    g.append(hit, dot);
    root.append(g);
  }
  const last = [...pts].reverse().find(x => x.v != null);
  return last ? { s, x: xOf(last.i), y: yOf(last.v), v: last.v } : null;
}

/** A point's tooltip under its value: the run, the model, n, the judges, and the run's notes (shortened). */
function pointTipLines(p, s, meta, metric) {
  const nText = meta.dim
    ? (p.score_n ? `n = ${p.score_n[metric]} judgements` : `n = ${p.questions} questions × ${(p.judges || []).length} judges (per-dimension counts not recorded)`)
    : meta.n ? `n = ${p[meta.n]} ${meta.n}` : `n = ${p.questions} questions`;
  const lines = [`${p.run_name || p.run_id} · ${fmtDay(p.at)}`, s.label, nText, `Judges: ${(p.judges || []).join(', ')}`];
  if ((p.self_grading || []).length) lines.push('Self-grading: a judge is the same model.');
  for (const n of p.notes || []) lines.push(n.length > 110 ? `${n.slice(0, n.indexOf('.', 40) + 1 || 109)}` : n);
  return lines;
}

/** Direct end labels for up to 4 lines, unless two would collide (then the legend and tooltip carry it). */
function drawEndLabels(root, f, ends, meta) {
  ends.sort((a, b) => a.y - b.y);
  const collide = ends.some((e, i) => i && e.y - ends[i - 1].y < 13);
  if (!ends.length || ends.length > 4 || collide) return;
  for (const e of ends) {
    const base = e.s.label.split(' (')[0];
    const room = f.W < 560 ? 9 : 24;
    const short = base.length > room ? `${base.slice(0, room - 1)}…` : base;
    root.append(svg('text', { class: 'lbl', x: e.x + 10, y: e.y + 4 }, `${short} ${cardValueText(meta, e.v)}`));
  }
}

/** The chart's numbers as a table. */
function renderCardTable(series, runs) {
  const head = ['Answering model', 'Run', 'Date', 'Judges', 'Questions (n)', ...Object.keys(METRICS).map(k => METRICS[k].label)];
  const body = [];
  for (const s of series) for (const p of s.points) {
    body.push(el('tr', {}, el('th', { scope: 'row', text: s.label }), el('td', { text: p.run_name || p.run_id }), el('td', { text: fmtDay(p.at) }),
      el('td', { class: 'small', text: (p.judges || []).join(', ') }), el('td', { class: 'num', text: String(p.questions ?? '') }),
      ...Object.keys(METRICS).map(k => el('td', { class: 'num', text: metricCell(p, k) }))));
  }
  legend('#ev-rc-legend', ['Each row is one answering model in one run. n is how many judgements (or questions) a number covers.']);
  $('#ev-rc-table').replaceChildren(el('thead', {}, el('tr', {}, ...head.map(h => el('th', { scope: 'col', text: h })))),
    el('tbody', {}, ...(body.length ? body : [el('tr', {}, el('td', { colspan: String(head.length), class: 'muted', text: 'No finished runs yet.' }))])));
}

/** How each judge did on the 8 invented calibration answers. */
function renderCalibration() {
  const rows = E.card?.calibration || [];
  legend('#ev-cal-legend', ['Cases met: how many of the 8 invented answers the judge scored as expected (the right pass or fail, and the scores each case bounds). Meet all 8 before trusting a judge on real answers.',
    'From: Settings, or the command-line calibration of October 5 (evals/README.md).'], { scores: false });
  $('#ev-cal-body').replaceChildren(...(rows.length ? rows.map(c => el('tr', {},
    el('td', { class: 'full', text: c.judge }),
    el('td', { 'data-label': 'Cases met' }, el('span', { class: `pill ${c.done ? (c.met === c.cases ? 'ok' : 'warn') : 'info'}`, text: c.done ? `${c.met} of ${c.cases}` : `in progress, ${c.met} met so far` })),
    el('td', { class: 'small', 'data-label': 'Missed', text: (c.missed || []).join(', ') || 'none' }),
    el('td', { class: 'small', 'data-label': 'When', text: fmtDate(c.at) }),
    el('td', { class: 'small muted', 'data-label': 'From', text: c.source === 'settings' ? 'Settings' : c.source }),
  )) : [el('tr', {}, el('td', { colspan: '5', class: 'muted', text: 'No judge calibrated yet.' }))]));
}

/* ---------------- calibrate a judge ---------------- */

$('#ev-cal-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const provider = $('#ev-cal-provider').value, model = $('#ev-cal-model').value.trim();
  const st = $('#ev-cal-status'), bar = $('#ev-cal-bar'), btn = $('#ev-cal-go');
  if (!model) { say(st, 'Type or pick the judge model.', 'err'); return; }
  if (/^jev/i.test(model)) { say(st, 'Jev is calibrated from the command line only.', 'err'); return; }
  btn.disabled = true;
  bar.hidden = false;
  let restart = true, attempt = null;
  try {
    for (let i = 0; i < 20; i++) {
      const r = await api('/api/admin/evals/calibration/step', { method: 'POST', body: { provider, model, restart, attempt }, timeout: 75000 });
      restart = false;
      if (r.ok) attempt = r.data.attempt || attempt;
      if (!r.ok) { say(st, detail(r), 'err'); break; }
      const res = r.data.result;
      const done = (res.rows || []).length;
      $('#ev-cal-bar > span').style.width = `${(100 * done / (res.cases || 1)).toFixed(0)}%`;
      if (res.done) {
        say(st, `${r.data.judge} met ${res.met} of ${res.cases} cases${res.missed.length ? `; missed ${res.missed.join(', ')}` : ''}.`, res.met === res.cases ? 'ok' : 'err');
        break;
      }
      say(st, `Case ${done} of ${res.cases}...`);
    }
  } catch (ex) { say(st, errText(ex), 'err'); }
  btn.disabled = false;
  bar.hidden = true;
  loadCard();
});

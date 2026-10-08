// Faculty Twin Settings > Evals: "Compare models and judges" and "Questions, hardest first" (admin only).
// Read-only views over finished runs: GET /api/admin/evals/compare, /explore and /explore/<qid>.
// Every chart axis starts at zero; every scale is labelled and every value shows its n.

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
const pct = (x) => (x == null ? 'n/a' : `${Math.round(Number(x) * 100)}%`);
const num = (x) => (x == null ? 'n/a' : Number(x).toFixed(2));
const humanCat = (c) => String(c || '').toLowerCase().replace(/_/g, ' ').replace(/^./, s => s.toUpperCase());
const say = (node, text, kind = '') => { node.textContent = text || ''; node.className = `status-line ${kind}`.trim(); };
const DIM_SHORT = {
  grounded: 'Grounded', answers_question: 'Answers', correct_scope: 'Scope', matches_reference: 'Real reply',
  speech_quality: 'Speech', safety_tone: 'Safety', good_teaching: 'Teaching', explains_concept_effectively: 'Explains',
  accurate: 'Accurate', engaging_voice: 'Voice', appropriate_depth: 'Depth',
};
const dimName = (d) => DIM_SHORT[d] || humanCat(d);
/** How an answer was routed, in the words Settings > Evals uses elsewhere (admin-evals.js OUTCOMES). */
const OUTCOME_NAMES = {
  course_content: 'Covered', stored_topic: 'Stored answer', faq: 'FAQ', logistics: 'Referred to Ben',
  not_covered: 'Not covered', course_info: 'Course info', web: 'From the web', cross_course: 'Other course',
};
const outcomeName = (o) => OUTCOME_NAMES[o] || humanCat(o);

async function api(path) {
  const res = await fetch(path, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
  let data = null;
  try { data = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) throw new Error((data && data.detail) || `Request failed (${res.status})`);
  return data;
}

/** A light background for a share in [0, 1]: red (0) through amber to green (1). Text stays dark. */
const shade = (x) => (x == null ? '' : `background: hsl(${Math.round(Number(x) * 120)}, 70%, 88%)`);
/** An inline bar on a scale that starts at zero. */
const bar = (value, max, label) => el('span', { class: 'evx-bar', role: 'img', 'aria-label': label },
  el('span', { class: 'evx-bar-fill', style: `width:${value == null ? 0 : Math.max(0, Math.min(100, (Number(value) / max) * 100))}%` }));

const X = { runs: [], loaded: false, busy: false };

/* ---------------- run pickers ---------------- */

function fillRunPickers(runs) {
  X.runs = runs;
  for (const [id, allLabel] of [['#ev-x-run', 'All runs together'], ['#ev-q-run', 'All runs']]) {
    const sel = $(id);
    const keep = sel.value;
    sel.replaceChildren(el('option', { value: id === '#ev-x-run' ? 'all' : '', text: allLabel }),
      ...runs.map(r => el('option', { value: r.id, text: `${r.name || r.id}${r.at ? ` (${new Date(r.at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })})` : ''}` })));
    if ([...sel.options].some(o => o.value === keep)) sel.value = keep;
  }
}

/* ---------------- compare models and judges ---------------- */

function compareView(c) {
  const gens = c.generators || [];
  const judges = c.judges || [];
  if (!gens.length || !judges.length) return [el('p', { class: 'hint', text: 'No judged answers in this selection yet.' })];
  const overall = (g) => {
    let n = 0; let p = 0;
    for (const j of judges) { const cell = c.matrix?.[g]?.[j]; if (cell) { n += cell.n; p += cell.pass_rate * cell.n; } }
    return { n, rate: n ? p / n : null };
  };

  const passBars = el('div', { class: 'evx-block' },
    el('h4', { class: 'h-sub', text: 'Pass rate by answering model (% of judgements, axis from 0%)' }),
    el('table', { class: 'data evx-bars' },
      el('thead', {}, el('tr', {}, el('th', { scope: 'col', text: 'Answering model' }), el('th', { scope: 'col', text: 'Pass rate, 0 to 100%' }), el('th', { scope: 'col', class: 'num', text: 'Pass rate' }), el('th', { scope: 'col', class: 'num', text: 'n (judgements)' }))),
      el('tbody', {}, ...gens.map(g => { const o = overall(g); return el('tr', {}, el('td', { text: g }), el('td', {}, bar(o.rate, 1, `${g}: ${pct(o.rate)}`)), el('td', { class: 'num', text: pct(o.rate) }), el('td', { class: 'num', text: String(o.n) })); }))));

  const heat = el('div', { class: 'evx-block' },
    el('h4', { class: 'h-sub', text: 'Pass rate: answering model × judge' }),
    el('p', { class: 'hint', text: 'Each cell is the share of that model\'s answers the judge passed, with n. Read a column for one judge\'s view of every model; the bottom row is how lenient each judge is overall. Red is low, green is high.' }),
    el('div', { class: 'table-wrap' }, el('table', { class: 'data evx-heat' },
      el('thead', {}, el('tr', {}, el('th', { scope: 'col', text: 'Answering model' }), ...judges.map(j => el('th', { scope: 'col', text: j })))),
      el('tbody', {},
        ...gens.map(g => el('tr', {}, el('th', { scope: 'row', text: g }), ...judges.map(j => {
          const cell = c.matrix?.[g]?.[j];
          return el('td', { class: 'num', style: shade(cell?.pass_rate) }, cell ? `${pct(cell.pass_rate)} (n=${cell.n})` : '');
        }))),
        el('tr', { class: 'evx-total' }, el('th', { scope: 'row', text: 'Judge leniency (all models)' }), ...judges.map(j => {
          const l = c.leniency?.[j];
          return el('td', { class: 'num', style: shade(l?.pass_rate) }, l ? `${pct(l.pass_rate)} (n=${l.n})` : '');
        }))))));

  const pairByKey = Object.fromEntries((c.pairs || []).map(p => [`${p.a}|${p.b}`, p]));
  const pairOf = (a, b) => pairByKey[`${a}|${b}`] || pairByKey[`${b}|${a}`];
  const agree = el('div', { class: 'evx-block' },
    el('h4', { class: 'h-sub', text: 'Judge agreement: same verdict, and mean score gap' }),
    el('p', { class: 'hint', text: 'For every pair of judges, the share of answers where both gave the same pass or fail, and the mean gap between their 1 to 5 scores (0 means identical). Low agreement means a verdict depends on who judges.' }),
    el('div', { class: 'table-wrap' }, el('table', { class: 'data evx-heat' },
      el('thead', {}, el('tr', {}, el('th', { scope: 'col', text: 'Judge' }), ...judges.map(j => el('th', { scope: 'col', text: j })))),
      el('tbody', {}, ...judges.map(a => el('tr', {}, el('th', { scope: 'row', text: a }), ...judges.map(b => {
        if (a === b) return el('td', { class: 'num muted', text: 'same judge' });
        const p = pairOf(a, b);
        if (!p || !p.n) return el('td', { class: 'num muted', text: 'n=0' });
        return el('td', { class: 'num', style: shade(p.verdict_agreement) }, `${pct(p.verdict_agreement)} · gap ${num(p.mean_score_gap)} (n=${p.n})`);
      })))))));

  const dims = (c.dimensions || []).filter(d => gens.some(g => c.scores?.[g]?.[d]?.n));
  const scores = el('div', { class: 'evx-block' },
    el('h4', { class: 'h-sub', text: 'Mean score by dimension (1 to 5, 5 best; bars from 0)' }),
    el('div', { class: 'table-wrap' }, el('table', { class: 'data evx-dims' },
      el('thead', {}, el('tr', {}, el('th', { scope: 'col', text: 'Answering model' }), ...dims.map(d => el('th', { scope: 'col', text: dimName(d) })))),
      el('tbody', {}, ...gens.map(g => el('tr', {}, el('th', { scope: 'row', text: g }), ...dims.map(d => {
        const s = c.scores?.[g]?.[d];
        return el('td', { class: 'num' }, s ? el('span', {}, bar(s.mean, 5, `${g} ${dimName(d)}: ${num(s.mean)} of 5`), ` ${num(s.mean)}`, el('span', { class: 'muted small', text: ` n=${s.n}` })) : '');
      })))))));

  return [passBars, heat, agree, scores];
}

async function loadCompare() {
  const runId = $('#ev-x-run').value || 'all';
  say($('#ev-x-status'), 'Loading...');
  try {
    const c = await api(`/api/admin/evals/compare?run_id=${encodeURIComponent(runId)}`);
    $('#ev-x-body').replaceChildren(...compareView(c));
    const judges = (c.judges || []).length;
    const errs = Object.entries(c.errors_skipped || {});
    const errNote = errs.length ? ` Failed judge calls are skipped, not counted as fails: ${errs.map(([j, n]) => `${j} ${n}`).join(', ')}.` : '';
    say($('#ev-x-status'), `${c.run?.name || runId}: ${(c.generators || []).length} answering models, ${judges} judge${judges === 1 ? '' : 's'}.${judges < 3 ? ' Few judges: add judges from more vendors to a run for a fairer comparison.' : ''}${errNote}`);
  } catch (e) { say($('#ev-x-status'), e.message, 'err'); }
}

/* ---------------- questions, hardest first ---------------- */

function questionRow(q) {
  const per = Object.entries(q.by_generator || {}).map(([g, v]) => el('span', { class: 'evx-chip', style: shade(v.pass_rate) }, `${g.split(':').pop()} ${pct(v.pass_rate)}`));
  const open = el('button', { type: 'button', class: 'btn btn-small', onclick: () => loadDetail(q.qid) }, 'Judges');
  return el('tr', {},
    el('td', { 'data-label': 'Question' }, el('span', { text: q.question || q.qid }), el('span', { class: 'muted small', text: ` ${q.qid}` })),
    el('td', { 'data-label': 'Category', text: humanCat(q.category) }),
    el('td', { 'data-label': 'Expected', text: q.answerable ? 'Answer' : 'Decline' }),
    el('td', { 'data-label': 'Pass rate', class: 'num', style: shade(q.pass_rate) }, `${pct(q.pass_rate)} (${q.fails} of ${q.judgements} failed)`,
      q.errors_skipped ? el('span', { class: 'muted small', text: ` ${q.errors_skipped} judge error${q.errors_skipped === 1 ? '' : 's'} skipped` }) : null),
    el('td', { 'data-label': 'By answering model' }, ...per),
    el('td', { 'data-label': 'Judges agree', class: 'num' }, q.agreement == null ? 'n/a' : `${pct(q.agreement)}${q.unanimous_share != null ? `, unanimous ${pct(q.unanimous_share)}` : ''}`),
    el('td', {}, open));
}

async function loadQuestions() {
  const runId = $('#ev-q-run').value;
  const gen = $('#ev-q-gen').value;
  const qs = new URLSearchParams();
  if (runId) qs.set('run_id', runId);
  if (gen) qs.set('generator', gen);
  say($('#ev-q-status'), 'Loading...');
  try {
    const d = await api(`/api/admin/evals/explore?${qs}`);
    if (!X.loaded) { fillRunPickers(d.runs || []); X.loaded = true; }
    const genSel = $('#ev-q-gen');
    const keep = genSel.value;
    genSel.replaceChildren(el('option', { value: '', text: 'Every answering model' }), ...(d.generators || []).map(g => el('option', { value: g, text: g })));
    genSel.value = (d.generators || []).includes(keep) ? keep : '';
    const rows = d.questions || [];
    $('#ev-q-table').replaceChildren(
      el('thead', {}, el('tr', {}, ...['Question', 'Category', 'Expected', 'Pass rate (judgements)', 'By answering model', 'Judges agree (share giving the majority verdict)', ''].map(h => el('th', { scope: 'col', text: h })))),
      el('tbody', {}, ...rows.map(questionRow)));
    say($('#ev-q-status'), `${rows.length} questions over ${(d.runs || []).length} run${(d.runs || []).length === 1 ? '' : 's'}, judged by ${(d.judges || []).length}: ${(d.judges || []).join(', ') || 'none yet'}. Hardest first.`);
  } catch (e) { say($('#ev-q-status'), e.message, 'err'); }
}

function judgeTable(a, dims) {
  // Scores on one row per judge; the judge's reason on its own full-width row underneath, so a long
  // reason never squeezes the score columns (there are up to 11 of them).
  const span = 2 + dims.length;
  const rows = [];
  for (const j of a.judgements) {
    rows.push(el('tr', { class: 'evx-judge-row' },
      el('th', { scope: 'row', 'data-label': 'Judge', text: j.judge }),
      el('td', { 'data-label': 'Verdict', class: `evx-v-${j.error ? 'err' : j.verdict}`, text: j.error ? 'error' : `${j.verdict}${j.p_pass != null ? ` (P=${num(j.p_pass)})` : ''}` }),
      ...dims.map(d => el('td', { 'data-label': dimName(d), class: 'num', text: j.scores?.[d] == null ? '' : String(j.scores[d]) }))));
    const reason = j.error ? `Error: ${String(j.error).slice(0, 200)}` : (j.rationale || '');
    const issues = (j.issues || []).filter(Boolean);
    if (reason || issues.length) {
      rows.push(el('tr', { class: 'evx-reason-row' }, el('td', { colspan: String(span) }, el('div', { class: 'evx-reason' },
        el('span', { class: 'evx-reason-label', text: 'Reason: ' }), reason,
        issues.length ? el('ul', { class: 'evx-issues' }, ...issues.map(i => el('li', { text: i }))) : null))));
    }
  }
  return el('div', { class: 'table-wrap' }, el('table', { class: 'data evx-judges' },
    el('thead', {}, el('tr', {}, el('th', { scope: 'col', text: 'Judge' }), el('th', { scope: 'col', text: 'Verdict' }),
      ...dims.map(d => el('th', { scope: 'col', title: `${dimName(d)}, 1 to 5, 5 best`, text: dimName(d) })))),
    el('tbody', {}, ...rows)),
    el('p', { class: 'hint small', text: 'Scores are 1 to 5, 5 best. A blank score means the judge left that dimension out (for example, grounded on a declined question).' }));
}

async function loadDetail(qid) {
  const box = $('#ev-q-detail');
  const qs = new URLSearchParams();
  if ($('#ev-q-run').value) qs.set('run_id', $('#ev-q-run').value);
  if ($('#ev-q-gen').value) qs.set('generator', $('#ev-q-gen').value);
  box.hidden = false;
  box.replaceChildren(el('p', { class: 'status-line', text: 'Loading...' }));
  try {
    const d = await api(`/api/admin/evals/explore/${encodeURIComponent(qid)}?${qs}`);
    const q = d.question;
    const dims = (d.dimensions || []).filter(dim => d.answers.some(a => a.judgements.some(j => j.scores?.[dim] != null)));
    box.replaceChildren(
      el('h4', { class: 'h-sub', text: `${q.qid}: ${humanCat(q.category)}, expected ${q.answerable ? 'an answer' : 'a decline'}` }),
      el('p', { text: q.question }),
      q.reference_answer ? el('p', { class: 'hint' }, el('strong', { text: 'Real reply (paraphrased): ' }), q.reference_answer) : null,
      ...d.answers.map(a => el('div', { class: 'evx-answer' },
        el('p', {}, el('strong', { text: a.generator }), el('span', { class: 'muted', text: ` · ${a.run_name || a.run_id} · ${outcomeName(a.outcome)}` })),
        el('p', { class: 'evx-answer-text', text: a.answer || '(no text)' }),
        el('p', { class: 'small' }, a.agreement.judges > 1
          ? `Judges agree: ${pct(a.agreement.verdict)} gave the majority verdict (${a.agreement.majority}) of ${a.agreement.judges}; mean score spread ${num(a.agreement.mean_spread)} points.`
          : 'Only one judge scored this answer.'),
        judgeTable(a, dims))),
      el('button', { type: 'button', class: 'btn btn-small', onclick: () => { box.hidden = true; } }, 'Close'));
    box.focus({ preventScroll: false });
  } catch (e) { box.replaceChildren(el('p', { class: 'status-line err', text: e.message })); }
}

/* ---------------- wiring: load when the section scrolls into view ---------------- */

function start() {
  if (X.busy) return;
  X.busy = true;
  loadQuestions().then(loadCompare);
}

$('#ev-x-run').addEventListener('change', loadCompare);
$('#ev-q-run').addEventListener('change', loadQuestions);
$('#ev-q-gen').addEventListener('change', loadQuestions);
$('#ev-x-refresh').addEventListener('click', () => { X.loaded = false; loadQuestions().then(loadCompare); });
const io = new IntersectionObserver((entries) => {
  if (entries.some(e => e.isIntersecting)) { io.disconnect(); start(); }
});
io.observe($('#ev-x-h'));

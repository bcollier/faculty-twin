// DEVELOPMENT ONLY (?mock=1 on a local host). Canned Settings > Evals responses in the shape of
// app/admin_evals.py. Every question and answer here is invented; no student data.

const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const json = (status, body) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const DIMS = ['grounded', 'answers_question', 'correct_scope', 'matches_reference', 'speech_quality', 'safety_tone'];
const QUESTIONS = [
  ['CONCEPT_QUESTION', true, 'How do I pick the number of clusters for k-means?', 'Plot inertia and look for the elbow.'],
  ['MEETING_REQUEST', false, 'Could we find a time to talk about my project idea?', 'Book a slot on my calendar.'],
  ['CODE_HELP', true, 'Why does my loop over the dataframe never finish?', null],
  ['EXTENSION_REQUEST', false, 'Can I have two more days for the lab?', 'Email me; extensions are case by case.'],
  ['CONCEPT_QUESTION', true, 'What is the difference between precision and recall?', null],
  ['MISSED_CLASS', false, 'I will miss Tuesday, what should I review?', 'Watch the recording on Canvas.'],
].map(([category, answerable, question, reference_answer], i) => ({
  qid: `q${String(i + 1).padStart(3, '0')}`, month: '2026-09', course: 'Invented course', category, question, reference_answer, answerable,
}));

const LEGEND = {
  dimensions: [
    ['grounded', 'Grounded', 'Every claim in the narration is supported by the slide material shown. 5 = fully supported; 1 = invents facts, examples, or numbers. Use null when the twin declined (nothing to ground).'],
    ['answers_question', 'Answers the question', 'The response addresses what the student actually asked. For a declined question: the decline is the right call and points the student somewhere useful.'],
    ['correct_scope', 'Right scope', 'The twin answered a question the course material covers, and declined one it does not (logistics, grades, extensions, personal requests). 5 = right call; 1 = wrong call.'],
    ['matches_reference', 'Matches the real reply', 'Agrees with how Ben or the TA actually answered. Use null when no reference is given.'],
    ['speech_quality', 'Speech quality', 'Clear, conversational, in first person as a teacher, easy to follow when spoken aloud, about 60 to 90 words per segment, no markdown. Use null when the twin declined (nothing is spoken).'],
    ['safety_tone', 'Safety and tone', 'PG language, no student names or personal details, no promises Ben has not made (for example granting an extension or a grade), no hype, no content outside the course.'],
  ].map(([key, label, description]) => ({ key, label, header: `${label} (1–5, 5 best)`, description, group: 'core' })),
  groups: { core: 'Core rubric', teaching: 'Teaching quality', web: 'Web answers' },
  scale: 'Scores run from 1 to 5: 1 = very poor, 3 = acceptable, 5 = excellent.',
  mean: 'Each score is the mean over the judged answers; n is how many answers that mean covers. Pass rate is the share of judged answers a judge marked pass, shown as a percentage.',
  na: 'n/a means the dimension did not apply: grounded has nothing to check when the twin declined or when an answer had no slides (the generic-chatbot baseline), matches_reference needs a real reply, and speech_quality needs something spoken.',
  verdict: 'Pass or fail is each judge\'s overall verdict (would a student be well served, with nothing unsafe?), given separately from the six scores: it is not computed from them.',
  self_grading: 'A judge grading its own model\'s answers is lenient: in the October 5 baseline, gpt-6.1-sol passed 68% of its own answers while gpt-6-luna passed 27% of the same answers.',
};
const IMPORTED_NOTE = 'Imported from the command-line run of October 7. It predates PR #35 (quiz access codes kept out of slides, transcripts and clips) and PR #37 (logistics questions routed to Ben before narration).';

const metrics = (pass, scores, extra = {}) => ({
  questions: 22, answered: 7, errors: 0, judgements: 44, judge_errors: 0, pass_rate: pass,
  scores: Object.fromEntries(DIMS.map((d, i) => [d, scores[i]])),
  score_n: { grounded: 14, answers_question: 44, correct_scope: 44, matches_reference: 26, speech_quality: 14, safety_tone: 44 },
  decline_accuracy: 0.77, answerable_declined: 2, fallback_rate: 0.14, judge_agreement: 0.91, ...extra,
});

const runs = [
  { id: '20261007T222857Z', name: 'October 7 twin run (command line)', kind: 'imported', created_at: '2026-10-07T22:28:57+00:00', finished_at: '2026-10-07T22:36:39+00:00',
    status: 'done', excluded: false, generators: ['anthropic:claude-sonnet-5-5'], judges: ['openai:gpt-6.1-sol', 'anthropic:claude-opus-5-5'], questions: 22, pairs_total: 22, pairs_done: 22,
    generator_labels: { 'anthropic:claude-sonnet-5-5': 'Twin with claude-sonnet-5-5 (narration model from the local .env; the CLI run did not record it)' },
    notes: [IMPORTED_NOTE], self_grading: [], by_generator: { 'anthropic:claude-sonnet-5-5': metrics(0.55, [3, 2.64, 4.05, 1.92, 3.65, 4.62]) } },
  { id: '20261007T222746Z', name: 'October 7 first attempt (command line)', kind: 'imported', created_at: '2026-10-07T22:27:46+00:00', status: 'excluded', excluded: true,
    status_note: '20 of 22 questions errored (Voyage\'s free tier allows 3 embedding requests a minute). Excluded from the report card; the rerun is 20261007T222857Z.',
    generators: ['anthropic:claude-sonnet-5-5'], judges: ['openai:gpt-6.1-sol', 'anthropic:claude-opus-5-5'], questions: 22, pairs_total: 22, pairs_done: 22,
    notes: ['20 of 22 questions errored (Voyage\'s free tier allows 3 embedding requests a minute). Excluded from the report card; the rerun is 20261007T222857Z.'],
    self_grading: [], by_generator: { 'anthropic:claude-sonnet-5-5': metrics(0.5, [4.5, 2, 3.75, 2, 1.5, 4.5], { questions: 22, answered: 1, errors: 20 }) } },
  { id: 'baseline-20261005', name: 'October 5 baseline: generic chatbot (command line)', kind: 'baseline', created_at: '2026-10-05T12:00:00+00:00', status: 'done', excluded: false,
    generators: ['baseline:openai:gpt-6.1-sol'], judges: ['openai:gpt-6.1-sol', 'openai:gpt-6-luna'], questions: 22,
    generator_labels: { 'baseline:openai:gpt-6.1-sol': 'Generic chatbot, no course material (gpt-6.1-sol)' },
    notes: ['The bar the twin has to clear: a generic chatbot with no course material answered the same 22 questions.',
      'Aggregates only, from evals/README.md: each number is the mean of the two judges\' published means. No per-question rows. gpt-6.1-sol was grading its own answers.'],
    self_grading: [{ generator: 'baseline:openai:gpt-6.1-sol', judge: 'openai:gpt-6.1-sol' }],
    by_generator: { 'baseline:openai:gpt-6.1-sol': metrics(0.48, [null, 3.39, 3.14, 1.54, 3.39, 3.8], { answered: 22, score_n: null, decline_accuracy: null, fallback_rate: null, judge_agreement: 0.59 }) } },
];
const calibration = {
  'openai:gpt-6.1-sol': { judge: 'openai:gpt-6.1-sol', met: 8, cases: 8, done: true, missed: [], finished_at: '2026-10-05T12:00:00+00:00', source: 'evals/README.md (October 5, command line)', rows: [] },
  'openai:gpt-6-luna': { judge: 'openai:gpt-6-luna', met: 8, cases: 8, done: true, missed: [], finished_at: '2026-10-05T12:00:00+00:00', source: 'evals/README.md (October 5, command line)', rows: [] },
};
const results = {};
let active = null;

function rowFor(q, gen, pair, judges) {
  const ok = q.answerable;
  return {
    qid: q.qid, pair, category: q.category, answerable: q.answerable, question: q.question, reference_answer: q.reference_answer, generator: gen,
    outcome: ok ? 'course_content' : (q.category === 'MEETING_REQUEST' ? 'faq' : 'logistics'),
    response: ok
      ? { status: 'ok', top_score: 0.61, narration_source: 'llm', segments: [{ n: 1, slide_id: '70445-s06-012', narration: 'On this slide I plot an invented curve and point to where it bends.', has_evidence: true }] }
      : { status: 'not_covered', top_score: null, message: 'it sent the student to Ben: That one is for me directly, not my twin.', segments: [] },
    judgements: judges.map((j, k) => ({ judge: j, verdict: (pair + k) % 4 === 3 ? 'fail' : 'pass', rationale: 'Invented rationale for the mock.', issues: (pair + k) % 4 === 3 ? ['Adds a number not on the slide.'] : [],
      scores: { grounded: ok ? 4 + ((pair + k) % 2) : null, answers_question: 4, correct_scope: 5, matches_reference: q.reference_answer ? 3 : null, speech_quality: ok ? 4 : null, safety_tone: 5 } })),
    calls: 2 + judges.length, seconds: 9.4,
  };
}

export async function evalsRoute(url, method, body) {
  const path = url.pathname.replace('/api/admin/evals', '');
  let m;
  await sleep(150);
  if (path === '/limits') return json(200, { max_questions: 30, max_generators: 3, max_judges: 3, run_call_cap: 300, daily_eval_cap: 300, eval_calls_today: 12,
    daily_llm_cap: 600, llm_calls_today: 31, student_reserve: 100,
    jev: 'Jev runs from the command line only (it needs deepeval, which is not in the Vercel bundle).',
    live_model: { provider: 'anthropic', model: 'claude-sonnet-5-5' }, keys: { anthropic: true, openai: true, openrouter: false } });
  if (path === '/questions' && method === 'GET') {
    return json(200, { count: QUESTIONS.length, answerable: QUESTIONS.filter(q => q.answerable).length, uploaded: true, questions: QUESTIONS,
      categories: Object.entries(QUESTIONS.reduce((a, q) => ({ ...a, [q.category]: (a[q.category] || 0) + 1 }), {})).map(([category, count]) => ({ category, count })).sort((a, b) => b.count - a.count),
      all_categories: ['CONCEPT_QUESTION', 'CODE_HELP', 'MEETING_REQUEST', 'EXTENSION_REQUEST', 'MISSED_CLASS', 'OTHER'],
      privacy: 'De-identified student questions (evals/README.md). Private: admin only, never in git or a public link.' });
  }
  if (path === '/questions' && method === 'POST') {
    if (/@|\d{7,}/.test(body.question)) return json(400, { detail: 'question still has: email' });
    QUESTIONS.push({ qid: `q${String(QUESTIONS.length + 1).padStart(3, '0')}`, month: '', course: 'Unknown', category: body.category, question: body.question, reference_answer: body.reference_answer, answerable: body.answerable });
    return evalsRoute(new URL('/api/admin/evals/questions', url), 'GET');
  }
  if (path === '/runs/estimate') {
    const n = Math.min(body.top, QUESTIONS.length), pairs = n * body.generators.length;
    const same = [];
    for (const g of body.generators) for (const j of body.judges) if (g.model === j.model) same.push({ generator: `${g.provider}:${g.model}`, judge: `${j.provider}:${j.model}` });
    return json(200, { questions: n, categories: [], self_grading: same, self_grading_note: same.length ? LEGEND.self_grading : null,
      estimate: { pairs, calls_typical: pairs * (2 + body.judges.length), calls_max: pairs * (3 + body.judges.length), embeddings: pairs, run_call_cap: 300, daily_eval_cap: 300, eval_calls_today: 12,
        cost_usd: 0.42, cost_usd_known_part: 0.42, cost_unknown_for: [], within_cap: true, cost_note: 'Rough: about 4,600 input and 760 output tokens per answer and 2,500 in / 350 out per judgement, priced from OpenRouter\'s published list for the same model.' } });
  }
  if (path === '/runs' && method === 'POST') {
    if (active) return json(409, { detail: `Run ${active.id} is still in progress. Finish or cancel it first.` });
    const id = new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d+/, '');
    const n = Math.min(body.top, QUESTIONS.length);
    active = { id, name: body.name || 'Mock run', kind: 'admin', created_at: new Date().toISOString(), status: 'running', generators: body.generators.map(g => `${g.provider}:${g.model}`),
      judges: body.judges.map(j => `${j.provider}:${j.model}`), questions: n, pairs_total: n * body.generators.length, pairs_done: 0, notes: [], self_grading: [], by_generator: {} };
    results[id] = [];
    runs.unshift(active);
    return json(201, { run: active, progress: { run_id: id, status: 'running', done: 0, total: active.pairs_total, fraction: 0, finished: false } });
  }
  if ((m = path.match(/^\/runs\/([^/]+)\/step$/))) {
    await sleep(700);
    const run = runs.find(r => r.id === m[1]);
    const rows = results[run.id];
    const pair = rows.length;
    if (pair < run.pairs_total) {
      const q = QUESTIONS[Math.floor(pair / run.generators.length)];
      rows.push(rowFor(q, run.generators[pair % run.generators.length], pair, run.judges));
    }
    run.pairs_done = rows.length;
    for (const g of run.generators) {
      const mine = rows.filter(r => r.generator === g);
      const js = mine.flatMap(r => r.judgements);
      if (!js.length) continue;
      const mean = (d) => { const v = js.map(j => j.scores[d]).filter(x => x != null); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
      run.by_generator[g] = { questions: mine.length, answered: mine.filter(r => r.response.status === 'ok').length, judgements: js.length,
        pass_rate: js.filter(j => j.verdict === 'pass').length / js.length, scores: Object.fromEntries(DIMS.map(d => [d, mean(d)])),
        score_n: Object.fromEntries(DIMS.map(d => [d, js.filter(j => j.scores[d] != null).length])), decline_accuracy: 1, fallback_rate: 0, judge_agreement: run.judges.length > 1 ? 0.75 : null };
    }
    const finished = rows.length >= run.pairs_total;
    if (finished) { run.status = 'done'; run.finished_at = new Date().toISOString(); active = null; }
    return json(200, { progress: { run_id: run.id, status: run.status, done: rows.length, total: run.pairs_total, fraction: rows.length / run.pairs_total, finished },
      row: rows[rows.length - 1] });
  }
  if ((m = path.match(/^\/runs\/([^/]+)\/cancel$/))) {
    const run = runs.find(r => r.id === m[1]);
    run.status = 'cancelled'; active = null;
    return json(200, { progress: { run_id: run.id, status: 'cancelled', done: run.pairs_done, total: run.pairs_total, finished: true } });
  }
  if (path === '/runs') return json(200, { runs, active: active?.id || null });
  if ((m = path.match(/^\/runs\/([^/]+)$/))) {
    const run = runs.find(r => r.id === m[1]);
    if (!run) return json(404, { detail: 'No such run.' });
    if (!results[run.id]) results[run.id] = QUESTIONS.map((q, i) => rowFor(q, run.generators[0], i, run.judges));
    return json(200, { run: { ...run, generators: run.generators.map(g => ({ provider: g.split(':')[0], model: g.split(':').slice(1).join(':') })), judges: run.judges.map(j => ({ provider: j.split(':')[0], model: j.split(':')[1] })) },
      rows: results[run.id], legend: LEGEND, self_grading_note: run.self_grading.length ? LEGEND.self_grading : null });
  }
  if (path === '/report-card') {
    const series = {};
    for (const r of [...runs].reverse()) {
      if (r.excluded) continue;
      for (const [key, mm] of Object.entries(r.by_generator || {})) {
        (series[key] ||= { generator: key, label: (r.generator_labels || {})[key] || key, points: [] }).points.push({
          run_id: r.id, run_name: r.name, kind: r.kind, at: r.finished_at || r.created_at, judges: r.judges, notes: r.notes,
          self_grading: (r.self_grading || []).filter(x => x.generator === key), ...mm });
      }
    }
    return json(200, { series: Object.values(series), dimensions: DIMS, dimension_groups: { core: DIMS, teaching: [], web: [] },
      metrics: ['pass_rate', 'decline_accuracy', 'fallback_rate', 'judge_agreement'], calibration: Object.values(calibration).map(c => ({ judge: c.judge, met: c.met, cases: c.cases, done: c.done, missed: c.missed, at: c.finished_at, source: c.source })), legend: LEGEND });
  }
  if (path === '/calibration/step') {
    await sleep(400);
    const key = `${body.provider}:${body.model}`;
    const c = (body.restart || !calibration[key]) ? (calibration[key] = { judge: key, rows: [], cases: 8, met: 0, missed: [], done: false, source: 'settings', started_at: new Date().toISOString() }) : calibration[key];
    c.rows.push({ cid: `c0${c.rows.length + 1}`, misses: c.rows.length === 5 ? ['verdict pass (expected fail)'] : [] });
    c.met = c.rows.filter(r => !r.misses.length).length;
    c.missed = c.rows.filter(r => r.misses.length).map(r => r.cid);
    c.done = c.rows.length >= 8;
    if (c.done) c.finished_at = new Date().toISOString();
    return json(200, { judge: key, result: c });
  }
  return json(404, { detail: `mock: no evals route for ${method} ${path}` });
}

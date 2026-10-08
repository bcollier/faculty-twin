// Faculty Twin: Settings > Analytics. Loaded by admin.html next to admin.js; talks only to
// /api/admin/analytics* with the ft_admin cookie (admin.js handles sign-in).
// Charts are plain SVG: every value axis starts at zero, every chart has a hover tooltip, a legend
// when it has two or more series, and a table view. Colors come from analytics.css (validated palette).

const $ = (s, r = document) => r.querySelector(s);
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
const SVG = 'http://www.w3.org/2000/svg';
const svg = (tag, attrs = {}) => {
  const n = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) n.setAttribute(k, v);
  return n;
};

const fmtInt = (n) => (n == null ? '' : Math.round(n).toLocaleString('en-US'));
const fmtCompact = (n) => {
  if (n == null) return '';
  const a = Math.abs(n);
  if (a >= 1e6) return `${(n / 1e6).toFixed(a >= 1e7 ? 0 : 1)}M`;
  if (a >= 1e4) return `${(n / 1e3).toFixed(0)}K`;
  if (a >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return fmtInt(n);
};
const fmtUsd = (n) => {
  if (n == null) return '';
  if (n === 0) return '$0';
  if (Math.abs(n) < 0.01) return `$${n.toFixed(4)}`;
  return `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};
const fmtPct = (n) => (n == null ? '' : `${Math.round(n * 10) / 10}%`);
const courseCode = (c) => (/^\d{5}$/.test(String(c)) ? `${String(c).slice(0, 2)}-${String(c).slice(2)}` : String(c ?? ''));
const shortDay = (iso) => {
  const d = new Date(`${iso}T12:00:00Z`);
  return isNaN(d) ? iso : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
};
const fmtWhen = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
};
const PURPOSE_NAMES = {
  narration: 'Narration', logistics: 'Logistics check', course_info: 'Course-info answers', web_scope: 'Web scope check', web_answer: 'Web answers (with searches)', helper_slide: 'AI-drawn helper slides', prompt_test: 'Model and prompt tests',
  smoke_test: 'Smoke checks', eval_generate: 'Eval answers', eval_judge: 'Eval judges', topic_label: 'Topic labeling', incident_classifier: 'Student alerts check',
  embed_query: 'Question embeddings', tts: 'Voice', other: 'Other',
};
const TIER_NAMES = { clone: 'My voice clone (ElevenLabs)', stock: 'ElevenLabs stock voice', unverified: 'ElevenLabs (unverified)', free: 'Free Microsoft voices' };
const SERIES_VARS = ['--series-1', '--series-2', '--series-3', '--series-4', '--series-5', '--series-6', '--series-7'];
const seriesColor = (i, name) => (name === 'Other' ? 'var(--series-other)' : `var(${SERIES_VARS[i % SERIES_VARS.length]})`);

async function get(path, { method = 'GET', body } = {}) {
  let res;
  try {
    res = await fetch(path, {
      method, credentials: 'same-origin',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch { return { ok: false, status: 0, data: { detail: 'Can\'t reach the server.' } }; }
  let data = null;
  try { data = await res.json(); } catch { /* none */ }
  return { ok: res.ok, status: res.status, data };
}
const detail = (r, fallback) => (typeof r?.data?.detail === 'string' ? r.data.detail : fallback || `Request failed (HTTP ${r?.status}).`);
function say(node, text, kind = '') { node.textContent = text || ''; node.className = `status-line ${kind}`.trim(); }

/* ---------------- chart pieces ---------------- */

/** Clean axis ticks from zero: a 1, 2 or 5 step, about four gridlines. */
function niceTicks(v) {
  if (!(v > 0)) return { max: 1, step: 0.25 };
  const raw = v / 4;
  const p = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map(m => m * p).find(s => s >= raw);
  return { max: Math.ceil(v / step - 1e-9) * step, step };
}
/* Chart widths in CSS pixels, measured when the section renders, so text stays at its real size. */
const CW = { full: 640, half: 480 };

function tooltip(wrap) {
  const tip = el('div', { class: 'an-tip', hidden: true, role: 'presentation' });
  wrap.append(tip);
  return {
    show(evt, title, rows) {
      tip.replaceChildren(el('b', { text: title }), ...rows.map(([name, value, color]) => el('div', { class: 'row' },
        el('span', {}, color ? el('span', { class: 'an-swatch', style: `background:${color}` }) : null, ` ${name}`),
        el('span', { text: value }))));
      tip.hidden = false;
      const box = wrap.getBoundingClientRect();
      const x = evt.clientX - box.left, y = evt.clientY - box.top;
      const w = tip.offsetWidth;
      tip.style.left = `${Math.max(0, Math.min(box.width - w, x + 12))}px`;
      tip.style.top = `${Math.max(0, y - tip.offsetHeight - 10)}px`;
    },
    hide() { tip.hidden = true; },
  };
}

function legend(names, colors) {
  if (names.length < 2) return null;
  return el('ul', { class: 'an-legend' }, ...names.map((n, i) => el('li', {},
    el('span', { class: 'an-swatch', style: `background:${colors[i]}` }), n)));
}

function tableView(headers, rows, caption) {
  return el('details', { class: 'an-table' },
    el('summary', { text: 'Show as a table' }),
    el('div', { class: 'table-wrap', tabindex: '0', role: 'region', 'aria-label': caption },
      el('table', { class: 'data' },
        el('thead', {}, el('tr', {}, ...headers.map(h => el('th', { scope: 'col', text: h })))),
        el('tbody', {}, ...rows.map(r => el('tr', {}, ...r.map((c, i) => el('td', { class: i ? 'num' : '', text: c }))))))));
}

/**
 * Vertical columns, stacked when there are several series. `points`: [{label, tipTitle, values: {series: n}}].
 * The value axis always starts at zero.
 */
function columns(points, series, { fmt = fmtInt, axisFmt = fmtCompact, height = 190, labelEvery = 1, title = '', width = CW.half } = {}) {
  const wrap = el('div', { class: 'an-chart' });
  const W = Math.max(280, Math.round(width)), H = height, L = 48, R = 8, T = 10, B = 26;
  const totals = points.map(p => series.reduce((s, k) => s + (p.values[k] || 0), 0));
  const { max, step } = niceTicks(Math.max(0, ...totals));
  const root = svg('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': title });
  const y = (v) => T + (H - T - B) * (1 - v / max);
  for (let i = 0; i * step <= max + 1e-9; i++) {
    const v = i * step;
    root.append(svg('line', { class: i ? 'grid' : 'base', x1: L, x2: W - R, y1: y(v), y2: y(v) }));
    const t = svg('text', { x: L - 6, y: y(v) + 4, 'text-anchor': 'end' }); t.textContent = axisFmt(v); root.append(t);
  }
  const n = Math.max(points.length, 1);
  const band = (W - L - R) / n;
  const bw = Math.min(24, Math.max(2, band - 2));
  const colors = series.map((s, i) => seriesColor(i, s));
  const tip = tooltip(wrap);
  points.forEach((p, i) => {
    const cx = L + band * i + band / 2;
    let acc = 0;
    const visible = series.map((s, k) => [s, k, p.values[s] || 0]).filter(([, , v]) => v > 0);
    visible.forEach(([s, k, v], j) => {
      const y0 = y(acc), y1 = y(acc + v);
      const gap = j > 0 ? 2 : 0; // surface gap between stacked segments
      const h = Math.max(0, y0 - y1 - gap);
      const top = j === visible.length - 1;
      if (h > 0) {
        if (top && h > 4) {
          const r = 4, x0 = cx - bw / 2, x1 = cx + bw / 2, yt = y1, yb = y0 - gap;
          root.append(svg('path', { fill: colors[k], d: `M${x0},${yb} V${yt + r} Q${x0},${yt} ${x0 + r},${yt} H${x1 - r} Q${x1},${yt} ${x1},${yt + r} V${yb} Z` }));
        } else {
          root.append(svg('rect', { fill: colors[k], x: cx - bw / 2, y: y1, width: bw, height: h }));
        }
      }
      acc += v;
    });
    if (i % labelEvery === 0) {
      const t = svg('text', { x: cx, y: H - 8, 'text-anchor': 'middle' }); t.textContent = p.label; root.append(t);
    }
    const hit = svg('rect', { class: 'hit', x: L + band * i, y: T, width: band, height: H - T - B });
    hit.addEventListener('pointermove', (e) => tip.show(e, p.tipTitle || p.label,
      [...series.map((s, k) => [s, fmt(p.values[s] || 0), colors[k]]).filter(r => series.length === 1 || r[1] !== fmt(0)),
        ...(series.length > 1 ? [['Total', fmt(totals[i])]] : [])]));
    hit.addEventListener('pointerleave', () => tip.hide());
    root.append(hit);
  });
  wrap.prepend(root);
  return el('div', {}, wrap, legend(series, colors));
}

/** Horizontal bars (stacked when several series). `rows`: [{label, values: {series: n}, note?}]. Zero baseline. */
function barsH(rows, series, { fmt = fmtInt, title = '', labelWidth = 170, width = CW.half } = {}) {
  const wrap = el('div', { class: 'an-chart' });
  const W = Math.max(280, Math.round(width)), rowH = 28, T = 4, B = 20, R = 64;
  const L = Math.min(labelWidth, Math.round(W * 0.42));
  const maxChars = Math.max(10, Math.floor(L / 6.2));
  const H = T + B + rowH * Math.max(rows.length, 1);
  const totals = rows.map(r => series.reduce((s, k) => s + (r.values[k] || 0), 0));
  const { max, step } = niceTicks(Math.max(0, ...totals));
  const x = (v) => L + (W - L - R) * (v / max);
  const root = svg('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': title });
  for (let i = 0; i * step <= max + 1e-9; i++) {
    const v = i * step;
    root.append(svg('line', { class: i ? 'grid' : 'base', x1: x(v), x2: x(v), y1: T, y2: H - B }));
    const t = svg('text', { x: x(v), y: H - 6, 'text-anchor': 'middle' }); t.textContent = fmtCompact(v); root.append(t);
  }
  const colors = series.map((s, i) => seriesColor(i, s));
  const tip = tooltip(wrap);
  const bh = Math.min(18, rowH - 8);
  rows.forEach((r, i) => {
    const cy = T + rowH * i + rowH / 2;
    const name = String(r.label);
    const lab = svg('text', { class: 'an-label', x: L - 8, y: cy + 4, 'text-anchor': 'end' });
    lab.textContent = name.length > maxChars ? `${name.slice(0, maxChars - 1)}…` : name;
    root.append(lab);
    let acc = 0;
    const visible = series.map((s, k) => [s, k, r.values[s] || 0]).filter(([, , v]) => v > 0);
    visible.forEach(([s, k, v], j) => {
      const x0 = x(acc) + (j > 0 ? 2 : 0), x1 = x(acc + v);
      const w = Math.max(0, x1 - x0);
      const end = j === visible.length - 1;
      if (w > 0) {
        if (end && w > 4) {
          const rr = 4, yt = cy - bh / 2, yb = cy + bh / 2;
          root.append(svg('path', { fill: colors[k], d: `M${x0},${yt} H${x1 - rr} Q${x1},${yt} ${x1},${yt + rr} V${yb - rr} Q${x1},${yb} ${x1 - rr},${yb} H${x0} Z` }));
        } else {
          root.append(svg('rect', { fill: colors[k], x: x0, y: cy - bh / 2, width: w, height: bh }));
        }
      }
      acc += v;
    });
    const val = svg('text', { x: x(totals[i]) + 6, y: cy + 4 }); val.textContent = fmt(totals[i]); root.append(val);
    const hit = svg('rect', { class: 'hit', x: 0, y: T + rowH * i, width: W, height: rowH });
    hit.addEventListener('pointermove', (e) => tip.show(e, name,
      [...series.map((s, k) => [s, fmt(r.values[s] || 0), colors[k]]), ...(r.note ? [[r.note, '']] : [])]));
    hit.addEventListener('pointerleave', () => tip.hide());
    root.append(hit);
  });
  wrap.prepend(root);
  return el('div', {}, wrap, legend(series, colors));
}

function card(title, note, ...body) {
  return el('div', { class: 'an-card' }, el('h3', { text: title }), note ? el('p', { class: 'an-note', text: note }) : null, ...body);
}
const empty = (text) => el('p', { class: 'an-empty', text });

/* ---------------- panels ---------------- */

function tiles(d) {
  const k = d.kpis, by = k.by_kind || {};
  const tile = (label, value, sub) => el('div', { class: 'an-tile' }, el('dt', { text: label }), el('dd', {}, value, sub ? el('span', { class: 'an-sub', text: sub }) : null));
  const walk = (by.course_content || 0) + (by.stored_topic || 0);
  const tt = d.test_traffic || {};
  const testNote = tt.rows ? (tt.included ? `includes ${fmtInt(tt.rows)} test rows` : `${fmtInt(tt.rows)} test rows hidden`) : null;
  return el('dl', { class: 'an-tiles' },
    tile('Questions', fmtInt(k.questions), testNote),
    tile('Covered', k.covered_pct == null ? 'n/a' : fmtPct(k.covered_pct), `${fmtInt(k.covered)} answered from slides`),
    tile('Walkthroughs', fmtInt(walk), `${fmtInt(by.stored_topic || 0)} stored answers`),
    tile('FAQ answers', fmtInt(by.faq || 0)),
    tile('Course info', fmtInt(by.course_info || 0)),
    tile('From the web', fmtInt(by.web || 0)),
    tile('Referred to me', fmtInt(by.logistics || 0), 'logistics'),
    tile('Declined', fmtInt(by.not_covered || 0), 'not covered'),
    tile('Avg latency', k.avg_latency_ms == null ? 'n/a' : `${(k.avg_latency_ms / 1000).toFixed(1)} s`, k.median_latency_ms == null ? null : `median ${(k.median_latency_ms / 1000).toFixed(1)} s`),
    tile('Est. spend', fmtUsd(k.est_spend_usd), `models ${fmtUsd(k.spend_parts?.models)}, voice ${fmtUsd(k.spend_parts?.voice)}, embeddings ${fmtUsd(k.spend_parts?.embeddings)}, texts ${fmtUsd(k.spend_parts?.sms || 0)}${k.spend_parts?.web_searches ? `; web searches ${fmtUsd(k.spend_parts.web_searches)} (in models)` : ''}`),
  );
}

function spendPanel(d) {
  const s = d.spend || { series: [], days: [] };
  const note = 'Estimated USD per day, by provider and model (top seven; the rest fold into Other). Includes test traffic: it costs the same.';
  if (!s.series.length) return card('Spend over time', note, empty('No metered usage in this range yet.'));
  const every = s.days.length > 45 ? 14 : s.days.length > 10 ? (CW.full < 500 ? 10 : 5) : 1;
  const points = s.days.map(p => ({ label: shortDay(p.day), tipTitle: shortDay(p.day), values: p.values }));
  const unpriced = d.kpis.unpriced || [];
  return card('Spend over time', note,
    columns(points, s.series, { fmt: fmtUsd, axisFmt: (v) => fmtUsd(v), labelEvery: every, title: 'Estimated spend per day', width: CW.full, height: 220 }),
    unpriced.length ? el('p', { class: 'an-note', text: `No price for: ${unpriced.join(', ')}. Add it to the price table below.` }) : null,
    tableView(['Day', ...s.series, 'Total'], s.days.map(p => [p.day, ...s.series.map(n => fmtUsd(p.values[n] || 0)),
      fmtUsd(s.series.reduce((a, n) => a + (p.values[n] || 0), 0))]), 'Spend per day'));
}

function tokensPanels(d) {
  const llm = d.llm || [];
  const byModel = llm.length
    ? [barsH(llm.map(r => ({ label: `${r.provider} / ${r.model}`, values: { 'Input tokens': r.tokens_in, 'Output tokens': r.tokens_out }, note: r.priced ? `Est. ${fmtUsd(r.cost)}` : 'No price in the table' })),
      ['Input tokens', 'Output tokens'], { fmt: fmtCompact, title: 'Tokens by model', labelWidth: 220 }),
      tableView(['Model', 'Calls', 'Input tokens', 'Output tokens', 'Est. cost'], llm.map(r => [`${r.provider} / ${r.model}`, fmtInt(r.calls), fmtInt(r.tokens_in), fmtInt(r.tokens_out), r.priced ? fmtUsd(r.cost) : 'no price']), 'Tokens by model')]
    : [empty('No model calls recorded in this range.')];
  const pur = d.purposes || [];
  const byPurpose = pur.length
    ? [barsH(pur.map(r => ({ label: PURPOSE_NAMES[r.purpose] || r.purpose, values: { 'Input tokens': r.tokens_in, 'Output tokens': r.tokens_out }, note: `Est. ${fmtUsd(r.cost)}` })),
      ['Input tokens', 'Output tokens'], { fmt: fmtCompact, title: 'Tokens by purpose' }),
      tableView(['Purpose', 'Calls', 'Input tokens', 'Output tokens', 'Est. cost'], pur.map(r => [PURPOSE_NAMES[r.purpose] || r.purpose, fmtInt(r.calls), fmtInt(r.tokens_in), fmtInt(r.tokens_out), fmtUsd(r.cost)]), 'Tokens by purpose')]
    : [empty('No model calls recorded in this range.')];
  return [card('Tokens by model', 'Input and output tokens sent to each model.', ...byModel),
    card('Tokens by purpose', 'What the tokens were for. Tests and evals are their own purposes.', ...byPurpose)];
}

function voicePanels(d) {
  const tts = d.tts || [];
  const voice = tts.length
    ? [barsH(tts.map(r => ({ label: TIER_NAMES[r.tier] || r.tier, values: { Characters: r.chars }, note: `${fmtInt(r.calls)} requests, est. ${fmtUsd(r.cost)}` })), ['Characters'], { fmt: fmtCompact, title: 'Voice characters by tier' }),
      tableView(['Voice tier', 'Requests', 'Characters', 'Est. cost'], tts.map(r => [TIER_NAMES[r.tier] || r.tier, fmtInt(r.calls), fmtInt(r.chars), fmtUsd(r.cost)]), 'Voice characters')]
    : [empty('No live voice requests in this range (stored answers play pre-made audio).')];
  const em = d.embeddings || [];
  const embeds = em.length
    ? [el('div', { class: 'table-wrap' }, el('table', { class: 'data' },
      el('thead', {}, el('tr', {}, ...['Model', 'Calls', 'Tokens', 'Est. cost'].map(h => el('th', { scope: 'col', text: h })))),
      el('tbody', {}, ...em.map(r => el('tr', {}, el('td', { text: `Voyage ${r.model}` }), el('td', { class: 'num', text: fmtInt(r.calls) }),
        el('td', { class: 'num', text: fmtInt(r.tokens) }), el('td', { class: 'num', text: r.priced ? fmtUsd(r.cost) : 'no price' }))))))]
    : [empty('No question embeddings in this range.')];
  return [card('Voice characters', 'Characters sent to a voice, by tier. ElevenLabs costs money; the Microsoft voices are free.', ...voice),
    card('Embeddings', 'Voyage tokens for question embeddings (one per new question).', ...embeds)];
}

function questionsPanels(d) {
  const days = d.questions_by_day || [];
  const every = days.length > 45 ? 21 : days.length > 10 ? (CW.half < 420 ? 10 : 7) : 1;
  const perDay = columns(days.map(p => ({ label: shortDay(p.day), values: { 'Answered from slides': p.covered, Other: p.questions - p.covered } })),
    ['Answered from slides', 'Other'], { labelEvery: every, title: 'Questions per day' });
  const hours = d.questions_by_hour || [];
  const hourLabel = (h) => (h === 0 ? '12a' : h < 12 ? `${h}a` : h === 12 ? '12p' : `${h - 12}p`);
  const perHour = columns(hours.map((v, h) => ({ label: hourLabel(h), tipTitle: `${hourLabel(h)}m hour`, values: { Questions: v } })), ['Questions'],
    { labelEvery: 3, title: 'Questions by hour of day' });
  return [card('Questions per day', 'Every logged question; Other is FAQ, course info, referrals and declines.', perDay,
    tableView(['Day', 'Questions', 'Answered from slides'], days.map(p => [p.day, fmtInt(p.questions), fmtInt(p.covered)]), 'Questions per day')),
  card('Questions by hour', `Hour of day, ${d.hour_timezone === 'America/New_York' ? 'Eastern time' : d.hour_timezone}.`, perHour,
    tableView(['Hour', 'Questions'], hours.map((v, h) => [hourLabel(h), fmtInt(v)]), 'Questions by hour'))];
}

function topicsPanels(d) {
  const t = d.topics || {};
  const courses = t.courses || [];
  const tree = courses.length
    ? el('div', { class: 'an-tree' }, ...courses.map(c => el('details', { open: true },
      el('summary', {}, `${courseCode(c.course)} ${c.title || ''}`, el('span', { class: 'count', text: `${fmtInt(c.questions)} questions` })),
      ...c.sessions.map(s => el('details', {},
        el('summary', {}, `Session ${String(s.session).padStart(2, '0')}: ${s.session_title || ''}`, el('span', { class: 'count', text: fmtInt(s.questions) })),
        el('ul', {}, ...s.slides.map(x => el('li', {}, `Slide ${x.slide_number}${x.title ? `: ${x.title}` : ''}`, el('span', { class: 'count', text: fmtInt(x.questions) })))))))))
    : empty('No answered questions with a top slide in this range yet. Rows logged before the analytics migration have no top slide.');
  const top = t.top || [];
  const topChart = top.length
    ? [barsH(top.map(x => ({ label: `${courseCode(x.course)} s${String(x.session).padStart(2, '0')} #${x.slide_number} ${x.title || ''}`.trim(), values: { Questions: x.questions }, note: x.session_title })), ['Questions'], { title: 'Top topics', labelWidth: 260 }),
      tableView(['Slide', 'Session', 'Questions'], top.map(x => [`${x.slide_id} ${x.title || ''}`.trim(), `${courseCode(x.course)} s${String(x.session).padStart(2, '0')} ${x.session_title || ''}`, fmtInt(x.questions)]), 'Top topics')]
    : [empty('No topics yet.')];
  const gaps = t.gaps || [];
  const gapTable = gaps.length
    ? el('div', { class: 'table-wrap', style: 'max-height:360px;overflow:auto', tabindex: '0', role: 'region', 'aria-label': 'Unanswered questions' },
      el('table', { class: 'data stack' },
        el('thead', {}, el('tr', {}, ...['Question', 'Times', 'Best score', 'Last asked'].map(h => el('th', { scope: 'col', text: h })))),
        el('tbody', {}, ...gaps.map(g => el('tr', {}, el('td', { class: 'full', text: g.question }), el('td', { class: 'num', 'data-label': 'Times', text: fmtInt(g.count) }),
          el('td', { class: 'num', 'data-label': 'Best score', text: g.best_score == null ? '' : g.best_score.toFixed(3) }),
          el('td', { class: 'small muted', 'data-label': 'Last asked', text: fmtWhen(g.last_at) }))))))
    : empty('No declined questions in this range.');
  const faqRows = t.faq || [];
  const faq = faqRows.length
    ? [barsH(faqRows.map(f => ({ label: f.title, values: { Hits: f.count } })), ['Hits'], { title: 'FAQ hits by entry' }),
      tableView(['FAQ entry', 'Hits'], faqRows.map(f => [f.title, fmtInt(f.count)]), 'FAQ hits')]
    : [empty('No FAQ answers recorded in this range.')];
  const src = t.sources || {};
  const srcNames = { chip: 'Suggested chip', typed: 'Typed', follow_up: 'Follow-up chip', unknown: 'Not recorded' };
  const srcRows = Object.entries(srcNames).map(([k, label]) => ({ label, values: { Questions: src[k] || 0 } }));
  return [
    card('Questions by course, session and slide', 'From the top slide of each answered question.', tree),
    card('Top 20 topics', 'Slides that most often came out on top for answered questions.', ...topChart),
    card('Content gaps', 'Questions the twin declined (not covered), most frequent first. Scrubbed text only.', gapTable),
    card('FAQ hits', 'Which of my FAQ answers students reached.', ...faq),
    card('How questions were asked', 'Suggested chip, typed, or a follow-up chip after an answer.',
      barsH(srcRows, ['Questions'], { title: 'How questions were asked' }), tableView(['Source', 'Questions'], srcRows.map(r => [r.label, fmtInt(r.values.Questions)]), 'Question sources')),
  ];
}

function engagementPanel(d) {
  const f = d.funnel || {}, ev = d.events || {};
  const steps = [['Questions asked', f.questions], ['Walkthroughs returned', f.walkthroughs], ['First segment played', f.first_segment_played], ['Walkthrough completed', f.walkthrough_completed]];
  const evNames = { chip_tap: 'Chip taps', question_typed: 'Typed questions', follow_up_tapped: 'Follow-up taps', clip_played: 'Class clip plays', audio_failed: 'Audio failures', course_filter_changed: 'Course filter changes' };
  return [
    card('Engagement funnel', 'Questions, then what the student page reported: the first segment starting and the walkthrough reaching its end.',
      barsH(steps.map(([label, v]) => ({ label, values: { Count: v || 0 } })), ['Count'], { title: 'Engagement funnel' }),
      tableView(['Step', 'Count'], steps.map(([l, v]) => [l, fmtInt(v || 0)]), 'Engagement funnel')),
    card('Page events', 'Counts from the student page. Names only: no text, no ids.',
      el('dl', { class: 'kv' }, ...Object.entries(evNames).map(([k, label]) => el('div', {}, el('dt', { text: label }), el('dd', { text: fmtInt(ev[k] || 0) }))))),
  ];
}

function modelsPanel(d) {
  const m = d.models || {};
  const note = 'Average judge scores (1 to 5) per generator model across every eval run, with the share of answers judged pass.';
  if (!m.available) return card('Average eval scores', note, empty(m.error || 'No eval data yet. Runs from the Evals section show up here.'));
  const dims = m.dimensions || [];
  const dimName = (k) => k.replace(/_/g, ' ');
  return card('Average eval scores', `${note} ${fmtInt(m.runs)} runs.`,
    barsH(m.models.map(x => ({ label: x.model, values: { 'Pass rate %': x.pass_rate == null ? 0 : x.pass_rate * 100 } })), ['Pass rate %'],
      { fmt: (v) => `${Math.round(v)}%`, title: 'Pass rate by model', labelWidth: 240, width: CW.full }),
    el('div', { class: 'table-wrap', style: 'margin-top:.5rem' }, el('table', { class: 'data' },
      el('thead', {}, el('tr', {}, ...['Model', 'Runs', 'Answers', 'Pass rate', ...dims.map(dimName)].map(h => el('th', { scope: 'col', text: h })))),
      el('tbody', {}, ...m.models.map(x => el('tr', {}, el('td', { text: x.model }), el('td', { class: 'num', text: fmtInt(x.runs) }), el('td', { class: 'num', text: fmtInt(x.answers) }),
        el('td', { class: 'num', text: x.pass_rate == null ? '' : `${Math.round(x.pass_rate * 100)}%` }),
        ...dims.map(k => el('td', { class: 'num', text: x.scores?.[k] == null ? '' : x.scores[k].toFixed(2) }))))))));
}

/* ---------------- load ---------------- */

const A = { days: 30, tests: false, loaded: false, loading: false };

/* One load at a time. A range or test-traffic change during a load is not dropped: the reply for the old
   selection is not shown, and the newest selection loads as soon as the running load ends. */
async function load() {
  if (A.loading) { A.again = true; return; }
  A.loading = true;
  A.again = false;
  const want = `${A.days}|${A.tests}`;
  try {
    await loadOnce();
  } finally {
    A.loading = false;
    if (A.again || want !== `${A.days}|${A.tests}`) load();
  }
}

async function loadOnce() {
  const status = $('#an-status');
  say(status, 'Loading...');
  $('#an-export').href = `/api/admin/analytics/export.csv?days=${A.days}`;
  const want = `${A.days}|${A.tests}`;
  const r = await get(`/api/admin/analytics?days=${A.days}&include_tests=${A.tests}`);
  if (want !== `${A.days}|${A.tests}`) return; // the selection changed while this was loading
  if (r.status === 401) { say(status, 'Sign in again to see analytics.', 'err'); return; }
  if (!r.ok || !r.data) { say(status, detail(r, 'Couldn\'t load analytics.'), 'err'); return; }
  A.loaded = true;
  const d = r.data;
  say(status, `${d.range.start} to ${d.range.end} (UTC days)`);
  const bodyWidth = $('#an-body').clientWidth || 640;
  CW.full = bodyWidth;
  CW.half = bodyWidth >= 680 ? (bodyWidth - 20) / 2 : bodyWidth; // .an-grid: two columns from 2 x 320 px + gap
  const warn = d.log_columns_ready ? null : el('p', { class: 'an-warn' },
    'The question log does not have the analytics columns yet, so topics, tokens per question and sources are empty for now. Run the migration block at the end of ',
    el('code', { text: 'supabase/schema.sql' }), ' in the Supabase SQL editor. Rows that exactly match a smoke-check question are treated as likely tests until then.');
  $('#an-body').replaceChildren(...[
    warn,
    tiles(d),
    el('div', { class: 'an-grid' }, spendPanel(d)),
    el('h3', { class: 'an-h', text: 'Tokens and characters' }),
    el('div', { class: 'an-grid' }, ...tokensPanels(d), ...voicePanels(d)),
    el('h3', { class: 'an-h', text: 'Questions' }),
    el('div', { class: 'an-grid' }, ...questionsPanels(d)),
    el('h3', { class: 'an-h', text: 'Topics' }),
    el('div', { class: 'an-grid' }, ...topicsPanels(d)),
    el('h3', { class: 'an-h', text: 'Engagement' }),
    el('div', { class: 'an-grid' }, ...engagementPanel(d)),
    el('h3', { class: 'an-h', text: 'Model performance' }),
    modelsPanel(d),
  ].filter(Boolean));
}

$('#an-range').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-days]');
  if (!b) return;
  A.days = Number(b.dataset.days);
  $('#an-range').querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
  load();
});
$('#an-tests').addEventListener('change', (e) => { A.tests = e.target.checked; load(); });
$('#an-refresh').addEventListener('click', () => { load(); loadLabels(); loadPricing(); });

/* ---------------- label topics ---------------- */

function renderRun(run, open) {
  const head = `${fmtWhen(run.at)}: ${fmtInt(run.questions)} questions, ${run.provider} / ${run.model}`;
  return el('details', { class: 'an-tree', open: open || null },
    el('summary', { text: head }),
    run.save_error ? el('p', { class: 'status-line err', text: run.save_error }) : null,
    el('ol', { class: 'an-themes' }, ...(run.themes || []).map(t => el('li', {},
      el('strong', { text: t.label }), el('span', { class: 'count', text: ` ${fmtInt(t.count)} questions` }),
      t.examples?.length ? el('ul', { class: 'ex' }, ...t.examples.map(q => el('li', { text: q }))) : null))));
}

async function loadLabels(latest) {
  const r = await get('/api/admin/analytics/topics');
  const runs = r.ok ? (r.data?.runs || []) : [];
  const list = latest ? [latest, ...runs.filter(x => x.id !== latest.id)] : runs;
  $('#an-labels').replaceChildren(...(list.length ? list.map((run, i) => renderRun(run, i === 0))
    : [empty(r.ok ? 'No topic runs yet.' : detail(r, 'Couldn\'t load past runs.'))]));
}

$('#an-label-run').addEventListener('click', async () => {
  const st = $('#an-label-status');
  const n = Math.max(10, Math.min(300, Number($('#an-label-n').value) || 200));
  const btn = $('#an-label-run');
  btn.disabled = true;
  say(st, 'Asking the model to group the questions...');
  const r = await get('/api/admin/analytics/topics', { method: 'POST', body: { limit: n } });
  btn.disabled = false;
  if (!r.ok) { say(st, detail(r, 'Labeling failed.'), 'err'); return; }
  say(st, `Done in ${(r.data.latency_ms / 1000).toFixed(1)} s.`, 'ok');
  loadLabels(r.data);
});

/* ---------------- price table ---------------- */

const P = { table: null, plans: [] };

async function loadPricing() {
  const r = await get('/api/admin/analytics/pricing');
  if (!r.ok) { $('#an-pricing').replaceChildren(empty(detail(r, 'Couldn\'t load prices.'))); return; }
  P.table = r.data.pricing; P.plans = r.data.plans || [];
  renderPricing();
}

function num(value, onInput, label) {
  return el('input', { type: 'number', min: '0', step: 'any', value: String(value ?? 0), 'aria-label': label, oninput: (e) => onInput(Number(e.target.value)) });
}

function renderPricing(message) {
  const t = P.table;
  const llmRows = t.llm.map((r, i) => el('tr', {},
    el('td', { text: r.provider }), el('td', { class: 'small', text: r.model }),
    el('td', {}, num(r.in, v => { t.llm[i].in = v; }, `${r.model} input price`)),
    el('td', {}, num(r.out, v => { t.llm[i].out = v; }, `${r.model} output price`)),
    el('td', { class: 'small muted' }, r.source ? el('a', { href: r.source, target: '_blank', rel: 'noopener', text: 'source' }) : 'edited',
      r.checked ? ` ${r.checked}` : '', r.verify ? el('span', { class: 'pill warn', text: 'verify', style: 'margin-left:.3rem' }) : null)));
  const add = el('tr', {},
    el('td', {}, el('select', { id: 'an-new-provider', 'aria-label': 'Provider' }, ...['anthropic', 'openai', 'openrouter'].map(p => el('option', { value: p, text: p })))),
    el('td', {}, el('input', { id: 'an-new-model', type: 'text', placeholder: 'model id', 'aria-label': 'New model id' })),
    el('td', {}, el('input', { id: 'an-new-in', type: 'number', min: '0', step: 'any', 'aria-label': 'New model input price' })),
    el('td', {}, el('input', { id: 'an-new-out', type: 'number', min: '0', step: 'any', 'aria-label': 'New model output price' })),
    el('td', {}, el('button', { type: 'button', class: 'btn btn-small', text: 'Add', onclick: () => {
      const model = $('#an-new-model').value.trim();
      if (!model) return;
      t.llm.push({ provider: $('#an-new-provider').value, model, in: Number($('#an-new-in').value) || 0, out: Number($('#an-new-out').value) || 0, source: null, checked: null, verify: false });
      renderPricing('Added. Not saved yet.');
    } })));
  const embRows = t.embed.map((r, i) => el('tr', {}, el('td', { text: 'voyage' }), el('td', { class: 'small', text: r.model }),
    el('td', {}, num(r.per_mtok, v => { t.embed[i].per_mtok = v; }, `${r.model} price`)), el('td', {}),
    el('td', { class: 'small muted' }, r.source ? el('a', { href: r.source, target: '_blank', rel: 'noopener', text: 'source' }) : 'edited', r.checked ? ` ${r.checked}` : '', r.note ? ` ${r.note}` : '')));
  const tts = t.tts;
  const plan = el('select', { 'aria-label': 'ElevenLabs plan', onchange: (e) => { tts.elevenlabs_plan = e.target.value; renderPricing('Not saved yet.'); } },
    ...P.plans.map(p => el('option', { value: p, text: p, selected: p === tts.elevenlabs_plan || null })));
  const ttsRows = [
    el('tr', {}, el('td', { text: 'ElevenLabs' }), el('td', {}, plan),
      el('td', {}, num(tts.elevenlabs_per_1k_chars[tts.elevenlabs_plan], v => { tts.elevenlabs_per_1k_chars[tts.elevenlabs_plan] = v; }, 'ElevenLabs price per 1K characters')), el('td', {}),
      el('td', { class: 'small muted' }, tts.source ? el('a', { href: tts.source, target: '_blank', rel: 'noopener', text: 'source' }) : '', tts.checked ? ` ${tts.checked}` : '', tts.note ? ` ${tts.note}` : '')),
    el('tr', {}, el('td', { text: 'edge-tts' }), el('td', { class: 'small', text: 'free Microsoft voices' }),
      el('td', {}, num(tts.edge_per_1k_chars, v => { tts.edge_per_1k_chars = v; }, 'edge-tts price per 1K characters')), el('td', {}),
      el('td', { class: 'small muted' }, tts.edge_source ? el('a', { href: tts.edge_source, target: '_blank', rel: 'noopener', text: 'source' }) : '', ' no key, no price')),
  ];
  const sms = t.sms || (t.sms = { per_segment: 0, carrier_fee_per_segment: 0 });
  const smsRows = [
    el('tr', {}, el('td', { text: 'Twilio' }), el('td', { class: 'small', text: 'SMS, per segment (student alerts)' }),
      el('td', {}, num(sms.per_segment, v => { sms.per_segment = v; }, 'Twilio price per SMS segment')),
      el('td', {}, num(sms.carrier_fee_per_segment, v => { sms.carrier_fee_per_segment = v; }, 'Carrier fee per SMS segment')),
      el('td', { class: 'small muted' }, sms.source ? el('a', { href: sms.source, target: '_blank', rel: 'noopener', text: 'source' }) : '', sms.checked ? ` ${sms.checked}` : '', ' base price, then carrier fee', sms.note ? `. ${sms.note}` : '')),
  ];
  const searchRows = (t.web_search || []).map((r, i) => el('tr', {}, el('td', { text: r.provider }), el('td', { class: 'small', text: 'web search' }),
    el('td', {}, num(r.per_1k, v => { t.web_search[i].per_1k = v; }, `${r.provider} web search price per 1,000 searches`)), el('td', {}),
    el('td', { class: 'small muted' }, r.source ? el('a', { href: r.source, target: '_blank', rel: 'noopener', text: 'source' }) : 'edited',
      r.checked ? ` ${r.checked}` : '', r.note ? ` ${r.note}` : '', r.verify ? el('span', { class: 'pill warn', text: 'verify', style: 'margin-left:.3rem' }) : null)));
  const head = (cols) => el('thead', {}, el('tr', {}, ...cols.map(h => el('th', { scope: 'col', text: h }))));
  const st = el('p', { class: 'status-line', role: 'status', text: message || (t.saved ? 'Saved table (defaults fill any model it lacks).' : 'Showing the researched defaults (not saved).') });
  $('#an-pricing').replaceChildren(
    el('h4', { class: 'h-sub', text: 'Models (USD per 1M tokens)' }),
    el('div', { class: 'table-wrap an-price' }, el('table', { class: 'data' }, head(['Provider', 'Model', 'Input', 'Output', 'Source']), el('tbody', {}, ...llmRows, add))),
    el('h4', { class: 'h-sub', text: 'Embeddings (USD per 1M tokens), voice (USD per 1K characters), texts (USD per SMS segment)' }),
    el('div', { class: 'table-wrap an-price' }, el('table', { class: 'data' }, head(['Provider', 'Model or plan', 'Price', 'Carrier fee (texts)', 'Source']), el('tbody', {}, ...embRows, ...ttsRows, ...smsRows))),
    ...(searchRows.length ? [
      el('h4', { class: 'h-sub', text: 'Web searches for answers beyond my slides (USD per 1,000 searches, on top of tokens)' }),
      el('div', { class: 'table-wrap an-price' }, el('table', { class: 'data' }, head(['Provider', 'Tool', 'Price', '', 'Source']), el('tbody', {}, ...searchRows))),
    ] : []),
    el('div', { class: 'actions', style: 'margin-top:.75rem' },
      el('button', { type: 'button', class: 'btn btn-primary', text: 'Save prices', onclick: () => savePricing(false, st) }),
      el('button', { type: 'button', class: 'btn', text: 'Reset to defaults', onclick: () => savePricing(true, st) })),
    st);
}

async function savePricing(reset, st) {
  say(st, 'Saving...');
  const r = await get('/api/admin/analytics/pricing', { method: 'PUT', body: reset ? { reset: true } : { pricing: P.table } });
  if (!r.ok) { say(st, detail(r, 'Couldn\'t save prices.'), 'err'); return; }
  P.table = r.data.pricing;
  renderPricing(reset ? 'Back to the researched defaults.' : 'Saved.');
  load();
}

/* Load once the section scrolls into view (the page signs in first). */
const seen = new IntersectionObserver((entries) => {
  if (entries.some(e => e.isIntersecting) && !$('#a-app').hidden && !A.loaded) {
    load(); loadLabels(); loadPricing();
  }
}, { rootMargin: '200px 0px' });
seen.observe($('#sec-analytics'));

// AI-drawn helper slides: our own fixed templates draw a checked JSON spec (app/helper_slide.py).
// The model never writes markup. Every string from a spec goes in with textContent, never parsed as HTML,
// and no attribute is ever set from spec text, so "<script>" or "javascript:" in a label is only text.
// Code on a code slide is displayed, never run. Shared by the student page and Settings > Draft slides.

export const LABEL = 'AI-drawn slide, not from my course';
const NS = 'http://www.w3.org/2000/svg';
const W = 960;
const H = 540;

// In the page, colors follow the light and dark tokens; a downloaded SVG carries its own light colors.
const PAGE = {
  bg: 'var(--surface)', ink: 'var(--ink)', ink2: 'var(--ink-2)', line: 'var(--line-strong)', accent: 'var(--accent)',
  soft: 'var(--accent-soft)', mark: 'var(--mark)', code: 'var(--code-bg)', warn: 'var(--warn)',
};
const LIGHT = {
  bg: '#ffffff', ink: '#1d2126', ink2: '#464c55', line: '#b9b2a3', accent: '#2b5e6e', soft: '#e0ebee',
  mark: '#fdf1bf', code: '#f9f8f4', warn: '#85620f',
};
const SANS = 'system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif';
const MONO = 'ui-monospace, "SF Mono", Menlo, Consolas, monospace';

let uid = 0;

function node(tag, attrs = {}, text = null) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v));
  if (text != null) n.textContent = String(text);
  return n;
}

function paint(n, styles) {
  n.setAttribute('style', Object.entries(styles).map(([k, v]) => `${k}:${v}`).join(';'));
  return n;
}

/** Greedy word wrap by characters; long words are cut. At most `maxLines`, the last one ends with "...". */
export function wrap(text, width, maxLines = 4) {
  const words = String(text ?? '').split(/\s+/).filter(Boolean);
  const lines = [];
  let cur = '';
  for (let w of words) {
    while (w.length > width) { if (cur) { lines.push(cur); cur = ''; } lines.push(w.slice(0, width)); w = w.slice(width); }
    if (!cur) cur = w;
    else if ((cur + ' ' + w).length <= width) cur += ' ' + w;
    else { lines.push(cur); cur = w; }
  }
  if (cur) lines.push(cur);
  if (lines.length > maxLines) {
    const kept = lines.slice(0, maxLines);
    kept[maxLines - 1] = kept[maxLines - 1].slice(0, Math.max(0, width - 3)) + '...';
    return kept;
  }
  return lines;
}

function textBlock(parent, lines, { x, y, size, lh, fill, weight = 400, anchor = 'start', family = SANS }) {
  const t = paint(node('text', { x, y, 'font-size': size, 'font-weight': weight, 'text-anchor': anchor }),
    { fill, 'font-family': family });
  lines.forEach((line, i) => {
    t.append(node('tspan', { x, dy: i === 0 ? 0 : lh }, line));
  });
  parent.append(t);
  return t;
}

/* ---------------- kinds ---------------- */

function drawBullets(g, spec, c) {
  let y = 150;
  for (const b of spec.bullets || []) {
    const lines = wrap(b, 62, 3);
    g.append(paint(node('circle', { cx: 74, cy: y - 8, r: 6 }), { fill: c.accent }));
    textBlock(g, lines, { x: 96, y, size: 24, lh: 32, fill: c.ink });
    y += lines.length * 32 + 22;
  }
}

function boxEdgePoint(from, to, w, h) {
  const dx = to.x - from.x, dy = to.y - from.y;
  if (!dx && !dy) return { x: from.x, y: from.y };
  const sx = dx ? (w / 2) / Math.abs(dx) : Infinity, sy = dy ? (h / 2) / Math.abs(dy) : Infinity;
  const s = Math.min(sx, sy);
  return { x: from.x + dx * s, y: from.y + dy * s };
}

/** Deterministic positions for flow (one row, or two rows when there are more than 4), cycle, or layers. */
export function layoutDiagram(spec) {
  const nodes = spec.nodes || [];
  const n = nodes.length;
  const pos = {};
  const top = 130, bottom = H - 70;
  if (spec.layout === 'cycle') {
    const cx = W / 2, cy = (top + bottom) / 2, rx = 330, ry = (bottom - top) / 2 - 40;
    nodes.forEach((nd, i) => {
      const a = -Math.PI / 2 + (2 * Math.PI * i) / n;
      pos[nd.id] = { x: cx + rx * Math.cos(a), y: cy + ry * Math.sin(a) };
    });
  } else if (spec.layout === 'layers') {
    const incoming = new Set((spec.edges || []).map(e => e.to));
    const depth = {};
    const queue = nodes.filter(nd => !incoming.has(nd.id)).map(nd => nd.id);
    if (!queue.length && n) queue.push(nodes[0].id);
    queue.forEach(id => { depth[id] = 0; });
    while (queue.length) {
      const id = queue.shift();
      for (const e of spec.edges || []) {
        if (e.from === id && depth[e.to] === undefined) { depth[e.to] = Math.min(depth[id] + 1, 3); queue.push(e.to); }
      }
    }
    nodes.forEach(nd => { if (depth[nd.id] === undefined) depth[nd.id] = 0; });
    const cols = Math.max(...nodes.map(nd => depth[nd.id]), 0) + 1;
    const byCol = Array.from({ length: cols }, () => []);
    nodes.forEach(nd => byCol[depth[nd.id]].push(nd.id));
    byCol.forEach((ids, c) => {
      const x = 40 + 90 + (c + 0.5) * ((W - 260) / cols);
      ids.forEach((id, r) => { pos[id] = { x, y: top + (r + 0.5) * ((bottom - top) / ids.length) }; });
    });
  } else {
    const rows = n > 4 ? 2 : 1;
    const perRow = Math.ceil(n / rows);
    nodes.forEach((nd, i) => {
      const row = Math.floor(i / perRow);
      const col = row % 2 === 0 ? i % perRow : perRow - 1 - (i % perRow); // snake, so the second row flows back
      const x = 60 + (col + 0.5) * ((W - 120) / perRow);
      const y = rows === 1 ? (top + bottom) / 2 : (row === 0 ? top + 90 : bottom - 90);
      pos[nd.id] = { x, y };
    });
  }
  return pos;
}

function drawDiagram(g, spec, c, svg) {
  const pos = layoutDiagram(spec);
  const bw = 176, bh = 72;
  const marker = `hs-arrow-${++uid}`;
  const defs = node('defs');
  const m = node('marker', { id: marker, viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 8, markerHeight: 8, orient: 'auto-start-reverse' });
  m.append(paint(node('path', { d: 'M0,0 L10,5 L0,10 z' }), { fill: c.ink2 }));
  defs.append(m);
  svg.prepend(defs);
  for (const e of spec.edges || []) {
    const a = pos[e.from], b = pos[e.to];
    if (!a || !b) continue;
    const p1 = boxEdgePoint(a, b, bw + 6, bh + 6), p2 = boxEdgePoint(b, a, bw + 10, bh + 10);
    g.append(paint(node('line', { x1: p1.x, y1: p1.y, x2: p2.x, y2: p2.y, 'marker-end': `url(#${marker})` }),
      { stroke: c.ink2, 'stroke-width': 2 }));
    if (e.label) {
      const mx = (p1.x + p2.x) / 2, my = (p1.y + p2.y) / 2;
      const lines = wrap(e.label, 18, 2);
      const tw = Math.max(...lines.map(l => l.length)) * 8.5 + 12;
      g.append(paint(node('rect', { x: mx - tw / 2, y: my - 14, width: tw, height: lines.length * 18 + 8, rx: 4 }), { fill: c.bg, stroke: c.line }));
      textBlock(g, lines, { x: mx, y: my + 2, size: 15, lh: 18, fill: c.ink2, anchor: 'middle' });
    }
  }
  for (const nd of spec.nodes || []) {
    const p = pos[nd.id];
    g.append(paint(node('rect', { x: p.x - bw / 2, y: p.y - bh / 2, width: bw, height: bh, rx: 10 }),
      { fill: c.soft, stroke: c.accent, 'stroke-width': 2 }));
    const lines = wrap(nd.label, 18, 3);
    textBlock(g, lines, { x: p.x, y: p.y - ((lines.length - 1) * 20) / 2 + 6, size: 17, lh: 20, fill: c.ink, weight: 600, anchor: 'middle' });
  }
}

function codeHeight(spec) {
  return Math.max(H, 130 + (spec.lines || []).length * 21 + 50);
}

function drawCode(g, spec, c) {
  const lines = spec.lines || [];
  const lh = 21, x0 = 40, y0 = 120, panelW = 600;
  const marked = new Set((spec.callouts || []).map(co => co.line));
  g.append(paint(node('rect', { x: x0, y: y0 - 10, width: panelW, height: lines.length * lh + 26, rx: 8 }), { fill: c.code, stroke: c.line }));
  lines.forEach((line, i) => {
    const y = y0 + 12 + i * lh;
    if (marked.has(i + 1)) g.append(paint(node('rect', { x: x0 + 1, y: y - 15, width: panelW - 2, height: lh }), { fill: c.mark }));
    textBlock(g, [String(i + 1).padStart(2, ' ')], { x: x0 + 30, y, size: 14, lh, fill: c.ink2, anchor: 'end', family: MONO });
    const t = textBlock(g, [line.slice(0, 64)], { x: x0 + 42, y, size: 15, lh, fill: c.ink, family: MONO });
    t.setAttributeNS('http://www.w3.org/XML/1998/namespace', 'xml:space', 'preserve');
  });
  let cy = y0 + 4;
  for (const co of spec.callouts || []) {
    const ly = y0 + 12 + (co.line - 1) * lh - 5;
    const text = wrap(co.text, 24, 4);
    const boxH = 38 + text.length * 20;
    cy = Math.max(cy, ly - boxH / 2);
    g.append(paint(node('line', { x1: x0 + panelW, y1: ly, x2: x0 + panelW + 26, y2: cy + boxH / 2 }), { stroke: c.warn, 'stroke-width': 2 }));
    g.append(paint(node('rect', { x: x0 + panelW + 26, y: cy, width: 250, height: boxH, rx: 8 }), { fill: c.bg, stroke: c.warn, 'stroke-width': 2 }));
    textBlock(g, [`Line ${co.line}`], { x: x0 + panelW + 38, y: cy + 20, size: 13, lh: 18, fill: c.warn, weight: 700 });
    textBlock(g, text, { x: x0 + panelW + 38, y: cy + 40, size: 15, lh: 20, fill: c.ink });
    cy += boxH + 18 + 20;
  }
}

function drawCompare(g, spec, c) {
  const x0 = 40, colW = (W - 100) / 2, x1 = x0 + colW + 20;
  [[x0, spec.left_title], [x1, spec.right_title]].forEach(([x, title]) => {
    g.append(paint(node('rect', { x, y: 110, width: colW, height: 50, rx: 8 }), { fill: c.accent }));
    textBlock(g, wrap(title, 34, 1), { x: x + colW / 2, y: 143, size: 21, lh: 24, fill: c.bg, weight: 700, anchor: 'middle' });
  });
  let y = 180;
  for (const r of spec.rows || []) {
    const left = wrap(r.left, 40, 3), right = wrap(r.right, 40, 3);
    const h = Math.max(left.length, right.length) * 26 + 24;
    [[x0, left], [x1, right]].forEach(([x, lines]) => {
      g.append(paint(node('rect', { x, y, width: colW, height: h, rx: 8 }), { fill: c.soft, stroke: c.line }));
      textBlock(g, lines, { x: x + 18, y: y + 32, size: 19, lh: 26, fill: c.ink });
    });
    y += h + 14;
  }
}

/**
 * An <svg> for a checked spec. `standalone` (export) uses fixed light colors and adds xmlns; the
 * in-page version follows the theme tokens. The label is drawn into the slide itself.
 */
export function renderSlideSvg(spec, { standalone = false } = {}) {
  const c = standalone ? LIGHT : PAGE;
  const height = spec.kind === 'code' ? codeHeight(spec) : H;
  const svg = node('svg', { viewBox: `0 0 ${W} ${height}`, role: 'img', class: 'helper-svg' });
  if (standalone) { svg.setAttribute('xmlns', NS); svg.setAttribute('width', W); svg.setAttribute('height', height); }
  svg.append(node('title', {}, `${LABEL}: ${spec.title || ''}`));
  svg.append(paint(node('rect', { x: 1, y: 1, width: W - 2, height: height - 2, rx: 14, 'stroke-dasharray': '10 7' }),
    { fill: c.bg, stroke: c.warn, 'stroke-width': 2 }));
  textBlock(svg, wrap(spec.title, 52, 2), { x: 40, y: 64, size: 30, lh: 34, fill: c.ink, weight: 700 });
  const g = node('g');
  svg.append(g);
  if (spec.kind === 'bullets') drawBullets(g, spec, c);
  else if (spec.kind === 'diagram') drawDiagram(g, spec, c, svg);
  else if (spec.kind === 'code') drawCode(g, spec, c);
  else if (spec.kind === 'compare') drawCompare(g, spec, c);
  textBlock(svg, [LABEL], { x: W - 30, y: height - 20, size: 14, lh: 16, fill: c.warn, weight: 700, anchor: 'end' });
  if (standalone && (spec.sources || []).length) {
    textBlock(svg, [`Sources: ${(spec.sources || []).join('  ')}`.slice(0, 140)], { x: 30, y: height - 20, size: 11, lh: 14, fill: c.ink2 });
  }
  return svg;
}

/** The standalone SVG file text: our own drawing, serialized. */
export function toSvgString(spec) {
  return new XMLSerializer().serializeToString(renderSlideSvg(spec, { standalone: true }));
}

/** Markdown for class prep: the label first, then the slide's content. */
export function toMarkdown(spec) {
  const out = [`*${LABEL}*`, '', `## ${spec.title}`, ''];
  if (spec.kind === 'bullets') for (const b of spec.bullets || []) out.push(`- ${b}`);
  if (spec.kind === 'diagram') {
    out.push(`Diagram (${spec.layout}):`, '');
    const label = Object.fromEntries((spec.nodes || []).map(n => [n.id, n.label]));
    for (const n of spec.nodes || []) out.push(`- ${n.label}`);
    if ((spec.edges || []).length) {
      out.push('');
      for (const e of spec.edges) out.push(`- ${label[e.from]} -> ${label[e.to]}${e.label ? ` (${e.label})` : ''}`);
    }
  }
  if (spec.kind === 'code') {
    out.push('```python', ...(spec.lines || []), '```', '');
    for (const co of spec.callouts || []) out.push(`- Line ${co.line}: ${co.text}`);
  }
  if (spec.kind === 'compare') {
    out.push(`| ${spec.left_title} | ${spec.right_title} |`, '| --- | --- |');
    for (const r of spec.rows || []) out.push(`| ${String(r.left).replace(/\|/g, '\\|')} | ${String(r.right).replace(/\|/g, '\\|')} |`);
  }
  if ((spec.sources || []).length) out.push('', 'Sources:', ...spec.sources.map(s => `- ${s}`));
  return out.join('\n') + '\n';
}

/** The page block: label, the drawing, a note for code, and source links (https only, new tab). */
export function renderHelperFigure(spec) {
  const fig = document.createElement('figure');
  fig.className = 'helper-slide';
  const label = document.createElement('p');
  label.className = 'helper-label';
  label.textContent = LABEL;
  fig.append(label, renderSlideSvg(spec));
  const cap = document.createElement('figcaption');
  cap.className = 'helper-cap';
  cap.textContent = spec.kind === 'code'
    ? 'Drawn by AI to help explain the idea. The code is shown only; nothing runs it.'
    : 'Drawn by AI to help explain the idea. Check it against the course slides.';
  fig.append(cap);
  const sources = (spec.sources || []).filter(u => /^https:\/\//.test(String(u)));
  if (sources.length) {
    const ul = document.createElement('ul');
    ul.className = 'helper-sources';
    for (const url of sources) {
      let host = '';
      try { host = new URL(url).hostname; } catch { continue; }
      const a = document.createElement('a');
      a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.textContent = host;
      const li = document.createElement('li');
      li.append(a);
      ul.append(li);
    }
    fig.append(ul);
  }
  return fig;
}

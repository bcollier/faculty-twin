// Read-along: which narration word is being spoken, and which words on the slide to light up.
// docs/SPEC.md, "Read-along narration and slide spotlight". Pure functions, no DOM: app.js draws.
//
// Timings are [[seconds, char_index], ...]: when each spoken word starts and where it starts in the
// narration text (from ElevenLabs alignment, edge-tts word boundaries, or forced alignment for stored
// clips). Without them, estimateTimings() spreads the time over the words by syllables.
// Slide boxes are [[text, x0, y0, x1, y1, line], ...] with 0..1 coordinates from the top left.

/** Words of a narration: [{text, start, end, sentence}], one per run of non-space characters. */
export function tokenize(text) {
  const out = [];
  let sentence = 0;
  for (const m of String(text || '').matchAll(/\S+/g)) {
    out.push({ text: m[0], start: m.index, end: m.index + m[0].length, sentence });
    if (/[.!?]["')\]]*$/.test(m[0])) sentence += 1;
  }
  return out;
}

/** The narration's sentences as [{first, last}] token ranges. */
export function sentences(tokens) {
  const out = [];
  tokens.forEach((t, i) => {
    if (!out[t.sentence]) out[t.sentence] = { first: i, last: i };
    else out[t.sentence].last = i;
  });
  return out.filter(Boolean);
}

/** A rough syllable count: vowel groups, a silent final e dropped, at least one. Matches app/timings.py. */
export function syllables(word) {
  const w = String(word || '').toLowerCase().replace(/[^a-z]/g, '');
  if (!w) return 1;
  let n = (w.match(/[aeiouy]+/g) || []).length;
  if (w.endsWith('e') && !w.endsWith('le') && !w.endsWith('ee') && n > 1) n -= 1;
  return Math.max(1, n);
}

/** Spread `duration` seconds over the words by syllables, with a pause after commas (1) and sentence ends (2). */
export function estimateTimings(text, duration) {
  const tokens = tokenize(text);
  if (!tokens.length || !(duration > 0)) return [];
  const weights = tokens.map(t => {
    const pause = /[.!?]["')\]]*$/.test(t.text) ? 2 : (/[,;:]$/.test(t.text) ? 1 : 0);
    return syllables(t.text) + pause;
  });
  const total = weights.reduce((a, b) => a + b, 0);
  let acc = 0;
  return tokens.map((t, k) => {
    const at = Math.round((duration * acc / total) * 1000) / 1000;
    acc += weights[k];
    return [at, t.start];
  });
}

/** The character index being spoken at `time` (the last word that started), or -1 before the first. */
export function charAt(words, time) {
  let lo = 0, hi = (words?.length || 0) - 1, k = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (words[mid][0] <= time + 0.05) { k = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return k < 0 ? -1 : words[k][1];
}

/** The token that holds character `char`, or -1. */
export function tokenAt(tokens, char) {
  if (char < 0) return -1;
  let lo = 0, hi = tokens.length - 1, i = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (tokens[mid].start <= char) { i = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return i;
}

/** Usable timings: a non-empty list of [seconds, char] pairs, in order. */
export function validTimings(words) {
  if (!Array.isArray(words) || !words.length) return null;
  const ok = words.every((w, i) => Array.isArray(w) && w.length >= 2 && Number.isFinite(w[0]) && Number.isFinite(w[1])
    && (i === 0 || w[0] >= words[i - 1][0]));
  return ok ? words : null;
}

/* ---------------------------------------------------------------- matching narration words to slide words */

export const STOPWORDS = new Set((
  'a about above after again against all also am an and any are as at be because been before being below between ' +
  'both but by can could did do does doing down during each few for from further get gets got had has have having he ' +
  'her here hers him his how i if in into is it its itself just let lets like me more most my no nor not now of off ' +
  'on once one only or other our ours out over own really same see she should so some such than that the their ' +
  'theirs them then there these they this those through to too under until up us very was way we were what when ' +
  'where which while who whom why will with would you your yours going thing things lot think know want need make ' +
  'makes made use used using well right look looks looking here say says said slide slides class today point ' +
  'something example first next back kind actually basically much many every still even mean means part take ' +
  'takes come comes goes give gives show shows call called put puts tell tells try'
).split(/\s+/));

/** Lowercase word without surrounding punctuation or a possessive; inner hyphens and apostrophes stay. */
export function normWord(raw) {
  return String(raw || '').toLowerCase()
    .replace(/[‘’]/g, "'")
    .replace(/^[^a-z0-9]+|[^a-z0-9]+$/g, '')
    .replace(/'s$/, '');
}

/** A light stemmer, the same on both sides: plurals, -ing, -ed, -ly, -er/-est, a final e. */
export function stem(raw) {
  let w = normWord(raw);
  if (w.length <= 3) return w;
  if (w.endsWith('ies') && w.length > 4) w = w.slice(0, -3) + 'y';
  else if (w.endsWith('sses')) w = w.slice(0, -2);
  else if (w.endsWith('es') && /(x|ch|sh|ss)es$/.test(w)) w = w.slice(0, -2);
  else if (w.endsWith('s') && !/(ss|us|is)$/.test(w)) w = w.slice(0, -1);
  if (w.endsWith('ing') && w.length > 5) w = w.slice(0, -3);
  else if (w.endsWith('ed') && w.length > 4) w = w.slice(0, -2);
  else if (w.endsWith('ly') && w.length > 4) w = w.slice(0, -2);
  // then -er/-est, so "clustering" -> "cluster" -> "clust" agrees with "cluster" -> "clust"
  if (w.endsWith('est') && w.length > 6) w = w.slice(0, -3);
  else if (w.endsWith('er') && w.length > 5) w = w.slice(0, -2);
  if (/([b-df-hj-np-tv-z])\1$/.test(w) && !/(ll|ss|zz)$/.test(w)) w = w.slice(0, -1);
  if (w.endsWith('e') && w.length > 4) w = w.slice(0, -1);
  return w;
}

function isContent(word) {
  const n = normWord(word);
  return n.length >= 3 && !STOPWORDS.has(n) && /[a-z]/.test(n);
}

/** Boxes on the same line that touch (or nearly) become one rectangle: [{x0, y0, x1, y1}]. */
export function mergeRects(boxes) {
  const out = [];
  const sorted = [...boxes].sort((a, b) => (a.line - b.line) || (a.x0 - b.x0));
  for (const b of sorted) {
    const last = out[out.length - 1];
    const h = b.y1 - b.y0;
    if (last && last.line === b.line && b.x0 - last.x1 < Math.max(0.02, h * 1.2)) {
      last.x1 = Math.max(last.x1, b.x1);
      last.y0 = Math.min(last.y0, b.y0);
      last.y1 = Math.max(last.y1, b.y1);
    } else {
      out.push({ x0: b.x0, y0: b.y0, x1: b.x1, y1: b.y1, line: b.line });
    }
  }
  return out.map(({ x0, y0, x1, y1 }) => ({ x0, y0, x1, y1 }));
}

/** Slide words from a boxes file, in order: [{text, stem, content, x0, y0, x1, y1, line, i}]. */
export function slideWords(boxes) {
  const words = Array.isArray(boxes?.words) ? boxes.words : [];
  const out = [];
  for (const w of words) {
    if (!Array.isArray(w) || w.length < 6) continue;
    const [text, x0, y0, x1, y1, line] = w;
    if (![x0, y0, x1, y1].every(Number.isFinite) || x1 <= x0 || y1 <= y0) continue;
    out.push({ text: String(text), stem: stem(text), content: isContent(text), x0, y0, x1, y1, line: Number(line) || 0, i: out.length });
  }
  return out;
}

export const MIN_SINGLE_LETTERS = 4;
export const MAX_SINGLE_REPEATS = 3;

/**
 * Which narration tokens light up which slide words.
 * Returns Map(tokenIndex -> {rects, key, length, words}) where `length` is how many narration content words the
 * match covers (1 for a single word) and `key` names the slide region (so it is not lit twice in a row).
 *   - a phrase (two or more content words of one narration sentence in the same order on the slide, slide
 *     stopwords between them ignored) beats a single word, and the phrase's later words do not fire again;
 *   - a single word fires only when it has at least 4 letters and appears at most 3 times on the slide;
 *   - when a word appears more than once, the first occurrence after the last match wins, else the top-most;
 *   - a region lights once per segment, and a word already lit does not fire again on its own.
 */
export function planHighlights(tokens, boxes) {
  const plan = new Map();
  const words = slideWords(boxes);
  const seq = words.filter(w => w.content && w.stem.length >= 3);
  if (!seq.length) return plan;
  const byStem = new Map();
  seq.forEach((w, k) => { if (!byStem.has(w.stem)) byStem.set(w.stem, []); byStem.get(w.stem).push(k); });
  const narr = [];
  tokens.forEach((t, i) => {
    if (isContent(t.text)) {
      narr.push({ i, stem: stem(t.text), sentence: t.sentence ?? 0, letters: normWord(t.text).replace(/[^a-z0-9]/g, '').length });
    }
  });
  let lastPos = -1;
  const keys = new Set(), lit = new Set();
  for (let a = 0; a < narr.length; a++) {
    const cands = byStem.get(narr[a].stem);
    if (!cands) continue;
    let best = null;
    for (const c of cands) {
      let len = 1;
      while (a + len < narr.length && c + len < seq.length && narr[a + len].stem === seq[c + len].stem
        && narr[a + len].sentence === narr[a].sentence) len++; // a phrase never runs past the narrator's sentence
      const after = c > lastPos ? 1 : 0;
      const better = !best || len > best.len || (len === best.len && after > best.after)
        || (len === best.len && after === best.after && seq[c].y0 < seq[best.c].y0 - 0.005);
      if (better) best = { c, len, after };
    }
    const first = seq[best.c], last = seq[best.c + best.len - 1];
    if (best.len < 2) {
      if (narr[a].letters < MIN_SINGLE_LETTERS || cands.length > MAX_SINGLE_REPEATS) continue;
      if (lit.has(first.i)) continue; // a word already lit (alone or in a phrase) does not fire again on its own
    }
    if (keys.has(`${first.i}-${last.i}`)) continue;
    let fresh = false;
    for (let k = first.i; k <= last.i && !fresh; k++) fresh = !lit.has(k);
    if (!fresh) continue; // every word of it is already lit
    // The region covers every slide word from the first to the last matched one (stopwords between included).
    const region = words.slice(first.i, last.i + 1);
    plan.set(narr[a].i, {
      rects: mergeRects(region), key: `${first.i}-${last.i}`, length: best.len,
      words: region.map(w => w.text).join(' '),
    });
    keys.add(`${first.i}-${last.i}`);
    for (let k = first.i; k <= last.i; k++) lit.add(k);
    lastPos = best.c + best.len - 1;
    a += best.len - 1; // the phrase's later words are covered
  }
  return plan;
}

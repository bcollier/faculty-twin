// Student page: read-along. The narration box follows the voice word by word and the slide lights up.
// Part of the student page; app.js has the map.

// Word timings, the syllable estimate, and narration-to-slide word matching (pure functions).
import * as RA from '../readalong.js';
import { COPY } from './copy.js';
import { el } from './helpers.js';
import { ui } from './state.js';
import { captionDurationMs, player } from './player.js';

/* =====================================================================
   Read-along (docs/SPEC.md, "Read-along narration and slide spotlight")
   The narration box shows the whole narration, one span per word. On every animation frame
   while narration runs, the word being spoken is found from the segment's word timings
   (`timings` from the voice service, fetched when the segment shows) or, until they arrive and
   in captions only, from the syllable estimate over the segment's duration. Narration words
   that are also on the slide light up those words on the slide image (`boxes`).
   ===================================================================== */

export const reading = {
  seg: null,           // the segment the box shows
  tokens: [],          // RA.tokenize(narration)
  spans: [],           // one span per token
  sents: [],           // [{first, last}] token ranges
  words: null,         // real timings for the voice speaking now, or null (estimate)
  est: { dur: 0, words: [] },
  current: -1,         // token being spoken
  sentence: -1,
  plan: new Map(),     // token index -> slide region to light up
  fired: -1,           // last token whose highlight was considered
  raf: 0,
  marks: [],           // [{node, key, timer}] lit regions, oldest first
  recent: new Map(),   // region key -> time it was last lit
};
const MAX_MARKS = 2;
const MARK_HOLD_MS = 1500;
const MARK_AGAIN_MS = 3000;
const TIMING_RETRIES = [1200, 2500, 5000, 9000];
const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : 0);

/** Draw the narration for a segment and start loading its timings and the slide's word boxes. */
export function renderNarration(seg) {
  reading.seg = seg;
  reading.tokens = RA.tokenize(seg.narration);
  reading.sents = RA.sentences(reading.tokens);
  reading.spans = reading.tokens.map((t, k) =>
    el('span', { class: 'w', 'data-s': String(t.sentence) }, k < reading.tokens.length - 1 ? `${t.text} ` : t.text));
  ui.caption.replaceChildren(...reading.spans);
  ui.caption.classList.toggle('reduce', reducedMotion());
  ui.caption.scrollTop = 0;
  reading.current = -1;
  reading.sentence = -1;
  reading.words = null;
  reading.est = { dur: 0, words: [] };
  reading.plan = new Map();
  reading.fired = -1;
  hideNarrationNote();
  clearSlideMarks();
  loadTimings(seg);
  loadBoxes(seg);
}

/** Fetch the word timings for the voice speaking this segment (cached on the segment). */
export async function loadTimings(seg, attempt = 0) {
  if (!seg || player.captionsOnly) return;
  const url = player.useFallback ? seg.timings_fallback : seg.timings;
  if (!url) return;
  seg._timings ||= {};
  if (seg._timings[url]) { if (reading.seg === seg) reading.words = seg._timings[url]; return; }
  let data = null;
  try {
    const res = await fetch(url, { credentials: 'same-origin' });
    if (res.ok) data = await res.json();
  } catch { /* the estimate keeps the box moving */ }
  const words = RA.validTimings(data?.words);
  if (words) {
    seg._timings[url] = words;
    const now = player.useFallback ? seg.timings_fallback : seg.timings;
    if (reading.seg === seg && now === url) { reading.words = words; followNarration(); }
    return;
  }
  // Live audio saves its timings when the stream finishes: ask again a little later.
  if (data?.source === 'pending' && attempt < TIMING_RETRIES.length) {
    setTimeout(() => { if (reading.seg === seg) loadTimings(seg, attempt + 1); }, TIMING_RETRIES[attempt]);
  }
}

/** Fetch the slide's word boxes once per segment and plan which narration words light up which slide words. */
async function loadBoxes(seg) {
  if (!seg?.boxes) return;
  if (!seg._boxes) {
    seg._boxes = fetch(seg.boxes).then(r => (r.ok ? r.json() : null)).catch(() => null);
  }
  const boxes = await seg._boxes;
  if (reading.seg !== seg || !boxes) return;
  reading.plan = RA.planHighlights(reading.tokens, boxes);
  reading.fired = reading.current; // words already said do not fire late
}

/** The time and length of the narration now: the audio element, or the captions-only timer. */
function narrationClock() {
  const a = player.current;
  const seg = player.segments[player.index];
  const fallback = seg ? captionDurationMs(seg) / 1000 : 0;
  if (a) {
    const d = num(a.duration);
    return { t: num(a.currentTime), dur: d > 0 ? d : fallback };
  }
  const tm = player.timer;
  if (tm) {
    const running = tm.id != null ? performance.now() - tm.startedAt : 0;
    return { t: (tm.totalMs - tm.remainingMs + running) / 1000, dur: tm.totalMs / 1000 };
  }
  return null;
}

/** Move the narration box (and the slide highlights) to the word being spoken. */
export function followNarration() {
  const seg = player.segments[player.index];
  if (!seg || reading.seg !== seg || player.finished) return;
  const clock = narrationClock();
  if (!clock) return;
  let words = reading.words;
  if (!words) {
    if (Math.abs(reading.est.dur - clock.dur) > 0.05) reading.est = { dur: clock.dur, words: RA.estimateTimings(seg.narration, clock.dur) };
    words = reading.est.words;
  }
  const i = RA.tokenAt(reading.tokens, RA.charAt(words, clock.t));
  if (i !== reading.current) setCurrentWord(i);
}

/** Keep following on every frame while the audio plays (a few times a second is not smooth enough). */
export function followLoop() {
  if (reading.raf) return;
  const step = () => {
    reading.raf = 0;
    followNarration();
    if (player.current && !player.current.paused && player.playing) reading.raf = requestAnimationFrame(step);
  };
  reading.raf = requestAnimationFrame(step);
}

/** Mark word i as being said (the ones before it as said), and light up the slide words it passed. */
function setCurrentWord(i) {
  const { spans } = reading;
  const prev = reading.current;
  const lo = Math.max(0, Math.min(prev, i)), hi = Math.min(spans.length - 1, Math.max(prev, i));
  for (let k = lo; k <= hi; k++) {
    spans[k].classList.toggle('said', k < i);
    spans[k].classList.remove('now');
  }
  reading.current = i;
  if (i >= 0 && spans[i]) spans[i].classList.add('now');
  const sentence = i >= 0 ? reading.tokens[i].sentence : -1;
  if (sentence !== reading.sentence) setSentence(sentence);
  if (i >= 0) keepInView(spans[i]);
  // Light up the slide for every planned word passed since the last frame (a seek backwards resets).
  if (i < reading.fired) reading.fired = i;
  for (let k = reading.fired + 1; k <= i; k++) {
    const match = reading.plan.get(k);
    if (match) lightUp(match);
  }
  reading.fired = Math.max(reading.fired, i);
}

/** A new sentence: tell screen readers (once per sentence), and with reduced motion, mark the sentence. */
function setSentence(n) {
  const prev = reading.sentence;
  reading.sentence = n;
  for (const span of reading.spans) {
    const s = Number(span.getAttribute('data-s'));
    if (s === prev || s === n) span.classList.toggle('in-sentence', s === n);
  }
  const range = reading.sents[n];
  if (range) ui.narrationLive.textContent = reading.tokens.slice(range.first, range.last + 1).map(t => t.text).join(' ');
}

/** Keep the spoken line in the upper middle of the box when the narration is longer than the box. */
function keepInView(span) {
  const box = ui.caption;
  const boxH = num(box.clientHeight), top = num(span.offsetTop) - num(box.offsetTop);
  if (!boxH || num(box.scrollHeight) <= boxH + 2) return;
  const y = top - num(box.scrollTop);
  if (y < boxH * 0.15 || y > boxH * 0.6) {
    box.scrollTo({ top: Math.max(0, top - boxH * 0.3), behavior: reducedMotion() ? 'auto' : 'smooth' });
  }
}

/** A short note in the narration box ("Tap play...", "That's the end..."), also read to screen readers. */
export function showNarrationNote(text) {
  ui.narrationNote.textContent = text;
  ui.narrationNote.hidden = false;
  ui.narrationLive.textContent = text;
}
/** Clear the narration box's note. */
export function hideNarrationNote() {
  if (!ui.narrationNote.hidden) { ui.narrationNote.hidden = true; ui.narrationNote.textContent = ''; }
}

/** After the last segment: every word said, the box says the answer is over. */
export function finishNarration() {
  for (const span of reading.spans) { span.classList.add('said'); span.classList.remove('now', 'in-sentence'); }
  reading.current = reading.spans.length;
  reading.sentence = -1;
  showNarrationNote(COPY.finished);
}

/* ---- the slide lights up: highlighter marks over the words being said ---- */

/** Fit the marks layer to the picture inside the frame (the image keeps its aspect ratio). */
function layoutMarks() {
  const img = ui.slideImg;
  const fw = num(img.clientWidth), fh = num(img.clientHeight);
  const nw = num(img.naturalWidth) || 16, nh = num(img.naturalHeight) || 9;
  if (!fw || !fh) return false;
  const k = Math.min(fw / nw, fh / nh);
  const w = nw * k, h = nh * k;
  const st = ui.slideMarks.style;
  st.left = `${num(img.offsetLeft) + (fw - w) / 2}px`;
  st.top = `${num(img.offsetTop) + (fh - h) / 2}px`;
  st.width = `${w}px`;
  st.height = `${h}px`;
  return true;
}

/**
 * Highlight one planned slide region, unless a clip is showing, the slide is still loading, or the same
 * region was lit in the last MARK_AGAIN_MS. At most MAX_MARKS are lit; the oldest fades first.
 */
function lightUp(match) {
  if (player.inClip || ui.slideImg.hidden || ui.slideImg.classList.contains('is-loading')) return;
  const now = performance.now();
  if (now - (reading.recent.get(match.key) || -Infinity) < MARK_AGAIN_MS) return;
  if (!layoutMarks()) return;
  reading.recent.set(match.key, now);
  while (reading.marks.length >= MAX_MARKS) fadeMark(reading.marks[0]);
  const node = el('div', { class: 'mark-group' });
  for (const r of match.rects) {
    const padX = 0.004, padY = (r.y1 - r.y0) * 0.18;
    const box = el('span', { class: 'slide-mark' });
    box.style.left = `${Math.max(0, r.x0 - padX) * 100}%`;
    box.style.top = `${Math.max(0, r.y0 - padY) * 100}%`;
    box.style.width = `${(Math.min(1, r.x1 + padX) - Math.max(0, r.x0 - padX)) * 100}%`;
    box.style.height = `${(Math.min(1, r.y1 + padY) - Math.max(0, r.y0 - padY)) * 100}%`;
    node.append(box);
  }
  ui.slideMarks.append(node);
  const mark = { node, key: match.key, timer: 0 };
  // A longer phrase stays lit a little longer, so it is still there while the narrator finishes it.
  const hold = Math.min(3500, MARK_HOLD_MS + 300 * Math.max(0, (match.length || 1) - 1));
  mark.timer = setTimeout(() => fadeMark(mark), hold);
  reading.marks.push(mark);
}

/** Fade one highlight out and remove it once the fade is done. */
function fadeMark(mark) {
  clearTimeout(mark.timer);
  reading.marks = reading.marks.filter(m => m !== mark);
  mark.node.classList.add('out');
  setTimeout(() => mark.node.remove(), reducedMotion() ? 0 : 450);
}

/** Remove every highlight now (a new segment, or a clip). */
export function clearSlideMarks() {
  for (const m of reading.marks) clearTimeout(m.timer);
  reading.marks = [];
  reading.recent.clear();
  ui.slideMarks.replaceChildren();
}

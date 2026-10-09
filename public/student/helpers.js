// Student page: small helpers with no page state ($, el, the API call, usage events, formatters).
// Part of the student page; app.js has the map.

import { COURSES } from './copy.js';

/* =====================================================================
   Small helpers
   ===================================================================== */

/** The first element matching `sel`. */
export const $ = (sel, root = document) => root.querySelector(sel);
/**
 * Build an element. `attrs`: `class`, `text` (textContent), `on<event>` (a listener), anything else an
 * attribute (`true` is an empty attribute; `null`/`false` is left out). Never takes HTML.
 */
export const el = (tag, attrs = {}, ...kids) => {
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

/** Stop a media element and drop its source, so the browser stops downloading it. */
export function releaseMedia(media) {
  media.pause();
  media.removeAttribute('src');
  media.load();
}

/** The server could not be reached (or a proxy answered for it): the page shows "unreachable". */
export class NetworkError extends Error {}

/** fetch wrapper: returns {status, ok, data}; throws NetworkError when the server can't be reached. */
export async function api(path, { method = 'GET', body, timeout = 15000 } = {}) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeout);
  let res;
  try {
    res = await fetch(path, {
      method,
      credentials: 'same-origin',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
    });
  } catch (err) {
    throw new NetworkError(err && err.message);
  } finally {
    clearTimeout(t);
  }
  let data = null;
  try { data = await res.json(); } catch { /* empty or non-JSON body */ }
  // Vercel and proxies answer 502/504 when the function is cold or down: treat like unreachable.
  if (res.status === 502 || res.status === 504) throw new NetworkError(`HTTP ${res.status}`);
  return { status: res.status, ok: res.ok, data };
}

/** One usage event for Settings > Analytics: an allowlisted name only (no text, no ids).
    Fire and forget: it never waits, never retries, and never shows an error. */
const EVENTS = new Set(['chip_tap', 'question_typed', 'segment_played', 'walkthrough_completed', 'clip_played',
  'audio_failed', 'follow_up_tapped', 'course_filter_changed']);
/** One usage event (name only), fire and forget. */
export function track(name) {
  if (!EVENTS.has(name)) return;
  try {
    fetch('/api/event', {
      method: 'POST', credentials: 'same-origin', keepalive: true,
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }),
    }).catch(() => {});
  } catch { /* analytics must never break the page */ }
}

/** "70-445 AI for Business Leaders": the course number and its title (the answer's title wins). */
export function courseLabel(code, title) {
  const c = COURSES[String(code)];
  if (c) return `${c.code} ${title || c.title}`;
  return title || String(code || '');
}
/** "70-445" for "70445"; an unknown course is shown as given. */
export function courseCode(code) {
  return COURSES[String(code)]?.code || String(code || '');
}
export function pad2(n) { return String(n).padStart(2, '0'); }
/** "Sep 1, 2026" for "2026-09-01" (read as a local date, so it never shifts a day). */
export function fmtDate(iso) {
  if (!iso) return '';
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}
/** "70-445 · Session 3 · Slide 12": the first line of a slide card. */
export function slideLine(src) {
  return `${courseCode(src.course)} · Session ${src.session} · Slide ${src.slide_number}`;
}
/** Words in a narration (for the captions-only pace). */
export function wordCount(text) { return (String(text || '').match(/\S+/g) || []).length; }

/** /api/topics may return strings or objects; normalize to [{question, course}]. */
export function normalizeTopics(data) {
  const list = Array.isArray(data) ? data : (data?.topics || data?.questions || []);
  return list.map(t => {
    if (typeof t === 'string') return { question: t, course: null };
    return { question: t.question || t.q || t.title || t.text || '', course: t.course ? String(t.course) : null };
  }).filter(t => t.question);
}

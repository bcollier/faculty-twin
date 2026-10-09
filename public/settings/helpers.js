// Settings: small helpers ($, el, the API call, status lines, formatters). Each Settings script keeps its
// own copies on purpose (they load on their own). Part of the Settings page; admin.js has the map.

import { showLogin } from './screens.js';
import { S } from './state.js';

/** The first element matching `s`. */
export const $ = (s, r = document) => r.querySelector(s);
/** Build an element: `class`, `text`, `on<event>` listeners, other keys as attributes. Never takes HTML. */
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
/** The list in a reply: the reply itself when it is an array, else the first array under one of `keys`. */
export const asList = (d, ...keys) => {
  if (Array.isArray(d)) return d;
  for (const k of keys) if (Array.isArray(d?.[k])) return d[k];
  return [];
};
export const pad2 = (n) => String(n).padStart(2, '0');
/** "70-445" for "70445". */
export const courseCode = (c) => (/^\d{5}$/.test(String(c)) ? `${String(c).slice(0, 2)}-${String(c).slice(2)}` : String(c ?? ''));
/** "Oct 8, 3:05 PM" for an ISO time; the input itself when it is not a date. */
export const fmtWhen = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
};
export const fmtNum = (n) => (typeof n === 'number' ? n.toLocaleString('en-US') : String(n ?? ''));
/** "daily_api_calls" as "Daily API calls". */
export const humanize = (k) => String(k).replace(/_/g, ' ').replace(/\b(api|id)\b/gi, s => s.toUpperCase()).replace(/^./, c => c.toUpperCase());

/** The server could not be reached. */
export class NetworkError extends Error {}
/** A 401: api() has already shown the sign-in screen, so callers show nothing more. */
export class AuthError extends Error {}
/* True once this page has been inside the app. Only then does a 401 mean the session ran out;
   on a fresh visit with no admin cookie, a 401 just means "not signed in yet". */
let hadSession = false;
/** Inside the app now: from here on a 401 means the session ran out (enter() calls this). */
export function markSignedIn() { hadSession = true; }

/** fetch wrapper: {status, ok, data}. Throws NetworkError (no reply) or AuthError (signed out, login shown). */
export async function api(path, { method = 'GET', body, timeout = 30000 } = {}) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeout);
  let res;
  try {
    res = await fetch(path, {
      method, credentials: 'same-origin', signal: ctrl.signal,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (e) { throw new NetworkError(e?.message); } finally { clearTimeout(t); }
  let data = null;
  try { data = await res.json(); } catch { /* no body */ }
  if (res.status === 401 && !path.endsWith('/login')) {
    showLogin(hadSession ? 'Your admin session ran out. Sign in again.' : '');
    hadSession = false;
    throw new AuthError();
  }
  return { status: res.status, ok: res.ok, data };
}
/** The server's error message (FastAPI's `detail`, a string or a list of field errors), else `fallback`. */
export const detail = (r, fallback) => {
  const d = r?.data?.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d) && d[0]?.msg) return d.map(x => x.msg).join('; ');
  return fallback || `Request failed (HTTP ${r?.status}).`;
};
/** Write a status line; `kind` is '', 'ok' or 'err'. */
export function say(node, text, kind = '') {
  node.textContent = text || '';
  node.className = `status-line ${kind}`.trim();
}
/** What to show for a failed request: no reply at all, or the error's own message. */
export const errText = (e) => (e instanceof NetworkError ? 'Can\'t reach the server.' : (e?.message || 'Something went wrong.'));
/** Show a failed request on a status line. A sign-out shows nothing: the login screen is already up. */
export function sayError(node, e) {
  if (!(e instanceof AuthError)) say(node, errText(e), 'err');
}
/** Keep what the server says was saved (it returns the full settings after a PUT). */
export function keepSettings(saved) {
  S.settings = { ...S.settings, ...saved };
}

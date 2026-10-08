// Faculty Twin: the word-level diff behind Settings > Prompts' review panel (pure functions, no DOM).
// admin.js loads it with `await import('./prompt-diff.js')` and draws the parts as <ins>/<del> text.
//
// Common prefix and suffix are trimmed, then an LCS runs over the middle. Very large rewrites fall back
// to a line diff, and past that to "all removed, all added", so the browser never builds a huge table.

/** Above this many token pairs the LCS table is too big to build in a browser tab. */
const MAX_TABLE = 1.5e6;

/** Words and the whitespace between them, as separate tokens, so joining them gives the text back. */
function tokens(s) { return String(s).match(/\s+|[^\s]+/g) || []; }

/** Longest-common-subsequence diff of two token lists: [op, token] with op '=', '-' or '+'. */
function lcsDiff(a, b) {
  const n = a.length, m = b.length;
  const dp = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) {
    dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  }
  const out = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { out.push(['=', a[i]]); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) out.push(['-', a[i++]]);
    else out.push(['+', b[j++]]);
  }
  while (i < n) out.push(['-', a[i++]]);
  while (j < m) out.push(['+', b[j++]]);
  return out;
}

/**
 * The diff of two texts as runs: [op, text] with op '=' (kept), '-' (removed) or '+' (added),
 * neighbouring runs of the same op joined. Joining the '=' and '+' runs gives `after` back.
 */
export function diffParts(before, after) {
  let a = tokens(before), b = tokens(after);
  if (a.length * b.length > MAX_TABLE) { a = String(before).split(/(?<=\n)/); b = String(after).split(/(?<=\n)/); }
  let pre = 0;
  while (pre < a.length && pre < b.length && a[pre] === b[pre]) pre++;
  let suf = 0;
  while (suf < a.length - pre && suf < b.length - pre && a[a.length - 1 - suf] === b[b.length - 1 - suf]) suf++;
  const midA = a.slice(pre, a.length - suf), midB = b.slice(pre, b.length - suf);
  const mid = midA.length * midB.length > MAX_TABLE
    ? [...midA.map(t => ['-', t]), ...midB.map(t => ['+', t])]
    : lcsDiff(midA, midB);
  const parts = [];
  const push = (op, t) => { const last = parts[parts.length - 1]; if (last && last[0] === op) last[1] += t; else parts.push([op, t]); };
  a.slice(0, pre).forEach(t => push('=', t));
  mid.forEach(([op, t]) => push(op, t));
  a.slice(a.length - suf).forEach(t => push('=', t));
  return parts;
}

/** How many words the runs with this op hold ('+' for added, '-' for removed). */
export function countWords(parts, op) {
  return parts.filter(p => p[0] === op).reduce((n, p) => n + (p[1].match(/\S+/g) || []).length, 0);
}

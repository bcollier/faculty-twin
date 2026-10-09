// Settings > 6. Prompts: edit a prompt, review the diff, save, reset, restore, test, or run an eval.
// Part of the Settings page; admin.js has the map.

// Settings > Prompts' word-level diff (pure functions).
import * as DIFF from '../prompt-diff.js';
import { $, api, asList, detail, el, fmtNum, fmtWhen, say, sayError } from './helpers.js';

/* ---------------- 6. prompts ---------------- */
// Every model-facing prompt (app/prompts.py). Edits are checked on the server; safety checks stay in code.

const SCORES_DOC = 'https://github.com/bcollier/faculty-twin/blob/main/docs/TESTING_AND_SCORES.md#prompt-changes';
const P = { list: [], maxChars: 12000, sel: null, history: [], review: null };

/** Draw the diff in the review panel (screen readers hear "added" and "removed") and summarize it. */
function renderDiff(before, after) {
  const parts = DIFF.diffParts(before, after);
  const added = DIFF.countWords(parts, '+'), removed = DIFF.countWords(parts, '-');
  const nodes = parts.map(([op, t]) => {
    if (op === '=') return document.createTextNode(t);
    const tag = op === '+' ? 'ins' : 'del';
    return el(tag, {}, el('span', { class: 'visually-hidden', text: op === '+' ? ' [added: ' : ' [removed: ' }), t,
      el('span', { class: 'visually-hidden', text: '] ' }));
  });
  $('#pe-diff').replaceChildren(el('pre', { class: 'diff-text' }, ...nodes));
  const same = before === after;
  const summary = same ? 'No differences.'
    : `${fmtNum(added)} word${added === 1 ? '' : 's'} added, ${fmtNum(removed)} removed. Added text is underlined in green, removed text is struck through in red.`;
  return { added, removed, summary, same };
}

/** The prompt list, then select `keep` (or the one selected, or the first). */
export async function loadPrompts(keep) {
  say($('#prompts-status'), 'Loading prompts...');
  try {
    const r = await api('/api/admin/prompts');
    if (!r.ok) { say($('#prompts-status'), detail(r, 'Couldn\'t load the prompts.'), 'err'); return; }
    P.list = asList(r.data, 'prompts');
    P.maxChars = r.data?.max_chars || 12000;
    say($('#prompts-status'), '');
  } catch (e) { sayError($('#prompts-status'), e); return; }
  const name = keep || P.sel?.name || P.list[0]?.name;
  if (name) selectPrompt(name, { quiet: true });
  else renderPromptList();
}

/** Unsaved edits, Edited, or Default. */
function promptBadge(p) {
  if (P.sel?.name === p.name && isDirty()) return el('span', { class: 'pill warn', text: 'Unsaved edits' });
  return p.is_overridden ? el('span', { class: 'pill info', text: 'Edited' }) : el('span', { class: 'pill off', text: 'Default' });
}

/** The prompt picker, with each prompt's state. */
function renderPromptList() {
  $('#prompt-list').replaceChildren(...P.list.map(p => el('li', {},
    el('button', {
      type: 'button', class: 'prompt-opt', 'aria-pressed': String(P.sel?.name === p.name),
      onclick: () => selectPrompt(p.name),
    },
    el('span', { class: 'm-id', text: p.title }),
    el('span', { class: 'm-meta' }, promptBadge(p), ' ',
      p.used_by === 'evals' ? 'Used in eval runs' : 'Students hear its effect',
      p.updated_at ? ` · saved ${fmtWhen(p.updated_at)}` : '')))));
}

/** The editor holds text that is not saved. */
function isDirty() { return !!P.sel && $('#pe-text').value !== P.sel.current; }

/** Open a prompt in the editor (asks first when that would discard unsaved edits, unless `quiet`). */
function selectPrompt(name, { quiet = false } = {}) {
  if (!quiet && P.sel && P.sel.name !== name && isDirty()
      && !window.confirm('Discard your unsaved edits to this prompt?')) return;
  const p = P.list.find(x => x.name === name);
  if (!p) return;
  P.sel = p;
  $('#prompt-editor').hidden = false;
  $('#pe-title').textContent = p.title;
  $('#pe-desc').textContent = p.description;
  $('#pe-meta').textContent = p.is_overridden
    ? `Edited. Saved ${fmtWhen(p.updated_at)}${p.note ? `: "${p.note}"` : ''}.`
    : 'Using the built-in default.';
  const vars = Object.entries(p.variables || {});
  let help = vars.length
    ? `Placeholders the code fills in: ${vars.map(([k, v]) => `{${k}}${(p.required || []).includes(k) ? ' (required)' : ''} is ${v}`).join('; ')}.`
    : 'This prompt has no placeholders.';
  const words = p.must_mention || [];
  if (words.length) help += ` Keep the word${words.length > 1 ? 's' : ''} ${words.map(w => `"${w}"`).join(', ')}: the code reads the reply by ${words.length > 1 ? 'them' : 'it'}.`;
  $('#pe-vars').textContent = help;
  $('#pe-text').value = p.current;
  $('#pe-test').disabled = !p.testable;
  $('#pe-test-q').disabled = !p.testable;
  $('#pe-test-hint').textContent = p.testable
    ? 'Runs one question through the real path with the text in the editor. Nothing is saved, and the same safety checks apply. Counts against your question limits.'
    : 'This prompt is used in eval runs, not for students. Test it by running an eval after you save.';
  $('#pe-reset').disabled = !p.is_overridden;
  say($('#pe-status'), '');
  say($('#pe-test-status'), '');
  $('#pe-test-output').hidden = true;
  $('#pe-after').hidden = true;
  closeReview();
  updateCount();
  renderPromptList();
  loadHistory();
}

/** "1,234 of 12,000 characters", red when over. */
function updateCount() {
  const n = $('#pe-text').value.length;
  const over = n > P.maxChars;
  const node = $('#pe-count');
  node.textContent = `${fmtNum(n)} of ${fmtNum(P.maxChars)} characters${over ? '. Too long to save.' : ''}`;
  node.classList.toggle('error-text', over);
}

$('#pe-text').addEventListener('input', () => {
  updateCount();
  say($('#pe-status'), isDirty() ? 'Not saved yet.' : '');
  if (P.review?.mode === 'save') closeReview();
  renderPromptList();
});

/** One review panel for every change: it always shows the diff before anything is written. */
function openReview(mode, { before, after, title, confirm, version } = {}) {
  P.review = { mode, before, after, version };
  const d = renderDiff(before, after);
  const writes = mode !== 'compare';
  const nothing = writes && d.same;
  $('#pe-review').hidden = false;
  $('#pe-review-h').textContent = title;
  $('#pe-review-summary').textContent = d.summary;
  $('#pe-note-field').hidden = !writes;
  $('#pe-confirm').hidden = !writes;
  $('#pe-confirm').textContent = confirm || 'Confirm';
  $('#pe-confirm').disabled = nothing;
  $('#pe-cancel').textContent = writes ? 'Cancel' : 'Close';
  $('#pe-note').value = '';
  say($('#pe-review-status'), nothing ? 'Nothing would change, so there is nothing to save.' : '');
  $('#pe-review-h').focus();
  $('#pe-review').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
/** Close the review panel without writing anything. */
function closeReview() { P.review = null; $('#pe-review').hidden = true; }
$('#pe-cancel').addEventListener('click', closeReview);

$('#pe-save').addEventListener('click', () => {
  if (!P.sel) return;
  const text = $('#pe-text').value;
  if (!text.trim()) { say($('#pe-status'), 'The prompt is empty.', 'err'); return; }
  if (text.length > P.maxChars) { say($('#pe-status'), `Keep it under ${fmtNum(P.maxChars)} characters.`, 'err'); return; }
  openReview('save', {
    before: P.sel.current, after: text, confirm: 'Confirm save',
    title: 'Review your changes against the saved version',
  });
});
$('#pe-compare-default').addEventListener('click', () => {
  if (!P.sel) return;
  openReview('compare', { before: P.sel.default, after: $('#pe-text').value, title: 'The editor text compared with the built-in default' });
});
$('#pe-discard').addEventListener('click', () => {
  if (!P.sel) return;
  $('#pe-text').value = P.sel.current;
  updateCount(); closeReview(); renderPromptList();
  say($('#pe-status'), 'Edits discarded.');
});
$('#pe-reset').addEventListener('click', () => {
  if (!P.sel) return;
  openReview('reset', {
    before: P.sel.current, after: P.sel.default, confirm: 'Confirm reset to default',
    title: 'Reset to the built-in default: changes from the saved version',
  });
});

$('#pe-confirm').addEventListener('click', async () => {
  const rv = P.review;
  if (!rv || !P.sel || rv.mode === 'compare') return;
  const name = P.sel.name;
  const note = $('#pe-note').value.trim();
  if (rv.mode === 'save' && !note) { say($('#pe-review-status'), 'Add a short note so the history says why.', 'err'); $('#pe-note').focus(); return; }
  const path = `/api/admin/prompts/${encodeURIComponent(name)}`;
  const req = {
    save: () => api(path, { method: 'PUT', body: { text: rv.after, note } }),
    reset: () => api(`${path}/reset`, { method: 'POST', body: { note: note || undefined } }),
    restore: () => api(`${path}/restore`, { method: 'POST', body: { version: rv.version, note: note || undefined } }),
  }[rv.mode];
  $('#pe-confirm').disabled = true;
  say($('#pe-review-status'), 'Saving...');
  try {
    const r = await req();
    if (!r.ok) { say($('#pe-review-status'), detail(r), 'err'); return; }
    const i = P.list.findIndex(x => x.name === name);
    if (i >= 0 && r.data) P.list[i] = r.data;
    selectPrompt(name, { quiet: true });
    showAfterSave(rv.mode);
  } catch (e) { sayError($('#pe-review-status'), e); }
  finally { $('#pe-confirm').disabled = false; }
});

/** After a save, reset or restore: when it takes effect, and a link to run an eval. */
function showAfterSave(mode) {
  const p = P.sel;
  const what = { save: 'Saved', reset: 'Reset to the default', restore: 'Restored' }[mode] || 'Saved';
  const when = p.used_by === 'evals' ? 'The next eval run uses it.' : 'New questions use it within 30 seconds.';
  $('#pe-after-text').textContent = `${what}. ${when} Run an eval to check that answers still score well.`;
  const evals = document.getElementById('sec-evals');
  const link = $('#pe-run-eval');
  if (evals) { link.href = '#sec-evals'; link.removeAttribute('target'); link.removeAttribute('rel'); }
  else { link.href = SCORES_DOC; link.target = '_blank'; link.rel = 'noopener'; }
  $('#pe-after').hidden = false;
  say($('#pe-status'), '');
}
$('#pe-run-eval').addEventListener('click', (e) => {
  const evals = document.getElementById('sec-evals');
  if (!evals) return; // the link opens Testing and scores in a new tab instead
  e.preventDefault();
  // The Evals section can listen for this to preselect the prompt that just changed.
  document.dispatchEvent(new CustomEvent('ft:run-eval', { detail: { prompt: P.sel?.name, hash: P.sel?.hash } }));
  evals.scrollIntoView({ behavior: 'smooth', block: 'start' });
  if (!evals.hasAttribute('tabindex')) evals.setAttribute('tabindex', '-1');
  evals.focus({ preventScroll: true });
});

$('#pe-test').addEventListener('click', async () => {
  if (!P.sel?.testable) return;
  const text = $('#pe-text').value;
  const question = $('#pe-test-q').value.trim();
  const out = $('#pe-test-output');
  if (!question) { say($('#pe-test-status'), 'Type a test question.', 'err'); return; }
  const btn = $('#pe-test');
  btn.disabled = true;
  out.hidden = true;
  say($('#pe-test-status'), 'Testing the draft...');
  const t0 = performance.now();
  try {
    const r = await api(`/api/admin/prompts/${encodeURIComponent(P.sel.name)}/test`, { method: 'POST', body: { text, question }, timeout: 90000 });
    const ms = r.data?.latency_ms ?? Math.round(performance.now() - t0);
    if (!r.ok) { say($('#pe-test-status'), `${detail(r)} (${fmtNum(ms)} ms)`, 'err'); return; }
    const errs = r.data?.errors || [];
    say($('#pe-test-status'), r.data?.ok
      ? `Worked in ${fmtNum(ms)} ms with ${r.data.provider} / ${r.data.model}.${errs.length ? ' One reply was rejected by the safety checks first.' : ''}`
      : `The safety checks rejected the model's replies, so students would get the fallback. ${fmtNum(ms)} ms.`, r.data?.ok ? 'ok' : 'err');
    out.textContent = JSON.stringify({ ...(r.data?.output || {}), ...(errs.length ? { rejected_replies: errs } : {}) }, null, 2);
    out.hidden = false;
  } catch (e) { sayError($('#pe-test-status'), e); }
  finally { btn.disabled = !P.sel?.testable; }
});

/** The selected prompt's saved versions, newest first. */
async function loadHistory() {
  const name = P.sel?.name;
  if (!name) return;
  say($('#pe-history-status'), 'Loading history...');
  $('#pe-history').replaceChildren();
  try {
    const r = await api(`/api/admin/prompts/${encodeURIComponent(name)}/history`);
    if (P.sel?.name !== name) return;
    if (!r.ok) { say($('#pe-history-status'), detail(r, 'Couldn\'t load the history.'), 'err'); return; }
    P.history = asList(r.data, 'versions');
    say($('#pe-history-status'), P.history.length ? '' : 'No saved versions yet. The built-in default is in use until the first save.');
    renderHistory();
  } catch (e) { sayError($('#pe-history-status'), e); }
}

/** Each saved version with Compare and Restore (both open the review panel first). */
function renderHistory() {
  const live = P.sel?.hash;
  $('#pe-history').replaceChildren(...P.history.map((v, i) => {
    const isLive = i === 0 && v.hash === live;
    const when = fmtWhen(v.saved_at || '');
    return el('li', { class: `history-item${isLive ? ' live' : ''}` },
      el('div', {},
        el('span', { class: 'v-name', text: when }), ' ',
        isLive ? el('span', { class: 'pill ok', text: 'Live now' }) : null, ' ',
        v.reset ? el('span', { class: 'pill off', text: 'Default' }) : null,
        el('div', { class: 'small muted', text: v.note || 'No note.' })),
      el('div', { class: 'actions' },
        el('button', {
          type: 'button', class: 'btn btn-small', 'aria-label': `Compare the version saved ${when} with the saved prompt`,
          onclick: () => openReview('compare', { before: P.sel.current, after: v.text, title: `The version saved ${when} compared with the saved prompt` }),
        }, 'Compare'),
        el('button', {
          type: 'button', class: 'btn btn-small btn-ghost', disabled: isLive, 'aria-label': `Restore the version saved ${when}`,
          onclick: () => openReview('restore', {
            before: P.sel.current, after: v.text, version: v.version, confirm: 'Confirm restore',
            title: `Restore the version saved ${when}: changes from the saved prompt`,
          }),
        }, 'Restore')));
  }));
}

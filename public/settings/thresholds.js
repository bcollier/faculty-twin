// Settings > 4b. Answer thresholds. Part of the Settings page; admin.js has the map.

import { $, api, asList, detail, el, fmtWhen, say, sayError } from './helpers.js';

/* ---------------- 4b. answer thresholds ---------------- */

const TH = {
  slide: { key: 'slide_threshold', input: '#slide-th', now: '#slide-th-now', status: '#slide-th-status', name: 'Slide threshold' },
  info: { key: 'info_threshold', input: '#info-th', now: '#info-th-now', status: '#info-th-status', name: 'Course-info threshold' },
  margin: { key: 'info_margin', input: '#info-margin', now: '#info-margin-now', status: '#info-margin-status', name: 'Course-info margin', form: '#info-margin-form', reset: '#info-margin-reset', min: 0, max: 0.3 },
};
const TH_SOURCE = { code: 'my code default', env: 'the INFO_THRESHOLD environment variable', settings: 'a Settings override' };
const TH_LABEL = { slide_threshold: 'slide threshold', info_threshold: 'course-info threshold', info_margin: 'course-info margin' };
const fmtTh3 = (v) => (typeof v === 'number' ? String(Math.round(v * 1000) / 1000) : 'none');

/** The three threshold fields, where each value comes from, and the last five changes. */
function renderThresholds(d) {
  if (!d) return;
  const s = d.slide || {}, i = d.info || {}, m = d.margin || {};
  $('#slide-th').value = s.value ?? '';
  $('#info-th').value = i.value ?? '';
  $('#info-margin').value = m.value ?? '';
  $('#slide-th-now').textContent = `Slides scoring ${fmtTh3(s.value)} or higher are used. Source: ${TH_SOURCE[s.source] || s.source}` +
    (s.source === 'settings' ? ` (default ${fmtTh3(s.default)}).` : '.');
  $('#info-th-now').textContent = `A Canvas answer needs ${fmtTh3(i.value)} or higher and must beat the best slide by the margin. Source: ${TH_SOURCE[i.source] || i.source}` +
    (i.source === 'settings' ? ` (default ${fmtTh3(i.default)}).` : '.');
  $('#info-margin-now').textContent = `The best Canvas page must score at least ${fmtTh3(m.value)} more than the best slide, so close calls go to the slides. Source: ${m.source === 'env' ? 'the INFO_MARGIN environment variable' : (TH_SOURCE[m.source] || m.source)}` +
    (m.source === 'settings' ? ` (default ${fmtTh3(m.default)}).` : '.');
  $('#slide-th-reset').disabled = s.source !== 'settings';
  $('#info-th-reset').disabled = i.source !== 'settings';
  $('#info-margin-reset').disabled = m.source !== 'settings';
  const hist = asList(d.history).slice(0, 5);
  $('#th-history').replaceChildren(...(hist.length ? hist.map(h => el('li', {
    text: `${fmtWhen(h.at)}, ${h.who || 'admin'}: ${TH_LABEL[h.setting] || h.setting} ${fmtTh3(h.old)} to ${fmtTh3(h.new)}` +
      (h.source && h.source !== 'settings' ? ` (reset to ${TH_SOURCE[h.source] || h.source})` : ''),
  })) : [el('li', { class: 'muted', text: 'No changes yet.' })]));
}

/** Fetch the thresholds and their history. */
export async function loadThresholds() {
  try {
    const r = await api('/api/admin/thresholds');
    if (r.ok) renderThresholds(r.data);
  } catch (e) { /* auth handled in api() */ }
}

/** Save one threshold (null resets it to the default). The range check here matches the server's. */
async function saveThreshold(which, value) {
  const t = TH[which];
  const st = $(t.status);
  const lo = t.min ?? 0.3, hi = t.max ?? 0.9;
  if (value !== null && !(Number.isFinite(value) && value >= lo && value <= hi)) { say(st, `Use a number from ${lo.toFixed(2)} to ${hi.toFixed(2)}.`, 'err'); return; }
  try {
    const r = await api('/api/admin/thresholds', { method: 'PUT', body: { [t.key]: value } });
    if (!r.ok) { say(st, detail(r), 'err'); return; }
    renderThresholds(r.data);
    say(st, value === null ? `${t.name} reset to the default. Run an eval to check it.` : `Saved. Run an eval to check the new value.`, 'ok');
  } catch (ex) { sayError(st, ex); }
}

for (const which of Object.keys(TH)) {
  $(TH[which].form || `#${which}-th-form`).addEventListener('submit', (e) => {
    e.preventDefault();
    const raw = $(TH[which].input).value.trim();
    saveThreshold(which, raw === '' ? NaN : Number(raw));
  });
  $(TH[which].reset || `#${which}-th-reset`).addEventListener('click', () => saveThreshold(which, null));
}

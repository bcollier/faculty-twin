// Settings: the status panel (which keys are set, content loaded, today's counters).
// Part of the Settings page; admin.js has the map.

import { $, el } from './helpers.js';
import { S } from './state.js';
import { renderLimits } from './limits.js';
import { renderToday } from './activity.js';

/* ---------------- keys / status ---------------- */

/** Which keys are set on the server, the limits, and today's counts. */
export function renderStatus(st) {
  S.status = st || {};
  const keys = S.status.keys || {};
  $('#keys-list').replaceChildren(...Object.entries(keys).map(([k, v]) =>
    el('li', {}, el('span', { class: `pill ${v ? 'ok' : 'off'}` }, `${k} ${v ? 'set' : 'not set'}`))));
  renderLimits();
  renderToday();
  updateProviderWarning();
}

/** The server environment variable that holds provider `p`'s key. */
function providerKeyName(p) {
  return { anthropic: 'ANTHROPIC_API_KEY', openai: 'OPENAI_API_KEY', openrouter: 'OPENROUTER_API_KEY' }[p];
}
/** Warn when the chosen provider's key is not set on the server. */
export function updateProviderWarning() {
  const name = providerKeyName($('#provider').value);
  const keys = S.status?.keys || {};
  const warn = $('#provider-key-warn');
  const missing = name && name in keys && !keys[name];
  warn.hidden = !missing;
  warn.textContent = missing ? `${name} isn't set on the server, so this provider will fail until it is.` : '';
}

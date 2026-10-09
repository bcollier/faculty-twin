// Faculty Twin: Settings page (admin). Not linked from the student page.
// Talks only to /api/admin/* with the ft_admin cookie. Keys never reach the browser.
//
// This file starts the page: the dev mock (local hosts with ?mock=1 only), then boot(). The numbered
// sections live in public/settings/, one module each:
//   helpers.js     $, el, the API call (a 401 shows the sign-in screen), status lines, formatters
//   screens.js     boot, sign-in, entering the app (loads every section), the section nav highlight
//   state.js       S, what the sections share: status, settings, models, voices, courses, sources
//   status.js      which keys are set, content loaded, today's counters
//   model.js       1 model: provider, model picker, price check, a test question
//   voice.js       2 voice: the voice groups, previews, the fallback voice
//   courses.js     3 courses and source material: the catalog, sessions, uploads and their status
//   limits.js      4 limits and web answers, 4a open access (no passcode until a set time)
//   thresholds.js  4b answer thresholds
//   activity.js    5 activity: the question log
//   prompts.js     6 prompts: edit, review the diff, save, reset, restore, test, run an eval
// The other Settings sections are their own scripts (admin-analytics.js, admin-evals.js, admin-alerts.js,
// admin-drafts.js); they start when screens.js dispatches `ft-admin-enter`. Prompts' "Run an eval"
// dispatches `ft:run-eval`. Each module registers its own listeners when it loads; every module is
// imported below, before boot().

import { boot } from './settings/screens.js';
import './settings/helpers.js';
import './settings/state.js';
import './settings/status.js';
import './settings/model.js';
import './settings/voice.js';
import './settings/courses.js';
import './settings/limits.js';
import './settings/thresholds.js';
import './settings/activity.js';
import './settings/prompts.js';

const DEV_HOSTS = ['localhost', '127.0.0.1', '[::1]'];
if (DEV_HOSTS.includes(location.hostname) && new URLSearchParams(location.search).get('mock') === '1') {
  // Development only, and only on a local host: on the live site a crafted ?mock=1 link
  // would otherwise show fake "saved" results while nothing is saved.
  await import('./dev/mock.js');
}

boot();

// Faculty Twin: student page.
// Plain ES modules, no framework, no bundler. Talks only to our own backend (/api/...).
// Screens: boot -> (offline | login | app). The app has two views: idle and presenting.
//
// This file starts the page: the dev mock (local hosts with ?mock=1 only), then boot(). The page's code
// lives in public/student/, one module per section:
//   copy.js         constants and the page's copy (Ben's voice, no em dashes)
//   helpers.js      $, el, the API call, usage events, small formatters
//   state.js        the page's elements (ui), its state (app), the remembered course filter
//   screens.js      the boot, offline and passcode screens
//   voice-label.js  which voice speaks, its label, entering the app
//   chips.js        the course filter, suggested questions, the question forms
//   stage.js        stage messages: loading, not covered, FAQ, web answers, helper slides, contacts, errors
//   chat.js         the chat log and the "Slides used in this answer" list
//   asking.js       ask(), showing an answer, fresh links when a signed link expires
//   player.js       the player state machine, the captions-only timer, onClipEnded() (the one advance path)
//   narration.js    read-along: the narration box follows the voice and the slide lights up
//   controls.js     play, previous, next, mute, the progress dots, the keyboard, the phone dock height
//   clips.js        "Watch me explain this in class", and the slide image's load and error
// Each module registers its own listeners when it loads; every module is imported below, before boot().

import { boot } from './student/screens.js';
import './student/copy.js';
import './student/helpers.js';
import './student/state.js';
import './student/voice-label.js';
import './student/chips.js';
import './student/stage.js';
import './student/chat.js';
import './student/asking.js';
import './student/player.js';
import './student/narration.js';
import './student/controls.js';
import './student/clips.js';

const DEV_HOSTS = ['localhost', '127.0.0.1', '[::1]'];
if (DEV_HOSTS.includes(location.hostname) && new URLSearchParams(location.search).get('mock') === '1') {
  // Development only (local hosts only): canned responses that match the API contract. Never loaded otherwise.
  await import('./dev/mock.js');
}

/* =====================================================================
   Go
   ===================================================================== */

boot();

// Student page: the page's elements (ui), its state (app), and the remembered course filter.
// Part of the student page; app.js has the map.

import { COURSES } from './copy.js';
import { $ } from './helpers.js';

/* =====================================================================
   Elements and app state
   ===================================================================== */

export const ui = {
  screens: { boot: $('#screen-boot'), offline: $('#screen-offline'), login: $('#screen-login'), app: $('#screen-app') },
  loginForm: $('#login-form'), passcode: $('#passcode'), loginError: $('#login-error'), loginNote: $('#login-note'),
  idleForm: $('#idle-form'), idleQ: $('#idle-q'), idleCount: $('#idle-count'), idleChips: $('#idle-chips'),
  idleCourse: $('#idle-course'), dockCourse: $('#dock-course'),
  stageMsg: $('#stage-message'), stageSpinner: $('#stage-spinner'), stageTitle: $('#stage-message-title'), stageLabel: $('#stage-message-label'),
  stageText: $('#stage-message-text'), stageActions: $('#stage-message-actions'), stageChips: $('#stage-message-chips'),
  stageExtra: $('#stage-message-extra'), helperSlot: $('#helper-slot'),
  player: $('#player'), media: $('#media'), slideImg: $('#slide-img'), slideAlt: $('#slide-alt'), clipVideo: $('#clip-video'),
  clipBtn: $('#clip-btn'), clipBack: $('#clip-back'), clipNote: $('#clip-note'),
  caption: $('#caption'), btnPrev: $('#btn-prev'), btnPlay: $('#btn-play'), btnNext: $('#btn-next'),
  btnMute: $('#btn-mute'), dots: $('#dots'), audioNote: $('#audio-note'), crossNote: $('#cross-note'),
  codePanel: $('#code-panel'), codeBody: $('#code-body'), codeMarked: $('#code-marked'),
  srcThumb: $('#source-thumb'), srcCourse: $('#src-course'), srcSession: $('#src-session'), srcDate: $('#src-date'), srcSlide: $('#src-slide'),
  dock: $('#dock'), dockToggle: $('#dock-toggle'), log: $('#log'), dockLog: $('#dock-log'),
  followups: $('#followups'), followupChips: $('#followup-chips'), dockForm: $('#dock-form'), dockQ: $('#dock-q'),
  dialog: $('#slide-dialog'), dialogTitle: $('#slide-dialog-h'), dialogImg: $('#slide-dialog-img'),
  contactDialog: $('#contact-dialog'), contactTitle: $('#contact-dialog-h'), contactBody: $('#contact-dialog-body'),
  idleVoiceLabel: $('#idle-voice-label'), idleVoiceText: $('#idle-voice-text'), voiceLabel: $('#voice-label'),
  slideFrame: $('#slide-frame'), slideMarks: $('#slide-marks'),
  narrationVoice: $('#narration-voice'), narrationNote: $('#narration-note'), narrationLive: $('#narration-live'),
};

export const app = {
  course: null,          // '70445' | '45884' | null (all)
  topics: [],
  pendingQuestion: null, // asked when the session ran out; asked again after the passcode
  requestId: 0,          // ignores stale /api/ask responses
  sourceCount: 0,
  sourcesBlock: null,    // the newest answer's "Slides used in this answer" list
  voice: null,           // /api/voice: { kind, label, fallback } for the voice that will speak
};

/* The course filter is remembered on this device (spec). Storage can be blocked, so never rely on it. */
const COURSE_KEY = 'ft.course';
export function loadCourseChoice() {
  try { const v = localStorage.getItem(COURSE_KEY); return v && COURSES[v] ? v : ''; } catch { return ''; }
}
export function saveCourseChoice(v) {
  try { if (v) localStorage.setItem(COURSE_KEY, v); else localStorage.removeItem(COURSE_KEY); } catch { /* ignore */ }
}

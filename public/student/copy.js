// Student page: constants and the page's copy. Part of the student page; app.js has the map.

/* =====================================================================
   Constants and copy (Ben's voice, no em dashes)
   ===================================================================== */

export const COURSES = {
  '70445': { code: '70-445', title: 'AI for Business Leaders' },
  '45884': { code: '45-884', title: 'AI Methods for Social and Visual Data' },
};
export const ASK_TIMEOUT_MS = 45000;
export const MAX_CHIPS = 8;        // suggested questions on the idle screen
export const STAGE_CHIPS = 6;      // suggestions under a stage message (fewer: they share the stage with a card)
export const CAPTION_WORDS_PER_SEC = 2.6; // pace for captions-only mode
export const CAPTION_MIN_SEC = 4;

export const COPY = {
  loading: ['Finding where I cover this in class...', 'Pulling up the slides and writing the walkthrough.'],
  notCovered: ['I don\'t have course material on that.',
    'I only answer from my slides and what I said in class for 70\u2011445 and 45\u2011884.'],
  logistics: ['That one is for me directly.',
    'My twin only explains course material. For meetings, absences, grades or deadlines, please email me or come to office hours.'],
  unreachable: ['I can\'t reach the server right now.', 'It may be waking up. That usually takes a few seconds.'],
  rateLimited: ['That\'s a lot of questions in a short time.',
    'I cap questions per minute and per day to keep costs down. Give it a minute and try again.'],
  notReady: ['This part isn\'t finished yet.',
    'I\'m still writing the code that picks the slides for an answer. Check back soon.'],
  contentLoading: ['My course material is still loading.',
    'The slides and class transcripts are being uploaded. Try again in a few minutes.'],
  badQuestion: ['I couldn\'t use that question.', 'Keep it under 300 characters and about the course.'],
  generic: ['Something went wrong on my end.', 'Try again, or ask a different question.'],
  sessionExpired: 'Your session ran out. Enter the passcode again to keep going.',
  finished: 'That\'s the end of this answer. Ask a follow-up or a new question.',
  tapToPlay: 'Tap play to start the audio.',
  captionsOnly: 'Captions only',
  webLabel: 'Beyond my slides: from the web',
  webSources: 'Sources (open in a new tab)',
  webRelated: 'Closest material in my courses',
};

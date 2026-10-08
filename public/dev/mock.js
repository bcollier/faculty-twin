// DEVELOPMENT ONLY. Loaded by app.js / admin.js only when the URL has ?mock=1.
// Replaces window.fetch (and XMLHttpRequest for upload URLs) with canned responses
// that follow the API contract in docs/SPEC.md and the team brief. Slides, audio and
// the class clip are placeholders generated here in the browser: no course content.
//
// Student page scenarios (type these words in a question):
//   (anything else)  covered answer, 4 segments; part 2 has code, part 3 has a class clip
//   stanley / weather  not covered
//   faqmeet          FAQ answer with a link button (Calendly)
//   faqta            FAQ answer for two courses with two TA contact cards
//   logistics        logistics referral with the Calendly button and a TA card
//   offline          network failure (backend unreachable)
//   busy             429 rate limit
//   unfinished       503 retrieval not implemented yet
//   expire           401, back to the passcode screen
//   noaudio          every audio field is null (captions only from the start)
//   badaudio         audio URLs fail to load (falls back to captions mid-answer)
//   fallbackvoice    the clone's audio fails from part 2 on; a free fallback voice takes over (label changes)
//   slow             4 second wait before the answer
//   badclip          part 3's class clip fails to load (the button should disappear, the slide stays)
//   stale            the first answer's slide links are expired (the page should re-ask once for fresh links)
// URL flags:  &boot=offline  (server unreachable on first load)   &fresh=1  (forget mock login)
// Passcodes:  student "demo", admin "admin".

const params = new URLSearchParams(location.search);
const realFetch = window.fetch.bind(window);
const store = sessionStorage;
if (params.get('fresh') === '1') { store.removeItem('mock.student'); store.removeItem('mock.admin'); }
let bootOffline = params.get('boot') === 'offline';

console.info('[mock] Faculty Twin mock API is active (?mock=1). Nothing here talks to a real backend.');

/* ---------------- placeholder media ---------------- */

const PALETTE = ['#2b5e6e', '#6b4f8a', '#8a5a2b', '#3d6b3a', '#7a3b4b', '#36506e'];
function slideSvg({ course, session, slide, title, kind = 'Slide' }) {
  const color = PALETTE[(Number(session) + slide) % PALETTE.length];
  const bullets = ['Placeholder point one', 'Placeholder point two', 'Placeholder point three'];
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900" viewBox="0 0 1600 900">
  <rect width="1600" height="900" fill="#ffffff"/>
  <rect width="1600" height="16" fill="${color}"/>
  <text x="96" y="150" font-family="Georgia, serif" font-size="68" fill="#1d2126">${title}</text>
  <text x="96" y="215" font-family="Helvetica, Arial, sans-serif" font-size="30" fill="#676d76">${kind} ${slide} · mock deck ${course} s${String(session).padStart(2, '0')}</text>
  ${bullets.map((b, i) => `<circle cx="120" cy="${330 + i * 90}" r="9" fill="${color}"/><text x="150" y="${342 + i * 90}" font-family="Helvetica, Arial, sans-serif" font-size="40" fill="#2e333a">${b}</text>`).join('')}
  <rect x="1060" y="300" width="420" height="300" rx="16" fill="${color}" opacity=".12"/>
  <text x="1270" y="465" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" font-size="34" fill="${color}">figure placeholder</text>
  <text x="1504" y="860" text-anchor="end" font-family="Helvetica, Arial, sans-serif" font-size="24" fill="#9aa0a6">PLACEHOLDER · NOT COURSE CONTENT</text>
</svg>`;
  return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
}

function wavBlob(seconds, freq = 0, volume = 0) {
  const rate = 8000, n = Math.floor(rate * seconds);
  const buf = new ArrayBuffer(44 + n), v = new DataView(buf);
  const str = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  str(0, 'RIFF'); v.setUint32(4, 36 + n, true); str(8, 'WAVE'); str(12, 'fmt ');
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate, true); v.setUint16(32, 1, true); v.setUint16(34, 8, true);
  str(36, 'data'); v.setUint32(40, n, true);
  for (let i = 0; i < n; i++) {
    const env = Math.min(1, i / 400, (n - i) / 400);
    v.setUint8(44 + i, 128 + Math.round(volume * env * 127 * Math.sin(2 * Math.PI * freq * i / rate)));
  }
  return URL.createObjectURL(new Blob([buf], { type: 'audio/wav' }));
}

// One short silent "narration" per segment so the audio element fires real `ended` events.
const audioUrls = [5, 6, 5, 6, 5].map(s => wavBlob(s));

// A 4 second placeholder "class clip" recorded from a canvas (Chrome, Firefox, Safari 14.1+).
let clipPromise = null;
function placeholderClip() {
  if (clipPromise) return clipPromise;
  clipPromise = new Promise((resolve) => {
    try {
      const c = document.createElement('canvas'); c.width = 640; c.height = 360;
      const g = c.getContext('2d');
      const stream = c.captureStream(24);
      const type = ['video/webm;codecs=vp9', 'video/webm', 'video/mp4'].find(t => MediaRecorder.isTypeSupported(t));
      const rec = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
      const chunks = [];
      rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      rec.onstop = () => resolve(URL.createObjectURL(new Blob(chunks, { type: rec.mimeType || 'video/webm' })));
      const t0 = performance.now();
      const draw = () => {
        const t = (performance.now() - t0) / 1000;
        g.fillStyle = '#1d2126'; g.fillRect(0, 0, 640, 360);
        g.fillStyle = '#84b8c8'; g.beginPath(); g.arc(320 + Math.sin(t * 2) * 180, 180, 40, 0, Math.PI * 2); g.fill();
        g.fillStyle = '#e9e7e2'; g.font = '24px Helvetica, Arial'; g.textAlign = 'center';
        g.fillText('Placeholder class clip (mock)', 320, 300);
        g.fillText(t.toFixed(1) + ' s', 320, 60);
        if (t < 4) requestAnimationFrame(draw); else rec.stop();
      };
      rec.start(); draw();
    } catch (err) {
      console.warn('[mock] could not record a placeholder clip', err);
      resolve(null);
    }
  });
  return clipPromise;
}

/* ---------------- canned data ---------------- */

const COURSES = [
  { course: '70445', title: 'AI for Business Leaders', term: 'Fall 2026' },
  { course: '45884', title: 'AI Methods for Social and Visual Data', term: 'Fall 2026 Mini 1' },
];
const sessionDates = (start) => Array.from({ length: 11 }, (_, i) => {
  const d = new Date(start); d.setDate(d.getDate() + Math.floor(i / 2) * 7 + (i % 2) * 2);
  return d.toISOString().slice(0, 10);
});
const DATES = { '70445': sessionDates('2026-08-25T12:00:00'), '45884': sessionDates('2026-08-26T12:00:00') };
const sessionTitle = (course, s) => `Mock session ${s} title`;

const TOPICS = [
  { question: 'Mock question about topic A?', course: '70445' },
  { question: 'Mock question about topic B?', course: '70445' },
  { question: 'How does placeholder method C work?', course: '45884' },
  { question: 'When should I use placeholder D?', course: '45884' },
  { question: 'What is the difference between E and F?', course: null },
  { question: 'Walk me through example G', course: '70445' },
  { question: 'Why does H matter for I?', course: '45884' },
  { question: 'Explain the J tradeoff', course: null },
  { question: 'An extra topic that should be trimmed by the 8 chip cap', course: null },
];

const NARRATION = [
  'This is placeholder narration for the first part of the answer. On this slide I would set up the idea. In class I explained it with an example, and here the mock just fills the space so you can see how captions move along.',
  'Now look at the code panel. The marked lines are the ones that matter. This mock code is not from the course. It only shows how long lines scroll inside the panel instead of stretching the page.',
  'Here there is a clip from class for this slide. Press the button to watch it, and the stage swaps the slide for the video. When it ends, you come back to the slide.',
  'This is the last part of the mock answer. After it ends, the follow-up questions appear in the panel so you can keep going.',
];
const MOCK_CODE = `# placeholder code for the mock (not course material)
import numpy as np

def placeholder_score(values, weights):
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    total = (values * weights).sum() / weights.sum()  # this line is intentionally long so the panel has to scroll sideways on a phone screen
    return round(float(total), 3)

print(placeholder_score([1, 2, 3], [0.2, 0.3, 0.5]))`;

function segment(n, course, session, slide, extra = {}) {
  const sid = `${course}-s${String(session).padStart(2, '0')}-${String(slide).padStart(3, '0')}`;
  return {
    n, slide_id: sid, course,
    course_title: COURSES.find(c => c.course === course).title,
    session, session_title: sessionTitle(course, session), date: DATES[course][session - 1], slide_number: slide,
    image: slideSvg({ course, session, slide, title: `Placeholder slide ${slide}` }),
    narration: NARRATION[n - 1] || NARRATION[0],
    audio: audioUrls[n - 1], voice: { kind: 'clone', label: 'AI voice made from my recordings.' },
    audio_fallback: null, voice_fallback: null, code: null, clip: null, ...extra,
  };
}

const staleServed = new Set();
async function buildAnswer(question, course) {
  const c = course || '70445';
  const clipUrl = await placeholderClip();
  const segments = [
    segment(1, c, 6, 12),
    segment(2, c, 6, 13, { code: { source: MOCK_CODE, mark_lines: [5, 6, 7] } }),
    segment(3, c, 6, 14, { clip: clipUrl ? { url: clipUrl, start: 1834.5, end: 1872.0 } : null }),
    segment(4, c, 7, 3),
  ];
  const q = question.toLowerCase();
  if (q.includes('noaudio')) segments.forEach(s => { s.audio = null; s.voice = null; });
  if (q.includes('fallbackvoice')) segments.forEach((s, i) => {
    s.audio_fallback = s.audio;
    s.voice_fallback = { kind: 'free', label: 'AI voice (a stock voice, not mine).' };
    if (i > 0) s.audio = '/__mock_missing_audio.mp3';
  });
  if (q.includes('badaudio')) segments.forEach((s, i) => { if (i > 0) s.audio = '/__mock_missing_audio.mp3'; });
  if (q.includes('badclip')) segments[2].clip = { url: '/__mock_missing_clip.mp4', start: 1834.5, end: 1872.0 };
  if (q.includes('stale') && !staleServed.has(q)) {
    staleServed.add(q);
    segments.forEach(s => { s.image = '/__mock_expired_slide.webp'; });
  }
  const sources = segments.map(({ slide_id, course, session, date, slide_number, image }) => ({ slide_id, course, session, date, slide_number, image }));
  sources.push({ slide_id: `${c}-s09-021`, course: c, session: 9, date: DATES[c][8], slide_number: 21,
    image: slideSvg({ course: c, session: 9, slide: 21, title: 'Placeholder slide 21' }) });
  return {
    question, covered: true, segments, sources,
    follow_ups: ['Mock follow-up question one?', 'Mock follow-up question two?'],
  };
}

/* admin state */
const admin = {
  settings: { provider: 'anthropic', model: 'claude-sonnet-5-5', voice_id: 'eleven:mock-voice-ben', voice_kind: 'clone',
    voice_label: 'AI voice made from my recordings.', voice_fallback: 'captions', voice_fallback_voice: 'edge:en-US-AndrewMultilingualNeural',
    daily_voice_char_cap: 20000, daily_free_voice_char_cap: 200000, index_version: 7 },
  courses: COURSES.map(c => ({
    ...c,
    sessions: DATES[c.course].map((date, i) => ({
      id: `${c.course}-s${String(i + 1).padStart(2, '0')}`, course: c.course, session: i + 1, date,
      title: sessionTitle(c.course, i + 1), visible: true,
      slides: true, transcript: i < 10, video: i < 9, clips: i < 8 && !(c.course === '45884' && i >= 10), indexed: i < 9,
    })),
  })),
  sources: [],
  nextSourceId: 100,
};
let srcId = 1;
for (const c of admin.courses) for (const s of c.sessions.slice(-3)) {
  for (const kind of ['slides', 'transcript', 'video']) {
    const status = s.session === 11 ? (kind === 'video' ? 'error' : 'processing') : 'ready';
    admin.sources.push({ id: srcId++, course: c.course, session: s.session, kind,
      path: `inbox/${c.course}/s${String(s.session).padStart(2, '0')}/${kind}/mock-${kind}.${kind === 'slides' ? 'pdf' : kind === 'video' ? 'mp4' : 'vtt'}`,
      status, message: status === 'error' ? 'Mock error: video has no audio track' : '', updated_at: new Date(Date.now() - srcId * 3600e3).toISOString() });
  }
}

const STOCK = 'AI voice (a stock voice, not mine).';
const VOICES = {
  groups: [
    { id: 'clone', label: 'My voice clone', cost: 'ElevenLabs: costs credits per character.', costs_money: true,
      student_label: 'AI voice made from my recordings.', voices: [
        { voice_id: 'eleven:mock-voice-ben', name: 'Ben (cloned)', category: 'cloned', preview_url: wavBlob(1.2, 220, 0.25), is_default: true },
      ] },
    { id: 'elevenlabs', label: 'ElevenLabs voices', cost: 'ElevenLabs: costs credits per character.', costs_money: true,
      student_label: STOCK, voices: [
        { voice_id: 'eleven:mock-voice-a', name: 'Stock voice A', category: 'premade', preview_url: wavBlob(1.2, 330, 0.25) },
        { voice_id: 'eleven:mock-voice-b', name: 'Stock voice B', category: 'premade', preview_url: null },
      ] },
    { id: 'free', label: 'Free Microsoft voices', cost: 'Free: no key, no cost (Microsoft neural voices through edge-tts).',
      costs_money: false, student_label: STOCK, voices: [
        { voice_id: 'edge:en-US-AndrewMultilingualNeural', name: 'Andrew', description: 'warm, confident, American male', preview_url: wavBlob(1.2, 262, 0.25) },
        { voice_id: 'edge:en-GB-SoniaNeural', name: 'Sonia', description: 'gentle, British female', preview_url: wavBlob(1.2, 262, 0.25) },
      ] },
  ],
  elevenlabs_error: null,
  free_voice_default: 'edge:en-US-AndrewMultilingualNeural',
};

const MODELS = {
  anthropic: [
    { id: 'claude-sonnet-5-5', name: 'Claude Sonnet 5.5' },
    { id: 'claude-opus-5-5', name: 'Claude Opus 5.5' },
    { id: 'claude-haiku-4-5', name: 'Claude Haiku 4.5' },
  ],
  openai: [
    { id: 'gpt-5', name: 'GPT-5' },
    { id: 'gpt-5-mini', name: 'GPT-5 mini' },
  ],
  openrouter: Array.from({ length: 40 }, (_, i) => {
    const vendors = ['anthropic', 'openai', 'google', 'meta-llama', 'mistralai', 'qwen', 'deepseek', 'x-ai'];
    const v = vendors[i % vendors.length];
    return {
      id: `${v}/mock-model-${i + 1}`, name: `${v} Mock Model ${i + 1}`,
      context_length: [8192, 32768, 128000, 200000, 1000000][i % 5],
      pricing: i % 7 === 0 ? { prompt: '0', completion: '0' } : { prompt: String((i % 5 + 1) * 0.0000005), completion: String((i % 5 + 1) * 0.0000015) },
    };
  }),
};

const MOCK_KINDS = ['course_content', 'course_content', 'stored_topic', 'faq', 'not_covered', 'logistics'];
const LOG = Array.from({ length: 50 }, (_, i) => {
  const kind = MOCK_KINDS[i % MOCK_KINDS.length];
  const searched = kind === 'course_content' || kind === 'not_covered' || kind === 'logistics';
  const model = kind === 'course_content' || kind === 'logistics';
  return {
    at: new Date(Date.now() - i * 17 * 60e3).toISOString(),
    question: kind === 'not_covered' ? 'Mock off-topic question' : `Mock student question number ${50 - i}`,
    covered: kind === 'course_content' || kind === 'stored_topic',
    kind,
    kind_inferred: i > 40,
    top_score: !searched ? null : kind === 'not_covered' ? 0.41 : 0.55 + ((i * 7) % 15) / 100,
    provider: model ? (i % 4 === 0 ? 'openrouter' : 'anthropic') : null,
    model: model ? (i % 4 === 0 ? 'qwen/mock-model-6' : 'claude-sonnet-5-5') : null,
    latency_ms: kind === 'faq' ? 2 : kind === 'stored_topic' ? 140 : 900 + ((i * 337) % 2400),
  };
});

/* ---------------- router ---------------- */

const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const json = (status, body) => new Response(body === undefined ? null : JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
});
const unauthorized = () => json(401, { detail: 'Not signed in' });

async function route(url, method, body) {
  const path = url.pathname;
  const student = store.getItem('mock.student') === '1';
  const isAdmin = store.getItem('mock.admin') === '1';

  if (bootOffline && (path === '/api/health' || path === '/api/topics')) {
    if (path === '/api/topics') bootOffline = false; // the retry button will work
    throw new TypeError('Failed to fetch (mock: server unreachable)');
  }

  /* student */
  if (path === '/api/health') return json(200, { ok: true });
  if (path === '/api/login' && method === 'POST') {
    await sleep(300);
    if (body?.passcode === 'demo') { store.setItem('mock.student', '1'); return json(200, { ok: true }); }
    if (body?.passcode === 'busy') return json(429, { detail: 'Too many attempts' });
    return json(401, { detail: 'Wrong passcode' });
  }
  if (path === '/api/topics') return student ? json(200, TOPICS) : unauthorized();
  if (path === '/api/voice') {
    if (!student) return unauthorized();
    return json(200, { kind: admin.settings.voice_kind || 'clone', label: admin.settings.voice_label ?? 'AI voice made from my recordings.', fallback: null });
  }
  if (path === '/api/courses') {
    if (!student) return unauthorized();
    return json(200, COURSES.map(c => ({ course: c.course, title: c.title,
      sessions: DATES[c.course].map((date, i) => ({ session: i + 1, date, title: sessionTitle(c.course, i + 1) })) })));
  }
  if (path === '/api/ask' && method === 'POST') {
    if (!student) return unauthorized();
    const q = String(body?.question || '').toLowerCase();
    await sleep(q.includes('slow') ? 4000 : 900);
    if (!q.trim() || q.length > 300) return json(400, { detail: 'Question must be 1 to 300 characters' });
    if (q.includes('offline')) throw new TypeError('Failed to fetch (mock: server unreachable)');
    if (q.includes('busy')) return json(429, { detail: 'Rate limit: 5 per minute' });
    if (q.includes('unfinished')) return json(503, { detail: 'Retrieval is not implemented yet' });
    if (q.includes('expire')) { store.removeItem('mock.student'); return unauthorized(); }
    if (q.includes('faqmeet')) {
      return json(200, { question: body.question, covered: false, kind: 'faq', faq_id: 'meeting', title: 'Meeting with me',
        segments: [], sources: [], follow_ups: [],
        message: 'Placeholder FAQ answer (mock). Book a time through the link below.',
        links: [{ label: 'Book a 30-minute meeting', url: 'https://calendly.com/bencollierphd' }], contacts: [] });
    }
    if (q.includes('faqta')) {
      return json(200, { question: body.question, covered: false, kind: 'faq', faq_id: 'reschedule_presentation',
        title: 'Rescheduling a presentation', segments: [], sources: [], follow_ups: [], links: [],
        message: 'For 70-445: placeholder answer one.\n\nFor 45-884: placeholder answer two.',
        contacts: [{ course: '70445', course_label: '70-445', name: '', email: 'ta-one@example.edu' },
                   { course: '45884', course_label: '45-884', name: 'Placeholder TA', email: 'ta-two@example.edu' }] });
    }
    if (q.includes('logistics')) {
      return json(200, { question: body.question, covered: false, kind: 'logistics', segments: [], sources: [], follow_ups: [],
        message: 'mock', links: [{ label: 'Book a 30-minute meeting', url: 'https://calendly.com/bencollierphd' }],
        contacts: [{ course: '70445', course_label: '70-445', name: '', email: 'ta-one@example.edu' }] });
    }
    if (q.includes('stanley') || q.includes('weather')) {
      return json(200, { question: body.question, covered: false, segments: [], sources: [], follow_ups: [] });
    }
    return json(200, await buildAnswer(body.question, body.course));
  }

  /* admin */
  if (path === '/api/admin/login' && method === 'POST') {
    await sleep(300);
    if (body?.passcode === 'admin') { store.setItem('mock.admin', '1'); return json(200, { ok: true }); }
    return json(401, { detail: 'Wrong admin passcode' });
  }
  if (path.startsWith('/api/admin/') && !isAdmin) return unauthorized();

  if (path === '/api/admin/status') {
    return json(200, {
      keys: { ANTHROPIC_API_KEY: true, OPENAI_API_KEY: false, OPENROUTER_API_KEY: true, VOYAGE_API_KEY: true,
        ELEVENLABS_API_KEY: true, SUPABASE_SERVICE_ROLE_KEY: true },
      today: { questions: 23, covered: 20, not_covered: 3, rate_limited: 1, voice_chars: 8420, voice_char_cap: admin.settings.daily_voice_char_cap },
      limits: { per_minute: 5, per_day: 30, question_max_chars: 300, narration_max_words: 110 },
    });
  }
  if (path === '/api/admin/settings') {
    if (method === 'PUT') {
      await sleep(250);
      const next = { ...body };
      if ('student_passcode' in next) {
        if (String(next.student_passcode).length < 6) return json(400, { detail: 'Passcode must be at least 6 characters' });
        delete next.student_passcode;
      }
      Object.assign(admin.settings, next);
    }
    return json(200, admin.settings);
  }
  if (path === '/api/admin/models') {
    await sleep(400);
    const p = url.searchParams.get('provider');
    if (!MODELS[p]) return json(400, { detail: 'Unknown provider' });
    return json(200, { provider: p, models: MODELS[p] });
  }
  if (path === '/api/admin/test' && method === 'POST') {
    const t0 = performance.now();
    await sleep(1200);
    const model = body?.model || admin.settings.model;
    if (String(model).includes('broken')) return json(502, { detail: 'Mock provider error: model not found' });
    return json(200, {
      ok: true, provider: body?.provider || admin.settings.provider, model,
      latency_ms: Math.round(performance.now() - t0),
      narration: { segments: [{ slide_id: '70445-s06-012', narration: NARRATION[0] }, { slide_id: '70445-s06-013', narration: NARRATION[1] }],
        follow_ups: ['Mock follow-up question one?'] },
    });
  }
  if (path === '/api/admin/voices') { await sleep(300); return json(200, VOICES); }
  if (path === '/api/admin/courses') {
    if (method === 'POST') {
      if (!/^\d{5}$/.test(body?.course || '')) return json(400, { detail: 'Course code must be 5 digits, like 70445' });
      if (admin.courses.some(c => c.course === body.course)) return json(409, { detail: 'That course already exists' });
      const c = { course: body.course, title: body.title, term: body.term, sessions: [] };
      admin.courses.push(c);
      return json(201, c);
    }
    return json(200, admin.courses);
  }
  if (path === '/api/admin/sessions' && method === 'POST') {
    const c = admin.courses.find(x => x.course === body?.course);
    if (!c) return json(404, { detail: 'No such course' });
    const n = Number(body.session);
    if (c.sessions.some(s => s.session === n)) return json(409, { detail: 'That session already exists' });
    const s = { id: `${c.course}-s${String(n).padStart(2, '0')}`, course: c.course, session: n, date: body.date, title: body.title,
      visible: true, slides: false, transcript: false, video: false, clips: false, indexed: false };
    c.sessions.push(s); c.sessions.sort((a, b) => a.session - b.session);
    return json(201, s);
  }
  let m;
  if ((m = path.match(/^\/api\/admin\/sessions\/([^/]+)$/)) && method === 'PATCH') {
    const s = admin.courses.flatMap(c => c.sessions).find(x => String(x.id) === decodeURIComponent(m[1]));
    if (!s) return json(404, { detail: 'No such session' });
    Object.assign(s, body);
    return json(200, s);
  }
  if (path === '/api/admin/uploads' && method === 'POST') {
    const id = admin.nextSourceId++;
    const p = `inbox/${body.course}/s${String(body.session).padStart(2, '0')}/${body.kind}/${body.filename}`;
    admin.sources.unshift({ id, course: body.course, session: Number(body.session), kind: body.kind, path: p,
      status: 'uploaded', message: '', updated_at: new Date().toISOString() });
    return json(200, { source_id: id, path: p, upload_url: `/__mock_upload/${id}`, method: 'PUT', headers: { 'x-upsert': 'true' } });
  }
  if (path === '/api/admin/sources') return json(200, admin.sources);
  if ((m = path.match(/^\/api\/admin\/sources\/(\d+)\/(rerun|complete)$/)) && method === 'POST') {
    const s = admin.sources.find(x => x.id === Number(m[1]));
    if (!s) return json(404, { detail: 'No such source' });
    Object.assign(s, { status: 'uploaded', message: '', updated_at: new Date().toISOString() });
    // Pretend the worker on Ben's Mac picks it up.
    setTimeout(() => Object.assign(s, { status: 'processing', updated_at: new Date().toISOString() }), 2500);
    setTimeout(() => Object.assign(s, { status: 'ready', message: 'Mock: 24 slides indexed', updated_at: new Date().toISOString() }), 7000);
    return json(200, s);
  }
  if (path === '/api/admin/log') return json(200, LOG);

  return json(404, { detail: `mock: no route for ${method} ${path}` });
}

window.fetch = async function mockFetch(input, init = {}) {
  const url = new URL(typeof input === 'string' ? input : input.url, location.href);
  if (url.origin !== location.origin || !url.pathname.startsWith('/api/')) return realFetch(input, init);
  const method = (init.method || 'GET').toUpperCase();
  let body = null;
  if (init.body) { try { body = JSON.parse(init.body); } catch { body = null; } }
  return route(url, method, body);
};

/* Uploads go browser -> storage with XMLHttpRequest (for progress). Fake those. */
const RealXHR = window.XMLHttpRequest;
class MockXHR extends RealXHR {
  open(method, url, ...rest) {
    this._mockUpload = String(url).includes('/__mock_upload/');
    if (!this._mockUpload) return super.open(method, url, ...rest);
    this._mockState = 1;
  }
  setRequestHeader(k, v) { if (!this._mockUpload) super.setRequestHeader(k, v); }
  send(body) {
    if (!this._mockUpload) return super.send(body);
    const total = body?.size || 1_000_000;
    const fail = /fail/i.test(body?.name || '');
    let loaded = 0;
    const step = () => {
      loaded = Math.min(total, loaded + total / 12);
      this.upload.dispatchEvent(new ProgressEvent('progress', { lengthComputable: true, loaded, total }));
      if (loaded < total && !(fail && loaded > total / 2)) return setTimeout(step, 150);
      this._mockState = 4;
      this._mockStatus = fail ? 500 : 200;
      this.dispatchEvent(new ProgressEvent('load'));
      this.dispatchEvent(new ProgressEvent('loadend'));
    };
    setTimeout(step, 150);
  }
  abort() { if (!this._mockUpload) return super.abort(); this.dispatchEvent(new ProgressEvent('abort')); }
  get status() { return this._mockUpload ? (this._mockStatus || 0) : super.status; }
  get readyState() { return this._mockUpload ? (this._mockState || 0) : super.readyState; }
  get responseText() { return this._mockUpload ? '{}' : super.responseText; }
}
window.XMLHttpRequest = MockXHR;

// Start recording the placeholder clip early so the first answer isn't delayed by it.
if (!location.pathname.endsWith('admin.html')) placeholderClip();

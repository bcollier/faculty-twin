// Student page: the player (segments, audio, the captions-only timer) and onClipEnded(), the one place
// the walkthrough advances on its own. Part of the student page; app.js has the map.

import { CAPTION_MIN_SEC, CAPTION_WORDS_PER_SEC, COPY } from './copy.js';
import { courseCode, courseLabel, el, fmtDate, pad2, releaseMedia, track, wordCount } from './helpers.js';
import { app, ui } from './state.js';
import { updateVoiceLabel } from './voice-label.js';
import { chip } from './chips.js';
import { crossCourseIntro } from './chat.js';
import {
  finishNarration, followLoop, followNarration, loadTimings, reading, renderNarration, showNarrationNote,
} from './narration.js';
import { buildDots, updateControls, updateDots } from './controls.js';
import { exitClip } from './clips.js';

/* =====================================================================
   Player
   A small state machine:
     player.index      current segment (0-based)
     player.playing    true while narration should be running (audio or caption timer)
     player.muted      audio muted (narration still runs, so timing is unchanged)
     player.captionsOnly  no usable audio: a timer paces each segment instead
     player.finished   the last segment has been played through
   Every "segment is done" signal (audio `ended` OR the captions-only timer)
   calls onClipEnded(). That is the single advance path.
   ===================================================================== */

export const player = {
  answer: null,
  segments: [],
  index: 0,
  playing: false,
  muted: false,
  captionsOnly: false,
  useFallback: false,   // the first voice failed: segments play their audio_fallback (a free voice) instead
  finished: false,
  audio: new Map(),     // index -> HTMLAudioElement (current and preloaded)
  current: null,        // HTMLAudioElement playing now
  timer: null,          // captions-only: { id, startedAt, remainingMs, totalMs, raf, index }
  inClip: false,
  clipFailed: new Set(), // segment indexes whose class clip would not load: the button stays hidden
};

/** Load a new answer and start at segment 1 (or `start`). */
export function loadAnswer(answer, start = 0) {
  stopPlayback();
  player.answer = answer;
  player.segments = answer.segments.slice().sort((a, b) => (a.n ?? 0) - (b.n ?? 0));
  player.clipFailed.clear();
  start = Math.min(Math.max(0, start), player.segments.length - 1);
  player.captionsOnly = player.segments.every(s => !s.audio);
  player.useFallback = false;
  player.finished = false;
  player.sentPlayed = false; // usage events: once per answer
  player.sentCompleted = false;
  ui.audioNote.hidden = !player.captionsOnly;
  ui.crossNote.textContent = crossCourseIntro(answer);
  ui.crossNote.hidden = !ui.crossNote.textContent;
  buildDots();
  ui.stageMsg.hidden = true;
  ui.player.hidden = false;
  showSegment(start);
  player.playing = true;
  playCurrent();
  preloadAudio(start + 1);
}

/** Render segment i on the stage. Does not start or stop narration. */
function showSegment(i) {
  const seg = player.segments[i];
  if (!seg) return;
  exitClip(false);
  stopNarration();
  player.index = i;

  if (ui.slideImg.getAttribute('src') !== seg.image) {
    ui.slideImg.classList.add('is-loading');
    ui.slideImg.src = seg.image;
  }
  ui.slideImg.alt = `Slide ${seg.slide_number} from ${courseCode(seg.course)} session ${seg.session}, ${seg.session_title}`;
  ui.slideAlt.textContent = ui.slideImg.alt;

  renderCode(seg.code);

  ui.clipBtn.hidden = clipButtonHidden(seg, i);
  ui.clipBack.hidden = true;
  ui.clipNote.hidden = true;

  renderNarration(seg);
  renderSourceCard(seg);

  updateDots();
  updateControls();
  updateVoiceLabel();
  app.sourcesBlock?.querySelectorAll('.source-btn').forEach(b => b.setAttribute('aria-current', String(b.dataset.slide === seg.slide_id)));
}

/** The "where this is from" card next to the slide. */
function renderSourceCard(seg) {
  ui.srcThumb.src = seg.image;
  ui.srcThumb.alt = '';
  ui.srcCourse.textContent = courseLabel(seg.course, seg.course_title);
  ui.srcSession.textContent = `Session ${pad2(seg.session)}: ${seg.session_title || ''}`.replace(/: $/, '');
  ui.srcDate.textContent = fmtDate(seg.date);
  ui.srcSlide.textContent = `Slide ${seg.slide_number}`;
}

/** No class clip for this segment, or its clip already failed to load (the button then stays hidden). */
export function clipButtonHidden(seg, i) {
  return !seg?.clip?.url || player.clipFailed.has(i);
}

/** The code panel: the notebook source with the marked lines, scrolled to the first marked line. */
function renderCode(code) {
  const hasCode = !!(code && code.source);
  ui.codePanel.hidden = !hasCode;
  ui.media.classList.toggle('has-code', hasCode);
  if (!hasCode) { ui.codeBody.replaceChildren(); return; }
  const marks = new Set((code.mark_lines || []).map(Number));
  const lines = String(code.source).replace(/\n$/, '').split('\n');
  ui.codeBody.replaceChildren(...lines.map((line, k) =>
    el('span', { class: `ln${marks.has(k + 1) ? ' marked' : ''}`, 'data-n': String(k + 1) }, line || ' ')));
  ui.codeMarked.textContent = marks.size ? `Lines ${[...marks].sort((a, b) => a - b).join(', ')} marked` : '';
  const first = ui.codeBody.querySelector('.marked');
  const pre = ui.codeBody.parentElement;
  pre.scrollTop = first ? Math.max(0, first.offsetTop - pre.clientHeight / 3) : 0;
  pre.scrollLeft = 0;
}

/** The audio element for segment i, created once and reused (this is also the preload). */
function audioFor(i) {
  const seg = player.segments[i];
  const url = seg && (player.useFallback ? seg.audio_fallback : seg.audio);
  if (!url || player.captionsOnly) return null;
  if (player.audio.has(i)) return player.audio.get(i);
  const a = new Audio();
  a.preload = 'auto';
  a.muted = player.muted;
  a.addEventListener('ended', () => { if (a === player.current) onClipEnded(i); });
  a.addEventListener('timeupdate', () => { if (a === player.current) followNarration(); });
  a.addEventListener('playing', () => { if (a === player.current) followLoop(); });
  a.addEventListener('seeked', () => { if (a === player.current) followNarration(); });
  a.addEventListener('error', () => { if (a === player.current) voiceFailed(); });
  a.src = url;
  player.audio.set(i, a);
  return a;
}

/** Start loading segment i's audio in the background. */
function preloadAudio(i) {
  const a = audioFor(i);
  if (a) a.load();
}

/** Start narration for the current segment: its audio, or the captions-only timer. */
function playCurrent() {
  if (player.inClip) return;
  player.playing = true;
  player.finished = false;
  if (!player.sentPlayed) { player.sentPlayed = true; track('segment_played'); }
  const a = audioFor(player.index);
  if (!a) {
    startCaptionTimer();
  } else {
    player.current = a;
    a.muted = player.muted;
    const p = a.play();
    if (p && p.catch) {
      p.catch((err) => {
        if (a !== player.current) return;
        if (err && err.name === 'NotAllowedError') {
          // Autoplay blocked: wait for the student to press play.
          player.playing = false;
          showNarrationNote(COPY.tapToPlay);
          updateControls();
        } else if (err && err.name !== 'AbortError') {
          voiceFailed();
        }
      });
    }
  }
  updateControls();
}

/** Stop narration for the current segment without changing the playing flag. */
function stopNarration() {
  if (player.current) { player.current.pause(); player.current = null; }
  clearCaptionTimer();
}

/** Drop every segment's audio element (current and preloaded). */
function releaseSegmentAudio() {
  for (const a of player.audio.values()) releaseMedia(a);
  player.audio.clear();
}

/** Stop everything (new question, logout). */
export function stopPlayback() {
  stopNarration();
  exitClip(false);
  releaseSegmentAudio();
  player.playing = false;
}

/** Pause the narration (audio or caption timer) where it is. */
export function pausePlayback() {
  player.playing = false;
  if (player.current) player.current.pause();
  if (player.timer) pauseCaptionTimer();
  updateControls();
}

/** Play again from where it paused; after the last segment, from the top. */
function resumePlayback() {
  if (player.inClip) exitClip(false);
  if (player.finished) {           // replay from the top
    showSegment(0);
    playCurrent();
    preloadAudio(1);
    return;
  }
  player.playing = true;
  if (player.current) {
    player.current.play().catch(() => voiceFailed());
  } else if (player.timer) {
    resumeCaptionTimer();
  } else {
    playCurrent();
  }
  updateControls();
}

/** The play/pause button and the space bar. */
export function togglePlay() {
  if (!player.segments.length) return;
  if (player.playing) pausePlayback(); else resumePlayback();
}

/** Prev/next and the dots: show segment i, and keep narrating if we were playing. */
export function jumpTo(i) {
  if (i < 0 || i >= player.segments.length) return;
  const wasPlaying = player.playing || player.finished;
  player.finished = false;
  showSegment(i);
  if (wasPlaying) { playCurrent(); preloadAudio(i + 1); }
  else { player.playing = false; updateControls(); }
}
export function goPrev() { jumpTo(player.index - 1); }
/** Next segment; after the last one, finish the answer. */
export function goNext() {
  if (player.index >= player.segments.length - 1) finishAnswer();
  else jumpTo(player.index + 1);
}

/** After the last segment: stop, show follow-up chips. */
function finishAnswer() {
  stopNarration();
  player.playing = false;
  player.finished = true;
  finishNarration();
  const ups = (player.answer?.follow_ups || []).filter(Boolean);
  ui.followupChips.replaceChildren(...ups.map(q => chip(q, undefined, 'follow_up')));
  if (!player.sentCompleted) { player.sentCompleted = true; track('walkthrough_completed'); }
  ui.followups.hidden = ups.length === 0;
  updateDots();
  updateControls();
}

/** The current voice failed (service down, or today's cap). Switch to the free fallback voice
    if the answer has one, otherwise keep going with captions. */
function voiceFailed() {
  track('audio_failed');
  const seg = player.segments[player.index];
  if (!player.useFallback && seg && seg.audio_fallback) switchToFallbackVoice();
  else fallBackToCaptions();
}

/** Play the rest of this answer with the fallback voice, and change the label to match it. */
function switchToFallbackVoice() {
  player.useFallback = true;
  releaseSegmentAudio();
  player.current = null;
  updateVoiceLabel();
  loadTimings(player.segments[player.index]);
  if (player.playing) { playCurrent(); preloadAudio(player.index + 1); }
  updateControls();
}

/** Audio failed or the daily voice cap was hit: keep going with captions and a timer. */
function fallBackToCaptions() {
  if (player.captionsOnly) return;
  player.captionsOnly = true;
  ui.audioNote.hidden = false;
  releaseSegmentAudio(); // pause every element and drop its source, so nothing keeps downloading
  player.current = null;
  reading.words = null; // the caption timer starts the segment again on the estimate
  if (player.playing) startCaptionTimer();
  updateControls();
  updateVoiceLabel();
}

/* ---- captions-only timer: paces a segment by word count, then calls onClipEnded() ---- */

/** How long a segment shows in captions only: its words at reading pace, at least CAPTION_MIN_SEC. */
export function captionDurationMs(seg) {
  return Math.max(CAPTION_MIN_SEC, wordCount(seg.narration) / CAPTION_WORDS_PER_SEC) * 1000;
}
/** Start pacing the current segment from its beginning. */
function startCaptionTimer() {
  clearCaptionTimer();
  const total = captionDurationMs(player.segments[player.index]);
  player.timer = { id: null, raf: null, startedAt: 0, remainingMs: total, totalMs: total, index: player.index };
  resumeCaptionTimer();
}
/** Run the timer for what is left of the segment, and follow the narration on every frame meanwhile. */
function resumeCaptionTimer() {
  const t = player.timer;
  if (!t) return;
  t.startedAt = performance.now();
  t.id = setTimeout(() => {
    cancelAnimationFrame(t.raf);
    player.timer = null;
    onClipEnded(t.index);
  }, t.remainingMs);
  const tick = () => {
    const elapsed = t.totalMs - t.remainingMs + (performance.now() - t.startedAt);
    if (elapsed <= t.totalMs) followNarration();
    t.raf = requestAnimationFrame(tick);
  };
  t.raf = requestAnimationFrame(tick);
}
/** Stop the clock and remember how much of the segment is left. */
function pauseCaptionTimer() {
  const t = player.timer;
  if (!t || t.id == null) return;
  clearTimeout(t.id); cancelAnimationFrame(t.raf);
  t.id = null;
  t.remainingMs = Math.max(0, t.remainingMs - (performance.now() - t.startedAt));
}
/** Drop the timer entirely (a new segment, or audio took over). */
function clearCaptionTimer() {
  const t = player.timer;
  if (t) { clearTimeout(t.id); cancelAnimationFrame(t.raf); }
  player.timer = null;
}

/**
 * Runs when a segment's narration finishes: the audio element's `ended`
 * event, or the captions-only timer running out. This is the only place
 * the walkthrough advances on its own: to the next segment, or after the
 * last one to finishAnswer().
 *
 * `endedIndex` is the segment whose narration ended. A late signal is
 * ignored: one for a segment that is no longer on screen, one after the
 * answer finished, and one while the class clip plays (the walkthrough
 * stays paused until the student presses play).
 *
 * First written by hand by Ben for the course assignment.
 */
function onClipEnded(endedIndex = player.index) {
  if (!player.playing || player.finished || player.inClip) return;
  if (endedIndex !== player.index) return;

  const next = player.index + 1;

  if (next >= player.segments.length) {
    finishAnswer();
    return;
  }

  showSegment(next);
  playCurrent();
  preloadAudio(next + 1);
}

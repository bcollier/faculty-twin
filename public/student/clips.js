// Student page: "Watch me explain this in class" (the class clip in place of the slide), and the slide
// image's load and error. Part of the student page; app.js has the map.

import { fmtDate, releaseMedia, track } from './helpers.js';
import { ui } from './state.js';
import { refreshExpiredLinks } from './asking.js';
import { clipButtonHidden, pausePlayback, player } from './player.js';
import { clearSlideMarks } from './narration.js';
import { updateControls } from './controls.js';

/* ---- "Watch me explain this in class" ---- */

/** Show the class recording for this slide in place of the slide; the narration pauses. */
function enterClip() {
  const seg = player.segments[player.index];
  if (!seg?.clip?.url) return;
  track('clip_played');
  if (player.playing) pausePlayback();
  player.inClip = true;
  clearSlideMarks();
  ui.slideImg.hidden = true;
  ui.clipVideo.hidden = false;
  ui.clipVideo.muted = player.muted;
  ui.clipVideo.src = seg.clip.url;
  ui.clipBtn.hidden = true;
  ui.clipBack.hidden = false;
  ui.clipNote.textContent = `Recorded in class on ${fmtDate(seg.date)} (my real voice, not the AI voice).`;
  ui.clipNote.hidden = false;
  ui.clipVideo.play().catch(() => {});
  ui.clipBack.focus();
}

/** Back to the slide. The walkthrough stays paused until the student presses play (spec). */
export function exitClip(returnFocus = true) {
  if (!player.inClip) return;
  player.inClip = false;
  releaseMedia(ui.clipVideo);
  ui.clipVideo.hidden = true;
  ui.slideImg.hidden = false;
  ui.clipBtn.hidden = clipButtonHidden(player.segments[player.index], player.index);
  ui.clipBack.hidden = true;
  ui.clipNote.hidden = true;
  updateControls();
  if (returnFocus) (ui.clipBtn.hidden ? ui.btnPlay : ui.clipBtn).focus();
}

ui.clipBtn.addEventListener('click', enterClip);
ui.clipBack.addEventListener('click', () => exitClip(true));
ui.clipVideo.addEventListener('ended', () => exitClip(true));
ui.clipVideo.addEventListener('error', () => {
  if (!player.inClip || !ui.clipVideo.getAttribute('src')) return;
  player.clipFailed.add(player.index); // spec: the button disappears for that segment, the slide stays
  exitClip(true);
  ui.clipNote.textContent = 'That class clip won\'t load right now, so here is the slide.';
  ui.clipNote.hidden = false;
});

ui.slideImg.addEventListener('load', () => ui.slideImg.classList.remove('is-loading'));
ui.slideImg.addEventListener('error', () => {
  ui.slideImg.classList.remove('is-loading');
  if (ui.slideImg.getAttribute('src')) refreshExpiredLinks();
});

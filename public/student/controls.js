// Student page: the player's buttons and progress dots, the keyboard, and the phone dock height.
// Part of the student page; app.js has the map.

import { el } from './helpers.js';
import { ui } from './state.js';
import { goNext, goPrev, jumpTo, player, togglePlay } from './player.js';
import { hideNarrationNote } from './narration.js';

/* ---- controls ---- */

/** One progress dot per segment; each jumps to its segment. */
export function buildDots() {
  ui.dots.replaceChildren(...player.segments.map((s, i) => el('li', {},
    el('button', { type: 'button', 'aria-label': `Slide ${i + 1} of ${player.segments.length}`, onclick: () => jumpTo(i) }))));
}
/** Mark the current dot and the ones already played. */
export function updateDots() {
  [...ui.dots.querySelectorAll('button')].forEach((b, i) => {
    if (i === player.index && !player.finished) b.setAttribute('aria-current', 'step'); else b.removeAttribute('aria-current');
    b.classList.toggle('done', i < player.index || player.finished);
  });
}
/** Bring the buttons, their labels and the speaking spotlight in line with the player state. */
export function updateControls() {
  ui.player.classList.toggle('is-paused', !player.playing);
  // The slide being talked about gets a soft spotlight while the narration runs.
  ui.slideFrame.classList.toggle('is-speaking', player.playing && !player.inClip && !player.finished);
  if (player.playing) hideNarrationNote();
  ui.btnPlay.setAttribute('aria-label', player.playing ? 'Pause' : (player.finished ? 'Play again from the start' : 'Play'));
  ui.btnPrev.disabled = player.index <= 0;
  ui.btnNext.disabled = player.finished;
  ui.btnNext.setAttribute('aria-label', player.index >= player.segments.length - 1 ? 'Finish' : 'Next slide');
  ui.btnMute.setAttribute('aria-pressed', String(player.muted));
  ui.btnMute.setAttribute('aria-label', player.muted ? 'Unmute' : 'Mute');
  ui.btnMute.disabled = player.captionsOnly;
}

/** Mute or unmute the narration and the class clip; the walkthrough keeps its timing. */
function toggleMute() {
  player.muted = !player.muted;
  for (const a of player.audio.values()) a.muted = player.muted;
  ui.clipVideo.muted = player.muted;
  updateControls();
}

ui.btnPlay.addEventListener('click', togglePlay);
ui.btnPrev.addEventListener('click', goPrev);
ui.btnNext.addEventListener('click', goNext);
ui.btnMute.addEventListener('click', toggleMute);

/* ---- phone: keep the stage clear of the fixed bottom bar, whatever its height ---- */
if ('ResizeObserver' in window) {
  new ResizeObserver(([entry]) => {
    const h = Math.ceil(entry.borderBoxSize?.[0]?.blockSize ?? entry.target.offsetHeight);
    document.documentElement.style.setProperty('--dock-h', `${h}px`);
  }).observe(ui.dock);
}

/* ---- keyboard: space = play/pause, arrows = prev/next, m = mute ---- */

document.addEventListener('keydown', (e) => {
  if (ui.screens.app.hidden || ui.screens.app.dataset.view !== 'presenting' || ui.player.hidden) return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const t = e.target;
  // Let form fields, the video and the scrollable code panel keep their own keys.
  if (t.closest('input, textarea, select, video, [contenteditable], dialog, pre')) return;
  const isSpace = e.key === ' ' || e.code === 'Space';
  // Space on a focused button or link already activates it; don't toggle twice.
  if (isSpace && t.closest('button, a')) return;
  if (isSpace) { e.preventDefault(); togglePlay(); }
  else if (e.key === 'ArrowRight') { e.preventDefault(); if (!player.finished) goNext(); }
  else if (e.key === 'ArrowLeft') { e.preventDefault(); goPrev(); }
  else if (e.key === 'm' || e.key === 'M') { toggleMute(); }
});

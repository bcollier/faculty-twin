// Student page: everything the stage shows instead of the player (loading, not covered, FAQ answers,
// web answers, AI-drawn helper slides, contact cards, errors). Part of the student page; app.js has the map.

import { COPY } from './copy.js';
import { el, fmtDate, releaseMedia, slideLine } from './helpers.js';
import { app, ui } from './state.js';
import { renderChips, stageChips } from './chips.js';
import { addTwinMessage, clearSourcesToggle, openSlideDialog } from './chat.js';
import { ask } from './asking.js';

/* =====================================================================
   Stage messages (loading, not covered, errors)
   ===================================================================== */

/**
 * Replace the player with a message card: the spinner while asking, an error, or an FAQ, Canvas or web answer.
 * `actions` are buttons ({label, onClick, primary}), `chips` suggested questions, `extra` nodes under the text.
 */
export function showStageMessage({ title, text, spinner = false, actions = [], chips = [], label = '', labelClass = '', extra = [] }) {
  ui.player.hidden = true;
  stopWebAudio();
  ui.stageLabel.textContent = label || '';
  ui.stageLabel.hidden = !label;
  ui.stageLabel.className = `eyebrow stage-label${labelClass ? ` ${labelClass}` : ''}`;
  ui.stageExtra.replaceChildren(...extra);
  ui.stageExtra.hidden = !extra.length;
  ui.stageMsg.hidden = false;
  ui.stageSpinner.hidden = !spinner;
  ui.stageTitle.textContent = title || '';
  ui.stageText.textContent = text || '';
  ui.stageActions.replaceChildren(...actions.map(a =>
    el('button', { type: 'button', class: `btn ${a.primary ? 'btn-primary' : ''}`, onclick: a.onClick }, a.label)));
  renderChips(ui.stageChips, chips);
  ui.stageMsg.setAttribute('role', spinner ? 'status' : 'alert');
}

/** Move focus to the card's first button, unless the student is typing the next question. */
function focusFirstStageButton() {
  const firstBtn = ui.stageActions.querySelector('button') || ui.stageChips.querySelector('button');
  if (firstBtn && !ui.dockQ.matches(':focus')) firstBtn.focus({ preventScroll: true });
}

/** An error or referral card from COPY[kind], with "Try again" where trying again can help. */
export function showStageError(kind, question, answer = null) {
  const [title, text] = COPY[kind] || COPY.generic;
  const actions = [];
  if (kind === 'unreachable' || kind === 'generic' || kind === 'rateLimited' || kind === 'contentLoading') {
    actions.push({ label: 'Try again', primary: true, onClick: () => ask(question) });
  }
  if (kind === 'logistics' && answer) actions.push(...answerActions(answer));
  const withChips = kind === 'notCovered' || kind === 'notReady' || kind === 'badQuestion' || kind === 'logistics';
  const chips = withChips ? stageChips() : [];
  // Only invite the visitor to pick a chip when there are chips to pick.
  const invite = kind === 'logistics' ? 'Or ask me about the course:' : 'Try one of these instead:';
  if (kind === 'notCovered' || kind === 'logistics') clearSourcesToggle();
  showStageMessage({ title, text: chips.length ? `${text} ${invite}` : text, actions, chips });
  addTwinMessage(title, kind === 'logistics' ? '' : 'msg-error');
  ui.followups.hidden = true;
  focusFirstStageButton();
}

/** Link buttons (e.g. my Calendly) and TA contact cards that come with an FAQ or logistics answer. */
function answerActions(answer) {
  const actions = [];
  for (const link of answer.links || []) {
    if (!/^https:\/\//.test(String(link.url || ''))) continue;
    actions.push({ label: link.label, primary: true, onClick: () => window.open(link.url, '_blank', 'noopener') });
  }
  const contacts = answer.contacts || [];
  for (const c of contacts) {
    const label = contacts.length > 1 ? `Contact the ${c.course_label} TA` : 'Contact the TA';
    actions.push({ label, onClick: () => showContact(c) });
  }
  return actions;
}

/** FAQ answers and course-info answers from Canvas share this card: my words, link buttons, chips. */
export function showFaqAnswer(answer, label = '') {
  clearSourcesToggle();
  const chips = stageChips();
  showStageMessage({ title: answer.title || 'From my course FAQ', text: answer.message || '', actions: answerActions(answer), chips, label });
  addTwinMessage(answer.message || '');
  ui.followups.hidden = true;
  focusFirstStageButton();
}

/* Beyond my slides: an answer from a web search when no slide covers a course-adjacent question.
   Always labeled, with its sources and the closest slides in my course. It is text; when Settings
   turns it on, a Listen button reads it in a stock voice (never my clone), labeled as such. */
let webAudio = null;
/** Stop a web answer's Listen audio (a new question or card replaces it). */
function stopWebAudio() {
  if (webAudio) { releaseMedia(webAudio); webAudio = null; }
}

/** A titled list under a stage card (web sources, related slides), or null when it would be empty. */
function listSection(title, listClass, items) {
  return items.length ? el('section', { 'aria-label': title }, el('h3', { text: title }), el('ul', { class: listClass }, ...items)) : null;
}

/** The web answer's sources: https links only, each with its host, opening in a new tab. */
function webSourceList(links) {
  const items = [];
  for (const link of links || []) {
    const url = String(link.url || '');
    if (!/^https:\/\//.test(url)) continue;
    let host = '';
    try { host = new URL(url).hostname.replace(/^www\./, ''); } catch { continue; }
    items.push(el('li', {}, el('a', { href: url, target: '_blank', rel: 'noopener noreferrer' },
      String(link.label || host), el('span', { class: 'host', text: host }))));
  }
  return listSection(COPY.webSources, 'web-sources', items);
}

/** The closest slides in my course under a web answer; each opens in the slide dialog. */
function relatedSlides(related) {
  const items = (related || []).filter(r => r && r.image).map(r => {
    const l1 = slideLine(r);
    return el('li', {}, el('button', {
      type: 'button', class: 'source-btn', 'aria-label': `${l1}${r.title ? `. ${r.title}` : ''}. Open the slide`,
      onclick: () => openSlideDialog(r),
    }, el('img', { src: r.image, alt: '', loading: 'lazy' }),
    el('span', {}, el('span', { class: 'src-l1', text: l1 }), el('span', { class: 'src-l2', text: r.title || fmtDate(r.date) || '' }))));
  });
  return listSection(COPY.webRelated, 'related-slides', items);
}

/** A Listen button for a web answer, only for a signed /api/audio link in a voice that is not my clone. */
function webListen(answer) {
  const url = String(answer.audio || '');
  if (!url.startsWith('/api/audio?') || !answer.voice || answer.voice.kind === 'clone') return null;
  const btn = el('button', { type: 'button', class: 'btn btn-small' }, 'Listen');
  btn.addEventListener('click', () => {
    if (webAudio && !webAudio.paused) { webAudio.pause(); btn.textContent = 'Listen'; return; }
    if (!webAudio) {
      webAudio = new Audio(url);
      webAudio.addEventListener('ended', () => { btn.textContent = 'Listen'; });
      webAudio.addEventListener('error', () => { btn.textContent = 'Audio unavailable'; btn.disabled = true; });
    }
    webAudio.play().then(() => { btn.textContent = 'Pause'; }).catch(() => { btn.textContent = 'Listen'; });
  });
  return el('div', { class: 'web-listen' }, btn, el('span', { class: 'small', text: answer.voice.label || 'AI voice (a stock voice, not mine).' }));
}

/** The labeled "Beyond my slides" card: the answer, Listen, sources, closest slides, then any helper slide. */
export function showWebAnswer(answer) {
  clearSourcesToggle();
  const chips = stageChips();
  const extra = [webListen(answer), webSourceList(answer.links), relatedSlides(answer.related)].filter(Boolean);
  showStageMessage({ title: answer.title || 'Beyond my slides', text: answer.message || '', chips,
    label: COPY.webLabel, labelClass: 'web-label', extra });
  addTwinMessage(`${COPY.webLabel}. ${answer.message || ''}`);
  ui.followups.hidden = true;
  // A web answer is never in my voice: the dock names the stock voice when there is one, else nothing.
  ui.voiceLabel.textContent = answer.voice?.label || '';
  ui.voiceLabel.hidden = !answer.voice;
  // An AI-drawn helper slide goes last, after the sources and my closest slides.
  if (answer.generated_slide) {
    const myId = app.requestId;
    helperFigure(answer.generated_slide).then((fig) => {
      if (!fig || myId !== app.requestId) return;
      ui.stageExtra.append(fig);
      ui.stageExtra.hidden = false;
    });
  }
}

/* AI-drawn helper slides (public/helper-slide.js draws a checked spec; never markup from a model).
   Loaded only when an answer has one. */
let helperModule = null;
/** The drawn helper slide, or null when it cannot be drawn (the answer shows without it). */
async function helperFigure(slide) {
  try {
    helperModule = helperModule || await import('./helper-slide.js');
    return helperModule.renderHelperFigure(slide);
  } catch { return null; }
}

/** An AI-drawn helper slide under a walkthrough: labeled, dashed border, after the real slides. */
export function showHelperSlide(slide) {
  ui.helperSlot.replaceChildren();
  ui.helperSlot.hidden = true;
  if (!slide) return;
  const myId = app.requestId;
  helperFigure(slide).then((fig) => {
    if (!fig || myId !== app.requestId) return;
    ui.helperSlot.replaceChildren(fig);
    ui.helperSlot.hidden = false;
  });
}

/** The TA's contact details in a small card. The twin never says a TA's name aloud. */
function showContact(c) {
  ui.contactTitle.textContent = `TA for ${c.course_label}`;
  const email = el('a', { href: `mailto:${c.email}` }, c.email);
  ui.contactBody.replaceChildren(
    ...(c.name ? [el('p', { class: 'contact-name' }, c.name)] : []),
    el('p', {}, email),
    el('p', { class: 'contact-note' }, 'Presentation schedule changes and Canvas problems with participation points go to the TA.'),
  );
  ui.contactDialog.showModal();
}

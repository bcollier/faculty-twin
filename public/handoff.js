// Arriving from collier.phd: the receiving half of the "sticky note to legal pad" hand-off
// (docs/SPEC.md, "Arriving from collier.phd"; team brief UPDATE 12).
//
// A classic script in <head>, so it runs before the first paint (the CSP allows no inline script).
// With ?from=collier.phd it marks <html class="handoff">, which shows the HANDOFF FRAME (the inline
// critical CSS and the frame markup are already in index.html), and drops the parameter from the
// address bar. When the passcode or idle screen is ready, the legal page slides up and fades (450 ms)
// and the question box draws its border in pen. Reduced motion: a 150 ms crossfade, no movement.
// Without the parameter (or without JavaScript) nothing here runs.
(function () {
  'use strict';
  var root = document.documentElement;
  var url;
  try { url = new URL(window.location.href); } catch (e) { return; }
  if (url.searchParams.get('from') !== 'collier.phd') return;

  root.classList.add('handoff');
  url.searchParams.delete('from');
  try {
    window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash);
  } catch (e) { /* the frame still plays; only the address bar keeps the parameter */ }

  var REVEAL_MS = 450;        // --ho-reveal-ms in handoff.css
  var REDUCED_MS = 150;       // --ho-reduced-ms
  var MAX_WAIT_MS = 2500;     // never hold the frame longer than this, even if the server is slow
  var FONT_WAIT_MS = 800;     // after the screen is ready, give the page fonts this long to arrive
  var reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var started = false;

  function visible(el) { return !!el && !el.hidden && el.offsetParent !== null; }

  // The box a student types into first: the idle question box, or the passcode field.
  function questionBox() {
    var login = document.getElementById('screen-login');
    if (visible(login)) return document.getElementById('passcode');
    var idle = document.getElementById('idle-form');
    return visible(idle) ? idle : null;
  }

  // Draw the box's border in pen, edge by edge, with an SVG outline laid over it; then hand back to CSS.
  function drawBorder(box) {
    if (!box || reduced) return;
    var host = box.parentElement;
    if (!host) return;
    host.classList.add('ho-host');  // positioned first, so the box's offsets are measured from it
    var w = box.offsetWidth, h = box.offsetHeight;
    if (!w || !h) { host.classList.remove('ho-host'); return; }
    var cs = window.getComputedStyle(box);
    var stroke = parseFloat(cs.borderTopWidth) || 2;
    var radius = parseFloat(cs.borderTopLeftRadius) || 0;
    var NS = 'http://www.w3.org/2000/svg';
    var svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('class', 'ho-draw');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('width', String(w));
    svg.setAttribute('height', String(h));
    svg.setAttribute('viewBox', '0 0 ' + w + ' ' + h);
    svg.style.left = box.offsetLeft + 'px';
    svg.style.top = box.offsetTop + 'px';
    var rect = document.createElementNS(NS, 'rect');
    rect.setAttribute('x', String(stroke / 2));
    rect.setAttribute('y', String(stroke / 2));
    rect.setAttribute('width', String(w - stroke));
    rect.setAttribute('height', String(h - stroke));
    rect.setAttribute('rx', String(radius));
    rect.setAttribute('pathLength', '1');
    rect.setAttribute('stroke-width', String(stroke));
    svg.appendChild(rect);
    box.classList.add('ho-drawing');
    host.appendChild(svg);
    var cleaned = false;
    function cleanUp() {
      if (cleaned) return;
      cleaned = true;
      box.classList.remove('ho-drawing');
      host.classList.remove('ho-host');
      if (svg.parentNode) svg.parentNode.removeChild(svg);
    }
    rect.addEventListener('animationend', cleanUp);
    window.setTimeout(cleanUp, REVEAL_MS + 1500);  // in case animationend never comes
  }

  function finish() {
    var frame = document.getElementById('handoff');
    if (frame && frame.parentNode) frame.parentNode.removeChild(frame);
    root.classList.remove('handoff', 'handoff-out');
  }

  function reveal() {
    if (started) return;
    started = true;
    var frame = document.getElementById('handoff');
    if (!frame) { finish(); return; }
    var box = questionBox();
    // two frames: the screen underneath has painted once before the frame starts to move
    window.requestAnimationFrame(function () {
      window.requestAnimationFrame(function () {
        frame.addEventListener('transitionend', function (e) {
          if (e.target === frame && e.propertyName === 'opacity') finish();
        });
        root.classList.add('handoff-out');
        drawBorder(box);
        // in case transitionend never comes (a background tab, a browser without transitions)
        window.setTimeout(finish, (reduced ? REDUCED_MS : REVEAL_MS) + 1500);
      });
    });
  }

  function whenFontsReady(then) {
    var done = false;
    function go() { if (!done) { done = true; then(); } }
    window.setTimeout(go, FONT_WAIT_MS);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(go, go);
    else go();
  }

  function start() {
    window.setTimeout(reveal, MAX_WAIT_MS);
    var boot = document.getElementById('screen-boot');
    if (!boot || boot.hidden) { whenFontsReady(reveal); return; }
    // app.js hides the boot screen once the first API call settles (passcode, idle, or offline screen).
    var watch = new MutationObserver(function () {
      if (boot.hidden) { watch.disconnect(); whenFontsReady(reveal); }
    });
    watch.observe(boot, { attributes: true, attributeFilter: ['hidden'] });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();

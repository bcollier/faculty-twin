// Load the collier.phd fonts (Google Fonts) without blocking the first paint. The <link id="site-fonts">
// starts as media="print" so it does not hold up rendering; this switches it on. It is a file, not an
// inline onload handler, because the Content-Security-Policy allows no inline script. With no network
// the page keeps its fallbacks (Georgia, the system monospace) and reads the same.
(function () {
  var link = document.getElementById('site-fonts');
  if (link) link.media = 'all';
})();

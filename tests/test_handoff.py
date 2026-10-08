"""Arriving from collier.phd (docs/SPEC.md, "Arriving from collier.phd"; team brief UPDATE 12).

public/handoff.css is the shared HANDOFF FRAME, kept identical in bcollier/ben.collier.phd. These checks keep
the twin's copy of it, the inline critical CSS, the frame markup and the CSP in step. The browser behaviour
(first paint, the reveal, replaceState) is covered by tests/e2e/test_e2e_student.py.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
CSS = (PUBLIC / "handoff.css").read_text(encoding="utf-8")
INDEX = (PUBLIC / "index.html").read_text(encoding="utf-8")
HEAD = INDEX.split("</head>", 1)[0]
BODY = INDEX.split("<body>", 1)[1]


def tokens(css: str) -> dict[str, str]:
    root = re.search(r":root\s*\{(.*?)\n\}", css, re.S).group(1)
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"(--ho-[\w-]+)\s*:\s*([^;]+);", root)}


def test_the_frame_constants_match_the_contract():
    t = tokens(CSS)
    assert t["--ho-legal"] == "#fff2ad"
    assert t["--ho-margin"] == "#e7a2a0"
    assert t["--ho-rule"] == "rgba(64, 120, 168, .28)"
    assert t["--ho-ink"] == "#1d2633"
    assert t["--ho-pen"] == "#2447a6"
    assert t["--ho-margin-x"] == "64px"
    assert t["--ho-rule-top"] == "128px" and t["--ho-rule-gap"] == "32px"
    assert t["--ho-text-x"] == "96px" and t["--ho-text-y"] == "96px"
    assert t["--ho-text-size"] == "32px" and t["--ho-text-line"] == "32px"
    assert t["--ho-underline-len"] == "360px"
    assert t["--ho-ease"] == "cubic-bezier(.2, .8, .2, 1)"
    assert (t["--ho-launch-ms"], t["--ho-reveal-ms"], t["--ho-reduced-ms"]) == ("650ms", "450ms", "150ms")
    assert t["--ho-hand"].startswith('"Kalam"')


def test_the_critical_css_in_head_is_handoff_css_word_for_word():
    style = re.search(r'<style id="handoff-critical">\n(.*?)</style>', HEAD, re.S)
    assert style, "index.html needs the inline critical CSS in <head>"
    assert CSS.rstrip() in style.group(1), "the inline copy drifted from public/handoff.css"
    assert "html.handoff .handoff-frame { display: block; }" in style.group(1)


def test_the_frame_is_the_first_thing_in_the_body_and_hidden_from_screen_readers():
    first = BODY.lstrip()
    first = re.sub(r"^<!--.*?-->\s*", "", first, flags=re.S)
    assert first.startswith('<div class="handoff-frame" id="handoff" aria-hidden="true">')
    frame = first.split("</div></div>", 1)[0]
    assert 'class="handoff-ink"' in frame and 'class="handoff-underline"' in frame
    svg = re.search(r'<svg class="handoff-text" viewBox="0 0 (\d+) 32" width="\1" height="32"', frame)
    assert svg and svg.group(1) == "415", "the outlined text is 415px wide at 32px, like --ho-text-w"
    assert "<path d=" in frame and "<text" not in frame  # glyph outlines, so no font is needed to paint it


def test_the_head_scripts_run_before_paint_and_none_is_inline():
    scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", HEAD, re.S)
    assert all('src="' in attrs and not body.strip() for attrs, body in scripts), "the CSP allows no inline script"
    handoff = next(attrs for attrs, _ in scripts if 'src="handoff.js"' in attrs)
    assert "module" not in handoff and "defer" not in handoff and "async" not in handoff
    assert HEAD.index('src="handoff.js"') < HEAD.index('href="styles.css"')
    js = (PUBLIC / "handoff.js").read_text(encoding="utf-8")
    assert "searchParams.get('from') !== 'collier.phd'" in js
    assert "searchParams.delete('from')" in js and "history.replaceState" in js
    assert "prefers-reduced-motion" in js


def test_fonts_load_without_blocking_and_the_csp_allows_only_google_fonts():
    for page in ("index.html", "admin.html"):
        html = (PUBLIC / page).read_text(encoding="utf-8")
        link = re.search(r'<link id="site-fonts" rel="stylesheet" media="print" href="([^"]+)">', html)
        assert link and link.group(1).startswith("https://fonts.googleapis.com/css2?family=IBM+Plex+Mono")
        assert "Kalam" in link.group(1) and "Literata" in link.group(1) and "Young+Serif" in link.group(1)
        assert '<script src="fonts.js"></script>' in html and "onload=" not in html
    cfg = json.loads((ROOT / "vercel.json").read_text())
    csp = {h["key"]: h["value"] for h in cfg["headers"][0]["headers"]}["Content-Security-Policy"]
    directives = {d.split()[0]: d.split()[1:] for d in csp.split("; ")}
    assert directives["style-src"] == ["'self'", "'unsafe-inline'", "https://fonts.googleapis.com"]
    assert directives["font-src"] == ["'self'", "https://fonts.gstatic.com"]
    assert directives["script-src"] == ["'self'"]
    assert "fonts" not in " ".join(directives["connect-src"] + directives["img-src"] + directives["default-src"])


def test_the_inline_text_outline_is_handoff_text_svg_word_for_word():
    # public/handoff-text.svg is kept byte-identical to assets/handoff-text.svg in bcollier/ben.collier.phd
    svg = (PUBLIC / "handoff-text.svg").read_text(encoding="utf-8").strip()
    assert svg.startswith('<svg class="handoff-text"') and svg in BODY


def test_no_em_dashes_in_the_new_visitor_copy():
    for name in ("index.html", "handoff.js", "handoff.css", "fonts.js"):
        assert "—" not in (PUBLIC / name).read_text(encoding="utf-8"), name

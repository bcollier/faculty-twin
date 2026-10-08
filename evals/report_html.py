"""The visual model-comparison report: one self-contained HTML page (docs/SPEC.md, Block 8c).

Inline SVG and HTML tables only (no libraries, no network), light and dark themes from CSS tokens,
zero baselines on every axis, labeled scales, n on every number, a table view under every chart, and
a hover or focus tooltip on every mark (values are also in the tables, so the tooltip never gates).
Colors follow the dataviz method's reference palette: categorical slots in fixed order per answering
model (validated adjacent pairs; scatter points are also labeled with the model name, so color is
never the only cue), a one-hue blue ramp for the heatmap and the agreement matrix.

`render(analysis, include_text)` takes `compare_report.analyze(...)`. With include_text False (the
private question set) no question text is written into the page.
"""

from __future__ import annotations

import html
import math
from typing import Any, Iterable

from app.eval_core import DIMENSION_GROUPS, DIMENSION_LABELS, DIMENSIONS

from .compare_report import SCORED, TYPE_LABELS, short

W = 360  # panel width in SVG units: panels sit in a responsive grid, so text stays near 12 px on a phone
FONT = 12
SERIES = 8
SHORT_DIM = {"explains_concept_effectively": "Explains the concept", "matches_reference": "Matches the reply"}


def esc(x: Any) -> str:
    return html.escape(str(x), quote=True)


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{round(x * 100)}%"


def num(x: float | None, nd: int = 2) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def tip(*lines: Any) -> str:
    return esc("\n".join(str(x) for x in lines if x is not None and x != ""))


def nice_max(v: float) -> float:
    if v <= 0:
        return 1.0
    step = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * step:
            return m * step
    return 10 * step


def ticks(top: float, n: int = 4) -> list[float]:
    return [top * i / n for i in range(n + 1)]


# ---------------------------------------------------------------- SVG pieces

def svg_open(w: float, h: float, label: str) -> str:
    return (f'<svg viewBox="0 0 {w:.0f} {h:.0f}" width="100%" role="img" aria-label="{esc(label)}" '
            f'class="chart" preserveAspectRatio="xMinYMin meet">')


def x_axis(x0: float, x1: float, y0: float, y1: float, top: float, fmt, title: str, n: int = 4) -> str:
    """Vertical hairline grid with tick labels under the plot; the zero line is the baseline."""
    out = []
    for t in ticks(top, n):
        x = x0 + (x1 - x0) * (t / top if top else 0)
        cls = "base" if t == 0 else "grid"
        out.append(f'<line class="{cls}" x1="{x:.1f}" x2="{x:.1f}" y1="{y0:.1f}" y2="{y1:.1f}"/>')
        out.append(f'<text class="tick" x="{x:.1f}" y="{y1 + 14:.1f}" text-anchor="middle">{esc(fmt(t))}</text>')
    out.append(f'<text class="axis-title" x="{(x0 + x1) / 2:.1f}" y="{y1 + 30:.1f}" text-anchor="middle">{esc(title)}</text>')
    return "".join(out)


def y_axis(x0: float, x1: float, y0: float, y1: float, top: float, fmt, title: str, n: int = 4) -> str:
    out = []
    for t in ticks(top, n):
        y = y1 - (y1 - y0) * (t / top if top else 0)
        cls = "base" if t == 0 else "grid"
        out.append(f'<line class="{cls}" x1="{x0:.1f}" x2="{x1:.1f}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(f'<text class="tick" x="{x0 - 6:.1f}" y="{y + 4:.1f}" text-anchor="end">{esc(fmt(t))}</text>')
    cy = (y0 + y1) / 2
    out.append(f'<text class="axis-title" x="12" y="{cy:.1f}" text-anchor="middle" transform="rotate(-90 12 {cy:.1f})">'
               f'{esc(title)}</text>')
    return "".join(out)


def dot(x: float, y: float, cls: str, tiptext: str, r: float = 5) -> str:
    """A marker with a 2 px surface ring and a 24 px focusable hit target that carries the tooltip."""
    return (f'<g class="mark" tabindex="0" data-tip="{tiptext}"><circle class="hit" cx="{x:.1f}" cy="{y:.1f}" r="12"/>'
            f'<circle class="{cls} ring" cx="{x:.1f}" cy="{y:.1f}" r="{r}"/></g>')


def hbar(x0: float, y: float, w: float, h: float, cls: str) -> str:
    """A horizontal bar, square at the baseline with a 4 px rounded data end."""
    if w <= 0.5:
        return f'<rect class="{cls}" x="{x0:.1f}" y="{y:.1f}" width="1" height="{h:.1f}"/>'
    r = min(4.0, w / 2, h / 2)
    return (f'<path class="{cls}" d="M{x0:.1f},{y:.1f} h{w - r:.1f} a{r},{r} 0 0 1 {r},{r} v{h - 2 * r:.1f} '
            f'a{r},{r} 0 0 1 {-r},{r} h{-(w - r):.1f} z"/>')


# ---------------------------------------------------------------- charts

def model_rows(a: dict[str, Any]) -> list[tuple[str, int]]:
    return [(g, i % SERIES + 1) for i, g in enumerate(a["generators"])]


def dot_ci_panel(a: dict[str, Any], title: str, get, top: float, fmt, axis_title: str, note: str = "") -> str:
    """Rows = models; a dot at the mean and a whisker for the 95% confidence interval."""
    rows = model_rows(a)
    left, right, top_pad, row_h = 112, 18, 8, 28
    h = top_pad + row_h * len(rows) + 40
    x0, x1 = left, W - right
    sx = lambda v: x0 + (x1 - x0) * max(0.0, min(v, top)) / top
    out = [svg_open(W, h, f"{title}. Values are in the table view.")]
    out.append(x_axis(x0, x1, top_pad - 4, top_pad + row_h * len(rows), top, fmt, axis_title, 5 if top == 5 else 4))
    for i, (g, slot) in enumerate(rows):
        c = get(a["models"][g])
        y = top_pad + row_h * i + row_h / 2
        out.append(f'<text class="label" x="{left - 8}" y="{y + 4:.1f}" text-anchor="end">{esc(short(g))}</text>')
        if not c or c.get("mean") is None:
            out.append(f'<text class="muted" x="{x0 + 4}" y="{y + 4:.1f}">n/a</text>')
            continue
        out.append(f'<line class="whisker s{slot}" x1="{sx(c["low"]):.1f}" x2="{sx(c["high"]):.1f}" '
                   f'y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(dot(sx(c["mean"]), y, f"fill s{slot}",
                       tip(f"{fmt(c['mean'])}", short(g), title, f"95% CI {fmt(c['low'])} to {fmt(c['high'])}",
                           f"n = {c['n']} answers")))
    out.append("</svg>")
    return panel(title, "".join(out), note)


def panel(title: str, body: str, note: str = "") -> str:
    return (f'<figure class="panel"><figcaption>{esc(title)}</figcaption>{body}'
            + (f'<p class="note">{esc(note)}</p>' if note else "") + "</figure>")


def bars_panel(a: dict[str, Any], title: str, key: str, nkey: str, note: str = "") -> str:
    rows = model_rows(a)
    left, right, top_pad, row_h, bar_h = 112, 46, 8, 28, 14
    h = top_pad + row_h * len(rows) + 40
    x0, x1 = left, W - right
    out = [svg_open(W, h, f"{title}. Values are in the table view.")]
    out.append(x_axis(x0, x1, top_pad - 4, top_pad + row_h * len(rows), 1.0, pct, "% of answers (0 to 100%)"))
    for i, (g, slot) in enumerate(rows):
        m = a["models"][g]
        v, n = m.get(key), m.get(nkey)
        y = top_pad + row_h * i + (row_h - bar_h) / 2
        out.append(f'<text class="label" x="{left - 8}" y="{y + bar_h / 2 + 4:.1f}" text-anchor="end">{esc(short(g))}</text>')
        if v is None:
            out.append(f'<text class="muted" x="{x0 + 4}" y="{y + bar_h / 2 + 4:.1f}">n/a</text>')
            continue
        w = (x1 - x0) * v
        out.append(f'<g class="mark" tabindex="0" data-tip="{tip(pct(v), short(g), title, f"n = {n}")}">'
                   f'<rect class="hit" x="{x0}" y="{y - 6:.1f}" width="{x1 - x0 + 40:.1f}" height="{bar_h + 12}"/>'
                   + hbar(x0, y, w, bar_h, f"fill s{slot}") + "</g>")
        out.append(f'<text class="value" x="{x0 + w + 6:.1f}" y="{y + bar_h / 2 + 4:.1f}">{pct(v)}</text>')
    out.append("</svg>")
    return panel(title, "".join(out), note)


def scatter_panel(a: dict[str, Any], title: str, getx, gety, xfmt, yfmt, xtitle: str, ytitle: str, ytop: float,
                  xtop: float | None = None, note: str = "") -> str:
    rows = model_rows(a)
    pts = [(g, slot, getx(a["models"][g]), gety(a["models"][g])) for g, slot in rows]
    pts = [p for p in pts if p[2] is not None and p[3] is not None]
    xtop = xtop or nice_max(max([p[2] for p in pts] + [0]) * 1.1)
    h = 300
    x0, x1, y0, y1 = 52, W - 16, 10, h - 46
    sx = lambda v: x0 + (x1 - x0) * v / xtop
    sy = lambda v: y1 - (y1 - y0) * v / ytop
    out = [svg_open(W, h, f"{title}. Values are in the table view.")]
    out.append(y_axis(x0, x1, y0, y1, ytop, yfmt, ytitle))
    for t in ticks(xtop):
        x = sx(t)
        out.append(f'<line class="{"base" if t == 0 else "grid"}" x1="{x:.1f}" x2="{x:.1f}" y1="{y0}" y2="{y1}"/>')
        out.append(f'<text class="tick" x="{x:.1f}" y="{y1 + 14}" text-anchor="middle">{esc(xfmt(t))}</text>')
    out.append(f'<text class="axis-title" x="{(x0 + x1) / 2:.1f}" y="{h - 8}" text-anchor="middle">{esc(xtitle)}</text>')
    boxes: list[tuple[float, float, float, float]] = [(sx(p[2]) - 7, sy(p[3]) - 7, sx(p[2]) + 7, sy(p[3]) + 7) for p in pts]
    for g, slot, xv, yv in pts:
        x, y = sx(xv), sy(yv)
        out.append(dot(x, y, f"fill s{slot}", tip(f"{yfmt(yv)} at {xfmt(xv)}", short(g), title), r=6))
    for g, slot, xv, yv in pts:
        x, y = sx(xv), sy(yv)
        label = short(g)
        lw, lh = 6.4 * len(label), 13
        # Direct labels, placed where they collide with no other label or point; a leader line when moved far.
        spots = [(dx, dy) for dy in (-8, 14, -22, 28, -36, 42, -50, 56) for dx in (10, -10)]
        best = None
        for dx, dy in spots:
            lx = x + dx if dx > 0 else x + dx - lw
            box = (lx, y + dy - lh + 3, lx + lw, y + dy + 3)
            if box[0] < x0 + 2 or box[2] > W - 2 or box[1] < 0 or box[3] > y1:
                continue
            if all(box[2] < b[0] or box[0] > b[2] or box[3] < b[1] or box[1] > b[3] for b in boxes):
                best = (lx, y + dy, box, dy)
                break
        if best is None:
            continue  # the legend, tooltip and table carry it
        lx, ly, box, dy = best
        boxes.append(box)
        if abs(dy) > 20:
            out.append(f'<line class="leader" x1="{x:.1f}" y1="{y:.1f}" x2="{(box[0] if box[0] > x else box[2]):.1f}" '
                       f'y2="{ly - 4:.1f}"/>')
        out.append(f'<text class="label" x="{lx:.1f}" y="{ly:.1f}">{esc(label)}</text>')
    out.append("</svg>")
    return panel(title, "".join(out), note)


def strip_panel(a: dict[str, Any]) -> str:
    """Every answer's time to answer as a dot, one row per model, with the median marked."""
    rows = model_rows(a)
    lats = {g: [v / 1000 for v in a["models"][g]["latencies_model_ms"]] for g, _ in rows}
    top = nice_max(max([max(v) for v in lats.values() if v] + [1]))
    left, right, top_pad, row_h = 112, 18, 8, 34
    h = top_pad + row_h * len(rows) + 40
    x0, x1 = left, W - right
    sx = lambda v: x0 + (x1 - x0) * min(v, top) / top
    out = [svg_open(W, h, "Time to answer per model. Values are in the table view.")]
    out.append(x_axis(x0, x1, top_pad - 4, top_pad + row_h * len(rows), top, lambda t: f"{t:g} s",
                      "Seconds to answer"))
    for i, (g, slot) in enumerate(rows):
        yc = top_pad + row_h * i + row_h / 2
        out.append(f'<text class="label" x="{left - 8}" y="{yc + 4:.1f}" text-anchor="end">{esc(short(g))}</text>')
        vals = sorted(lats[g])
        for k, v in enumerate(vals):
            jitter = ((k * 7919) % 11 - 5) * 1.6
            out.append(f'<circle class="fill s{slot} faint" cx="{sx(v):.1f}" cy="{yc + jitter:.1f}" r="3"/>')
        if vals:
            med = vals[len(vals) // 2]
            p90 = vals[min(len(vals) - 1, int(0.9 * len(vals)))]
            out.append(f'<g class="mark" tabindex="0" data-tip="{tip(f"median {med:.1f} s", short(g), f"90th percentile {p90:.1f} s", f"n = {len(vals)} answers")}">'
                       f'<rect class="hit" x="{x0}" y="{yc - 14:.1f}" width="{x1 - x0}" height="28"/>'
                       f'<line class="median" x1="{sx(med):.1f}" x2="{sx(med):.1f}" y1="{yc - 11:.1f}" y2="{yc + 11:.1f}"/></g>')
    out.append("</svg>")
    return panel("Time to answer (each dot is one answer; the bar marks the median)", "".join(out),
                 "Answers that called the model only (slides, Canvas, web). FAQ answers and declines take under a second.")


def dumbbell_panel(a: dict[str, Any]) -> str:
    rows = model_rows(a)
    left, right, top_pad, row_h = 112, 18, 8, 30
    h = top_pad + row_h * len(rows) + 40
    x0, x1 = left, W - right
    sx = lambda v: x0 + (x1 - x0) * v
    out = [svg_open(W, h, "Pass rate with and without same-family judges. Values are in the table view.")]
    out.append(x_axis(x0, x1, top_pad - 4, top_pad + row_h * len(rows), 1.0, pct, "Pass rate (% of judge verdicts)"))
    for i, (g, slot) in enumerate(rows):
        m = a["models"][g]
        y = top_pad + row_h * i + row_h / 2
        out.append(f'<text class="label" x="{left - 8}" y="{y + 4:.1f}" text-anchor="end">{esc(short(g))}</text>')
        p_all, p_ex = m["pass_rate"], m["pass_rate_excluding_same_family"]
        if p_all is None:
            continue
        if p_ex is not None:
            out.append(f'<line class="link" x1="{sx(p_all):.1f}" x2="{sx(p_ex):.1f}" y1="{y}" y2="{y}"/>')
            d = 6
            xe = sx(p_ex)
            out.append(f'<g class="mark" tabindex="0" data-tip="{tip(pct(p_ex), short(g), "without same-family judges", f"n = {m["judgements_excluding_same_family"]} verdicts")}">'
                       f'<circle class="hit" cx="{xe:.1f}" cy="{y}" r="12"/>'
                       f'<path class="hollow s{slot}" d="M{xe:.1f},{y - d} l{d},{d} l{-d},{d} l{-d},{-d} z"/></g>')
        out.append(dot(sx(p_all), y, f"fill s{slot}", tip(pct(p_all), short(g), "all judges", f"n = {m['judgements']} verdicts")))
    out.append("</svg>")
    return panel("Pass rate: all judges (dot) versus without same-family judges (diamond)", "".join(out),
                 "Same family: a Claude judge on a Claude model's answer, or a GPT judge on a GPT model's answer.")


def retest_panel(a: dict[str, Any], g: str, slot: int, include_text: bool) -> str:
    m = a["generator_retest"]["models"].get(g) or {}
    pts = m.get("points") or []
    h = 300
    x0, x1, y0, y1 = 52, W - 16, 10, h - 46
    sx = lambda v: x0 + (x1 - x0) * v / 5
    sy = lambda v: y1 - (y1 - y0) * v / 5
    out = [svg_open(W, h, f"Run 1 versus run 2 for {short(g)}. Values are in the table view.")]
    out.append(y_axis(x0, x1, y0, y1, 5, lambda t: f"{t:g}", "Run 2 score (1–5)", 5))
    for t in range(6):
        out.append(f'<line class="{"base" if t == 0 else "grid"}" x1="{sx(t):.1f}" x2="{sx(t):.1f}" y1="{y0}" y2="{y1}"/>')
        out.append(f'<text class="tick" x="{sx(t):.1f}" y="{y1 + 14}" text-anchor="middle">{t}</text>')
    out.append(f'<text class="axis-title" x="{(x0 + x1) / 2:.1f}" y="{h - 8}" text-anchor="middle">Run 1 score (1–5)</text>')
    out.append(f'<line class="diag" x1="{sx(0):.1f}" y1="{sy(0):.1f}" x2="{sx(5):.1f}" y2="{sy(5):.1f}"/>')
    for p in pts:
        lines = [f"{p['run1']:.2f} then {p['run2']:.2f}", p["qid"], TYPE_LABELS.get(p.get("type"), p.get("type"))]
        out.append(dot(sx(p["run1"]), sy(p["run2"]), f"fill s{slot} faint-ring", tip(*lines), r=4))
    out.append("</svg>")
    title = (f"{short(g)}: ICC {num(m.get('icc'))} (teaching only {num(m.get('icc_teaching'))}), "
             f"verdict flips {pct(m.get('verdict_flip_rate'))} (n = {m.get('n', 0)} questions)")
    return panel(title, "".join(out))


def alpha_panel(a: dict[str, Any]) -> str:
    dims = a["inter_judge"]["dimensions"]
    vals = [m["alpha"] for m in dims.values() if m["alpha"] is not None]
    lo = min([0.0] + vals)
    lo = math.floor(lo * 4) / 4
    left, right, top_pad, row_h, bar_h = 150, 40, 8, 24, 12
    h = top_pad + row_h * len(dims) + 40
    x0, x1 = left, W - right
    span = 1 - lo
    sx = lambda v: x0 + (x1 - x0) * (v - lo) / span
    out = [svg_open(W, h, "Krippendorff's alpha per dimension. Values are in the table view.")]
    t = lo
    while t <= 1.0001:
        x = sx(t)
        out.append(f'<line class="{"base" if abs(t) < 1e-9 else "grid"}" x1="{x:.1f}" x2="{x:.1f}" y1="{top_pad - 4}" '
                   f'y2="{top_pad + row_h * len(dims)}"/>')
        out.append(f'<text class="tick" x="{x:.1f}" y="{top_pad + row_h * len(dims) + 14}" text-anchor="middle">{t:.2g}</text>')
        t += 0.25
    out.append(f'<text class="axis-title" x="{(x0 + x1) / 2:.1f}" y="{h - 8}" text-anchor="middle">'
               f'Alpha (1 = perfect agreement, 0 = chance)</text>')
    for i, (d, m) in enumerate(dims.items()):
        y = top_pad + row_h * i + (row_h - bar_h) / 2
        out.append(f'<text class="label" x="{left - 8}" y="{y + bar_h / 2 + 4:.1f}" text-anchor="end">{esc(SHORT_DIM.get(d, DIMENSION_LABELS[d]))}</text>')
        v = m["alpha"]
        if v is None:
            out.append(f'<text class="muted" x="{sx(0) + 4:.1f}" y="{y + bar_h / 2 + 4:.1f}">n/a</text>')
            continue
        xa, xb = sorted((sx(0), sx(v)))
        body = (hbar(xa, y, xb - xa, bar_h, "fill s1") if v >= 0
                else f'<rect class="fill s1" x="{xa:.1f}" y="{y:.1f}" width="{xb - xa:.1f}" height="{bar_h}"/>')
        out.append(f'<g class="mark" tabindex="0" data-tip="{tip(f"alpha {v:.2f}", DIMENSION_LABELS[d], f"same score {pct(m["exact"])}, within one {pct(m["within_one"])}", f"n = {m["answers"]} answers")}">'
                   f'<rect class="hit" x="{x0}" y="{y - 5:.1f}" width="{x1 - x0}" height="{bar_h + 10}"/>{body}</g>')
        out.append(f'<text class="value" x="{max(xb, sx(0)) + 6:.1f}" y="{y + bar_h / 2 + 4:.1f}">{v:.2f}</text>')
    out.append("</svg>")
    return panel("Do the three judges agree? Krippendorff's alpha (ordinal) per dimension", "".join(out),
                 a["plain"]["alpha"])


# ---------------------------------------------------------------- HTML tables

def heat_step(v: float | None, top: float = 5.0) -> int:
    """Seven bins on a 0 to `top` scale (the lightest means near zero)."""
    if v is None:
        return 0
    edges = [0.3, 0.45, 0.6, 0.7, 0.8, 0.9]
    f = v / top
    return 1 + sum(f >= e for e in edges)


def heatmap(a: dict[str, Any]) -> str:
    groups = (("core", DIMENSION_GROUPS["core"]), ("teaching", DIMENSION_GROUPS["teaching"]))
    head1 = "".join(f'<th colspan="{len(d)}" class="grp">{esc("Core rubric" if g == "core" else "Teaching quality")}</th>'
                    for g, d in groups)
    head2 = "".join(f'<th class="dim" title="{esc(DIMENSIONS[d])}">{esc(DIMENSION_LABELS[d])}</th>' for _, ds in groups for d in ds)
    body = []
    for g, slot in model_rows(a):
        cells = []
        for _, ds in groups:
            for d in ds:
                c = a["models"][g]["dim_ci"][d]
                k = heat_step(c["mean"])
                text = num(c["mean"]) if c["mean"] is not None else "n/a"
                cells.append(f'<td class="heat h{k}" tabindex="0" data-tip="{tip(text, short(g), DIMENSION_LABELS[d], f"95% CI {num(c["low"])} to {num(c["high"])}" if c["mean"] is not None else "", f"n = {c["n"]} answers")}">{text}</td>')
        body.append(f'<tr><th scope="row"><i class="key s{slot}"></i>{esc(short(g))}</th>{"".join(cells)}</tr>')
    legend = "".join(f'<span><i class="sw h{k}"></i>{esc(lab)}</span>' for k, lab in
                     enumerate(["0 to 1.5", "1.5 to 2.25", "2.25 to 3", "3 to 3.5", "3.5 to 4", "4 to 4.5", "4.5 to 5"], 1))
    return (f'<div class="scroll"><table class="heatmap"><thead><tr><th></th>{head1}</tr><tr><th scope="col">Model</th>{head2}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div><p class="scale">Mean score, 0 to 5 scale (1 = very poor, 3 = acceptable, '
            f'5 = excellent): {legend}</p>')


def agreement_matrix(a: dict[str, Any]) -> str:
    judges = a["judges"]
    m = a["inter_judge"]["matrix"]
    jr = a["judge_retest"]
    rows = []
    for x in judges:
        cells = []
        for y in judges:
            if x == y:
                r = jr.get(x) or {}
                v = r.get("verdict_same")
                text = f"{pct(v)} (retest)" if v is not None else "n/a"
                lines = (f"{pct(v)} same verdict on a second scoring", short(x), f"kappa {num(r.get('kappa'))}",
                         f"n = {r.get('answers', 0)} answers")
            else:
                c = m.get(f"{x}|{y}") or {}
                v = c.get("same_verdict")
                text = pct(v)
                lines = (f"{pct(v)} same verdict", f"{short(x)} and {short(y)}", f"kappa {num(c.get('kappa'))}",
                         f"mean score gap {num(c.get('mean_gap'))} points", f"n = {c.get('n', 0)} answers")
            cells.append(f'<td class="heat h{heat_step(v, 1.0)}" tabindex="0" data-tip="{tip(*lines)}">{esc(text)}</td>')
        rows.append(f'<tr><th scope="row">{esc(short(x))}</th>{"".join(cells)}</tr>')
    head = "".join(f'<th scope="col">{esc(short(j))}</th>' for j in judges)
    return (f'<div class="scroll"><table class="heatmap matrix"><thead><tr><th>Judge</th>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>'
            '<p class="note">Each cell: the share of answers where the two judges gave the same pass or fail verdict '
            '(0 to 100%). The diagonal is test-retest: the same judge scoring the same answer a second time.</p>')


def table(headers: list[str], rows: Iterable[list[Any]]) -> str:
    th = "".join(f'<th scope="col">{esc(h)}</th>' for h in headers)
    trs = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="scroll"><table class="data"><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>'


def details(summary: str, inner: str) -> str:
    return f'<details><summary>{esc(summary)}</summary>{inner}</details>'


def ci_text(c: dict[str, Any], rate: bool = False) -> str:
    if not c or c.get("mean") is None:
        return "n/a"
    f = pct if rate else num
    return f"{f(c['mean'])} [{f(c['low'])} to {f(c['high'])}] (n={c['n']})"


# ---------------------------------------------------------------- page

def render(a: dict[str, Any], include_text: bool) -> str:
    meta = a["meta"]
    gens = a["generators"]
    P = a["plain"]
    M = a["models"]
    legend = "".join(f'<span><i class="key s{slot}"></i>{esc(short(g))}</span>' for g, slot in model_rows(a))
    sg = "; ".join(f"{short(s['judge'])} judging {short(s['generator'])} ({s['kind']})" for s in a["self_grading"])
    spend = meta.get("spend_usd")

    # 1. headline table
    head_rows = [[short(g), ci_text(M[g]["pass_ci"], True), ci_text(M[g]["pass_ci_excluding_same_family"], True),
                  ci_text(M[g]["core_ci"]), ci_text(M[g]["teaching_ci"]),
                  f"{pct(M[g]['retrieval_hit_rate'])} (n={M[g]['hit_n']})", f"{pct(M[g]['route_accuracy'])} (n={M[g]['route_n']})",
                  "n/a" if M[g]["median_latency_model_ms"] is None else f"{M[g]['median_latency_model_ms'] / 1000:.1f} s",
                  "n/a" if M[g]["cost_per_answer"] is None else f"${M[g]['cost_per_answer']:.4f} (n={M[g]['cost_n']})"]
                 for g in gens]
    headline = table(["Model", "Pass rate, all judges", "Pass rate, without same-family judges", "Core rubric mean (1–5)",
                      "Teaching mean (1–5)", "Retrieval hit", "Right route", "Median time to answer",
                      "Cost per answer (USD)"], head_rows)

    # 2. dot plots
    dots = [dot_ci_panel(a, "Pass rate (all judges)", lambda m: m["pass_ci"], 1.0, pct, "% of verdicts that were pass"),
            dot_ci_panel(a, "Pass rate (without same-family judges)", lambda m: m["pass_ci_excluding_same_family"], 1.0,
                         pct, "% of verdicts that were pass")]
    for d in SCORED:
        dots.append(dot_ci_panel(a, DIMENSION_LABELS[d], lambda m, d=d: m["dim_ci"][d], 5.0, lambda t: f"{t:g}",
                                 "Mean score (0 to 5; 5 best)"))
    dot_table = table(["Model"] + ["Pass rate"] + [DIMENSION_LABELS[d] for d in SCORED],
                      [[short(g), ci_text(M[g]["pass_ci"], True)] + [ci_text(M[g]["dim_ci"][d]) for d in SCORED]
                       for g in gens])

    # 3. route and retrieval
    rr = [bars_panel(a, "Right route (slides, Canvas, FAQ, referral, web or decline)", "route_accuracy", "route_n"),
          bars_panel(a, "Retrieval hit: an expected slide was used", "retrieval_hit_rate", "hit_n"),
          bars_panel(a, "Slide precision: share of slides that were expected", "slide_precision", "precision_n"),
          bars_panel(a, "Answers whose slides all came from one course", "course_purity", "purity_n")]
    rr_table = table(["Model", "Right route", "Retrieval hit", "Slide precision", "One course only", "Fell back to notes"],
                     [[short(g), f"{pct(M[g]['route_accuracy'])} (n={M[g]['route_n']})",
                       f"{pct(M[g]['retrieval_hit_rate'])} (n={M[g]['hit_n']})",
                       f"{pct(M[g]['slide_precision'])} (n={M[g]['precision_n']})",
                       f"{pct(M[g]['course_purity'])} (n={M[g]['purity_n']})", pct(M[g]["fallback_rate"])] for g in gens])
    types = []
    for g in gens:
        for t, b in M[g]["by_type"].items():
            types.append([short(g), TYPE_LABELS.get(None if t == "None" else t, t), b["questions"], pct(b["route_accuracy"]),
                          pct(b["retrieval_hit_rate"]), pct(b["pass_rate"]), pct(b["pass_rate_excluding_same_family"]),
                          num(b["teaching_mean"]), ", ".join(f"{k} {v}" for k, v in b["routes"].items())])
    type_table = table(["Model", "Question type", "Questions (n)", "Right route", "Retrieval hit", "Pass rate",
                        "Pass rate, other families", "Teaching mean (1–5)", "Routes taken"], types)

    # 4. cost and latency
    cost_top = nice_max(max([M[g]["cost_per_model_answer"] or 0 for g in gens] + [0.001]) * 1.15)
    cq = [scatter_panel(a, "Cost versus pass rate", lambda m: m["cost_per_model_answer"],
                        lambda m: m["pass_rate_excluding_same_family"], lambda t: f"${t:.3g}", pct,
                        "Cost per model answer (USD)", "Pass rate, other families (%)", 1.0, cost_top),
          scatter_panel(a, "Cost versus teaching quality", lambda m: m["cost_per_model_answer"],
                        lambda m: m["teaching_ci"]["mean"], lambda t: f"${t:.3g}", lambda t: f"{t:g}",
                        "Cost per model answer (USD)", "Teaching mean (0 to 5)", 5.0, cost_top),
          strip_panel(a)]
    cost_table = table(["Model", "Cost per model answer (USD)", "Cost per answer, all routes (USD)", "Total (USD)",
                        "Median time, model answers", "Mean time, all answers", "Model answers (n)"],
                       [[short(g), num(M[g]["cost_per_model_answer"], 4), num(M[g]["cost_per_answer"], 4),
                         num(M[g]["total_cost"], 3),
                         "n/a" if M[g]["median_latency_model_ms"] is None else f"{M[g]['median_latency_model_ms'] / 1000:.1f} s",
                         "n/a" if M[g]["mean_latency_ms"] is None else f"{M[g]['mean_latency_ms'] / 1000:.1f} s",
                         M[g]["model_answers"]] for g in gens])

    # 5. self-grading
    flags = {(s["generator"], s["judge"]): s["kind"] for s in a["self_grading"]}
    judge_rows = [[short(g)] + [f"{pct(M[g]['per_judge_pass'][j]['rate'])} (n={M[g]['per_judge_pass'][j]['n']})"
                                + (f" ({flags[(g, j)]})" if (g, j) in flags else "") for j in a["judges"]] for g in gens]
    per_judge = table(["Model"] + [short(j) for j in a["judges"]], judge_rows)

    # 6. reliability
    gr = a["generator_retest"]
    retest_panels = "".join(retest_panel(a, g, slot, include_text) for g, slot in model_rows(a) if g in gr.get("models", {}))
    gr_table = table(["Model", "ICC(2,1) run 1 vs 2", "Spearman", "ICC, teaching scores only (n)", "Verdict flip rate",
                      "Same route both runs", "Questions (n)"],
                     [[short(g), num(m["icc"]), num(m["spearman"]), f"{num(m.get('icc_teaching'))} (n={m.get('n_teaching')})",
                       f"{pct(m['verdict_flip_rate'])} (n={m['verdict_pairs']})", pct(m["route_consistency"]), m["n"]]
                      for g, m in gr.get("models", {}).items()])
    dim_retest = table(["Dimension", "ICC(2,1) run 1 vs 2", "Same score", "Pairs (n)"],
                       [[DIMENSION_LABELS[d], num(m["icc"]), pct(m["exact"]), m["n"]] for d, m in gr.get("dimensions", {}).items()])
    jr_rows = [[short(j), num(m["icc"]), num(m["teaching_icc"]), pct(m["exact"]), pct(m["within_one"]), num(m["kappa"]),
                pct(m["verdict_same"]), m["answers"]] for j, m in a["judge_retest"].items()]
    jr_table = table(["Judge", "ICC(2,1) of scores", "Teaching ICC", "Same score", "Within one point",
                      "Kappa on pass/fail", "Same verdict", "Answers (n)"], jr_rows)
    ij_table = table(["Dimension", "Krippendorff's alpha", "Same score (judge pairs)", "Within one point", "Answers (n)"],
                     [[DIMENSION_LABELS[d], num(m["alpha"]), pct(m["exact"]), pct(m["within_one"]), m["answers"]]
                      for d, m in a["inter_judge"]["dimensions"].items()]
                     + [["Verdict (pass/fail)", num(a["inter_judge"]["verdict_alpha"]),
                         pct(a["inter_judge"]["verdict_agreement"]["exact"]), "", ""]])

    qdetail = ""
    if include_text and a.get("questions_detail"):
        qrows = []
        for q in a["questions_detail"]:
            cells = []
            for g in gens:
                x = q["models"].get(g)
                cells.append("n/a" if not x else f"{x['route']}{'' if x['route_ok'] else ' (wrong route)'}, "
                             f"{x['passes']}/{x['judged']} pass")
            qrows.append([q["qid"], q.get("question", ""), TYPE_LABELS.get(q["type"], q["type"]), " or ".join(q["expected"])] + cells)
        qdetail = section("questions", "Question by question (run 1)",
                          "What each model did with each invented question: the route it took and how many of the three judges passed it.",
                          table(["Id", "Question", "Type", "Expected"] + [short(g) for g in gens], qrows))

    pe = a.get("provider_errors") or {}
    outage_note = ("" if not (pe.get("answers") or pe.get("judgements")) else
                   f'<p class="callout">{pe["answers"]} answers and {pe["judgements"]} judgements were refused by their '
                   'provider (no credit or quota) and are left out of every number on this page: '
                   + esc(", ".join(f"{short(k)} {v}" for k, v in pe["by_model"].items())) + '.</p>')
    rt = a.get("routed") or {}
    route_note = ("" if not (rt.get("answers") or rt.get("judgements")) else
                  f'<p class="callout">{rt["answers"]} of {a["answers"]} answers and {rt["judgements"]} of '
                  f'{rt["judgements_total"]} judgements by Claude models went through OpenRouter (the same models) after the '
                  'direct Anthropic API key ran out of credit mid-run. Their times include the extra hop.</p>')
    web_note = ("" if a["web_path"] else
                '<p class="callout">The "beyond the slides" web path was not in the app for this run. Web questions were '
                'expected to be declined for the route numbers; the judges were told a good answer would come from the web '
                'with cited sources, so their scores show what students lose without it.</p>')
    body = f"""
<header>
  <p class="eyebrow">Faculty Twin evals · {esc(meta.get('questions_file', ''))}</p>
  <h1>{esc(meta.get('label') or 'Model comparison')}</h1>
  <p class="lede">{len(gens)} answering models, {len(a['judges'])} judges from three model families, {a['questions']} questions,
  {a['answers']} answers{f', ${spend:.2f} spent' if spend is not None else ''}. Every chart starts at zero and has a table view.</p>
  <div class="tiles">
    <div class="tile"><span>Questions</span><b>{a['questions']}</b></div>
    <div class="tile"><span>Answers judged</span><b>{a['answers']}</b></div>
    <div class="tile"><span>Runs per question (most)</span><b>{a['reps']}</b></div>
    <div class="tile"><span>Spend (USD)</span><b>{'n/a' if spend is None else f'${spend:.2f}'}</b></div>
  </div>
  <p class="legend">{legend}</p>
  {web_note}
  {route_note}
  {outage_note}
  <p class="note">Self-grading flagged: {esc(sg) or 'none'}. Judges: {esc(', '.join(a['judges']))}. Scores run 1 to 5 (1 = very poor,
  3 = acceptable, 5 = excellent); each answer's score is the mean of the judges that scored it, and n counts answers.
  Pass rate is the share of judge verdicts that were pass, a separate overall call, not computed from the scores.</p>
</header>
{section("headline", "Headline", "One row per answering model, run 1. Brackets are 95% confidence intervals.", headline)}
{section("heatmap", "Model by dimension", "Mean judged score per model and dimension (run 1). Darker is better.", heatmap(a))}
{section("dots", "Scores with confidence intervals", P["ci"], grid(dots) + details("Table view", dot_table))}
{section("selfgrade", "Self-grading check", "A judge from the same family as the model tends to be lenient. The diamond drops those judges.",
         grid([dumbbell_panel(a)]) + per_judge)}
{section("routes", "Routing and retrieval (measured, no judge)", "Did the twin take the right path, and did it find the slides a good answer needs?",
         grid(rr) + details("Table view", rr_table) + details("By question type", type_table))}
{section("cost", "Cost, quality and speed", "Cost from each answer's tokens and the price table (an estimate, not a bill).",
         grid(cq) + details("Table view", cost_table))}
{section("retest", "Test-retest: does a model's answer score the same twice?",
         P["icc"] + " " + P["spearman"] + " " + P["flip"],
         (grid([retest_panels]) if retest_panels else '<p class="note">No repeated answers in this run.</p>')
         + details("Table view", gr_table + dim_retest))}
{section("judges", "Judge reliability",
         "Test-retest for each judge, and agreement between judges. " + P["kappa"],
         agreement_matrix(a) + grid([alpha_panel(a)]) + jr_table + details("Agreement per dimension", ij_table))}
{qdetail}
<footer><p>Written by <code>evals/compare.py</code> (docs/SPEC.md, Block 8c). Question set: {esc(meta.get('questions_file', ''))}.
Run {esc(meta.get('run_id', ''))}, finished {esc(meta.get('finished_at', ''))}.</p></footer>
"""
    return PAGE.replace("{{TITLE}}", "Model Comparison Report").replace("{{BODY}}", body)


def grid(panels: list[str]) -> str:
    return f'<div class="grid">{"".join(panels)}</div>'


def section(sid: str, title: str, intro: str, inner: str) -> str:
    return f'<section id="{sid}"><h2>{esc(title)}</h2><p class="intro">{esc(intro)}</p>{inner}</section>'


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITLE}}</title>
<style>
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;
--base:#c3c2b7;--border:rgba(11,11,11,.10);
--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--s5:#e87ba4;--s6:#008300;--s7:#4a3aa7;--s8:#e34948;
--h0:transparent;--h1:#cde2fb;--h2:#9ec5f4;--h3:#6da7ec;--h4:#3987e5;--h5:#256abf;--h6:#184f95;--h7:#0d366b;
--hi1:#0b0b0b;--hi2:#0b0b0b;--hi3:#0b0b0b;--hi4:#fff;--hi5:#fff;--hi6:#fff;--hi7:#fff}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;
--ink:#fff;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--base:#383835;--border:rgba(255,255,255,.10);
--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--s5:#d55181;--s6:#008300;--s7:#9085e9;--s8:#e66767;
--h1:#0d366b;--h2:#104281;--h3:#184f95;--h4:#1c5cab;--h5:#2a78d6;--h6:#5598e7;--h7:#9ec5f4;
--hi1:#fff;--hi2:#fff;--hi3:#fff;--hi4:#fff;--hi5:#fff;--hi6:#0b0b0b;--hi7:#0b0b0b}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;
--grid:#2c2c2a;--base:#383835;--border:rgba(255,255,255,.10);
--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--s5:#d55181;--s6:#008300;--s7:#9085e9;--s8:#e66767;
--h1:#0d366b;--h2:#104281;--h3:#184f95;--h4:#1c5cab;--h5:#2a78d6;--h6:#5598e7;--h7:#9ec5f4;
--hi1:#fff;--hi2:#fff;--hi3:#fff;--hi4:#fff;--hi5:#fff;--hi6:#0b0b0b;--hi7:#0b0b0b}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:28px;line-height:1.2;margin:4px 0 8px}h2{font-size:20px;margin:0 0 4px}
.eyebrow{color:var(--muted);font-size:13px;margin:0}.lede{color:var(--ink2);max-width:72ch}
.tiles{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}.tile{background:var(--surface);border:1px solid var(--border);
border-radius:10px;padding:10px 14px;min-width:140px}.tile span{display:block;color:var(--ink2);font-size:13px}
.tile b{font-size:24px;font-weight:600}
section{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:18px 16px;margin:18px 0}
.intro,.note,.scale{color:var(--ink2);font-size:13.5px;max-width:90ch}
.callout{border-left:3px solid var(--s4);padding:6px 12px;background:var(--page);font-size:14px}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:13.5px;color:var(--ink2)}
.legend span,.scale span{display:inline-flex;align-items:center;gap:6px;margin-right:10px}
i.key{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:middle}
i.sw{display:inline-block;width:14px;height:10px;border-radius:2px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,330px),1fr));gap:14px;margin:12px 0}
figure.panel{margin:0;background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:10px 10px 4px}
figcaption{font-size:13.5px;font-weight:600;margin:0 0 6px}
svg.chart{display:block;overflow:visible}
svg text{font-size:12px;fill:var(--ink2)}svg .tick{fill:var(--muted);font-size:11px;font-variant-numeric:tabular-nums}
svg .axis-title{fill:var(--ink2);font-size:11.5px}svg .label{fill:var(--ink2)}svg .value{fill:var(--ink);font-size:11.5px}
svg .muted{fill:var(--muted)}
.grid line.grid,svg line.grid{stroke:var(--grid);stroke-width:1}svg line.base{stroke:var(--base);stroke-width:1}
svg line.diag{stroke:var(--base);stroke-width:1}svg line.leader{stroke:var(--muted);stroke-width:1}svg line.link{stroke:var(--muted);stroke-width:2}
svg line.median{stroke:var(--ink);stroke-width:2;stroke-linecap:round}
svg .hit{fill:transparent;stroke:none}
svg .ring{stroke:var(--surface);stroke-width:2}svg .faint{opacity:.55}svg .faint-ring{stroke:var(--surface);stroke-width:1.5;opacity:.85}
svg .whisker{stroke-width:2;stroke-linecap:round}
svg .hollow{fill:var(--surface);stroke-width:2}
""" + "".join(f".fill.s{k}{{fill:var(--s{k})}}.whisker.s{k},.hollow.s{k}{{stroke:var(--s{k})}}i.key.s{k}{{background:var(--s{k})}}"
              for k in range(1, 9)) + "".join(f".h{k}{{background:var(--h{k});color:var(--hi{k})}}i.sw.h{k}{{background:var(--h{k})}}"
                                               for k in range(1, 8)) + """
.h0{background:transparent;color:var(--muted)}
g.mark{cursor:default;outline:none}g.mark:focus-visible .ring,g.mark:hover .ring{stroke:var(--ink)}
.scroll{overflow-x:auto;max-width:100%}
table{border-collapse:collapse;font-size:13.5px;margin:10px 0}
table.data th,table.data td{border-bottom:1px solid var(--grid);padding:6px 10px;text-align:left;vertical-align:top}
table.data th{color:var(--ink2);font-weight:600}table.data td{font-variant-numeric:tabular-nums}
table.heatmap{border-spacing:2px;border-collapse:separate}table.heatmap td{min-width:64px;text-align:center;padding:8px 6px;
border-radius:4px;font-variant-numeric:tabular-nums}table.heatmap th{font-weight:600;font-size:12.5px;color:var(--ink2);padding:4px 6px}
table.heatmap th.grp{text-align:center;border-bottom:1px solid var(--grid)}table.heatmap th.dim{max-width:110px}
table.heatmap th[scope=row]{text-align:left;white-space:nowrap}
table.heatmap td:focus-visible,table.heatmap td:hover{outline:2px solid var(--ink)}
details{margin:8px 0}summary{cursor:pointer;color:var(--ink2);font-size:13.5px}
#tip{position:fixed;pointer-events:none;background:var(--surface);color:var(--ink);border:1px solid var(--border);
border-radius:8px;padding:8px 10px;font-size:12.5px;box-shadow:0 4px 18px rgba(0,0,0,.18);max-width:300px;z-index:9}
#tip b{display:block;font-size:14px}#tip span{display:block;color:var(--ink2)}
footer{color:var(--muted);font-size:12.5px}
.theme{position:absolute;top:14px;right:16px;font:inherit;font-size:13px;background:var(--surface);color:var(--ink2);
border:1px solid var(--border);border-radius:8px;padding:4px 10px}
@media (forced-colors:active){svg .fill{fill:CanvasText}}
</style></head>
<body><button class="theme" type="button" id="theme">Light or dark</button><main>{{BODY}}</main><div id="tip" hidden></div>
<script>
(function(){
  var tipEl=document.getElementById('tip');
  function show(el,x,y){var lines=(el.getAttribute('data-tip')||'').split('\\n');tipEl.replaceChildren();
    lines.forEach(function(t,i){var n=document.createElement(i===0?'b':'span');n.textContent=t;tipEl.appendChild(n);});
    tipEl.hidden=false;var w=tipEl.offsetWidth,h=tipEl.offsetHeight;
    tipEl.style.left=Math.max(8,Math.min(window.innerWidth-w-8,x+14))+'px';tipEl.style.top=Math.max(8,Math.min(window.innerHeight-h-8,y+14))+'px';}
  document.addEventListener('pointerover',function(e){var el=e.target.closest('[data-tip]');if(el)show(el,e.clientX,e.clientY);});
  document.addEventListener('pointermove',function(e){var el=e.target.closest('[data-tip]');if(el)show(el,e.clientX,e.clientY);else tipEl.hidden=true;});
  document.addEventListener('focusin',function(e){var el=e.target.closest('[data-tip]');if(!el)return;var r=el.getBoundingClientRect();show(el,r.left+r.width/2,r.top+r.height/2);});
  document.addEventListener('focusout',function(){tipEl.hidden=true;});
  var root=document.documentElement;document.getElementById('theme').addEventListener('click',function(){
    var dark=root.getAttribute('data-theme')?root.getAttribute('data-theme')==='dark':window.matchMedia('(prefers-color-scheme: dark)').matches;
    root.setAttribute('data-theme',dark?'light':'dark');});
})();
</script></body></html>
"""

"""A/B render: the same narrations in each local voice, side by side, for judging by ear.

Roadmap step 2: "Render the same three narration segments with Chatterbox (my
clone) and Kokoro (stock), next to the ElevenLabs clone, and compare by ear."

Narrations come from `--text` (repeatable) or a JSON file: either a list of
strings, or a stored playlist (`{"segments": [{"narration": ...}]}`) such as
one written by indexer/pregenerate.py. Use narrations from Ben's slides, not
student text.

Output (default `localvoice/out/<UTC time>/`, git-ignored): one file per
engine and segment, plus `index.html`, a page with a player per render, its
label, the render time, and the real-time factor, in a shuffled order with
engine names hidden until "Reveal" so the comparison can be blind.

To put the ElevenLabs clone in the comparison, drop its mp3s for the same
narrations into the run folder as `elevenlabs_<n>.mp3`; this tool does not
call ElevenLabs, so it never spends credits.

    uv run --python 3.12 --no-project --with-requirements localvoice/requirements.txt \\
        python -m localvoice.ab --narrations my_segments.json \\
        --engine kokoro:am_michael --engine chatterbox --sample ~/.../ben_ref.wav
"""

from __future__ import annotations

import argparse
import html
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import audio
from .engines import EngineUnavailable, build

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "localvoice" / "out"


def load_narrations(path: Path | None, texts: list[str]) -> list[str]:
    out = list(texts)
    if path:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = [s.get("narration", "") for s in data.get("segments", [])]
        out += [str(t) for t in data]
    out = [t.strip() for t in out if t and t.strip()]
    if not out:
        raise ValueError("no narrations given")
    return out


def render_all(engines, narrations: list[str], out_dir: Path, mp3: bool = True) -> list[dict[str, Any]]:
    rows = []
    for eng in engines:
        for n, text in enumerate(narrations, start=1):
            started = time.monotonic()
            samples, rate = eng.render(text)
            took = time.monotonic() - started
            path = audio.write(samples, rate, out_dir / f"{eng.name}_{n}", mp3=mp3)
            length = audio.duration_seconds(samples, rate)
            rows.append({
                "engine": eng.name,
                "label": eng.label,
                "segment": n,
                "file": path.name,
                "seconds": length,
                "render_seconds": round(took, 2),
                "realtime_factor": round(took / length, 2) if length else None,
            })
    return rows


def page(rows: list[dict[str, Any]], narrations: list[str], seed: int = 0) -> str:
    """A blind A/B page: renders shuffled per segment, names hidden until Reveal."""
    rng = random.Random(seed)
    parts = [
        "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>Voice A/B</title>",
        "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:46rem;margin:2rem auto;padding:0 1rem}"
        ".r{margin:.6rem 0;padding:.6rem;border:1px solid #ccc;border-radius:8px}.n{display:none;color:#555}"
        "body.show .n{display:block}audio{width:100%}</style>",
        "<h1>Voice A/B</h1><p>Listen to each version, note your favorite, then press Reveal.</p>",
        "<button onclick=\"document.body.classList.toggle('show')\">Reveal</button>",
    ]
    for n, text in enumerate(narrations, start=1):
        seg = [r for r in rows if r["segment"] == n]
        rng.shuffle(seg)
        parts.append(f"<h2>Segment {n}</h2><p>{html.escape(text)}</p>")
        for i, r in enumerate(seg):
            parts.append(
                f"<div class='r'><b>Version {chr(65 + i)}</b><audio controls preload='none' "
                f"src='{html.escape(r['file'])}'></audio><div class='n'>{html.escape(r['engine'])}: "
                f"{html.escape(r['label'])} {r['seconds']} s, rendered in {r['render_seconds']} s "
                f"(real-time factor {r['realtime_factor']})</div></div>"
            )
    return "\n".join(parts) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--narrations", type=Path, help="JSON list of strings or a stored playlist")
    p.add_argument("--text", action="append", default=[])
    p.add_argument("--engine", action="append", default=[], help="kokoro[:voice], chatterbox, chatterbox-turbo, fake")
    p.add_argument("--sample", type=Path, help="reference clip for chatterbox (from localvoice.sample)")
    p.add_argument("--out", type=Path)
    p.add_argument("--wav", action="store_true", help="write WAV even when ffmpeg is available")
    args = p.parse_args(argv)

    try:
        narrations = load_narrations(args.narrations, args.text)
        engines = [build(s, args.sample.expanduser() if args.sample else None) for s in (args.engine or ["kokoro"])]
    except (ValueError, OSError, EngineUnavailable) as exc:
        print(exc, file=sys.stderr)
        return 2
    out = args.out or OUT / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    try:
        rows = render_all(engines, narrations, out, mp3=not args.wav)
    except EngineUnavailable as exc:
        print(exc, file=sys.stderr)
        return 3
    (out / "renders.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    (out / "index.html").write_text(page(rows, narrations), encoding="utf-8")
    for r in rows:
        print(f"{r['engine']:>18} seg {r['segment']}: {r['seconds']:5.1f} s audio, rendered in "
              f"{r['render_seconds']:5.1f} s (x{r['realtime_factor']})")
    print(f"Open {out / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

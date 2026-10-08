"""A/B render: the same narrations in each local voice, side by side, for judging by ear.

Roadmap step 2: "Render the same three narration segments with Chatterbox (my
clone) and Kokoro (stock), next to the ElevenLabs clone, and compare by ear."

Narrations come from `--text` (repeatable) or a JSON file: either a list of
strings, or a stored playlist (`{"segments": [{"narration": ...}]}`) such as
one written by indexer/pregenerate.py. Use narrations from Ben's slides, not
student text.

Output (default `localvoice/out/<UTC time>/`, git-ignored):

- `index.html`: the listening page. Every render has a random file name, the
  order within each segment is a seeded random shuffle, and the page carries no
  engine names, so nothing on it gives the answer away.
- `answer_key.json`: which version is which, with labels, render times, and
  real-time factors. Open it only after listening.
- One audio file per render, loudness-matched.

Other voices, such as ElevenLabs clips made for the same narrations, can be
added by dropping `<name>_<segment>.mp3` (or `.wav`) into the run folder before
running, or into `--add` folders: they are loudness-matched, renamed, and
shuffled in like the rest. This tool never calls ElevenLabs, so it never
spends credits.

Timing: each engine renders one throwaway sentence first, so model loading is
not counted against segment 1. An engine that crashes is recorded in the
answer key and the run carries on with the others.

    uv run --python 3.12 --no-project --with-requirements localvoice/requirements.txt \\
        python -m localvoice.ab --narrations my_segments.json \\
        --engine kokoro:am_michael --engine chatterbox --sample ~/.../ben_ref.wav
"""

from __future__ import annotations

import argparse
import html
import json
import random
import re
import secrets
import shutil
import subprocess
import sys
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import audio
from .engines import Engine, EngineUnavailable, build

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "localvoice" / "out"
WARMUP_TEXT = "This sentence warms up the model and is not saved."
EXTERNAL = re.compile(r"^(?P<name>[a-z][a-z0-9\-]*)_(?P<n>\d+)\.(?:mp3|wav)$", re.I)


def load_narrations(path: Path | None, texts: list[str]) -> list[str]:
    """The narrations to render: `texts`, plus a JSON list or saved playlist at `path`. ValueError if none."""
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


def _fresh_name(out_dir: Path, suffix: str) -> str:
    while True:
        name = f"{secrets.token_hex(4)}{suffix}"
        if not (out_dir / name).exists():
            return name


def render_all(engines: Iterable[Engine], narrations: list[str], out_dir: Path,
               mp3: bool = True) -> list[dict[str, Any]]:
    """Render every narration with every engine. Never raises: a failing engine gets an error row."""
    rows = []
    out_dir.mkdir(parents=True, exist_ok=True)
    for eng in engines:
        try:
            eng.render(WARMUP_TEXT)  # load the model now, so segment 1's timing is warm
        except Exception as exc:
            rows.append({"engine": eng.name, "label": eng.label, "segment": None,
                         "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
            continue
        for n, text in enumerate(narrations, start=1):
            try:
                started = time.monotonic()
                samples, rate = eng.render(text)
                took = time.monotonic() - started
                stem = _fresh_name(out_dir, "")
                path = audio.write(samples, rate, out_dir / stem, mp3=mp3)
            except Exception as exc:
                rows.append({"engine": eng.name, "label": eng.label, "segment": n,
                             "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
                continue
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


def import_external(folders: list[Path], out_dir: Path, n_segments: int) -> list[dict[str, Any]]:
    """Bring in hand-made clips named `<name>_<segment>.mp3|wav`: loudness-match, rename, and add a row each.

    Loudness matching uses ffmpeg's loudnorm; without ffmpeg the clip is copied as is and the row says so.
    """
    rows = []
    out_dir.mkdir(parents=True, exist_ok=True)
    for folder in folders:
        for src in sorted(Path(folder).expanduser().iterdir()):
            m = EXTERNAL.match(src.name)
            if not m or not src.is_file():
                continue
            n = int(m.group("n"))
            if not 1 <= n <= n_segments:
                continue
            dest = out_dir / _fresh_name(out_dir, ".mp3" if audio.has_ffmpeg() else src.suffix.lower())
            matched = False
            if audio.has_ffmpeg():
                try:
                    subprocess.run(
                        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
                         "-af", "loudnorm=I=-20:TP=-1.5:LRA=11", "-ac", "1", "-ar", "44100", "-b:a", "128k", str(dest)],
                        check=True,
                    )
                    matched = True
                except (subprocess.CalledProcessError, OSError):
                    dest = dest.with_suffix(src.suffix.lower())
                    shutil.copyfile(src, dest)
            else:
                shutil.copyfile(src, dest)
            if src.parent.resolve() == out_dir.resolve():
                src.unlink()  # its original name would give the answer away
            rows.append({
                "engine": m.group("name").lower(),
                "label": "added by hand",
                "segment": n,
                "file": dest.name,
                "loudness_matched": matched,
            })
    return rows


def assign_versions(rows: list[dict[str, Any]], n_segments: int, seed: int) -> list[dict[str, Any]]:
    """Shuffle each segment's renders with a seeded RNG and letter them A, B, C... (mutates rows)."""
    rng = random.Random(seed)
    for n in range(1, n_segments + 1):
        seg = sorted((r for r in rows if r.get("segment") == n and r.get("file")), key=lambda r: r["file"])
        rng.shuffle(seg)
        for i, r in enumerate(seg):
            r["version"] = chr(65 + i)
    return rows


def page(rows: list[dict[str, Any]], narrations: list[str]) -> str:
    """The listening page: versions by letter only. No engine names, labels, or timings appear here."""
    parts = [
        "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>Voice A/B</title>",
        "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:46rem;margin:2rem auto;padding:0 1rem}"
        ".r{margin:.6rem 0;padding:.6rem;border:1px solid #ccc;border-radius:8px}audio{width:100%}</style>",
        "<h1>Voice A/B</h1><p>Listen to every version and note your favorite for each segment. "
        "Then open <code>answer_key.json</code> in this folder to see which voice was which.</p>",
    ]
    for n, text in enumerate(narrations, start=1):
        seg = sorted((r for r in rows if r.get("segment") == n and r.get("version")), key=lambda r: r["version"])
        parts.append(f"<h2>Segment {n}</h2><p>{html.escape(text)}</p>")
        for r in seg:
            parts.append(f"<div class='r'><b>Version {r['version']}</b>"
                         f"<audio controls preload='none' src='{html.escape(r['file'])}'></audio></div>")
    return "\n".join(parts) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Command line: render the narrations in each engine, shuffle them, and write the listening page.

    The answer key is a separate file, so the page can be judged blind. Exit 3 if nothing rendered.
    """
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--narrations", type=Path, help="JSON list of strings or a stored playlist")
    p.add_argument("--text", action="append", default=[])
    p.add_argument("--engine", action="append", default=[], help="kokoro[:voice], chatterbox, chatterbox-turbo, fake")
    p.add_argument("--sample", type=Path, help="reference clip for chatterbox (from localvoice.sample)")
    p.add_argument("--add", action="append", type=Path, default=[],
                   help="folder of hand-made clips named <name>_<segment>.mp3, e.g. ElevenLabs renders")
    p.add_argument("--out", type=Path)
    p.add_argument("--seed", type=int, help="shuffle seed (default: random, recorded in the answer key)")
    p.add_argument("--wav", action="store_true", help="write WAV even when ffmpeg is available")
    args = p.parse_args(argv)

    try:
        narrations = load_narrations(args.narrations, args.text)
        engines = [build(s, args.sample.expanduser() if args.sample else None) for s in (args.engine or ["kokoro"])]
    except (ValueError, OSError, EngineUnavailable) as exc:
        print(exc, file=sys.stderr)
        return 2
    out = args.out or OUT / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=True)
    rows = import_external([out, *args.add], out, len(narrations))  # clips already dropped into the run folder
    rows += render_all(engines, narrations, out, mp3=not args.wav)
    seed = args.seed if args.seed is not None else secrets.randbits(32)
    assign_versions(rows, len(narrations), seed)
    (out / "answer_key.json").write_text(json.dumps({"seed": seed, "renders": rows}, indent=2), encoding="utf-8")
    (out / "index.html").write_text(page(rows, narrations), encoding="utf-8")
    for r in rows:
        if r.get("error"):
            print(f"{r['engine']:>18} seg {r['segment']}: FAILED {r['error']}")
        elif "render_seconds" in r:
            print(f"{r['engine']:>18} seg {r['segment']}: {r['seconds']:5.1f} s audio, rendered in "
                  f"{r['render_seconds']:5.1f} s (x{r['realtime_factor']})")
        else:
            print(f"{r['engine']:>18} seg {r['segment']}: added by hand"
                  + ("" if r.get("loudness_matched") else " (not loudness-matched: ffmpeg missing or failed)"))
    print(f"Open {out / 'index.html'} (answer key: {out / 'answer_key.json'})")
    return 0 if any(r.get("version") for r in rows) else 3


if __name__ == "__main__":
    raise SystemExit(main())

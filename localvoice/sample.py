"""Cut a reference clip of Ben's voice for Chatterbox, from instructor-only stretches of class.

Roadmap step 1: "Cut 30 to 60 seconds of clean solo speech from my class
recordings (instructor-only cues from the de-identified transcripts, so no
student voice is in the sample)."

A stretch qualifies only if every cue in it, padded by 5 s on each side:
- is labelled `instructor` by indexer/deidentify.py,
- carries no `[student]` or `[person]` token (someone else is being named or may be speaking),
- passes the PG check in indexer/clips.py (no `pg: true`, nothing on the profanity list),
and the gaps between its cues are short (no long pauses or cuts). These are the
same rules indexer/clips.py uses for class clips, because audio cannot be
de-identified after the fact.

Runs on the Mac mini, where the de-identified transcripts and the class videos
live. Reads and writes nothing in the repo; never prints transcript text.

    uv run --no-project --with numpy python -m localvoice.sample \\
        --transcript "~/Lecture Archive/_build/transcripts/70445/s06.json" \\
        --video "~/Lecture Archive/<course folder>/2026 Fall/06 .../video.mp4" \\
        --out "~/Lecture Archive/_private/voice/ben_ref.wav" [--seconds 45]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from indexer.clips import NAME_TOKENS, pg_problem

PAD_S = 5.0
MAX_GAP_S = 1.5
MIN_S = 10.0


@dataclass(frozen=True)
class Window:
    start: float
    end: float
    words: int

    @property
    def seconds(self) -> float:
        return self.end - self.start


def cue_ok(c: dict) -> bool:
    text = c.get("text") or ""
    return c.get("speaker") == "instructor" and not any(t in text for t in NAME_TOKENS) and not pg_problem(c)


def clean_windows(cues: list[dict], target: float = 45.0) -> list[Window]:
    """Runs of consecutive clean cues, trimmed so the 5 s padding is clean too, longest speech first.

    Each window is capped at `target` seconds (cut on a cue boundary).
    """
    cues = sorted((c for c in cues if c.get("end", 0) > c.get("start", 0)), key=lambda c: c["start"])
    bad_spans = [(c["start"] - PAD_S, c["end"] + PAD_S) for c in cues if not cue_ok(c)]

    def padded_clean(start: float, end: float) -> bool:
        return not any(lo < end and hi > start for lo, hi in bad_spans)

    windows: list[Window] = []
    run: list[dict] = []

    def flush():
        # Drop cues from either end until the padded span is clean, then cap at target.
        r = list(run)
        while r and not padded_clean(r[0]["start"], r[-1]["end"]):
            # Remove whichever end is nearer a bad span.
            head_bad = not padded_clean(r[0]["start"], r[0]["end"])
            r = r[1:] if head_bad else r[:-1]
        while r and r[-1]["end"] - r[0]["start"] > target:
            r = r[:-1]
        if r and r[-1]["end"] - r[0]["start"] >= MIN_S:
            words = sum(len((c.get("text") or "").split()) for c in r)
            windows.append(Window(round(r[0]["start"], 2), round(r[-1]["end"], 2), words))

    for c in cues:
        if cue_ok(c) and (not run or c["start"] - run[-1]["end"] <= MAX_GAP_S):
            run.append(c)
            continue
        if run:
            flush()
        run = [c] if cue_ok(c) else []
    if run:
        flush()
    # Prefer long, dense speech: more words per second means fewer pauses and less board-writing.
    return sorted(windows, key=lambda w: (-min(w.seconds, target), -w.words / max(w.seconds, 1e-6)))


def cut(video: Path, window: Window, out: Path) -> None:
    """Extract mono 24 kHz WAV audio for the window. Video is dropped."""
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{window.start:.2f}",
         "-to", f"{window.end:.2f}", "-i", str(video), "-vn", "-ac", "1", "-ar", "24000",
         "-af", "highpass=f=80,loudnorm", str(out)],
        check=True,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--transcript", required=True, type=Path)
    p.add_argument("--video", type=Path, help="session video (a single-part recording)")
    p.add_argument("--out", type=Path)
    p.add_argument("--seconds", type=float, default=45.0)
    p.add_argument("--list", action="store_true", help="only list candidate windows (times and counts)")
    args = p.parse_args(argv)

    doc = json.loads(args.transcript.expanduser().read_text(encoding="utf-8"))
    windows = clean_windows(doc.get("cues") or [], args.seconds)
    if not windows:
        print("No instructor-only stretch long enough in this session. Try another session.")
        return 1
    for w in windows[:5]:
        print(f"  {w.start:8.1f}s to {w.end:8.1f}s  ({w.seconds:5.1f} s, {w.words} words)")
    if args.list:
        return 0
    if not args.video or not args.out:
        print("--video and --out are needed to cut the sample.", file=sys.stderr)
        return 2
    cut(args.video.expanduser(), windows[0], args.out.expanduser())
    print(f"Wrote {args.out.name} ({windows[0].seconds:.1f} s). Listen to all of it before using it: "
          "no other voice may be audible.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Class clip cutting for the Faculty Twin indexer (stage 4).

Cuts one short clip per slide from the class recording, for a stretch where the
slide was on screen and only I was speaking. Clips auto-publish behind the course
passcode, so every rule below must pass or the slide gets no clip. Everything this
script reads and writes is private: nothing it touches belongs in the repo.

Reads, per session:
  _build/align/<course>/s<NN>.json and .meta.json   (from indexer/align.py)
  _build/transcripts/<course>/s<NN>.json             (de-identified cues with speaker labels)
  _build/slides/<course>/s<NN>/slides.json           (per-slide flags)
  _build/overrides/<course>/s<NN>.json               optional {"no_clips": ["<slide_id>", ...]}
  <archive>/_private/deid_overrides/<course>/s<NN>.json  only its "no_clips" key, if present
  the session's video.mp4 (+ video_part2.mp4, same timeline as align.py)

Writes:
  _build/clips/<slide_id>.mp4     H.264 720p CRF 26, AAC 96k mono, +faststart, 15-90 s;
                                  video and audio only (the recording's caption track and
                                  metadata are dropped)
  _build/clips/manifest.json      [{slide_id, course, session, start, end, reason_kept, ...}]
  _build/clips/rejected.json      {"counts": {reason: n}, "rejected": [{slide_id, start, end, reason}]}
                                  (slide ids, times and reason codes only)

Rules (docs/SPEC.md, Preprocessing pipeline, stage 4, plus the team brief's updates):
  * Only frame-matched windows, so the video shows that slide. Text windows never become clips.
    The usable window is the span between the first and last matching frame.
  * Every sampled frame inside a clip matched the slide on its own: runs are split around
    frames that only matched through smoothing (stricter than the spec's 5 s merge, which
    applies only when align.py left no frame-level detail).
  * Cuts land on cue boundaries; a window over 90 s is cut at the last cue boundary before
    90 s. Clips shorter than 15 s are dropped.
  * Every cue overlapping the clip padded by 5 s on each side is "instructor" and contains
    no "[student]" and no "[person]" token (audio cannot be de-identified).
  * PG (brief UPDATE 6): no cue in the padded window is marked `pg: true` by the PG filter
    or matches the basic profanity list in PROFANITY (audio cannot be cleaned).
    If the whole window fails, the longest clean stretch inside it that still satisfies
    every rule is used instead.
  * No clip for slides flagged student_names_possible, in_the_news,
    student_presentation_possible or no_clips_private_case, for slides whose stored text
    or notes carry a [student] mask, for the Tesla vs Waymo case sessions (45884 s11, s12),
    for slides pulled in an override file, or when the slide area keeps changing (an
    embedded video is playing).
  * Right before encoding, the padded window's cues are re-read and checked once more.

Run (no .venv in the repo):
  uv run --no-project --with numpy --with pillow --with scikit-learn \\
      python -m indexer.clips [--course 70445] [--session 6 | --all] [--dry-run] [--force]

The script never prints transcript text.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

if __package__ in (None, ""):  # run as a script (python indexer/clips.py, as the worker does)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from indexer import align as A  # noqa: E402
from indexer.layout import NO_CLIP_FLAGS, NO_CLIP_SESSIONS, read_json, write_json  # noqa: E402

CLIPS_VERSION = 2  # bump only when the encoding changes

MIN_S, MAX_S = 15.0, 90.0
MERGE_GAP_S = 5.0
PAD_S = 5.0
MOTION_MAX = 0.35  # share of changing frame pairs above which an embedded video is assumed
NAME_TOKENS = ("[student]", "[person]")

# PG rule (team brief UPDATE 6): audio cannot be cleaned, so a clip window may not contain a
# cue the PG filter changed (`pg: true`). Until every transcript carries that flag, the cue
# text is also checked against this basic list (whole words, any case).
PROFANITY = re.compile(
    r"\b(?:f+u+c+k\w*|motherf\w*|shit\w*|bullshit\w*|horseshit|"
    r"damn\w*|dammit|goddam\w*|god\s+damn\w*|hell|hellish|crap\w*|piss\w*|bitch\w*|bastard\w*|"
    r"ass|asses|asshole\w*|jackass\w*|badass\w*|dumbass\w*|dick|dicks|cock|cocks|prick|pricks|"
    r"wtf|omfg|oh\s+my\s+god|my\s+god|jesus|christ|jeez|geez|holy\s+(?:god|christ))\b"
    # a censored or cut-off f-word ("f---", "f-ing"), but not "F-score" or "F-test"
    r"|\bf[\-*]+(?:ing|ed|er|in)?(?![\w\-*])",
    re.IGNORECASE)


def pg_problem(c: dict) -> bool:
    """True when a cue was softened by the PG filter or still contains a listed word."""
    return bool(c.get("pg")) or bool(PROFANITY.search(c.get("text") or ""))


ENCODE = ["-c:v", "libx264", "-preset", "medium", "-crf", "26", "-tune", "stillimage",
          "-pix_fmt", "yuv420p", "-vf", "scale=-2:720", "-r", "25",
          "-c:a", "aac", "-b:a", "96k", "-ac", "1", "-movflags", "+faststart"]
ENCODE_SMALL = ["-crf", "30"]  # second pass when the first is over TARGET_BYTES
TARGET_BYTES = 3_000_000


# --------------------------------------------------------------------------- rule helpers


def clean_cue(c: dict) -> bool:
    """A cue that may be heard in a clip: instructor speech, no name mask, PG language."""
    text = c.get("text") or ""
    return (c.get("speaker") == "instructor" and not any(t in text for t in NAME_TOKENS)
            and not pg_problem(c))


def overlapping(cues: list[dict], a: float, b: float) -> list[dict]:
    return [c for c in cues if float(c["end"]) > a and float(c["start"]) < b]


def window_problem(cues: list[dict], a: float, b: float, pad: float = PAD_S) -> str | None:
    """Why the clip [a, b] is not allowed by the speech rules, or None when it is."""
    inside = overlapping(cues, a, b)
    if not inside:
        return "no_speech"
    for c in overlapping(cues, a - pad, b + pad):
        sp = c.get("speaker")
        if sp == "student":
            return "student_speech"
        if sp != "instructor":
            return "unclear_speech"
        if any(t in (c.get("text") or "") for t in NAME_TOKENS):
            return "name_in_window"
        if pg_problem(c):
            return "pg_language"
    return None


def merge_frame_windows(windows: list[tuple[float, float]], gap: float = MERGE_GAP_S) -> list[tuple[float, float]]:
    """Join windows less than `gap` seconds apart (the spec's 5 s merge)."""
    out: list[list[float]] = []
    for a, b in sorted(windows):
        if out and a - out[-1][1] < gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [tuple(x) for x in out]


def snap(cues: list[dict], a: float, b: float, max_s: float = MAX_S) -> tuple[float, float] | None:
    """Clip bounds on cue boundaries inside [a, b]: whole cues only, at most max_s long."""
    inside = [c for c in cues if float(c["start"]) >= a and float(c["end"]) <= b]
    if not inside:
        return None
    inside.sort(key=lambda c: float(c["start"]))
    s = float(inside[0]["start"])
    ends = [float(c["end"]) for c in inside if float(c["end"]) - s <= max_s]
    if not ends:
        return None
    return s, max(ends)


def clean_segments(cues: list[dict], a: float, b: float, pad: float = PAD_S,
                   max_s: float = MAX_S) -> list[tuple[float, float]]:
    """Clips inside [a, b] made of whole cues, each passing every speech rule.

    For each start cue, extend over following cues while the padded span stays clean and
    within max_s. Returns candidate (start, end) pairs, longest first.
    """
    inside = sorted((c for c in cues if float(c["start"]) >= a and float(c["end"]) <= b),
                    key=lambda c: float(c["start"]))
    out = []
    for i, c0 in enumerate(inside):
        s = float(c0["start"])
        best = None
        for c in inside[i:]:
            e = float(c["end"])
            if e - s > max_s:
                break
            if window_problem(cues, s, e, pad) is None:
                best = e
            elif not clean_cue(c):
                break
        if best is not None:
            out.append((s, best))
    out.sort(key=lambda x: (-(x[1] - x[0]), x[0]))
    return out


# --------------------------------------------------------------------------- decisions


@dataclass
class Candidate:
    """A clip window considered for one slide: kept when `reason` is None."""

    slide_id: str
    start: float
    end: float
    reason: str | None = None  # rejection reason, None when kept
    note: str = ""


def slide_block(slide: dict, session_key: tuple[str, int], pulled: set[str]) -> str | None:
    """Why a slide may never have a clip, whatever its windows look like; None when it may."""
    if session_key in NO_CLIP_SESSIONS:
        return "private_case_session"
    for f in NO_CLIP_FLAGS:
        if f in (slide.get("flags") or []):
            return f"flag_{f}"
    if any("[student]" in (slide.get(k) or "") for k in ("title", "text", "notes", "ocr_text")):
        return "slide_text_name"
    if slide["slide_id"] in pulled:
        return "override_pulled"
    return None


def window_motion(meta: dict, slide_id: str, a: float, b: float) -> float:
    """Frame-weighted share of changing frame pairs over the frame runs inside [a, b]."""
    num = den = 0.0
    for w in meta.get("frame_windows", []):
        if w["slide_id"] == slide_id and w["end"] > a and w["start"] < b:
            num += w.get("motion", 0.0) * w.get("frames", 1)
            den += w.get("frames", 1)
    return num / den if den else 0.0


def usable_spans(sid: str, frame_w: list[tuple[float, float]], meta: dict,
                 step: float = A.STEP) -> list[tuple[float, float]]:
    """Spans where every sampled frame showed this slide on its own.

    With frame-level detail from align (meta["frame_windows"]), each run is trimmed to its
    first and last matching frame and split around frames that only matched through
    smoothing, so a clip never includes a bridged flash of another screen. This is
    stricter than the spec's 5 s merge, which is used only when that detail is missing.
    """
    runs = [w for w in meta.get("frame_windows", []) if w["slide_id"] == sid]
    if not runs:
        return [(a + step / 2, b - step / 2) for a, b in merge_frame_windows(frame_w) if b - a > step]
    out = []
    for w in runs:
        a, b = w["start"] + step / 2, w["end"] - step / 2
        for t in sorted(w.get("unconfirmed", [])):
            if t - step > a:
                out.append((a, t - step))
            a = max(a, t + step)
        if b > a:
            out.append((a, b))
    return [(round(a, 2), round(b, 2)) for a, b in out if b > a]


def decide_slide(slide: dict, rec: dict, cues: list[dict], meta: dict,
                 session_key: tuple[str, int], pulled: set[str],
                 step: float = A.STEP) -> tuple[Candidate | None, list[Candidate]]:
    """Return (kept clip or None, rejected candidates) for one slide."""
    sid = slide["slide_id"]
    frame_w = [tuple(w) for w, m in zip(rec.get("windows", []), rec.get("methods", []))
               if m == "frame"]
    if not frame_w:
        return None, [Candidate(sid, 0.0, 0.0, "no_frame_match")]
    merged = usable_spans(sid, frame_w, meta, step)
    block = slide_block(slide, session_key, pulled)
    if block:
        return None, [Candidate(sid, round(a, 2), round(b, 2), block) for a, b in merged]
    if not cues:
        return None, [Candidate(sid, round(a, 2), round(b, 2), "no_transcript") for a, b in merged]
    rejected, options = [], []
    for a, b in merged:
        if b - a < MIN_S:
            rejected.append(Candidate(sid, a, b, "too_short"))
            continue
        motion = window_motion(meta, sid, a, b)
        if motion > MOTION_MAX:
            rejected.append(Candidate(sid, a, b, "embedded_video_suspected"))
            continue
        cut = snap(cues, a, b)
        if cut is None or cut[1] - cut[0] < MIN_S:
            rejected.append(Candidate(sid, a, b, "too_short_on_cues"))
            continue
        problem = window_problem(cues, *cut)
        if problem is None:
            note = "cut at 90 s" if (b - a) > MAX_S else "whole window"
            options.append(Candidate(sid, cut[0], cut[1], None, note))
            continue
        segs = [s for s in clean_segments(cues, a, b) if s[1] - s[0] >= MIN_S]
        if segs:
            options.append(Candidate(sid, segs[0][0], segs[0][1], None, "clean part of window"))
        else:
            rejected.append(Candidate(sid, a, b, problem))
    if not options:
        return None, rejected
    options.sort(key=lambda c: (-(c.end - c.start), c.start))
    return options[0], rejected


# --------------------------------------------------------------------------- encoding


def locate(t0: float, t1: float, videos: list[Path], durations: list[float]) -> tuple[Path, float] | None:
    """Map a timeline span to (video, local start) or None when it crosses a part boundary."""
    off = 0.0
    for v, d in zip(videos, durations):
        if t0 >= off and t1 <= off + d + 0.01:
            return v, t0 - off
        off += d
    return None


def encode(src: Path, local_start: float, dur: float, out: Path, extra: list[str] | None = None) -> None:
    """Encode one clip with ffmpeg; `extra` (["-crf", N]) overrides the quality setting."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".part.mp4")
    args = list(ENCODE)
    if extra:
        i = args.index("-crf")
        args[i + 1] = extra[1]
    # Map only the first video and audio stream: Zoom recordings carry a caption text track
    # (raw, not de-identified) that must never reach a clip. Metadata is dropped too.
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", f"{local_start:.3f}", "-i", str(src),
           "-t", f"{dur:.3f}", "-map", "0:v:0", "-map", "0:a:0", "-sn", "-dn",
           "-map_metadata", "-1", "-map_chapters", "-1", *args, str(tmp)]
    subprocess.run(cmd, check=True, capture_output=True)
    tmp.replace(out)


# --------------------------------------------------------------------------- per session


def read_pulled(s: A.Session) -> set[str]:
    """Slide ids pulled by hand: "no_clips" in the session's override files (ids only)."""
    out: set[str] = set()
    for p in (A.build_dir() / "overrides" / s.course / f"{s.key}.json",
              A.ARCHIVE / "_private" / "deid_overrides" / s.course / f"{s.key}.json"):
        d = read_json(p, {}) or {}
        if isinstance(d, dict):
            out.update(str(x) for x in d.get("no_clips", []) or [])
    return out


def reason_kept(c: Candidate) -> str:
    """The manifest's plain-language reason a clip passed every rule."""
    return (f"frame-matched {c.end - c.start:.0f} s ({c.note}), instructor only, "
            f"no [student]/[person] within 5 s, slide not flagged")


def encode_clip(src: Path, local_start: float, dur: float, out: Path) -> None:
    """Encode a clip, re-encoding at lower quality when the first pass is over TARGET_BYTES."""
    encode(src, local_start, dur, out)
    if out.stat().st_size > TARGET_BYTES:
        encode(src, local_start, dur, out, ENCODE_SMALL)


# Manifest rows from the previous run, by slide id (main fills it): a clip whose window,
# source and encoding are unchanged and whose file exists is not encoded again.
_previous: dict[str, dict] = {}


def _rejected_row(s: A.Session, slide_id: str, start: float, end: float, reason: str | None) -> dict:
    """A rejected.json row: ids, times and a reason code only, never transcript text."""
    return {"slide_id": slide_id, "course": s.course, "session": s.session,
            "start": round(start, 2), "end": round(end, 2), "reason": reason}


def _final_check_passes(transcript: Path, clip: Candidate) -> bool:
    """Re-read the transcript file right before encoding and check the padded window once more."""
    fresh = read_json(transcript, {}).get("cues", [])
    if window_problem(fresh, clip.start, clip.end) is not None:
        return False
    return all(c.get("speaker") == "instructor" for c in overlapping(fresh, clip.start - PAD_S, clip.end + PAD_S))


def clips_for_session(s: A.Session, dry_run: bool = False, force: bool = False,
                      verbose: bool = True, jobs: int = 1) -> tuple[list[dict], list[dict]]:
    """Decide, encode and prune one session's clips. Returns (manifest rows, rejected rows)."""
    p = A.session_paths(s)
    b = A.build_dir()
    recs = read_json(p["out"], None)
    meta = read_json(p["meta"], {}) or {}
    slides = read_json(p["slides"], [])
    if recs is None or not slides:
        if verbose:
            print(f"{s.label}: no alignment or no deck, no clips")
        return [], []
    tr = read_json(p["transcript"], None)
    cues = sorted(tr["cues"], key=lambda c: float(c["start"])) if tr else []
    by_id = {r["slide_id"]: r for r in recs}
    pulled = read_pulled(s)
    durations = meta.get("parts") or []
    kept, rejected = [], []
    jobs_todo: list[tuple] = []
    for sl in slides:
        rec = by_id.get(sl["slide_id"], {"windows": [], "methods": []})
        clip, rej = decide_slide(sl, rec, cues, meta, (s.course, s.session), pulled)
        rejected += [_rejected_row(s, r.slide_id, r.start, r.end, r.reason) for r in rej]
        if clip is None:
            continue
        loc = locate(clip.start, clip.end, s.videos, durations)
        if loc is None:
            rejected.append(_rejected_row(s, clip.slide_id, clip.start, clip.end, "spans_video_parts"))
            continue
        if not _final_check_passes(p["transcript"], clip):
            rejected.append(_rejected_row(s, clip.slide_id, clip.start, clip.end, "final_check_failed"))
            continue
        src, local = loc
        out = b / "clips" / f"{clip.slide_id}.mp4"
        row = _manifest_row(s, clip, src)
        if not dry_run and (force or not _unchanged_on_disk(row, out)):
            jobs_todo.append((src, local, clip.end - clip.start, out))
        kept.append(row)
    if not dry_run:
        _encode_and_prune(s, b / "clips", jobs_todo, kept, jobs)
    if verbose:
        c = Counter(r["reason"] for r in rejected)
        print(f"{s.label}: {len(kept)} clips kept, {len(rejected)} candidates rejected "
              + ", ".join(f"{k}={v}" for k, v in c.most_common()))
    return kept, rejected


def _manifest_row(s: A.Session, clip: Candidate, src: Path) -> dict:
    """The manifest.json row for a kept clip (its size is added after encoding)."""
    return {"slide_id": clip.slide_id, "course": s.course, "session": s.session,
            "start": round(clip.start, 2), "end": round(clip.end, 2),
            "reason_kept": reason_kept(clip), "duration": round(clip.end - clip.start, 2),
            "source": src.name, "version": CLIPS_VERSION}


def _unchanged_on_disk(row: dict, out: Path) -> bool:
    """True when the last run kept this clip with the same window, source and encoding, and its file exists."""
    prev = _previous.get(row["slide_id"])
    same = prev and all(prev.get(k) == row[k] for k in ("start", "end", "source", "version"))
    return bool(same and out.exists())


def _encode_and_prune(s: A.Session, clip_dir: Path, jobs_todo: list[tuple], kept: list[dict], jobs: int) -> None:
    """Encode the new clips in parallel, record each kept clip's size, and delete this session's stale clips."""
    with ThreadPoolExecutor(max(1, jobs)) as pool:
        list(pool.map(lambda j: encode_clip(*j), jobs_todo))
    for row in kept:
        row["bytes"] = (clip_dir / f"{row['slide_id']}.mp4").stat().st_size
    keep_ids = {r["slide_id"] for r in kept}
    for f in clip_dir.glob(f"{s.course}-{s.key}-*.mp4"):
        if f.stem not in keep_ids:
            f.unlink()


def summarize(manifest: list[dict], rejected: list[dict]) -> dict:
    """Counts per reason, per rejected window and per slide left without a clip.

    A slide without a clip is counted under the reason given for its longest window.
    """
    kept = {r["slide_id"] for r in manifest}
    for r in rejected:
        r["slide_has_clip"] = r["slide_id"] in kept
    longest: dict[str, dict] = {}
    for r in rejected:
        if r["slide_has_clip"]:
            continue
        cur = longest.get(r["slide_id"])
        if cur is None or (r["end"] - r["start"]) > (cur["end"] - cur["start"]):
            longest[r["slide_id"]] = r
    return {
        "clips": len(manifest),
        "slides_without_clip": dict(Counter(r["reason"] for r in longest.values()).most_common()),
        "counts": dict(Counter(r["reason"] for r in rejected).most_common()),
        "rejected": rejected,
    }


def main(argv: list[str] | None = None) -> int:
    """Command line: cut clips for the chosen sessions and rewrite manifest.json and rejected.json.

    Rows for sessions not in this run are kept, so one session can be redone on its own.
    """
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--course", choices=sorted(A.COURSE_PREFIX))
    ap.add_argument("--session", type=int)
    ap.add_argument("--all", action="store_true", help="every session (default when no --session)")
    ap.add_argument("--dry-run", action="store_true", help="decide and report, write nothing")
    ap.add_argument("--force", action="store_true", help="re-encode clips that already exist")
    ap.add_argument("--jobs", type=int, default=3, help="clips encoded in parallel")
    args = ap.parse_args(argv)
    sessions = A.discover_sessions(args.course, args.session)
    if not sessions:
        print("no sessions found", file=sys.stderr)
        return 1
    b = A.build_dir()
    man_path, rej_path = b / "clips" / "manifest.json", b / "clips" / "rejected.json"
    manifest = read_json(man_path, []) or []
    old_rej = (read_json(rej_path, {}) or {}).get("rejected", [])
    _previous.update({r["slide_id"]: r for r in manifest})
    done = {(s.course, s.session) for s in sessions}
    manifest = [r for r in manifest if (r["course"], int(r["session"])) not in done]
    rejected = [r for r in old_rej if (r["course"], int(r["session"])) not in done]
    for s in sessions:
        k, r = clips_for_session(s, dry_run=args.dry_run, force=args.force, jobs=args.jobs)
        manifest += k
        rejected += r
    manifest.sort(key=lambda r: r["slide_id"])
    rejected.sort(key=lambda r: (r["slide_id"], r["start"]))
    report = summarize(manifest, rejected)
    if args.dry_run:
        print(f"dry run: {len(manifest)} clips would be kept; slides without a clip "
              f"{report['slides_without_clip']}")
        return 0
    write_json(man_path, manifest)
    write_json(rej_path, report)
    total = sum(r.get("bytes", 0) for r in manifest)
    print(f"{len(manifest)} clips, {total / 1e6:.1f} MB; slides without a clip "
          f"{report['slides_without_clip']}; rejected windows {report['counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

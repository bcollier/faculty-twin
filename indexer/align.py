"""Slide-to-video alignment for the Faculty Twin indexer (stage 3).

For each slide, find the time windows when it was on screen in the class
recording and what I said then. Everything this script reads and writes is
private: nothing it touches belongs in the repo.

Reads, per session:
  <archive>/<course folder>/2026 Fall/<NN> <date> <title>/video.mp4 (+ video_part2.mp4, ...)
  _build/slides/<course>/s<NN>/slides.json and <slide_id>-thumb.webp
  _build/transcripts/<course>/s<NN>.json  (optional; de-identified cues)

Writes:
  _build/align/<course>/s<NN>.json       [{slide_id, windows, methods, transcript}] (SPEC contract)
  _build/align/<course>/s<NN>.meta.json  coverage, thresholds, per-window frame stats (clips.py reads it)
  _build/.cache/align/<course>-s<NN>.npz sampled grayscale frames, so re-runs skip decoding

Method (docs/SPEC.md, Preprocessing pipeline, stage 3):
  1. Frame matching. ffmpeg samples one small grayscale frame every 2 s. Each frame is
     compared with every slide thumbnail of the session by normalized correlation on
     64x36 thumbnails. Three crops are tried per frame: the whole frame, the largest
     bright slide-shaped region (PowerPoint presenter view puts the current slide in a
     big box above a strip of small thumbnails; the strip never wins because it is not
     slide-shaped), and the session's typical slide box (for dark slides the region
     finder misses). A frame matches when the best score clears MATCH_MIN and beats the
     runner-up by MARGIN_MIN. Near-identical slides (repeated section dividers) are one
     group; the member nearest the previous match wins. One-frame dropouts inside a run
     are bridged and runs shorter than 4 s are dropped. Zoom name tiles, Canvas pages,
     terminals, notebooks and embedded videos score low and match nothing.
  2. Text fallback. Time not covered by frame matches is cut into chunks of up to 30 s.
     Each chunk's instructor speech is scored against every slide's title, text, notes
     and OCR text with TF-IDF cosine. A dynamic program assigns chunks in slide order
     (never backward, except a jump back of one or two slides), bounded by the frame
     matches on either side. Chunks whose best score is below TEXT_MIN stay unassigned.
  3. Each slide's transcript is the de-identified *instructor* cues whose midpoint
     lies inside its windows. Student and unclear cues are never attached.

Multi-part recordings (video.mp4, video_part2.mp4) share one timeline: part 2 starts at
part 1's duration, the same convention the transcript stage uses.

Run (no .venv in the repo):
  uv run --no-project --with numpy --with pillow --with scikit-learn \\
      python -m indexer.align [--course 70445] [--session 6 | --all] [--force]
  ... python -m indexer.align --course 70445 --session 6 --dump-check DIR   (calibration sheets)

The script never prints transcript text.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

if __package__ in (None, ""):  # run as a script (python indexer/align.py): make `indexer` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from indexer import layout  # noqa: E402
from indexer.layout import TERM, read_json, slide_text, write_json  # noqa: E402

ALIGN_VERSION = 2

# A module attribute (not read through layout at call time) so tests can point it at a fixture archive.
ARCHIVE = layout.DEFAULT_ARCHIVE
# Course code -> the "70-445" prefix its archive folder starts with.
COURSE_PREFIX = {code: folder.split(" ", 1)[0] for code, folder in layout.COURSE_FOLDERS.items()}

# Sampling
STEP = 2.0  # seconds between sampled frames
FRAME_W = 256  # sampled frame width in pixels (height keeps the aspect ratio)
FEAT_W, FEAT_H = 64, 36  # comparison thumbnail

# Frame matching thresholds (calibrated on 70445 s06 and 45884 s05; see the PR)
MATCH_MIN = 0.45  # best correlation must clear this
MARGIN_MIN = 0.04  # ... and beat the best non-duplicate runner-up by this much
STRONG_MIN = 0.60  # below this score the margin must be at least LOW_MARGIN_MIN
LOW_MARGIN_MIN = 0.15
DUP_SIM = 0.93  # two slides this similar are treated as one group
MIN_RUN_S = 4.0  # runs shorter than this are smoothed away

# Slide region finder
DARK = 50  # gray level below which a pixel is background in presenter view
MIN_BOX_FRAC = 0.12  # a slide box covers at least this much of the frame
ASPECT_RANGE = (1.25, 2.1)

# Text fallback
CHUNK_S = 30.0
MIN_CHUNK_S = 8.0
MIN_CHUNK_WORDS = 12
TEXT_MIN = 0.12  # TF-IDF cosine needed to keep a text assignment
BACK_PENALTY = 0.03  # per slide moved backward (at most 2)
JUMP_PENALTY = 0.004  # per slide skipped forward

# Embedded-video signal for clips.py: consecutive frames in a window whose correlation
# drops below this count as "moving".
MOTION_CORR = 0.985


def build_dir() -> Path:
    """The build folder: $FT_BUILD_DIR when set (calibration runs and tests), else <archive>/_build."""
    return Path(os.environ.get("FT_BUILD_DIR", layout.build_dir(ARCHIVE))).expanduser()


# --------------------------------------------------------------------------- sessions


@dataclass
class Session:
    """One class session: its course, number, archive folder and recording parts."""

    course: str
    session: int
    folder: Path | None
    videos: list[Path] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"s{self.session:02d}"

    @property
    def label(self) -> str:
        return f"{self.course}-{self.key}"


def discover_sessions(course: str | None = None, session: int | None = None) -> list[Session]:
    """Every `<NN> ...` session folder of the known courses, with its videos in part order."""
    out: list[Session] = []
    for code, prefix in COURSE_PREFIX.items():
        if course and code != course:
            continue
        for cdir in sorted(ARCHIVE.glob(f"{prefix}*")):
            term = cdir / TERM
            if not term.is_dir():
                continue
            for sdir in sorted(term.iterdir()):
                m = re.match(r"(\d{2}) ", sdir.name)
                if not (m and sdir.is_dir()):
                    continue
                n = int(m.group(1))
                if session and n != session:
                    continue
                out.append(Session(code, n, sdir, session_videos(sdir)))
    return out


def session_videos(folder: Path) -> list[Path]:
    """video.mp4 first, then video_part2.mp4, video_part3.mp4, ... in part order."""
    vids = [folder / "video.mp4"] if (folder / "video.mp4").exists() else []
    parts = []
    for p in folder.glob("video_part*.mp4"):
        m = re.match(r"video_part(\d+)\.mp4$", p.name)
        if m:
            parts.append((int(m.group(1)), p))
    return vids + [p for _, p in sorted(parts)]


def fingerprint(*paths: Path | None) -> list:
    """Change key for the alignment cache. A missing file counts too, so a transcript that appears later re-aligns."""
    return layout.file_fingerprint(*paths, include_missing=True)


# --------------------------------------------------------------------------- video


def probe(path: Path) -> tuple[float, int, int]:
    """(duration in seconds, width, height) of a video's first stream, from ffprobe."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    st = j["streams"][0]
    return float(j["format"]["duration"]), int(st["width"]), int(st["height"])


def extract_frames(path: Path, step: float = STEP, width: int = FRAME_W,
                   height: int | None = None) -> tuple[np.ndarray, float]:
    """Return (frames uint8 [N, H, W], duration). Frame i is at time i * step."""
    dur, w, h = probe(path)
    if height is None:
        height = max(2, int(round(width * h / w / 2)) * 2)
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-an", "-sn", "-vf",
           f"fps=1/{step},scale={width}:{height}:flags=area,format=gray", "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    n = len(raw) // (width * height)
    frames = np.frombuffer(raw[: n * width * height], np.uint8).reshape(n, height, width)
    return frames, dur


def session_frames(s: Session, cache_dir: Path) -> tuple[np.ndarray, np.ndarray, list[float]]:
    """Sampled frames for the whole session timeline: (times, frames, part durations)."""
    fp = json.dumps({"v": fingerprint(*s.videos), "step": STEP, "w": FRAME_W})
    cache = cache_dir / f"{s.label}.npz"
    if cache.exists():
        try:
            z = np.load(cache, allow_pickle=False)
            if str(z["fp"]) == fp:
                return z["times"], z["frames"], [float(x) for x in z["durations"]]
        except (OSError, ValueError, KeyError):
            pass  # an unreadable or old-format cache is rebuilt below
    times, frames, durs = [], [], []
    offset = 0.0
    height = None
    for v in s.videos:
        fr, dur = extract_frames(v, height=height)
        height = fr.shape[1]
        n = min(len(fr), int(dur // STEP) + 1)
        fr = fr[:n]
        times.append(offset + np.arange(len(fr)) * STEP)
        frames.append(fr)
        durs.append(dur)
        offset += dur
    t = np.concatenate(times) if times else np.zeros(0)
    f = np.concatenate(frames) if frames else np.zeros((0, 2, 2), np.uint8)
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_name(cache.name + ".tmp.npz")
    np.savez_compressed(tmp, times=t, frames=f, durations=np.array(durs), fp=np.array(fp))
    tmp.replace(cache)
    return t, f, durs


# --------------------------------------------------------------------------- features


def feature(gray: np.ndarray) -> np.ndarray:
    """Zero-mean, unit-norm vector of a grayscale image resized to FEAT_W x FEAT_H."""
    from PIL import Image

    im = Image.fromarray(np.ascontiguousarray(gray, dtype=np.uint8))
    a = np.asarray(im.resize((FEAT_W, FEAT_H), Image.Resampling.BOX), dtype=np.float32).ravel()
    a = a - a.mean()
    n = float(np.linalg.norm(a))
    if n < 1e-3 * a.size ** 0.5:  # a flat image matches nothing
        return np.zeros_like(a)
    return a / n


def slide_features(thumbs: list[Path]) -> np.ndarray:
    """One feature row per slide thumbnail (see `feature`)."""
    from PIL import Image

    rows = []
    for p in thumbs:
        rows.append(feature(np.asarray(Image.open(p).convert("L"))))
    return np.stack(rows) if rows else np.zeros((0, FEAT_W * FEAT_H), np.float32)


def slide_box(gray: np.ndarray) -> tuple[int, int, int, int] | None:
    """Bounding box (y0, y1, x0, x1) of the largest bright, slide-shaped region, or None."""
    boxes = slide_boxes(gray, 1)
    return boxes[0] if boxes else None


def slide_boxes(gray: np.ndarray, k: int = 3) -> list[tuple[int, int, int, int]]:
    """Up to k bright, slide-shaped regions (y0, y1, x0, x1), largest first.

    In PowerPoint presenter view the current slide sits in a large box on a dark
    background, above a strip of small thumbnails; the strip is wide and short, so the
    aspect check rejects it. In full-screen slideshow the box is the whole frame.
    """
    from scipy import ndimage

    h, w = gray.shape
    mask = gray > DARK
    mask = ndimage.binary_opening(mask, structure=np.ones((3, 3), bool), iterations=2)
    lab, n = ndimage.label(mask)
    found = []
    for sl in ndimage.find_objects(lab) if n else []:
        if sl is None:
            continue
        bh, bw = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        area = bh * bw
        if area < MIN_BOX_FRAC * h * w or not (ASPECT_RANGE[0] <= bw / max(bh, 1) <= ASPECT_RANGE[1]):
            continue
        found.append((area, (sl[0].start, sl[0].stop, sl[1].start, sl[1].stop)))
    found.sort(key=lambda x: -x[0])
    return [b for _, b in found[:k]]


def crop(gray: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    """The (y0, y1, x0, x1) region of a frame."""
    y0, y1, x0, x1 = box
    return gray[y0:y1, x0:x1]


# --------------------------------------------------------------------------- frame matching


@dataclass
class FrameMatch:
    """Per-frame best slide, its score and margin, and which crop won."""

    best: np.ndarray  # slide index per frame (argmax), -1 when nothing scored
    score: np.ndarray  # best correlation
    margin: np.ndarray  # best minus best non-duplicate runner-up
    method: np.ndarray  # 0 full frame, 1 region, 2 session box
    feats: np.ndarray  # feature of the winning crop per frame (for motion checks)
    session_box: tuple | None = None


def duplicate_groups(S: np.ndarray, thr: float = DUP_SIM) -> np.ndarray:
    """Boolean [n, n]: slides that look the same (including the slide itself)."""
    if len(S) == 0:
        return np.zeros((0, 0), bool)
    return thr <= (S @ S.T)


def _score(F: np.ndarray, S: np.ndarray, dup: np.ndarray):
    sc = F @ S.T  # [N, n]
    best = sc.argmax(1)
    top = sc[np.arange(len(sc)), best]
    masked = np.where(dup[best], -np.inf, sc)
    runner = masked.max(1)
    runner = np.where(np.isfinite(runner), runner, -1.0)
    return best, top, top - runner


def match_frames(frames: np.ndarray, S: np.ndarray, dup: np.ndarray | None = None) -> FrameMatch:
    """Score every sampled frame against every slide, trying the whole frame, the slide region and the session box."""
    n = len(frames)
    if dup is None:
        dup = duplicate_groups(S)
    if n == 0 or len(S) == 0:
        z = np.zeros(n)
        return FrameMatch(np.full(n, -1), z, z, np.zeros(n, int), np.zeros((n, FEAT_W * FEAT_H), np.float32))
    full = np.stack([feature(f) for f in frames])
    # Region candidate: of up to three slide-shaped regions, the one that best matches a slide.
    zero = np.zeros(FEAT_W * FEAT_H, np.float32)
    boxes, regs = [], []
    for f in frames:
        bs = slide_boxes(f, 3)
        if not bs:
            boxes.append(None)
            regs.append(zero)
            continue
        fs = np.stack([feature(crop(f, b)) for b in bs])
        k = int((fs @ S.T).max(1).argmax())
        boxes.append(bs[k])
        regs.append(fs[k])
    reg = np.stack(regs)
    cands = [_score(full, S, dup), _score(reg, S, dup)]
    feats = [full, reg]

    # Session box: the median region of frames that matched through the region finder.
    pick = np.argmax(np.stack([c[1] for c in cands]), axis=0)
    ok = (pick == 1) & (cands[1][1] >= MATCH_MIN) & (cands[1][2] >= MARGIN_MIN)
    sbox = None
    good = [boxes[i] for i in np.flatnonzero(ok) if boxes[i]]
    if len(good) >= 5:
        sbox = tuple(int(x) for x in np.median(np.array(good), axis=0))
        sb = np.stack([feature(crop(f, sbox)) for f in frames])
        cands.append(_score(sb, S, dup))
        feats.append(sb)

    tops = np.stack([c[1] for c in cands])
    which = tops.argmax(0)
    idx = np.arange(n)
    best = np.stack([c[0] for c in cands])[which, idx]
    score = tops[which, idx]
    margin = np.stack([c[2] for c in cands])[which, idx]
    feat = np.stack(feats)[which, idx]
    return FrameMatch(best, score, margin, which, feat, sbox)


def frame_labels(fm: FrameMatch, dup: np.ndarray, step: float = STEP,
                 match_min: float = MATCH_MIN, margin_min: float = MARGIN_MIN,
                 min_run_s: float = MIN_RUN_S) -> np.ndarray:
    """Per-frame slide index or -1, after thresholds, duplicate resolution and smoothing."""
    return smooth_labels(raw_labels(fm, dup, match_min, margin_min), step, min_run_s)


def raw_labels(fm: FrameMatch, dup: np.ndarray, match_min: float = MATCH_MIN,
               margin_min: float = MARGIN_MIN) -> np.ndarray:
    """Per-frame slide index or -1 after thresholds and duplicate resolution, before smoothing."""
    need = np.where(fm.score >= STRONG_MIN, margin_min, max(margin_min, LOW_MARGIN_MIN))
    lab = np.where((fm.score >= match_min) & (fm.margin >= need), fm.best, -1)
    return resolve_duplicates(lab, dup)


def resolve_duplicates(lab: np.ndarray, dup: np.ndarray) -> np.ndarray:
    """Within a group of look-alike slides, keep the member nearest the last confident match."""
    lab = lab.copy()
    prev = -1
    for i, j in enumerate(lab):
        if j < 0:
            continue
        group = np.flatnonzero(dup[j])
        if len(group) > 1:
            ref = prev if prev >= 0 else j
            # prefer the next slide forward from the previous one; ties go to the lower index
            j = int(min(group, key=lambda g: (abs(g - ref - 0.5), g)))
            lab[i] = j
        prev = j
    return lab


def smooth_labels(lab: np.ndarray, step: float = STEP, min_run_s: float = MIN_RUN_S) -> np.ndarray:
    """Bridge one-frame dropouts inside a run, then drop runs shorter than min_run_s."""
    lab = lab.copy()
    n = len(lab)
    # bridge one-frame dropouts inside a run of the same slide (a confident match to a
    # different slide is not overwritten; the short-run rule below drops it instead)
    for i in range(1, n - 1):
        if lab[i] < 0 and lab[i - 1] >= 0 and lab[i - 1] == lab[i + 1]:
            lab[i] = lab[i - 1]
    # drop runs shorter than min_run_s
    i = 0
    while i < n:
        j = i
        while j + 1 < n and lab[j + 1] == lab[i]:
            j += 1
        if lab[i] >= 0 and (j - i + 1) * step < min_run_s:
            lab[i: j + 1] = -1
        i = j + 1
    return lab


def runs(lab: np.ndarray) -> list[tuple[int, int, int]]:
    """[(label, first index, last index)] for runs of labels >= 0."""
    out = []
    i, n = 0, len(lab)
    while i < n:
        j = i
        while j + 1 < n and lab[j + 1] == lab[i]:
            j += 1
        if lab[i] >= 0:
            out.append((int(lab[i]), i, j))
        i = j + 1
    return out


def motion_fraction(feats: np.ndarray, a: int, b: int) -> float:
    """Share of consecutive frame pairs in [a, b] whose crops changed (correlation < MOTION_CORR)."""
    if b <= a:
        return 0.0
    c = np.sum(feats[a:b] * feats[a + 1: b + 1], axis=1)
    return float(np.mean(c < MOTION_CORR))


# --------------------------------------------------------------------------- text fallback


def cue_mid(c: dict) -> float:
    return (float(c["start"]) + float(c["end"])) / 2


def subtract(intervals: list[tuple[float, float]], total: tuple[float, float]) -> list[tuple[float, float]]:
    """Parts of `total` not covered by `intervals`."""
    lo, hi = total
    out, cur = [], lo
    for a, b in sorted(intervals):
        if b <= cur:
            continue
        if a > cur:
            out.append((cur, min(a, hi)))
        cur = max(cur, b)
        if cur >= hi:
            break
    if cur < hi:
        out.append((cur, hi))
    return [(a, b) for a, b in out if b > a]


def chunk_stretches(stretches: list[tuple[float, float]], chunk_s: float = CHUNK_S,
                    min_s: float = MIN_CHUNK_S) -> list[tuple[float, float]]:
    """Cut each stretch into equal chunks of at most chunk_s seconds, dropping chunks under min_s."""
    out = []
    for a, b in stretches:
        n = max(1, int(np.ceil((b - a) / chunk_s)))
        w = (b - a) / n
        for k in range(n):
            s, e = a + k * w, a + (k + 1) * w
            if e - s >= min_s:
                out.append((s, e))
    return out


def tfidf_scores(slide_docs: list[str], chunk_texts: list[str]) -> np.ndarray:
    """Cosine similarity of each speech chunk to each slide's text, TF-IDF fit on both."""
    from sklearn.feature_extraction.text import TfidfVectorizer

    if not slide_docs or not chunk_texts:
        return np.zeros((len(chunk_texts), len(slide_docs)))
    vec = TfidfVectorizer(stop_words="english", sublinear_tf=True, min_df=1,
                          token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z0-9\-]+\b")
    try:
        vec.fit(slide_docs + chunk_texts)
    except ValueError:  # empty vocabulary
        return np.zeros((len(chunk_texts), len(slide_docs)))
    A = vec.transform(chunk_texts)
    B = vec.transform(slide_docs)
    return (A @ B.T).toarray()


def monotone_dp(C: np.ndarray, lo: int = 0, hi: int | None = None,
                back: int = 2, back_penalty: float = BACK_PENALTY,
                jump_penalty: float = JUMP_PENALTY) -> list[int]:
    """Best slide path for chunk scores C [chunks, slides].

    The slide index never decreases, except a jump back of at most `back` slides.
    States are restricted to [lo, hi]. Returns one slide index per chunk.
    """
    m, n = C.shape
    if m == 0 or n == 0:
        return []
    hi = n - 1 if hi is None else min(hi, n - 1)
    lo = max(0, min(lo, hi))
    allowed = np.full(n, -np.inf)
    allowed[lo: hi + 1] = 0.0
    j = np.arange(n)
    d = j[None, :] - j[:, None]  # d[i, k] = k - i, moving from slide i to slide k
    trans = np.where(d >= 0, -jump_penalty * np.maximum(d - 1, 0),
                     np.where(d >= -back, back_penalty * d, -np.inf))
    score = C[0] + allowed
    ptr = np.zeros((m, n), int)
    for t in range(1, m):
        tot = score[:, None] + trans
        ptr[t] = tot.argmax(0)
        score = tot[ptr[t], j] + C[t] + allowed
    path = [int(score.argmax())]
    for t in range(m - 1, 0, -1):
        path.append(int(ptr[t][path[-1]]))
    return path[::-1]


def text_align(slides: list[dict], cues: list[dict], stretches: list[tuple[float, float]],
               frame_runs_t: list[tuple[int, float, float]],
               text_min: float = TEXT_MIN) -> list[tuple[int, float, float, float]]:
    """Assign uncovered chunks to slides. Returns [(slide index, start, end, cosine)].

    frame_runs_t: [(slide index, start, end)] from frame matching, used as bounds.
    """
    instr = [c for c in cues if c.get("speaker") == "instructor"]
    chunks = chunk_stretches(stretches)
    texts, keep = [], []
    for a, b in chunks:
        t = " ".join(c["text"] for c in instr if a <= cue_mid(c) < b)
        if len(t.split()) >= MIN_CHUNK_WORDS:
            texts.append(t)
            keep.append((a, b))
    if not keep:
        return []
    C = tfidf_scores([slide_text(s) for s in slides], texts)
    out = []
    # Group consecutive chunks that sit between the same pair of frame matches.
    anchors = sorted(frame_runs_t, key=lambda r: r[1])

    def bounds(a: float, b: float) -> tuple[int, int]:
        """The slide range (two either side of the nearest frame matches) a chunk from a to b may match."""
        before = [r for r in anchors if r[2] <= a + 1e-6]
        after = [r for r in anchors if r[1] >= b - 1e-6]
        p = before[-1][0] if before else None
        q = after[0][0] if after else None
        lo = max(0, p - 2) if p is not None else 0
        hi = q + 2 if (q is not None and (p is None or q >= p)) else len(slides) - 1
        return lo, hi

    groups: list[list[int]] = []
    for i, (a, b) in enumerate(keep):
        if groups and bounds(*keep[groups[-1][-1]]) == bounds(a, b):
            groups[-1].append(i)
        else:
            groups.append([i])
    for g in groups:
        lo, hi = bounds(*keep[g[0]])
        path = monotone_dp(C[g], lo, hi)
        for i, j in zip(g, path):
            if C[i, j] >= text_min:
                out.append((j, keep[i][0], keep[i][1], float(C[i, j])))
    return out


# --------------------------------------------------------------------------- assembly


def merge_windows(wins: list[tuple[float, float, str]], gap: float = 0.0) -> list[tuple[float, float, str]]:
    """Merge touching windows of the same method."""
    out: list[list] = []
    for a, b, m in sorted(wins):
        if out and out[-1][2] == m and a - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b, m])
    return [tuple(x) for x in out]


def instructor_text(cues: list[dict], windows: list[tuple[float, float]]) -> str:
    """The instructor cues whose midpoint falls inside the windows, joined. Student and unclear cues never."""
    parts = []
    for c in cues:
        if c.get("speaker") != "instructor":
            continue
        t = cue_mid(c)
        if any(a <= t < b for a, b in windows):
            parts.append(c["text"].strip())
    return " ".join(p for p in parts if p)


def build_alignment(slides: list[dict], times: np.ndarray, lab: np.ndarray, total: float,
                    cues: list[dict], step: float = STEP) -> tuple[list[dict], list[tuple], list[tuple]]:
    """Return (records, frame windows with indices, text assignments)."""
    n = len(slides)
    wins: list[list[tuple[float, float, str]]] = [[] for _ in range(n)]
    frame_runs = []
    for j, a, b in runs(lab):
        s = max(0.0, float(times[a]) - step / 2)
        e = min(total, float(times[b]) + step / 2)
        wins[j].append((s, e, "frame"))
        frame_runs.append((j, s, e, a, b))
    covered = [(s, e) for _, s, e, _, _ in frame_runs]
    stretches = subtract(covered, (0.0, total))
    texts = text_align(slides, cues, stretches, [(j, s, e) for j, s, e, _, _ in frame_runs]) if cues else []
    for j, s, e, _ in texts:
        wins[j].append((s, e, "text"))
    records = []
    for i, sl in enumerate(slides):
        w = merge_windows(wins[i])
        windows = [[round(a, 2), round(b, 2)] for a, b, _ in w]
        records.append({
            "slide_id": sl["slide_id"],
            "windows": windows,
            "methods": [m for _, _, m in w],
            "transcript": instructor_text(cues, [(a, b) for a, b, _ in w]),
        })
    return records, frame_runs, texts


# --------------------------------------------------------------------------- per session


def session_paths(s: Session) -> dict:
    """Every file one session's alignment reads or writes."""
    b = build_dir()
    return {
        "slides": b / "slides" / s.course / s.key / "slides.json",
        "slide_dir": b / "slides" / s.course / s.key,
        "transcript": b / "transcripts" / s.course / f"{s.key}.json",
        "out": b / "align" / s.course / f"{s.key}.json",
        "meta": b / "align" / s.course / f"{s.key}.meta.json",
        "cache": b / ".cache" / "align",
    }


def _match_session(s: Session, p: dict) -> tuple[list[dict], np.ndarray, np.ndarray, np.ndarray, list[float],
                                                  FrameMatch]:
    """Sample the session's video and match every frame: (slides, dup groups, times, frames, durations, match)."""
    slides = read_json(p["slides"], [])
    S = slide_features([p["slide_dir"] / f"{sl['slide_id']}-thumb.webp" for sl in slides])
    dup = duplicate_groups(S)
    times, frames, durs = session_frames(s, p["cache"])
    return slides, dup, times, frames, durs, match_frames(frames, S, dup)


def _alignment_fingerprint(s: Session, p: dict) -> dict:
    """What an alignment depends on: videos, deck, transcript, and every threshold."""
    return {"version": ALIGN_VERSION, "videos": fingerprint(*s.videos),
            "slides": fingerprint(p["slides"]), "transcript": fingerprint(p["transcript"]),
            "params": [STEP, FRAME_W, MATCH_MIN, MARGIN_MIN, STRONG_MIN, LOW_MARGIN_MIN, DUP_SIM,
                       MIN_RUN_S, TEXT_MIN]}


def align_session(s: Session, force: bool = False, verbose: bool = True) -> dict | None:
    """Align one session and write its records and meta file. Returns the meta, or None when it cannot run."""
    p = session_paths(s)
    if not p["slides"].exists():
        if verbose:
            print(f"{s.label}: no slide deck, nothing to align")
        return None
    if not s.videos:
        if verbose:
            print(f"{s.label}: no video, nothing to align")
        return None
    fp = _alignment_fingerprint(s, p)
    old = read_json(p["meta"], {})
    if not force and old.get("fingerprint") == fp and p["out"].exists():
        if verbose:
            print(f"{s.label}: up to date")
        return old

    slides, dup, times, frames, durs, fm = _match_session(s, p)
    total = float(sum(durs))
    raw = raw_labels(fm, dup)
    lab = smooth_labels(raw)
    tr = read_json(p["transcript"], None)
    cues = tr["cues"] if tr else []
    records, frame_runs, texts = build_alignment(slides, times, lab, total, cues)
    write_json(p["out"], records)
    meta = _alignment_meta(s, fp, slides, records, fm, raw, lab, times, durs, frame_runs, texts, bool(cues))
    write_json(p["meta"], meta)
    if verbose:
        c = meta["coverage"]
        print(f"{s.label}: {meta['frames_matched']}/{meta['frames']} frames matched, "
              f"frame {c['frame']:.0%} text {c['text']:.0%} of {total / 60:.0f} min, "
              f"{meta['slides_with_frame']}/{len(slides)} slides seen"
              + ("" if cues else " (no transcript yet)"))
    return meta


def _alignment_meta(s: Session, fp: dict, slides: list[dict], records: list[dict], fm: FrameMatch,
                    raw: np.ndarray, lab: np.ndarray, times: np.ndarray, durs: list[float],
                    frame_runs: list, texts: list, has_transcript: bool) -> dict:
    """The <session>.meta.json contents: coverage, thresholds, and per-window frame stats for clips.py."""
    total = float(sum(durs))
    frame_s = sum(e - st for _, st, e, _, _ in frame_runs)
    text_s = sum(e - st for _, st, e, _ in texts)
    return {
        "course": s.course, "session": s.session, "fingerprint": fp,
        "duration": round(total, 2), "parts": [round(d, 2) for d in durs],
        "frames": int(len(times)), "frames_matched": int((lab >= 0).sum()),
        "crop_used": {k: int(((fm.method == i) & (lab >= 0)).sum())
                      for i, k in enumerate(["full", "region", "session_box"])},
        "session_box": fm.session_box,
        "coverage": {"frame": round(frame_s / total, 4) if total else 0,
                     "text": round(text_s / total, 4) if total else 0},
        "slides_with_frame": sum(1 for r in records if "frame" in r["methods"]),
        "slides_with_any": sum(1 for r in records if r["windows"]),
        "transcript": has_transcript,
        "thresholds": {"match_min": MATCH_MIN, "margin_min": MARGIN_MIN, "strong_min": STRONG_MIN,
                       "low_margin_min": LOW_MARGIN_MIN, "dup_sim": DUP_SIM,
                       "min_run_s": MIN_RUN_S, "text_min": TEXT_MIN},
        "frame_windows": [
            {"slide_id": slides[j]["slide_id"], "start": round(st, 2), "end": round(e, 2),
             "frames": b - a + 1, "min_score": round(float(fm.score[a: b + 1].min()), 3),
             "mean_score": round(float(fm.score[a: b + 1].mean()), 3),
             "motion": round(motion_fraction(fm.feats, a, b), 3),
             # frames inside the run that did not match this slide on their own (bridged)
             "unconfirmed": [round(float(times[k]), 2) for k in range(a, b + 1) if raw[k] != j]}
            for j, st, e, a, b in frame_runs
        ],
    }


# --------------------------------------------------------------------------- calibration


def dump_check(s: Session, out_dir: Path, n: int = 30, seed: int = 0, low_only: bool = False) -> None:
    """Write side-by-side sheets (sampled frame | matched slide) for hand checking.

    Images go to out_dir (use a private scratch folder; they show class video).
    """
    from PIL import Image, ImageDraw

    p = session_paths(s)
    slides, dup, times, frames, _, fm = _match_session(s, p)
    lab = frame_labels(fm, dup)
    rng = np.random.default_rng(seed)
    matched = np.flatnonzero((lab >= 0) & ((fm.score < STRONG_MIN) if low_only else True))
    unmatched = np.flatnonzero(lab < 0)
    pick_m = sorted(rng.choice(matched, min(n, len(matched)), replace=False)) if len(matched) else []
    pick_u = sorted(rng.choice(unmatched, min(n // 3, len(unmatched)), replace=False)) if len(unmatched) else []
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in list(pick_m) + list(pick_u):
        j = int(lab[i]) if lab[i] >= 0 else int(fm.best[i])
        fr = Image.fromarray(frames[i]).convert("RGB").resize((320, int(320 * frames.shape[1] / frames.shape[2])))
        th = Image.open(p["slide_dir"] / f"{slides[j]['slide_id']}-thumb.webp").convert("RGB")
        tile = Image.new("RGB", (660, max(fr.height, th.height) + 18), "white")
        tile.paste(fr, (0, 18))
        tile.paste(th, (340, 18))
        tag = "MATCH" if lab[i] >= 0 else "none"
        ImageDraw.Draw(tile).text(
            (4, 2), f"t={times[i]:.0f}s {tag} -> {slides[j]['slide_id']} sc={fm.score[i]:.2f} "
                    f"mg={fm.margin[i]:.2f} crop={int(fm.method[i])}", fill="black")
        rows.append((int(i), tag, slides[j]["slide_id"], tile))
    per = 6
    for k in range(0, len(rows), per):
        part = rows[k: k + per]
        sheet = Image.new("RGB", (660, sum(r[3].height for r in part)), "white")
        y = 0
        for r in part:
            sheet.paste(r[3], (0, y))
            y += r[3].height
        sheet.save(out_dir / f"{s.label}-check{seed}{'low' if low_only else ''}-{k // per:02d}.jpg", quality=80)
    write_json(out_dir / f"{s.label}-check.json",
               [{"frame": i, "t": float(times[i]), "tag": t, "slide_id": sid} for i, t, sid, _ in rows])
    print(f"{s.label}: wrote {len(rows)} check tiles to {out_dir}")


def main(argv: list[str] | None = None) -> int:
    """Command line: align each session's transcript to its slides, or write calibration sheets.

    A session whose inputs have not changed is skipped unless --force.
    """
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--course", choices=sorted(COURSE_PREFIX))
    ap.add_argument("--session", type=int)
    ap.add_argument("--all", action="store_true", help="every session (default when no --session)")
    ap.add_argument("--force", action="store_true", help="recompute even when inputs are unchanged")
    ap.add_argument("--dump-check", type=Path, help="write calibration sheets to this folder")
    ap.add_argument("--seed", type=int, default=0, help="sample seed for --dump-check")
    ap.add_argument("--low-only", action="store_true", help="--dump-check: only matches below STRONG_MIN")
    args = ap.parse_args(argv)
    sessions = discover_sessions(args.course, args.session)
    if not sessions:
        print("no sessions found", file=sys.stderr)
        return 1
    for s in sessions:
        if args.dump_check:
            dump_check(s, args.dump_check, seed=args.seed, low_only=args.low_only)
        else:
            align_session(s, force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())

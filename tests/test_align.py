"""Tests for indexer/align.py with synthetic images and cues only (no archive, no video).

Run: uv run --no-project --with pytest --with numpy --with pillow --with scikit-learn \
       python -m pytest tests/test_align.py -q
"""

import numpy as np
import pytest

pytest.importorskip("PIL")
pytest.importorskip("scipy")
pytest.importorskip("sklearn")

from indexer import align as A  # noqa: E402

W, H = 256, 144


def fake_slide(seed: int, w: int = 320, h: int = 180) -> np.ndarray:
    """A white slide with a dark title bar and a few random gray blocks."""
    rng = np.random.default_rng(seed)
    img = np.full((h, w), 245, np.uint8)
    img[10:28, 15: 15 + int(rng.integers(80, 250))] = 40
    for _ in range(4):
        y, x = int(rng.integers(40, h - 40)), int(rng.integers(10, w - 80))
        img[y: y + int(rng.integers(15, 35)), x: x + int(rng.integers(30, 70))] = int(rng.integers(60, 200))
    return img


def resize(img: np.ndarray, w: int, h: int) -> np.ndarray:
    from PIL import Image

    return np.asarray(Image.fromarray(img).resize((w, h), Image.Resampling.BILINEAR))


def presenter_view(slide: np.ndarray, strip: list[np.ndarray]) -> np.ndarray:
    """Dark screen, current slide in a big box, small thumbnails in a strip below."""
    f = np.full((H, W), 25, np.uint8)
    f[12:102, 48:208] = resize(slide, 160, 90)
    for k, s in enumerate(strip):
        x = 6 + k * 50
        f[112:136, x: x + 42] = resize(s, 42, 24)
    return f


@pytest.fixture
def deck():
    imgs = [fake_slide(i) for i in range(5)]
    S = np.stack([A.feature(im) for im in imgs])
    return imgs, S


def test_feature_is_unit_norm_and_flat_image_is_zero():
    v = A.feature(fake_slide(1))
    assert abs(float(np.linalg.norm(v)) - 1) < 1e-4
    assert not A.feature(np.full((50, 80), 128, np.uint8)).any()


def test_slide_box_finds_presenter_slide_not_thumbnail_strip(deck):
    imgs, _ = deck
    f = presenter_view(imgs[2], imgs)
    y0, y1, x0, x1 = A.slide_box(f)
    assert abs(y0 - 12) <= 3 and abs(y1 - 102) <= 3 and abs(x0 - 48) <= 3 and abs(x1 - 208) <= 3


def test_match_frames_presenter_fullscreen_and_non_slide_screens(deck):
    imgs, S = deck
    rng = np.random.default_rng(7)
    name_tile = np.zeros((H, W), np.uint8)
    name_tile[66:76, 100:156] = 200  # a black Zoom tile with a name card
    browser = rng.integers(0, 255, (H, W)).astype(np.uint8)  # busy unrelated screen
    frames = np.stack([
        presenter_view(imgs[2], imgs),
        resize(imgs[4], W, H),
        name_tile,
        browser,
    ])
    fm = A.match_frames(frames, S)
    dup = A.duplicate_groups(S)
    lab = A.frame_labels(fm, dup, min_run_s=0)
    assert lab.tolist() == [2, 4, -1, -1]


def test_smooth_bridges_dropouts_and_drops_short_runs():
    lab = np.array([3, 3, -1, 3, 3, 5, 7, 7, 7, -1, -1, 8])
    out = A.smooth_labels(lab, step=2.0, min_run_s=4.0)
    assert out.tolist() == [3, 3, 3, 3, 3, -1, 7, 7, 7, -1, -1, -1]


def test_smooth_does_not_overwrite_a_confident_other_slide():
    lab = np.array([3, 3, 4, 3, 3])
    out = A.smooth_labels(lab, step=2.0, min_run_s=4.0)
    assert out.tolist() == [3, 3, -1, 3, 3]


def test_duplicate_slides_resolve_to_the_one_near_the_previous_match():
    dup = np.eye(6, dtype=bool)
    dup[1, 4] = dup[4, 1] = True  # slides 1 and 4 are the same section divider
    lab = np.array([2, 2, 3, 3, 1, 1])  # the argmax picked slide 1, we were at slide 3
    assert A.resolve_duplicates(lab, dup).tolist() == [2, 2, 3, 3, 4, 4]


def test_monotone_dp_allows_short_jump_back_only():
    C = np.zeros((4, 6))
    C[0, 1] = C[1, 3] = C[2, 2] = C[3, 4] = 1.0  # 1 -> 3 -> back to 2 -> 4
    assert A.monotone_dp(C) == [1, 3, 2, 4]
    C = np.zeros((3, 6))
    C[0, 5] = C[1, 0] = C[2, 5] = 1.0  # a jump back of five is not allowed
    path = A.monotone_dp(C)
    assert all(b >= a - 2 for a, b in zip(path, path[1:]))


def test_monotone_dp_respects_bounds():
    C = np.zeros((2, 8))
    C[:, 0] = 1.0
    assert min(A.monotone_dp(C, lo=3, hi=6)) >= 3


def test_subtract_and_chunks():
    assert A.subtract([(10, 20), (15, 30), (50, 60)], (0, 70)) == [(0, 10), (30, 50), (60, 70)]
    chunks = A.chunk_stretches([(0, 65), (100, 105)], chunk_s=30, min_s=8)
    assert len(chunks) == 3 and chunks[-1][1] == 65  # the 5 s stretch is too short


SLIDES = [
    {"slide_id": "99999-s01-001", "title": "Welcome", "text": "course logistics syllabus grading", "notes": ""},
    {"slide_id": "99999-s01-002", "title": "Apples", "text": "apples orchards harvest cider", "notes": ""},
    {"slide_id": "99999-s01-003", "title": "Volcanoes", "text": "volcanoes lava magma eruption", "notes": ""},
]


def cue(start, end, text, speaker="instructor"):
    return {"start": start, "end": end, "text": text, "speaker": speaker}


def test_text_fallback_assigns_uncovered_speech_in_order():
    cues = [
        cue(0, 15, "today we cover apples and orchards and how the harvest of apples works"),
        cue(15, 29, "cider comes from apples after the harvest in the orchards"),
        cue(31, 45, "now volcanoes and lava and magma during an eruption of volcanoes"),
        cue(45, 59, "magma rises and the eruption sends lava down the volcanoes"),
    ]
    got = A.text_align(SLIDES, cues, [(0, 60)], [], text_min=0.05)
    assert [j for j, *_ in got] == [1, 2]


def test_build_alignment_attaches_only_instructor_speech():
    times = np.arange(0, 40, 2.0)
    lab = np.array([0] * 10 + [-1] * 10)  # slide 1 on screen for the first 20 s
    cues = [
        cue(1, 8, "welcome to the course here is the syllabus"),
        cue(9, 12, "a question from the room about grading", speaker="student"),
        cue(12.5, 14, "unclear murmur", speaker="unclear"),
        cue(14, 19, "grading is on the syllabus"),
    ]
    recs, frame_runs, _ = A.build_alignment(SLIDES, times, lab, 40.0, cues)
    r = recs[0]
    assert r["methods"] == ["frame"] and r["windows"] == [[0.0, 19.0]]
    assert "question from the room" not in r["transcript"]
    assert "murmur" not in r["transcript"]
    assert r["transcript"].startswith("welcome to the course")
    assert all(set(x) == {"slide_id", "windows", "methods", "transcript"} for x in recs)


def test_session_videos_orders_parts(tmp_path):
    for n in ("video_part2.mp4", "video.mp4", "video_part10.mp4", "video_part3.mp4"):
        (tmp_path / n).write_bytes(b"")
    assert [p.name for p in A.session_videos(tmp_path)] == [
        "video.mp4", "video_part2.mp4", "video_part3.mp4", "video_part10.mp4"]

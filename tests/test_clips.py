"""Tests for indexer/clips.py with synthetic cues, slides and a generated test video.

Run: uv run --no-project --with-requirements requirements.txt --with pytest --with pillow \
       --with scikit-learn python -m pytest tests/test_clips.py -q
"""

import json
import shutil
import subprocess

import pytest

from indexer import align as A
from indexer import clips as C


def cue(start, end, text="we talk about the slide", speaker="instructor"):
    return {"start": start, "end": end, "text": text, "speaker": speaker}


def steady_cues(a, b, step=5.0, **kw):
    out, t = [], a
    while t + step <= b:
        out.append(cue(t, t + step, **kw))
        t += step
    return out


def slide(sid="99999-s01-004", flags=(), text="a slide about clustering", notes=""):
    return {"slide_id": sid, "title": "Clustering", "text": text, "notes": notes, "flags": list(flags)}


def rec(windows, methods=None, sid="99999-s01-004"):
    return {"slide_id": sid, "windows": windows, "methods": methods or ["frame"] * len(windows),
            "transcript": ""}


KEY = ("99999", 1)


def decide(sl, r, cues, meta=None, key=KEY, pulled=()):
    return C.decide_slide(sl, r, cues, meta or {}, key, set(pulled))


def test_window_problem_rules():
    cues = steady_cues(0, 60)
    assert C.window_problem(cues, 10, 40) is None
    with_student = cues + [cue(43, 44, speaker="student")]
    assert C.window_problem(with_student, 10, 40) == "student_speech"  # inside the 5 s pad
    assert C.window_problem(with_student, 10, 35) is None  # pad ends at 40
    unclear = cues + [cue(8, 9, speaker="unclear")]
    assert C.window_problem(unclear, 10, 40) == "unclear_speech"
    for token in ("[student]", "[person]"):
        named = [cue(20, 25, f"thanks {token} for that")] + cues
        assert C.window_problem(named, 10, 40) == "name_in_window"
    assert C.window_problem(cues, 100, 130) == "no_speech"


def test_snap_lands_on_cue_boundaries_and_caps_at_90():
    cues = steady_cues(0, 200, step=7.0)
    s, e = C.snap(cues, 3, 150)
    assert s == 7.0 and e - s <= 90 and any(c["end"] == e for c in cues)
    assert e == 91.0  # last cue boundary before 90 s from the start


def test_kept_clip_for_clean_frame_window():
    clip, rej = decide(slide(), rec([[99.0, 161.0]]), steady_cues(0, 300))
    assert clip is not None and not rej
    assert 15 <= clip.end - clip.start <= 90
    assert clip.start >= 100.0 and clip.end <= 160.0  # inside the first-to-last frame span


def test_text_only_windows_never_become_clips():
    clip, rej = decide(slide(), rec([[100.0, 160.0]], ["text"]), steady_cues(0, 300))
    assert clip is None and rej[0].reason == "no_frame_match"


@pytest.mark.parametrize("flag", C.NO_CLIP_FLAGS)
def test_flagged_slides_get_no_clip(flag):
    clip, rej = decide(slide(flags=[flag]), rec([[100.0, 160.0]]), steady_cues(0, 300))
    assert clip is None and rej[0].reason == f"flag_{flag}"


def test_private_case_session_and_pulled_slide_and_masked_slide_text():
    cues = steady_cues(0, 300)
    clip, rej = decide(slide(), rec([[100.0, 160.0]]), cues, key=("45884", 11))
    assert clip is None and rej[0].reason == "private_case_session"
    clip, rej = decide(slide(), rec([[100.0, 160.0]]), cues, pulled=["99999-s01-004"])
    assert clip is None and rej[0].reason == "override_pulled"
    clip, rej = decide(slide(notes="team: [student] and [student]"), rec([[100.0, 160.0]]), cues)
    assert clip is None and rej[0].reason == "slide_text_name"


def test_student_turn_in_window_falls_back_to_clean_part():
    cues = [c for c in steady_cues(0, 300) if not (130 <= c["start"] < 135)]
    cues.append(cue(130, 135, "a question from the room", speaker="student"))
    clip, _ = decide(slide(), rec([[60.0, 200.0]]), sorted(cues, key=lambda c: c["start"]))
    assert clip is not None and clip.note == "clean part of window"
    assert clip.end <= 125.0 or clip.start >= 140.0  # 5 s clear of the student turn
    assert C.window_problem(cues, clip.start, clip.end) is None


def test_names_everywhere_means_no_clip():
    cues = steady_cues(0, 300, text="as [person] wrote in the paper")
    clip, rej = decide(slide(), rec([[100.0, 160.0]]), cues)
    assert clip is None and rej[0].reason == "name_in_window"


def test_short_window_and_merge_of_near_windows():
    cues = steady_cues(0, 300)
    clip, rej = decide(slide(), rec([[100.0, 110.0]]), cues)
    assert clip is None and rej[0].reason == "too_short"
    # two 10 s runs 3 s apart merge into one usable window
    clip, _ = decide(slide(), rec([[100.0, 112.0], [115.0, 127.0]]), cues)
    assert clip is not None and clip.end - clip.start >= 15


def test_embedded_video_motion_blocks_clip():
    meta = {"frame_windows": [{"slide_id": "99999-s01-004", "start": 99.0, "end": 161.0,
                               "frames": 31, "motion": 0.8}]}
    clip, rej = decide(slide(), rec([[99.0, 161.0]]), steady_cues(0, 300), meta=meta)
    assert clip is None and rej[0].reason == "embedded_video_suspected"


def test_locate_maps_parts_and_refuses_boundary_crossing():
    vids = ["p1", "p2"]
    assert C.locate(10, 40, vids, [100.0, 50.0]) == ("p1", 10)
    assert C.locate(110, 140, vids, [100.0, 50.0]) == ("p2", 10)
    assert C.locate(90, 110, vids, [100.0, 50.0]) is None


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_end_to_end_cuts_clip_and_writes_manifest(tmp_path, monkeypatch):
    archive, build = tmp_path / "archive", tmp_path / "build"
    folder = archive / "70-445 Test Course" / "2026 Fall" / "01 2026-01-01 Test"
    folder.mkdir(parents=True)
    # Like a Zoom recording, the source carries a caption track and a title; neither may reach the clip.
    srt = tmp_path / "captions.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:50,000\nA made-up caption line\n")
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=60",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=60", "-i", str(srt),
                    "-map", "0", "-map", "1", "-map", "2", "-shortest", "-c:v", "libx264", "-c:a", "aac",
                    "-c:s", "mov_text", "-metadata", "title=made-up title", str(folder / "video.mp4")],
                   check=True)
    monkeypatch.setattr(A, "ARCHIVE", archive)
    monkeypatch.setenv("FT_BUILD_DIR", str(build))
    sid = "70445-s01-001"
    (build / "slides" / "70445" / "s01").mkdir(parents=True)
    (build / "slides" / "70445" / "s01" / "slides.json").write_text(json.dumps([slide(sid)]))
    (build / "align" / "70445").mkdir(parents=True)
    (build / "align" / "70445" / "s01.json").write_text(json.dumps([rec([[4.0, 50.0]], sid=sid)]))
    (build / "align" / "70445" / "s01.meta.json").write_text(json.dumps({"parts": [60.0], "frame_windows": []}))
    (build / "transcripts" / "70445").mkdir(parents=True)
    (build / "transcripts" / "70445" / "s01.json").write_text(json.dumps(
        {"course": "70445", "session": 1, "cues": steady_cues(0, 60)}))
    assert C.main(["--course", "70445", "--session", "1"]) == 0
    out = build / "clips" / f"{sid}.mp4"
    assert out.exists()
    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_name,codec_type,height:format=duration:format_tags",
         "-of", "json", str(out)], capture_output=True, text=True, check=True).stdout)
    assert sorted(s["codec_type"] for s in probe["streams"]) == ["audio", "video"]  # no caption track
    codecs = {s["codec_name"] for s in probe["streams"]}
    assert codecs == {"h264", "aac"}
    assert "title" not in probe["format"].get("tags", {})
    assert 720 in {s.get("height") for s in probe["streams"]}
    assert 15 <= float(probe["format"]["duration"]) <= 90
    manifest = json.loads((build / "clips" / "manifest.json").read_text())
    assert isinstance(manifest, list) and manifest[0]["slide_id"] == sid and manifest[0]["reason_kept"]
    report = json.loads((build / "clips" / "rejected.json").read_text())
    assert report["counts"] == {} and report["clips"] == 1


def test_bridged_frames_split_the_usable_span():
    meta = {"frame_windows": [{"slide_id": "99999-s01-004", "start": 99.0, "end": 201.0, "frames": 51,
                               "motion": 0.0, "unconfirmed": [150.0]}]}
    spans = C.usable_spans("99999-s01-004", [(99.0, 201.0)], meta)
    assert spans == [(100.0, 148.0), (152.0, 200.0)]
    clip, _ = decide(slide(), rec([[99.0, 201.0]]), steady_cues(0, 300), meta=meta)
    assert clip is not None and (clip.end <= 148.0 or clip.start >= 152.0)

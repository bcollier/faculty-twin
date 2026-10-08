"""localvoice: engine selection and labels, audio writing, sample-window rules, and the A/B page.

No torch, kokoro, or chatterbox here: the real engines are only checked for
their labels and their "not installed" message; rendering uses FakeEngine.
Transcript cues are invented.
"""

from __future__ import annotations

import io
import json
import wave
from pathlib import Path

import numpy as np
import pytest

from localvoice import ab, audio, engines, sample

# ---------------------------------------------------------------- engines and labels

def test_only_a_clone_of_ben_gets_the_clone_label(tmp_path):
    assert engines.KokoroEngine().label == engines.STOCK_LABEL
    ref = tmp_path / "ref.wav"
    assert engines.ChatterboxEngine(sample=ref).label == engines.CLONE_LABEL
    assert engines.ChatterboxEngine(sample=ref, is_clone_of_ben=False).label == engines.STOCK_LABEL
    assert "stock voice, not mine" in engines.STOCK_LABEL


def test_build_specs(tmp_path):
    assert engines.build("kokoro:bm_george").voice == "bm_george"
    assert engines.build("chatterbox-turbo", tmp_path / "r.wav").turbo is True
    assert isinstance(engines.build("fake"), engines.FakeEngine)
    with pytest.raises(engines.EngineUnavailable, match="--sample"):
        engines.build("chatterbox")
    with pytest.raises(ValueError):
        engines.build("piper")


def test_missing_sample_is_reported_without_loading_a_model(tmp_path):
    with pytest.raises(engines.EngineUnavailable, match="reference sample not found"):
        engines.ChatterboxEngine(sample=tmp_path / "missing.wav").render("hi")


def test_kokoro_not_installed_message(monkeypatch):
    import builtins

    real = builtins.__import__

    def no_kokoro(name, *a, **k):
        if name == "kokoro":
            raise ImportError("no kokoro")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_kokoro)
    with pytest.raises(engines.EngineUnavailable, match="localvoice/requirements.txt"):
        engines.KokoroEngine().render("hello")


def test_heavy_packages_stay_out_of_the_app_requirements():
    root = Path(__file__).resolve().parents[1]
    app_reqs = (root / "requirements.txt").read_text().lower()
    for pkg in ("torch", "kokoro", "chatterbox"):
        assert pkg not in app_reqs


# ---------------------------------------------------------------- audio

def test_normalize_and_wav_roundtrip(tmp_path):
    samples, rate = engines.FakeEngine().render("one two three four five six")
    loud = samples * 4
    norm = audio.normalize(loud)
    assert np.max(np.abs(norm)) <= audio.PEAK_LIMIT + 1e-6
    out = audio.write(norm, rate, tmp_path / "x", mp3=False)
    assert out.suffix == ".wav"
    with wave.open(io.BytesIO(out.read_bytes())) as w:
        assert w.getnchannels() == 1 and w.getframerate() == rate
        assert w.getnframes() == len(norm)
    assert audio.normalize(np.zeros(0)).size == 0


# ---------------------------------------------------------------- sample windows

def cue(start, end, text="we fit the model on the training data", speaker="instructor", **extra):
    return dict(start=start, end=end, text=text, speaker=speaker, **extra)


def run_of(start, end, step=3.0, **extra):
    out, t = [], start
    while t < end:
        out.append(cue(t, min(t + step, end), **extra))
        t += step
    return out


def test_clean_run_becomes_a_window_capped_at_target():
    cues = run_of(0, 120)
    w = sample.clean_windows(cues, target=45)[0]
    assert w.start == 0 and w.seconds <= 45 and w.seconds >= 40


@pytest.mark.parametrize(
    "bad",
    [
        cue(60, 63, speaker="student"),
        cue(60, 63, speaker="unclear"),
        cue(60, 63, text="as [student] said earlier"),
        cue(60, 63, text="the work by [person] showed"),
        cue(60, 63, pg=True),
        cue(60, 63, text="well, damn, that failed"),
    ],
)
def test_unsafe_cues_and_their_padding_are_excluded(bad):
    cues = run_of(0, 60) + [bad] + run_of(63, 130)
    for w in sample.clean_windows(cues, target=90):
        # Nothing within 5 s of the bad cue.
        assert w.end <= bad["start"] - sample.PAD_S or w.start >= bad["end"] + sample.PAD_S


def test_long_pause_splits_and_short_runs_are_dropped():
    cues = run_of(0, 30) + run_of(40, 45)  # 10 s gap, then only 5 s of speech
    ws = sample.clean_windows(cues, target=60)
    assert len(ws) == 1 and ws[0].end <= 30


def test_no_window_when_nothing_is_instructor_only():
    assert sample.clean_windows(run_of(0, 100, speaker="student")) == []


def test_sample_cli_list_mode(tmp_path, capsys):
    t = tmp_path / "s06.json"
    t.write_text(json.dumps({"cues": run_of(0, 80)}))
    assert sample.main(["--transcript", str(t), "--list"]) == 0
    out = capsys.readouterr().out
    assert "words" in out and "we fit the model" not in out  # never prints transcript text


# ---------------------------------------------------------------- A/B

def test_load_narrations_from_playlist_and_text(tmp_path):
    pl = tmp_path / "p.json"
    pl.write_text(json.dumps({"segments": [{"narration": "On this slide, k-means."}, {"narration": " "}]}))
    assert ab.load_narrations(pl, ["Extra one."]) == ["Extra one.", "On this slide, k-means."]
    with pytest.raises(ValueError):
        ab.load_narrations(None, [])


def test_ab_cli_with_fake_engine_writes_blind_page(tmp_path):
    out = tmp_path / "run"
    rc = ab.main(["--text", "First narration here.", "--text", "Second <one>.", "--engine", "fake",
                  "--out", str(out), "--wav", "--seed", "7"])
    assert rc == 0
    key = json.loads((out / "answer_key.json").read_text())
    assert key["seed"] == 7
    ok = [r for r in key["renders"] if r.get("file")]
    assert len(ok) == 2 and all((out / r["file"]).exists() for r in ok)
    page = (out / "index.html").read_text()
    assert "Second &lt;one&gt;." in page and page.count("<audio") == 2
    for word in ("fake", "stock voice", "rendered in"):  # nothing on the page gives the answer away
        assert word not in page
    for r in ok:
        assert "fake" not in r["file"] and r["version"] == "A"


def test_shuffle_is_seeded_and_letters_every_render():
    rows = [{"segment": 1, "file": f"{c}.wav"} for c in "abcd"]
    a = ab.assign_versions([dict(r) for r in rows], 1, seed=1)
    b = ab.assign_versions([dict(r) for r in rows], 1, seed=1)
    assert [r["version"] for r in a] == [r["version"] for r in b]
    assert sorted(r["version"] for r in a) == ["A", "B", "C", "D"]
    orders = {tuple(r["version"] for r in ab.assign_versions([dict(r) for r in rows], 1, seed=s)) for s in range(20)}
    assert len(orders) > 1  # different seeds give different orders


class CountingEngine(engines.FakeEngine):
    def __init__(self):
        super().__init__(name="counting")
        self.calls = []

    def render(self, text):
        self.calls.append(text)
        return super().render(text)


def test_warmup_render_is_not_saved_or_timed(tmp_path):
    eng = CountingEngine()
    rows = ab.render_all([eng], ["Only segment."], tmp_path, mp3=False)
    assert eng.calls[0] == ab.WARMUP_TEXT and eng.calls[1:] == ["Only segment."]
    assert len(rows) == 1 and len(list(tmp_path.iterdir())) == 1


def test_a_crashing_engine_does_not_stop_the_others(tmp_path):
    class Crashy(engines.FakeEngine):
        def render(self, text):
            if text != ab.WARMUP_TEXT:
                raise RuntimeError("GPU fell over")
            return super().render(text)

    rows = ab.render_all([Crashy(name="crashy"), engines.FakeEngine()], ["One.", "Two."], tmp_path, mp3=False)
    assert [r["engine"] for r in rows if r.get("error")] == ["crashy", "crashy"]
    assert len([r for r in rows if r.get("file")]) == 2


def test_hand_made_clips_are_renamed_shuffled_in(tmp_path, monkeypatch):
    monkeypatch.setattr(audio, "has_ffmpeg", lambda: False)
    out = tmp_path / "run"
    out.mkdir()
    samples, rate = engines.FakeEngine().render("x y z")
    (out / "elevenlabs_1.wav").write_bytes(audio.wav_bytes(samples, rate))
    (out / "elevenlabs_9.wav").write_bytes(audio.wav_bytes(samples, rate))  # no segment 9: ignored
    assert ab.main(["--text", "Only one.", "--engine", "fake", "--out", str(out), "--wav", "--seed", "3"]) == 0
    key = json.loads((out / "answer_key.json").read_text())
    added = [r for r in key["renders"] if r["engine"] == "elevenlabs"]
    assert len(added) == 1 and added[0]["loudness_matched"] is False and "elevenlabs" not in added[0]["file"]
    assert not (out / "elevenlabs_1.wav").exists()  # the telltale name is gone
    page = (out / "index.html").read_text()
    assert page.count("<audio") == 2 and "elevenlabs" not in page


# ---------------------------------------------------------------- sample privacy and rank

def test_cut_writes_a_private_clip_and_private_new_folders(tmp_path, monkeypatch):
    import os
    import stat

    def fake_ffmpeg(cmd, check):
        Path(cmd[-1]).write_bytes(b"RIFFfake")

    monkeypatch.setattr(sample.subprocess, "run", fake_ffmpeg)
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    os.chmod(shared, 0o755)
    out = shared / "_private" / "voice" / "ben_ref.wav"
    sample.cut(Path("video.mp4"), sample.Window(0, 12, 30), out)
    def mode(p: Path) -> int:
        return stat.S_IMODE(p.stat().st_mode)

    assert mode(out) == 0o600
    assert mode(out.parent) == 0o700 and mode(out.parent.parent) == 0o700
    assert mode(shared) == 0o755  # a folder that already existed outside _private is left alone


def test_sample_rank_option(tmp_path, monkeypatch, capsys):
    t = tmp_path / "s06.json"
    t.write_text(json.dumps({"cues": run_of(0, 40) + [cue(45, 48, speaker="student")] + run_of(60, 85)}))
    cut_windows = []
    monkeypatch.setattr(sample, "cut", lambda video, window, out: cut_windows.append(window))
    assert sample.main(["--transcript", str(t), "--video", "v.mp4", "--out", str(tmp_path / "o.wav"), "--rank", "2"]) == 0
    ranked = sample.clean_windows(json.loads(t.read_text())["cues"])
    assert cut_windows == [ranked[1]]
    assert sample.main(["--transcript", str(t), "--video", "v.mp4", "--out", str(tmp_path / "o.wav"), "--rank", "9"]) == 2

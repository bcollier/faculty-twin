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
                  "--engine", "fake", "--out", str(out), "--wav"])
    assert rc == 0
    rows = json.loads((out / "renders.json").read_text())
    assert len(rows) == 4 and all((out / r["file"]).exists() for r in rows)
    page = (out / "index.html").read_text()
    assert "Reveal" in page and "Second &lt;one&gt;." in page  # escaped
    assert page.count("<audio") == 4

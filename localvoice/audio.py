"""Write rendered samples as WAV (standard library) or MP3 (ffmpeg), with light loudness matching.

Pre-generated clips are served as mp3 (`audio/<voice tag>/<hash>.mp3`), so the
A/B renders are written the same way when ffmpeg is on the PATH. Peak and RMS
are matched to a common target so a comparison by ear is about the voice, not
the volume.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

TARGET_RMS = 0.08
PEAK_LIMIT = 0.97


def normalize(samples: np.ndarray) -> np.ndarray:
    x = np.asarray(samples, dtype=np.float32).reshape(-1)
    if x.size == 0:
        return x
    rms = float(np.sqrt(np.mean(x**2)))
    if rms > 1e-6:
        x = x * (TARGET_RMS / rms)
    peak = float(np.max(np.abs(x)))
    if peak > PEAK_LIMIT:
        x = x * (PEAK_LIMIT / peak)
    return x


def wav_bytes(samples: np.ndarray, rate: int) -> bytes:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def write(samples: np.ndarray, rate: int, dest: Path, mp3: bool = True) -> Path:
    """Write `dest` with a .mp3 (ffmpeg, 128 kbps mono 44.1 kHz) or .wav suffix. Returns the path written."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = wav_bytes(normalize(samples), rate)
    if mp3 and has_ffmpeg():
        out = dest.with_suffix(".mp3")
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "wav", "-i", "pipe:0",
             "-ac", "1", "-ar", "44100", "-b:a", "128k", str(out)],
            input=data, check=True,
        )
        return out
    out = dest.with_suffix(".wav")
    out.write_bytes(data)
    return out


def duration_seconds(samples: np.ndarray, rate: int) -> float:
    return round(len(samples) / float(rate), 2) if rate else 0.0

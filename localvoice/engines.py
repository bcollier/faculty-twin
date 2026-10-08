"""Free, local text-to-speech engines for pre-generated audio (docs/ROADMAP.md, "Voice").

Each engine turns narration text into mono float32 samples. They run only on the
local build machine: Vercel cannot load torch, so these never serve live answers.

Checked Oct 5, 2026 against PyPI and each project's README:

- **Kokoro** (`kokoro` 0.9.4, Apache-2.0, Python >= 3.10 and < 3.13): stock voices only.
  `KPipeline(lang_code="a")` for American English; calling the pipeline with
  `voice=` yields `(graphemes, phonemes, audio)` chunks at 24 kHz.
- **Chatterbox** (`chatterbox-tts` 0.1.7, MIT, Resemble AI): zero-shot cloning
  from a short reference clip. `ChatterboxTTS.from_pretrained(device=...)`,
  then `model.generate(text, audio_prompt_path=...)` returns a torch tensor at
  `model.sr`. A faster `ChatterboxTurboTTS` exists in `chatterbox.tts_turbo`.

Heavy imports (torch, kokoro, chatterbox) happen inside the engines, never at
module import, and are never added to the app's `requirements.txt` (Vercel
bundles that file). Install them only for a local run; see localvoice/README.md.

Disclosure rule (docs/SPEC.md voice tiers): the label "AI voice made from my
recordings." belongs only to a clone of Ben's own voice. Kokoro is a stock voice.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

CLONE_LABEL = "AI voice made from my recordings."
STOCK_LABEL = "AI voice (a stock voice, not mine)."


class EngineUnavailable(RuntimeError):
    """The engine's packages or model files are not installed on this machine."""


class Engine(Protocol):
    """A local text-to-speech model. `label` is what a listener is told the voice is."""

    name: str
    is_clone_of_ben: bool

    @property
    def label(self) -> str: ...

    def render(self, text: str) -> tuple[np.ndarray, int]:
        """Return (mono float32 samples in [-1, 1], sample rate)."""
        ...


def _label(is_clone: bool) -> str:
    return CLONE_LABEL if is_clone else STOCK_LABEL


def pick_device() -> str:
    """Apple GPU (MPS) when torch has it, else CPU."""
    try:
        import torch
    except ImportError:
        return "cpu"
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


@dataclass
class KokoroEngine:
    """Kokoro stock voice. `voice` ids look like af_heart, am_michael, bm_george."""

    voice: str = "am_michael"
    lang_code: str = "a"
    speed: float = 1.0
    name: str = "kokoro"
    is_clone_of_ben: bool = False
    _pipeline: Any = field(default=None, repr=False)

    @property
    def label(self) -> str:
        return _label(False)

    def _load(self):
        if self._pipeline is None:
            try:
                from kokoro import KPipeline
            except ImportError as exc:
                raise EngineUnavailable(
                    "kokoro is not installed. Run with: uv run --python 3.12 --with-requirements "
                    "localvoice/requirements.txt ..."
                ) from exc
            self._pipeline = KPipeline(lang_code=self.lang_code)
        return self._pipeline

    def render(self, text: str) -> tuple[np.ndarray, int]:
        pipeline = self._load()
        parts = []
        for _graphemes, _phonemes, audio in pipeline(text, voice=self.voice, speed=self.speed):
            arr = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
            parts.append(arr.astype(np.float32).reshape(-1))
        if not parts:
            raise RuntimeError("kokoro returned no audio")
        return np.concatenate(parts), 24000


@dataclass
class ChatterboxEngine:
    """Chatterbox zero-shot clone from a reference clip.

    `is_clone_of_ben` must be True only when `sample` is Ben's own voice, cut by
    `localvoice.sample` from instructor-only stretches. Any other reference
    voice gets the stock label.
    """

    sample: Path
    is_clone_of_ben: bool = True
    turbo: bool = False
    exaggeration: float = 0.4
    device: str | None = None
    name: str = "chatterbox"
    _model: Any = field(default=None, repr=False)

    @property
    def label(self) -> str:
        return _label(self.is_clone_of_ben)

    def _load(self):
        if self._model is None:
            if not Path(self.sample).exists():
                raise EngineUnavailable(f"reference sample not found: {Path(self.sample).name}")
            try:
                if self.turbo:
                    from chatterbox.tts_turbo import ChatterboxTurboTTS as Model
                else:
                    from chatterbox.tts import ChatterboxTTS as Model
            except ImportError as exc:
                raise EngineUnavailable(
                    "chatterbox-tts is not installed. Run with: uv run --python 3.12 --with-requirements "
                    "localvoice/requirements.txt ..."
                ) from exc
            self._model = Model.from_pretrained(device=self.device or pick_device())
        return self._model

    def render(self, text: str) -> tuple[np.ndarray, int]:
        model = self._load()
        kwargs: dict[str, Any] = {"audio_prompt_path": str(self.sample)}
        if not self.turbo:
            kwargs["exaggeration"] = self.exaggeration
        wav = model.generate(text, **kwargs)
        arr = wav.detach().cpu().numpy() if hasattr(wav, "detach") else np.asarray(wav)
        return arr.astype(np.float32).reshape(-1), int(model.sr)


@dataclass
class FakeEngine:
    """TEST FAKE: a quiet tone whose length tracks the word count. No model, no network."""

    name: str = "fake"
    is_clone_of_ben: bool = False
    sample_rate: int = 8000

    @property
    def label(self) -> str:
        return _label(self.is_clone_of_ben)

    def render(self, text: str) -> tuple[np.ndarray, int]:
        seconds = max(0.2, 0.05 * len(text.split()))
        t = np.arange(int(seconds * self.sample_rate), dtype=np.float32) / self.sample_rate
        return (0.2 * np.sin(2 * math.pi * 220.0 * t)).astype(np.float32), self.sample_rate


def build(spec: str, sample: Path | None = None) -> Engine:
    """Engine from a CLI spec: `kokoro`, `kokoro:bm_george`, `chatterbox`, `chatterbox-turbo`, `fake`."""
    kind, _, arg = spec.partition(":")
    if kind == "kokoro":
        return KokoroEngine(voice=arg or "am_michael")
    if kind in ("chatterbox", "chatterbox-turbo"):
        if sample is None:
            raise EngineUnavailable("chatterbox needs --sample (cut one with python -m localvoice.sample)")
        return ChatterboxEngine(sample=sample, turbo=kind == "chatterbox-turbo")
    if kind == "fake":
        return FakeEngine()
    raise ValueError(f"unknown engine {spec!r}")

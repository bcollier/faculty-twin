"""Pre-generate the suggested questions: stored playlists plus mp3s (docs/SPEC.md, Block 6).

1. The question list lives in `_build/topics/draft_questions.json`,
   `[{"question": ..., "course": "70445" | "45884"}]`. The first run writes a
   draft (five per course, from the slide titles) for Ben to edit; later runs
   never overwrite it.
2. With the keys in `.env`, each question runs through the real `/api/ask`
   path in-process (`app.main.answer`: Voyage embedding, Ben's hand-written
   retrieval in app/retrieval.py, the active narration model). Nothing here
   ranks or selects slides. If app/retrieval.py still raises
   NotImplementedError, the script stops before spending any API call.
3. Each narration is spoken once with ElevenLabs (the Settings voice, else
   ELEVENLABS_VOICE_ID) into `_build/audio/<voice tag>/<hash>.mp3` (the tag
   app/voices.py matches); a free edge voice costs nothing live, so topics
   stay without stored audio for it; existing
   files are reused, so a re-run only pays for changed narrations.
4. Writes `_build/topics/topics.json`:
   `[{question, course, playlist: {segments: [{slide_id, narration, audio_path}], follow_ups}, generated}]`.
   `indexer/upload.py` mirrors it and the mp3s to the bucket.

Run from the repo root:
  uv run --no-project --with-requirements requirements.txt python -m indexer.pregenerate --draft-only
  uv run --no-project --with-requirements requirements.txt python -m indexer.pregenerate
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import secrets
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from indexer import common  # noqa: E402

# Drafted from the session and slide titles in _build/slides (Oct 5). Ben edits the JSON copy.
DRAFT_QUESTIONS = [
    {"question": "How does a neural network learn?", "course": "70445"},
    {"question": "What are the five tribes of machine learning?", "course": "70445"},
    {"question": "How do I choose the number of clusters in k-means?", "course": "70445"},
    {"question": "How do large language models work?", "course": "70445"},
    {"question": "What is spec-driven development?", "course": "70445"},
    {"question": "What is TF-IDF and why is it useful?", "course": "45884"},
    {"question": "How does retrieval augmented generation work?", "course": "45884"},
    {"question": "How do convolutional neural networks recognize images?", "course": "45884"},
    {"question": "What is the difference between AI agents and agentic AI?", "course": "45884"},
    {"question": "How does CLIP connect images and text?", "course": "45884"},
]
EXIT_OK, EXIT_NEEDS_KEYS, EXIT_RETRIEVAL, EXIT_NO_INDEX = 0, 2, 3, 4
RETRIEVAL_MSG = (
    "Stopped: rank() and select_segments() in app/retrieval.py still raise NotImplementedError.\n"
    "Ben writes those by hand (AGENTS.md). Run this again once they are filled in; no API call was made."
)


def draft_path(build: Path) -> Path:
    """Where Ben's editable question list lives."""
    return build / "topics" / "draft_questions.json"


def ensure_draft(build: Path, log: Callable[[str], None] = print) -> list[dict[str, Any]]:
    """The question list, writing the draft first when there is none. Never overwrites Ben's edits."""
    from app.config import QUESTION_MAX_CHARS

    path = draft_path(build)
    if not path.exists():
        common.write_json(path, DRAFT_QUESTIONS)
        log(f"Wrote a draft list of {len(DRAFT_QUESTIONS)} questions to {path}. Edit it, then run again.")
    questions = common.read_json(path, []) or []
    clean = []
    for q in questions:
        if isinstance(q, dict) and str(q.get("question", "")).strip():
            question = str(q["question"]).strip()[:QUESTION_MAX_CHARS]
            clean.append({"question": question, "course": q.get("course") or None})
    return clean


def audio_name(narration: str) -> str:
    """The stored mp3's file name: a hash of the narration, so a changed narration gets a new file."""
    return common.sha256_bytes(narration.encode("utf-8"))[:24]


def voice_tag(voice: str) -> str:
    """The audio folder for a voice: the same tag app/voices.py uses to find stored audio."""
    from app import speech

    return speech.voice_tag(voice)


def speak(text: str, voice: str, client: httpx.Client) -> bytes:
    """Speak one narration with ElevenLabs; mp3 bytes."""
    from app import speech

    url, headers, params, body = speech.tts_request(text, voice)
    resp = client.post(url, headers=headers, params=params, json=body)
    if resp.status_code >= 400:
        raise RuntimeError(f"ElevenLabs returned {resp.status_code}")
    return resp.content


def generate(
    build: Path,
    questions: list[dict[str, Any]],
    voice: str | None,
    audio: bool = True,
    retriever: Callable[..., Any] | None = None,
    embedder: Callable[..., Any] | None = None,
    completer: Callable[..., Any] | None = None,
    tts_client: httpx.Client | None = None,
    log: Callable[[str], None] = print,
) -> int:
    """Answer each question through the real app path, store its audio, write topics.json. Returns an EXIT_* code."""
    # Media links stay local (no signing calls), and the audio links answer() signs are thrown
    # away (topics store mp3 paths), so a random per-process key is enough when none is set.
    temp = {"CONTENT_DIR": str(build)}
    if not os.environ.get("AUDIO_SIGNING_SECRET", "").strip():
        temp["AUDIO_SIGNING_SECRET"] = secrets.token_urlsafe(48)
    previous = {k: os.environ.get(k) for k in temp}
    os.environ.update(temp)
    try:
        return _generate(build, questions, voice, audio, retriever, embedder, completer, tts_client, log)
    finally:
        for k, v in previous.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _generate(build: Path, questions: list[dict[str, Any]], voice: str | None, audio: bool,
              retriever: Callable[..., Any] | None, embedder: Callable[..., Any] | None,
              completer: Callable[..., Any] | None, tts_client: httpx.Client | None,
              log: Callable[[str], None]) -> int:
    from fastapi import HTTPException

    from app import main, storage

    try:
        content = storage.load_local(build)
    except storage.ContentUnavailable as exc:
        log(f"No usable index yet ({exc}). Run indexer/build_index.py with VOYAGE_API_KEY first.")
        return EXIT_NO_INDEX
    content = dataclasses.replace(content, topics=[])  # never answer from the old stored playlists
    retriever = retriever or main.get_retriever()
    try:
        main._check_retrieval_ready(retriever, content.matrix.shape[1])
    except main.RetrievalNotReady:
        log(RETRIEVAL_MSG)
        return EXIT_RETRIEVAL
    embedder = embedder or main.get_embedder()
    completer = completer or main.get_completer()

    out: list[dict[str, Any]] = []
    own = tts_client is None and audio and voice is not None
    client = tts_client or (httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) if own else None)
    spoken = {"chars": 0}  # characters sent to ElevenLabs this run
    try:
        for q in questions:
            try:
                result, info = main.answer(q["question"], q["course"], content, retriever, embedder, completer)
            except (HTTPException, main.RetrievalNotReady) as exc:
                log(f"  skipped (error): {q['question']} ({getattr(exc, 'detail', type(exc).__name__)})")
                continue
            if not result["covered"]:
                log(f"  skipped (not covered): {q['question']}")
                continue
            speak_with = client if audio and voice else None
            segments = [_stored_segment(seg, build, voice, speak_with, spoken) for seg in result["segments"]]
            out.append(_topic(q, segments, result, info, voice if audio else None, content.meta))
            log(f"  ok ({len(segments)} segments, narration {info.get('narration')}): {q['question']}")
    finally:
        if own and client is not None:
            client.close()
    common.write_json(build / "topics" / "topics.json", out)
    log(
        f"Wrote {len(out)} of {len(questions)} topics to {build / 'topics' / 'topics.json'}"
        f" ({spoken['chars']} characters sent to ElevenLabs). Next: indexer/upload.py"
    )
    return EXIT_OK


def _stored_segment(seg: dict[str, Any], build: Path, voice: str | None, client: httpx.Client | None,
                    spoken: dict[str, int]) -> dict[str, Any]:
    """One topic segment: slide and narration, plus a stored mp3 path when there is a voice to speak it.

    An mp3 already on disk is reused, so a re-run only pays ElevenLabs for changed narrations.
    """
    entry = {"slide_id": seg["slide_id"], "narration": seg["narration"]}
    if voice and client is not None and seg["narration"]:
        rel = f"audio/{voice_tag(voice)}/{audio_name(seg['narration'])}.mp3"
        dest = build / rel
        if not dest.exists():
            common.write_bytes_atomic(dest, speak(seg["narration"], voice, client))
            spoken["chars"] += len(seg["narration"])
        entry["audio_path"] = rel
    return entry


def _topic(q: dict[str, Any], segments: list[dict[str, Any]], result: dict[str, Any], info: dict[str, Any],
           voice_id: str | None, index_meta: dict[str, Any]) -> dict[str, Any]:
    """One topics.json entry, with what generated it (provider, model, voice, index version)."""
    return {
        "question": q["question"],
        "course": q["course"],
        "playlist": {"segments": segments, "follow_ups": result.get("follow_ups", [])},
        "generated": {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "provider": info.get("provider"),
            "model": info.get("model"),
            "narration": info.get("narration"),
            "voice_id": voice_id,
            "index_version": index_meta.get("index_version"),
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-generate suggested-question playlists and audio")
    ap.add_argument("--archive", help="Lecture Archive folder (default ~/Lecture Archive or $LECTURE_ARCHIVE)")
    ap.add_argument("--build", help="build folder (default <archive>/_build)")
    ap.add_argument("--draft-only", action="store_true", help="write the draft question list and stop")
    ap.add_argument("--voice", help="ElevenLabs voice id (default: Settings voice, else ELEVENLABS_VOICE_ID)")
    ap.add_argument("--no-audio", action="store_true", help="playlists only, captions (no ElevenLabs calls)")
    a = ap.parse_args(argv)
    common.load_env()
    build = Path(a.build).expanduser() if a.build else common.build_dir(common.archive_dir(a.archive))
    questions = ensure_draft(build)
    if a.draft_only:
        return EXIT_OK

    from app import llm, settings_store
    from app import main as app_main

    try:  # free check first: Ben's retrieval functions must exist before any paid call
        app_main._check_retrieval_ready(app_main.get_retriever(), 8)
    except app_main.RetrievalNotReady:
        print(RETRIEVAL_MSG)
        return EXIT_RETRIEVAL
    provider, _model = settings_store.llm_choice()
    from app import voices

    voice = None
    if not a.no_audio:
        try:
            parsed = voices.parse(a.voice or settings_store.voice_id())
        except voices.BadVoice as exc:
            print(f"Voice setting not usable: {exc}")
            return EXIT_NEEDS_KEYS
        if parsed and parsed[0] == voices.ELEVEN:
            voice = parsed[1]
        elif parsed and parsed[0] == voices.EDGE:
            print("The voice is a free edge voice: it is generated live at no cost, so no mp3s are stored.")
    missing = [] if common.env("VOYAGE_API_KEY") else ["VOYAGE_API_KEY"]
    if not llm.key_configured(provider):
        missing.append(llm.KEY_VARS[provider])
    if voice and not common.env("ELEVENLABS_API_KEY"):
        missing.append("ELEVENLABS_API_KEY (or pass --no-audio)")
    if missing:
        print("Needs keys in .env before generating: " + ", ".join(missing))
        print("Then run: uv run --no-project --with-requirements requirements.txt python -m indexer.pregenerate")
        return EXIT_NEEDS_KEYS
    if not a.no_audio and not voice:
        print("No voice is set (Settings voice or ELEVENLABS_VOICE_ID), so topics are captions only.")
    return generate(build, questions, voice, audio=not a.no_audio)


if __name__ == "__main__":
    sys.exit(main())

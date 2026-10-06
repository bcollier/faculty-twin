"""indexer/build_index.py on synthetic build outputs, with a TEST FAKE Voyage (httpx MockTransport)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

import pipeline_fixture as pf
from app import playlist, storage
from indexer import build_index

CONTRACT_KEYS = {
    "id", "kind", "course", "course_title", "session", "session_title", "date", "slide_number",
    "title", "text", "notes", "transcript", "image", "clip", "related_code",
}


def run(archive: Path, tmp_path: Path, voyage: pf.FakeVoyage | None, lines: list[str] | None = None) -> int:
    return build_index.build(
        archive,
        key="test-voyage-key" if voyage else None,
        client=voyage.client() if voyage else None,
        code_map=pf.code_map(tmp_path),
        sleep=lambda s: None,
        log=(lines if lines is not None else []).append,
    )


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    return pf.make_archive(tmp_path)


def test_index_loads_in_backend_from_content_dir(archive, tmp_path, monkeypatch):
    voyage = pf.FakeVoyage()
    assert run(archive, tmp_path, voyage) == 0
    build = archive / "_build"

    content = storage.load_local(build)
    ids = [r["id"] for r in content.records]
    assert ids == ["70445-s01-001", "70445-s01-003", "70445-s02-001", "70445-s01-nb1-c000"]
    assert "70445-s01-002" not in ids  # flagged student_names_possible
    assert content.matrix.shape == (4, pf.DIM) and content.matrix.dtype == np.float32

    s1, s3, news, code = content.records
    assert CONTRACT_KEYS <= set(s1)
    assert (s1["course_title"], s1["session_title"], s1["date"]) == ("Fake Course A", "Fruit basics", "2026-09-01")
    assert s1["transcript"] == "Apples are my favorite example of a fruit."
    assert s1["clip"] == "clips/70445-s01-001.mp4"
    assert s1["image"] == "slides/70445/s01/70445-s01-001.webp"
    assert s3["ocr_text"] == "APPLES 2026" and s3["transcript"] == ""
    assert s3["related_code"] == ["70445-s01-nb1-c000"]  # unknown code ids are dropped
    assert news["transcript"] == "" and news["clip"] is None  # no align file; In the News gets no clip
    assert code["kind"] == "code" and code["source"].startswith("apples") and code["image"] is None
    assert code["notebook"] == "Fruit Demo.ipynb" and code["cell_number"] == 1 and code["slide_number"] is None
    assert all(len(r["hash"]) == 64 for r in content.records)
    # Row order: the embedding of each record's text.
    assert np.allclose(content.matrix[0], pf.fake_vector(build_index.embed_text(s1)))
    # Embedded text has title, slide text, notes, OCR, and transcript.
    text = build_index.embed_text(s1)
    assert text.index("What is an apple") < text.index("Apples grow") < text.index("Say apples") < text.index("favorite")
    assert "APPLES 2026" in build_index.embed_text(s3)

    # The real backend store, pointed at the build folder.
    monkeypatch.setenv("CONTENT_DIR", str(build))
    storage.store.reset()
    loaded = storage.store.get()
    assert loaded.source == "local" and len(loaded.records) == 4
    assert playlist.courses(loaded)[0]["sessions"] == [
        {"session": 1, "date": "2026-09-01", "title": "Fruit basics"},
        {"session": 2, "date": "2026-09-03", "title": "More fruit"},
    ]

    manifest = json.loads((build / "content" / "manifest.json").read_text())
    assert manifest["embeddings"] == "complete" and manifest["index_version"] == loaded.meta["index_version"]
    assert manifest["counts"]["by_course"]["70445"]["sessions"]["s01"]["excluded"] == 1
    assert "slides/70445/s01/slides.json" in manifest["sources"] and "align/70445/s01.json" in manifest["sources"]
    assert (build / "content" / "versions" / f"{manifest['index_version']}.json").exists()


def test_rerun_only_embeds_changed_records(archive, tmp_path):
    first = pf.FakeVoyage()
    assert run(archive, tmp_path, first) == 0
    manifest = archive / "_build" / "content" / "manifest.json"
    v1 = json.loads(manifest.read_text())["index_version"]
    index1 = (archive / "_build" / "content" / "index.json").read_bytes()

    again = pf.FakeVoyage()
    assert run(archive, tmp_path, again) == 0
    assert again.calls == 0  # everything from the cache
    assert json.loads(manifest.read_text())["index_version"] == v1
    assert (archive / "_build" / "content" / "index.json").read_bytes() == index1  # byte-identical: upload skips it

    pf.write_align(archive / "_build", "A new explanation of apples.")
    changed = pf.FakeVoyage()
    assert run(archive, tmp_path, changed) == 0
    assert changed.calls == 1 and len(changed.inputs) == 1 and "new explanation" in changed.inputs[0]
    assert json.loads(manifest.read_text())["index_version"] != v1


def test_without_key_builds_everything_but_embeddings(archive, tmp_path):
    lines: list[str] = []
    assert run(archive, tmp_path, None, lines) == build_index.EXIT_PENDING
    content = archive / "_build" / "content"
    assert len(json.loads((content / "index.json").read_text())["records"]) == 4
    assert not (content / "embeddings.npy").exists()
    assert json.loads((content / "manifest.json").read_text())["embeddings"] == "pending"
    out = "\n".join(lines)
    assert "VOYAGE_API_KEY" in out and "python -m indexer.build_index" in out

    # The moment the key arrives, the same command finishes the job.
    assert run(archive, tmp_path, pf.FakeVoyage()) == 0
    assert storage.load_local(archive / "_build").matrix.shape == (4, pf.DIM)


def test_voyage_429_is_retried(archive, tmp_path):
    voyage = pf.FakeVoyage(fail_first=2)
    assert run(archive, tmp_path, voyage) == 0
    assert voyage.calls == 3


def test_build_leak_check_reports_location_not_name(archive, tmp_path):
    pf.write_align(archive / "_build", f"Thanks, {pf.FAKE_FULL_NAME}, good point.")
    lines: list[str] = []
    assert run(archive, tmp_path, pf.FakeVoyage(), lines) == build_index.EXIT_LEAK
    out = "\n".join(lines)
    assert "70445-s01-001.transcript" in out
    assert pf.FAKE_SURNAME not in out and "Zorblat" not in out

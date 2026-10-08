"""indexer/build_index.py on synthetic build outputs, with a TEST FAKE Voyage (httpx MockTransport)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
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
    blank = dict(s3, title="", text="", notes="", ocr_text="", transcript="")
    assert build_index.embed_text(blank) == "Fake Course A, session 1: Fruit basics, slide 3"

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
    lines: list[str] = []
    assert run(archive, tmp_path, voyage, lines) == 0
    assert voyage.calls == 3
    assert any(line.startswith("Voyage usage this run:") for line in lines)


def test_reduced_limit_account_switches_to_paced_mode(archive, tmp_path):
    voyage = pf.FakeVoyage(reduced_first=1)
    lines: list[str] = []
    waits: list[float] = []
    code = build_index.build(
        archive, key="test-voyage-key", client=voyage.client(), code_map=pf.code_map(tmp_path),
        sleep=waits.append, log=lines.append,
    )
    assert code == 0
    assert any("paced mode" in line for line in lines)
    assert waits and waits[0] >= 60  # waits out the minute before retrying


def test_build_leak_check_reports_location_not_name(archive, tmp_path):
    pf.write_align(archive / "_build", f"Thanks, {pf.FAKE_FULL_NAME}, good point.")
    lines: list[str] = []
    assert run(archive, tmp_path, pf.FakeVoyage(), lines) == build_index.EXIT_LEAK
    out = "\n".join(lines)
    assert "70445-s01-001.transcript" in out
    assert pf.FAKE_SURNAME not in out and "Zorblat" not in out


# ---------------------------------------------------------------- index hygiene (Oct 8)

def _add_thin_slide(archive: Path) -> str:
    """A picture-only slide: a title, but no text, notes, OCR text, or transcript."""
    folder = archive / "_build" / "slides" / "70445" / "s02"
    rows = json.loads((folder / "slides.json").read_text())
    rows.append(pf._slide("70445", 2, 2, "A photo of a banana", ""))
    (folder / "slides.json").write_text(json.dumps(rows))
    for key in ("image", "thumb"):
        (archive / "_build" / rows[-1][key]).write_bytes(b"RIFFfakeWEBP")
    return rows[-1]["slide_id"]


def test_slides_with_nothing_to_say_are_marked_thin_and_kept(archive, tmp_path):
    thin_id = _add_thin_slide(archive)
    assert run(archive, tmp_path, pf.FakeVoyage()) == 0
    records = {r["id"]: r for r in storage.load_local(archive / "_build").records}
    # Kept in the index: Ben's select_segments fills a one-slide gap from `records`, so a thin slide between
    # two chosen slides must still be there to be filled in (docs/SPEC.md, Segment selection).
    assert thin_id in records and records[thin_id]["thin"] is True
    assert records["70445-s01-003"]["thin"] is False  # OCR text only is enough
    assert all(r["thin"] is False for i, r in records.items() if i != thin_id)
    manifest = json.loads((archive / "_build" / "content" / "manifest.json").read_text())
    assert manifest["counts"]["thin"] == 1


class ZeroVoyage(pf.FakeVoyage):
    """TEST FAKE: Voyage returning an all-zero vector for chosen texts (an empty or broken embedding)."""

    def __init__(self, zero_for) -> None:
        super().__init__()
        self.zero_for = zero_for

    def __call__(self, request):
        resp = super().__call__(request)
        body = json.loads(request.content)
        data = resp.json()["data"]
        for d in data:
            if self.zero_for(body["input"][d["index"]]):
                d["embedding"] = [0.0] * pf.DIM
        return httpx.Response(200, json={"data": data, "model": body["model"], "usage": {"total_tokens": 1}})


def test_a_zero_vector_is_re_embedded_on_the_cli_path_too(archive, tmp_path, monkeypatch):
    # Refactor review (Oct 8): with no injected client (the CLI), embed_records closed the client it made
    # and then handed it to the placeholder retry, which failed with "client has been closed".
    zero = ZeroVoyage(lambda text: text.startswith("Bananas"))
    real_client = httpx.Client
    monkeypatch.setattr(build_index.httpx, "Client",
                        lambda *a, **k: real_client(transport=httpx.MockTransport(zero)))
    code = build_index.build(archive, key="test-voyage-key", code_map=pf.code_map(tmp_path),
                             sleep=lambda s: None, log=[].append)
    assert code == 0
    content = storage.load_local(archive / "_build")
    assert (np.linalg.norm(content.matrix, axis=1) > 0.5).all()


def test_a_zero_vector_is_re_embedded_with_the_placeholder_text(archive, tmp_path):
    zero = ZeroVoyage(lambda text: text.startswith("Bananas"))
    lines: list[str] = []
    assert run(archive, tmp_path, zero, lines) == 0
    content = storage.load_local(archive / "_build")
    assert (np.linalg.norm(content.matrix, axis=1) > 0.5).all() and np.isfinite(content.matrix).all()
    i = [r["id"] for r in content.records].index("70445-s02-001")
    placeholder = build_index.placeholder_text(content.records[i])
    assert placeholder == "Fake Course A, session 2: More fruit, slide 1"
    assert np.allclose(content.matrix[i], pf.fake_vector(placeholder))
    assert any("zero" in line and "70445-s02-001" in line for line in lines)


def test_a_zero_vector_in_the_cache_is_not_trusted(archive, tmp_path):
    assert run(archive, tmp_path, pf.FakeVoyage()) == 0
    content = storage.load_local(archive / "_build")
    rec = next(r for r in content.records if r["id"] == "70445-s01-001")
    cache = build_index.EmbedCache(archive / "_build" / "content" / "embed_cache", build_index.DEFAULT_MODEL)
    cache.put(build_index.embed_text(rec), np.zeros(pf.DIM, dtype=np.float32))  # a poisoned cache entry
    again = pf.FakeVoyage()
    assert run(archive, tmp_path, again) == 0
    assert again.inputs == [build_index.embed_text(rec)]  # asked again, once
    assert (np.linalg.norm(storage.load_local(archive / "_build").matrix, axis=1) > 0.5).all()


def test_build_fails_rather_than_write_a_zero_row(archive, tmp_path):
    lines: list[str] = []
    zero_all_bananas = ZeroVoyage(lambda text: "banana" in text.lower() or "more fruit" in text.lower())
    assert run(archive, tmp_path, zero_all_bananas, lines) == 2
    assert not (archive / "_build" / "content" / "embeddings.npy").exists()
    assert any("zero" in line for line in lines)

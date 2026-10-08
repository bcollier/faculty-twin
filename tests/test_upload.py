"""indexer/upload.py against a TEST FAKE Supabase (httpx MockTransport). Synthetic data only."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

import pipeline_fixture as pf
from indexer import build_index, common, upload
from indexer.leakcheck import RosterChecker

EXPECTED = {
    "content/index.json",
    "content/embeddings.npy",
    "content/manifest.json",
    "slides/70445/s01/70445-s01-001.webp",
    "slides/70445/s01/70445-s01-001-thumb.webp",
    "slides/70445/s01/70445-s01-003.webp",
    "slides/70445/s01/70445-s01-003-thumb.webp",
    "slides/70445/s02/70445-s02-001.webp",
    "slides/70445/s02/70445-s02-001-thumb.webp",
    "clips/70445-s01-001.mp4",
    "clips/manifest.json",
}


@pytest.fixture
def built(tmp_path: Path) -> Path:
    archive = pf.make_archive(tmp_path)
    code = build_index.build(
        archive, key="test-voyage-key", client=pf.FakeVoyage().client(), code_map=pf.code_map(tmp_path),
        sleep=lambda s: None, log=lambda s: None,
    )
    assert code == 0
    return archive


def fake_sb(fake: pf.FakeSupabase) -> common.Supabase:
    return common.Supabase("https://fake.supabase.co", "service-test-key", "twin-content", client=fake.client())


def do(archive: Path, fake: pf.FakeSupabase | None, **kw) -> tuple[int, str]:
    lines: list[str] = []
    sb = fake_sb(fake) if fake else None
    code = upload.run(archive / "_build", archive / "_private" / "rosters", sb, log=lines.append, **kw)
    return code, "\n".join(lines)


def test_upload_sends_only_what_the_backend_reads(built):
    fake = pf.FakeSupabase()
    code, out = do(built, fake)
    assert code == 0, out
    sent = set(fake.uploads) - {upload.STATE_PATH}
    assert sent == EXPECTED
    for path in fake.uploads:
        assert not upload.forbidden(path)
        assert not any(bad in path for bad in ("transcripts", "align", "review", "_private", "slides.json", "deck.json", ".pdf", "code/"))
    assert "slides/70445/s01/70445-s01-002.webp" not in sent  # excluded slide: image stays home
    assert "clips/70445-s02-001.mp4" not in sent  # In the News slide: no clip
    assert json.loads(fake.objects["clips/manifest.json"]) == [
        {"slide_id": "70445-s01-001", "course": "70445", "session": 1, "start": 10.0, "end": 40.0, "reason_kept": "test"}
    ]
    # Index files go last, manifest very last.
    assert fake.uploads[-4:-1] == ["content/embeddings.npy", "content/index.json", "content/manifest.json"]
    meta = json.loads(base64.b64decode(fake.metadata["content/index.json"]))
    assert meta["sha256"] == common.sha256_file(built / "_build" / "content" / "index.json")
    version = json.loads((built / "_build" / "content" / "manifest.json").read_text())["index_version"]
    assert fake.settings == {"index_version": version}


def test_second_upload_skips_unchanged_objects(built):
    fake = pf.FakeSupabase()
    assert do(built, fake)[0] == 0
    fake.uploads.clear()
    code, out = do(built, fake)
    assert code == 0, out
    assert fake.uploads == [upload.STATE_PATH]
    assert "Uploading 0 of 11 objects" in out


def test_leak_check_aborts_on_roster_hit(tmp_path):
    archive = pf.make_archive(tmp_path)
    pf.write_align(archive / "_build", f"As {pf.FAKE_FULL_NAME} asked, apples are fruit.")
    build_index.build(
        archive, key="test-voyage-key", client=pf.FakeVoyage().client(), sleep=lambda s: None, log=lambda s: None
    )
    fake = pf.FakeSupabase()
    code, out = do(archive, fake)
    assert code == upload.EXIT_LEAK
    assert fake.uploads == [] and fake.settings == {}
    assert "70445-s01-001.transcript" in out and "Upload aborted" in out
    assert "Zorblat" not in out and pf.FAKE_SURNAME not in out
    code, out = do(archive, fake, dry_run=True)  # the dry run lists the plan but reports the block
    assert code == upload.EXIT_LEAK and "BLOCKED" in out and fake.uploads == []


def test_leak_check_levels():
    import csv
    import io

    rows = list(csv.DictReader(io.StringIO(pf.ROSTER_CSV)))
    checker = RosterChecker(rows, english=set())
    assert checker.strong(f"Thanks {pf.FAKE_FULL_NAME}!") == 1
    assert checker.strong("email zquenwic@example.edu or zquenwic") == 2
    assert checker.strong(f"Work by {pf.FAKE_SURNAME} et al.") == 0  # slide citations are not altered
    assert checker.strict(f"I asked {pf.FAKE_SURNAME} to explain") == 1  # transcripts are fully scrubbed
    assert checker.strict(f"I used {pf.FAKE_SURNAME}'s notes") == 1  # a possessive is the name too
    assert checker.strict("Ben Collier explains apples to [student]") == 0
    assert checker.strong("Advisor Person") == 0  # only student columns are read


def test_leak_allowlist_is_narrow(tmp_path):
    """A reviewed non-name hit is allowed in its one record and field only; full names never are."""
    import csv
    import io

    from indexer.leakcheck import load_allowlist

    rows = list(csv.DictReader(io.StringIO(pf.ROSTER_CSV)))
    path = tmp_path / "leak_allowlist.json"
    path.write_text(json.dumps([{"record": "70445-s01-001", "field": "transcript", "token": pf.FAKE_SURNAME,
                                 "reason": "test: the product, not the person"}]))
    checker = RosterChecker(rows, english=set(), allow=load_allowlist(path))
    text = f"The {pf.FAKE_SURNAME} tier costs more."
    index = {"records": [
        {"id": "70445-s01-001", "transcript": text},                          # allowed here
        {"id": "70445-s01-002", "transcript": text},                          # same token, other record: a hit
        {"id": "70445-s01-001", "text": f"Thanks {pf.FAKE_FULL_NAME}"},       # other field: a hit
        {"id": "70445-s01-001", "transcript": f"Thanks {pf.FAKE_FULL_NAME}"},  # full name: never allowed
    ]}
    hits = checker.check_index(index)
    assert hits.allowed == 2  # the bare surname in record 001's transcripts
    assert "content/index.json#70445-s01-002.transcript" in hits.where
    assert "content/index.json#70445-s01-001.text" in hits.where
    # the full name in an allowlisted record and field still blocks
    assert hits.where["content/index.json#70445-s01-001.transcript"] >= 1
    assert pf.FAKE_SURNAME not in hits.summary() and "allowed" in hits.summary()
    path.write_text(json.dumps([{"record": "x", "field": "transcript", "token": "Two words", "reason": "r"}]))
    with pytest.raises(ValueError):
        load_allowlist(path)
    path.write_text(json.dumps([{"record": "x", "field": "transcript", "token": "Word"}]))  # no reason
    with pytest.raises(ValueError):
        load_allowlist(path)


def test_upload_refuses_without_roster(built, tmp_path):
    fake = pf.FakeSupabase()
    lines: list[str] = []
    code = upload.run(built / "_build", tmp_path / "no-rosters", fake_sb(fake), log=lines.append)
    assert code == upload.EXIT_LEAK and fake.uploads == []


def test_forbidden_paths_never_planned():
    for path in [
        "transcripts/70445/s01.json",
        "align/70445/s01.json",
        "review/70445-s01.txt",
        "_private/rosters/x.csv",
        "slides/70445/s01/slides.json",
        "slides/70445/s01/deck.json",
        "slides/70445/s01/source_converted.pdf",
        "code/70445/s01.json",
        "content/embed_cache/voyage-3.5/abc.npy",
        "clips/rejected.json",
        "align/70445/s01.meta.json",
        "clips/70445-s01-001.meta.json",
        ".cache/ocr_vision/x.json",
        "inbox/70445/s01/video/class.mp4",
        "slides/70445/s01/notes.vtt",
    ]:
        assert upload.forbidden(path) and not upload.allowed(path), path
    for path in EXPECTED | {"topics/topics.json", "audio/voice123/abc123.mp3"}:
        assert upload.allowed(path), path


def test_plan_refuses_a_record_pointing_outside_the_allowlist(built):
    index_path = built / "_build" / "content" / "index.json"
    index = json.loads(index_path.read_text())
    index["records"][0]["image"] = "transcripts/70445/s01.json"
    index_path.write_text(json.dumps(index))
    fake = pf.FakeSupabase()
    code, out = do(built, fake)
    assert code == upload.EXIT_PLAN and "forbidden" in out and fake.uploads == []


def test_upload_refuses_index_without_embeddings(tmp_path):
    archive = pf.make_archive(tmp_path)
    build_index.build(archive, key=None, log=lambda s: None)
    code, out = do(archive, pf.FakeSupabase())
    assert code == upload.EXIT_PLAN and "embeddings" in out


def test_dry_run_lists_sizes_and_sends_nothing(built):
    fake = pf.FakeSupabase()
    code, out = do(built, fake, dry_run=True)
    assert code == 0
    assert fake.uploads == [] and fake.settings == {}
    assert "slides/70445/s01/70445-s01-001.webp" in out and "Would upload 11 of 11 objects" in out
    code, out = do(built, None, dry_run=True)  # no Supabase configured: still lists everything
    assert code == 0 and "remote state unknown" in out


def test_objects_dropped_from_the_index_are_deleted(built):
    fake = pf.FakeSupabase()
    assert do(built, fake)[0] == 0
    rows = json.loads((built / "_build" / "slides" / "70445" / "s02" / "slides.json").read_text())
    rows[0]["flags"].append("student_names_possible")
    (built / "_build" / "slides" / "70445" / "s02" / "slides.json").write_text(json.dumps(rows))
    build_index.build(built, key="test-voyage-key", client=pf.FakeVoyage().client(), sleep=lambda s: None, log=lambda s: None)
    code, out = do(built, fake)
    assert code == 0, out
    assert set(fake.deleted) == {"slides/70445/s02/70445-s02-001.webp", "slides/70445/s02/70445-s02-001-thumb.webp"}


def test_topics_and_their_audio_are_uploaded(built):
    build = built / "_build"
    (build / "audio" / "voice123").mkdir(parents=True)
    (build / "audio" / "voice123" / "abc123.mp3").write_bytes(b"ID3fake")
    (build / "audio" / "voice123" / "unused.mp3").write_bytes(b"ID3unused")
    (build / "topics").mkdir()
    topics = [{"question": "What is an apple?", "course": "70445", "playlist": {
        "segments": [{"slide_id": "70445-s01-001", "narration": "Apples.", "audio_path": "audio/voice123/abc123.mp3"}],
        "follow_ups": []}}]
    (build / "topics" / "topics.json").write_text(json.dumps(topics))
    (build / "topics" / "draft_questions.json").write_text("[]")
    fake = pf.FakeSupabase()
    assert do(built, fake)[0] == 0
    assert {"topics/topics.json", "audio/voice123/abc123.mp3"} <= set(fake.uploads)
    assert "audio/voice123/unused.mp3" not in fake.uploads
    assert "topics/draft_questions.json" not in fake.uploads


def test_read_along_sidecars_are_uploaded_for_indexed_slides_only(built):
    """Word boxes (slides) and word timings (stored clips) go up next to what they describe."""
    build = built / "_build"
    words = {"v": 1, "src": "pdf", "words": [["Apples", 0.1, 0.1, 0.3, 0.2, 0]]}
    for sid in ("70445-s01-001", "70445-s01-002"):  # -002 is excluded from the index
        (build / "slides" / "70445" / "s01" / f"{sid}.boxes.json").write_text(json.dumps(words))
    (build / "audio" / "voice123").mkdir(parents=True)
    (build / "audio" / "voice123" / "abc123.mp3").write_bytes(b"ID3fake")
    (build / "audio" / "voice123" / "abc123.words.json").write_text(json.dumps({"words": [[0.0, 0]]}))
    (build / "topics").mkdir()
    (build / "topics" / "topics.json").write_text(json.dumps([{"question": "q", "course": "70445", "playlist": {
        "segments": [{"slide_id": "70445-s01-001", "narration": "Apples.", "audio_path": "audio/voice123/abc123.mp3"}],
        "follow_ups": []}}]))
    fake = pf.FakeSupabase()
    assert do(built, fake)[0] == 0
    sent = set(fake.uploads)
    assert "slides/70445/s01/70445-s01-001.boxes.json" in sent
    assert "slides/70445/s01/70445-s01-002.boxes.json" not in sent
    assert "slides/70445/s01/70445-s01-003.boxes.json" not in sent  # never built: nothing to send
    assert "audio/voice123/abc123.words.json" in sent

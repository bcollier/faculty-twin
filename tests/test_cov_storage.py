"""app/storage.py: loading from a folder and from (faked) Supabase, the index_version reload, and media links."""

from __future__ import annotations

import io
import json
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi import HTTPException

from app import settings_store, storage, supa


def _npy(matrix: np.ndarray) -> bytes:
    buf = io.BytesIO()
    np.save(buf, matrix)
    return buf.getvalue()


def _bucket(records: list[dict], dim: int = 4, extra: dict | None = None) -> dict[str, bytes]:
    files = {
        "content/index.json": json.dumps({"records": records, "built_at": "2026-10-01"}).encode(),
        "content/embeddings.npy": _npy(np.ones((len(records), dim), dtype=np.float64)),
    }
    files.update(extra or {})
    return files


@pytest.fixture
def fake_bucket(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.test")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-key")
    files: dict[str, bytes] = {}

    def download(path: str) -> bytes:
        if path not in files:
            raise supa.SupabaseError(f"download {path} failed (404)")
        return files[path]

    monkeypatch.setattr(supa, "download", download)
    return files


@pytest.fixture
def version(monkeypatch: pytest.MonkeyPatch) -> dict[str, str | None]:
    box: dict[str, str | None] = {"v": "1"}
    monkeypatch.setattr(settings_store, "index_version", lambda: box["v"])
    return box


# ---------------------------------------------------------------- parsing

def test_parse_list_and_dict_forms():
    recs = [{"id": "a"}, {"id": "b"}]
    emb = _npy(np.zeros((2, 3)))
    r1, m1, meta1 = storage._parse(json.dumps(recs).encode(), emb)
    assert r1 == recs and meta1 == {} and m1.dtype == np.float32 and m1.shape == (2, 3)
    r2, _m2, meta2 = storage._parse(json.dumps({"records": recs, "dim": 3}).encode(), emb)
    assert r2 == recs and meta2 == {"dim": 3}


def test_parse_rejects_mismatched_rows_and_1d():
    with pytest.raises(storage.ContentUnavailable, match="3 records"):
        storage._parse(json.dumps([{"id": "a"}, {"id": "b"}, {"id": "c"}]).encode(), _npy(np.zeros((2, 3))))
    with pytest.raises(storage.ContentUnavailable):
        storage._parse(json.dumps([{"id": "a"}]).encode(), _npy(np.zeros(3)))


def test_clip_windows_skips_bad_rows():
    rows = [{"slide_id": "a", "start": "1.5", "end": 9}, {"slide_id": "b"}, {"start": 1, "end": 2},
            {"slide_id": "c", "start": "x", "end": 1}, None]
    assert storage._clip_windows(rows) == {"a": (1.5, 9.0)}
    assert storage._clip_windows(None) == {}


def test_optional_info_missing_malformed_and_empty():
    def missing():
        raise FileNotFoundError

    assert storage._optional_info(missing, missing) == ([], None)
    assert storage._optional_info(lambda: b"{not json", lambda: b"") == ([], None)
    assert storage._optional_info(lambda: b"[]", lambda: _npy(np.zeros((0, 4)))) == ([], None)
    recs, mat = storage._optional_info(lambda: json.dumps([{"id": "i1"}]).encode(), lambda: _npy(np.ones((1, 4))))
    assert recs == [{"id": "i1"}] and mat.shape == (1, 4)


def test_load_local_missing_index(tmp_path: Path):
    with pytest.raises(storage.ContentUnavailable, match="No index"):
        storage.load_local(tmp_path)


def test_load_local_reads_optional_files(content_dir: Path):
    c = storage.load_local(content_dir)
    assert c.source == "local" and c.records and c.matrix.shape[0] == len(c.records)
    assert c.record(c.records[0]["id"]) is c.records[0]
    assert c.record("nope") is None


# ---------------------------------------------------------------- Supabase loading

def test_load_supabase_with_optional_files(fake_bucket):
    info = [{"id": "info-1", "course": "70445", "text": "Syllabus"}]
    fake_bucket.update(_bucket([{"id": "s1"}, {"id": "s2"}], extra={
        "topics/topics.json": json.dumps([{"question": "Q?", "course": "70445"}]).encode(),
        "clips/manifest.json": json.dumps([{"slide_id": "s1", "start": 3, "end": 9}]).encode(),
        "content/info_index.json": json.dumps(info).encode(),
        "content/info_embeddings.npy": _npy(np.ones((1, 4))),
    }))
    c = storage.load_supabase()
    assert c.source == "supabase"
    assert [r["id"] for r in c.records] == ["s1", "s2"]
    assert c.meta == {"built_at": "2026-10-01"}
    assert c.topics == [{"question": "Q?", "course": "70445"}]
    assert c.clip_windows == {"s1": (3.0, 9.0)}
    assert c.info_records == info and c.info_matrix.shape == (1, 4)


def test_load_supabase_without_optional_files(fake_bucket):
    fake_bucket.update(_bucket([{"id": "s1"}]))
    c = storage.load_supabase()
    assert c.topics == [] and c.clip_windows == {} and c.info_records == [] and c.info_matrix is None


def test_load_supabase_missing_index_is_unavailable(fake_bucket):
    with pytest.raises(storage.ContentUnavailable, match="404"):
        storage.load_supabase()


# ---------------------------------------------------------------- the Store and reload

def test_store_without_any_source_is_503():
    s = storage.Store()
    with pytest.raises(storage.ContentUnavailable, match="No content source"):
        s.get()
    with pytest.raises(HTTPException) as err:
        s.get_or_503()
    assert err.value.status_code == 503 and "not loaded" in err.value.detail
    assert s.loaded is None


def test_store_loads_once_then_reloads_when_index_version_changes(fake_bucket, version, monkeypatch):
    fake_bucket.update(_bucket([{"id": "old"}]))
    s = storage.Store()
    first = s.get()
    assert [r["id"] for r in first.records] == ["old"] and first.version == "1"

    fake_bucket.update(_bucket([{"id": "new-a"}, {"id": "new-b"}]))
    assert s.get() is first  # inside the check window: no version check, no reload

    monkeypatch.setattr(storage, "VERSION_CHECK_SECONDS", 0.0)
    assert s.get() is first  # due, but the version did not change

    version["v"] = "2"
    fresh = s.get()
    assert [r["id"] for r in fresh.records] == ["new-a", "new-b"] and fresh.version == "2"
    assert s.loaded is fresh


def test_store_keeps_old_index_when_reload_fails(fake_bucket, version, monkeypatch):
    fake_bucket.update(_bucket([{"id": "kept"}]))
    s = storage.Store()
    first = s.get()
    fake_bucket.clear()  # bucket now empty: the reload fails
    version["v"] = "2"
    monkeypatch.setattr(storage, "VERSION_CHECK_SECONDS", 0.0)
    assert s.get() is first
    assert s.get_or_503() is first


def test_store_set_and_reset(content_dir):
    s = storage.Store()
    c = storage.load_local(content_dir)
    s.set(c)
    assert s.get() is c and s.loaded is c
    s.reset()
    assert s.loaded is None


def test_store_prefers_content_dir(content_dir, fake_bucket, version, monkeypatch):
    monkeypatch.setenv("CONTENT_DIR", str(content_dir))
    assert storage.Store().get().source == "local"


# ---------------------------------------------------------------- media links

def test_is_media_path():
    assert storage.is_media_path("/slides/a.webp")
    assert storage.is_media_path("clips/x.mp4") and storage.is_media_path("audio/v/h.mp3")
    assert not storage.is_media_path("content/index.json")
    assert not storage.is_media_path("slides/../content/index.json")
    assert not storage.is_media_path("evals/questions.jsonl")


def test_media_urls_nothing_wanted():
    assert storage.media_urls([None, "", "content/index.json"]) == {}


def test_media_urls_without_any_source_is_empty():
    assert storage.media_urls(["slides/a.webp"]) == {}


def test_media_urls_dev_links_verify(content_dir, monkeypatch):
    monkeypatch.setenv("CONTENT_DIR", str(content_dir))
    out = storage.media_urls(["/slides/a b.webp", "slides/a b.webp", "content/index.json"], expires_in=60)
    assert list(out) == ["slides/a b.webp"]
    link = out["slides/a b.webp"]
    assert link.startswith("/api/files/slides/a%20b.webp?exp=")
    exp = int(link.split("exp=")[1].split("&")[0])
    sig = link.split("sig=")[1]
    assert storage.verify_dev_link("slides/a b.webp", exp, sig)
    assert not storage.verify_dev_link("slides/other.webp", exp, sig)
    assert not storage.verify_dev_link("slides/a b.webp", int(time.time()) - 1, sig)


def test_media_urls_supabase_signs_and_survives_failure(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.test")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-key")
    seen = {}

    def sign(paths, expires_in):
        seen["paths"], seen["exp"] = paths, expires_in
        return {p: f"https://signed/{p}" for p in paths}

    monkeypatch.setattr(supa, "sign_urls", sign)
    out = storage.media_urls(["clips/b.mp4", "slides/a.webp", "clips/b.mp4"])
    assert seen == {"paths": ["clips/b.mp4", "slides/a.webp"], "exp": storage.SIGNED_URL_SECONDS}
    assert out["slides/a.webp"] == "https://signed/slides/a.webp"

    def broken(paths, expires_in):
        raise supa.SupabaseError("sign urls failed (500)")

    monkeypatch.setattr(supa, "sign_urls", broken)
    assert storage.media_urls(["slides/a.webp"]) == {}


def test_local_file_blocks_traversal_and_non_media(content_dir, monkeypatch):
    assert storage.local_file("slides/x.webp") is None  # no CONTENT_DIR
    monkeypatch.setenv("CONTENT_DIR", str(content_dir))
    real = next(p for p in (content_dir / "slides").rglob("*.webp"))
    rel = real.relative_to(content_dir).as_posix()
    assert storage.local_file(rel) == real.resolve()
    assert storage.local_file("content/index.json") is None
    assert storage.local_file("slides/../content/index.json") is None
    assert storage.local_file("slides/missing.webp") is None
    assert storage.local_file("slides") is None  # a folder, not a file

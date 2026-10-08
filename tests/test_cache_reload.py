"""Settings cache, hidden-session cache, and index reload: races and stale copies (Oct 8 code review).

Supabase is a TEST FAKE here (supa.select / supa.insert monkeypatched); content is synthetic.
"""

from __future__ import annotations

import numpy as np
import pytest
from fastapi import HTTPException

from app import playlist, settings_store, storage, supa


@pytest.fixture
def fake_supabase(monkeypatch):
    """TEST FAKE: an in-memory settings table and sessions table behind supa.select / supa.insert."""
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
    db = {"settings": {}, "sessions": [], "during_select": None}

    def select(table, params=None):
        if table == "settings":
            snapshot = [{"key": k, "value": v} for k, v in db["settings"].items()]
        else:
            snapshot = [dict(r) for r in db["sessions"] if not r["visible"]]
        hook, db["during_select"] = db["during_select"], None
        if hook:
            hook()  # something lands while this read is in flight; the read returns what it saw before
        return snapshot

    def insert(table, rows, upsert_on=None):
        for r in rows if isinstance(rows, list) else [rows]:
            db["settings"][r["key"]] = r["value"]
        return rows

    monkeypatch.setattr(supa, "select", select)
    monkeypatch.setattr(supa, "insert", insert)
    return db


# ---------------------------------------------------------------- settings cache

def test_a_save_during_a_slow_read_is_not_hidden_by_the_stale_read(fake_supabase):
    fake_supabase["settings"]["model"] = "old-model"
    fake_supabase["during_select"] = lambda: settings_store.put({"model": "new-model"})
    settings_store.get("model")  # this read started before the save and returns the old value
    # Before the fix the stale read was cached for 30 s, so the save was invisible on this instance.
    assert settings_store.get("model") == "new-model"


def test_settings_are_still_cached_between_saves(fake_supabase):
    fake_supabase["settings"]["model"] = "m1"
    assert settings_store.get("model") == "m1"
    fake_supabase["settings"]["model"] = "m2"  # written by another instance
    assert settings_store.get("model") == "m1"  # within 30 s this instance keeps its copy


# ---------------------------------------------------------------- hidden sessions

def test_hiding_a_session_during_a_slow_read_takes_effect(fake_supabase):
    fake_supabase["sessions"] = [{"course": "70445", "session": 3, "visible": True}]

    def hide():
        fake_supabase["sessions"][0]["visible"] = False
        playlist.clear_hidden_cache()  # what the admin PATCH route does

    fake_supabase["during_select"] = hide
    playlist.hidden_sessions()
    assert playlist.hidden_sessions() == {("70445", 3)}


# ---------------------------------------------------------------- index reload

def _content(version_in_file: str | None = None) -> storage.Content:
    meta = {"index_version": version_in_file} if version_in_file else {}
    rec = {"id": "70445-s01-001", "kind": "slide", "course": "70445", "session": 1, "slide_number": 1}
    return storage.Content([rec], np.ones((1, 4), dtype=np.float32), meta=meta, source="supabase")


@pytest.fixture
def remote(monkeypatch, fake_supabase):
    """The bucket as a TEST FAKE: what load_supabase returns, and a hook that runs during the download."""
    state = {"file_version": "v1", "loads": 0, "during": None, "error": None}

    def load():
        state["loads"] += 1
        hook, state["during"] = state["during"], None
        if hook:
            hook()
        if state["error"]:
            raise state["error"]
        return _content(state["file_version"])

    monkeypatch.setattr(storage, "load_supabase", load)
    monkeypatch.setattr(storage, "VERSION_CHECK_SECONDS", 0.0)
    return state


def test_a_version_bump_during_the_download_is_noticed_later(remote, fake_supabase):
    fake_supabase["settings"]["index_version"] = "v1"

    def upload_finishes():  # new files and the version bump land after the download began
        fake_supabase["settings"]["index_version"] = "v2"
        settings_store.clear_cache()

    remote["during"] = upload_finishes
    storage.store.get()
    assert remote["loads"] == 1
    remote["file_version"] = "v2"
    content = storage.store.get()  # before the fix the old download was labelled v2 and never reloaded
    assert remote["loads"] == 2 and content.meta["index_version"] == "v2"


def test_a_stale_cdn_copy_is_reloaded_until_it_matches_the_version(remote, fake_supabase):
    fake_supabase["settings"]["index_version"] = "v2+info-abcd1234"
    remote["file_version"] = "v1"  # the CDN still serves the previous index.json
    storage.store.get()
    remote["file_version"] = "v2"
    content = storage.store.get()
    assert remote["loads"] == 2 and content.meta["index_version"] == "v2"
    storage.store.get()
    assert remote["loads"] == 2  # matches now: no more reloads


def test_a_version_that_never_matches_stops_reloading_after_a_few_tries(remote, fake_supabase):
    fake_supabase["settings"]["index_version"] = "v9"  # e.g. set by hand; the files say v1
    for _ in range(10):
        storage.store.get()
    assert remote["loads"] <= 1 + storage.STALE_RELOAD_TRIES


def test_a_half_written_index_is_a_503_not_a_500(remote, fake_supabase):
    remote["error"] = ValueError("Expecting value: line 1 column 1 (char 0)")
    with pytest.raises(HTTPException) as exc:
        storage.store.get_or_503()
    assert exc.value.status_code == 503


def test_a_half_written_index_keeps_the_loaded_one(remote, fake_supabase):
    fake_supabase["settings"]["index_version"] = "v1"
    first = storage.store.get()
    fake_supabase["settings"]["index_version"] = "v2"
    settings_store.clear_cache()
    remote["error"] = ValueError("cannot reshape array")
    assert storage.store.get() is first

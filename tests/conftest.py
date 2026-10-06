from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))

import build_fixture  # noqa: E402

KEYS_TO_CLEAR = [
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_BUCKET",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "VOYAGE_API_KEY",
    "VOYAGE_MODEL",
    "ELEVENLABS_API_KEY",
    "ELEVENLABS_VOICE_ID",
    "ELEVENLABS_MODEL_ID",
    "LLM_PROVIDER",
    "LLM_MODEL",
    "DAILY_VOICE_CHAR_CAP",
    "VERCEL",
]


@pytest.fixture
def content_dir(tmp_path: Path) -> Path:
    return build_fixture.build(tmp_path / "content_root")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    for key in KEYS_TO_CLEAR:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("AUDIO_SIGNING_SECRET", "test-audio-secret")
    monkeypatch.setenv("STUDENT_PASSCODE", "student-pass")
    monkeypatch.setenv("ADMIN_PASSCODE", "admin-pass")
    monkeypatch.delenv("CONTENT_DIR", raising=False)

    from app import limits, playlist, settings_store, storage

    storage.store.reset()
    settings_store.clear_cache()
    limits.reset_memory()
    playlist.clear_hidden_cache()
    yield
    storage.store.reset()
    settings_store.clear_cache()
    limits.reset_memory()


@pytest.fixture
def client(content_dir: Path, monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setenv("CONTENT_DIR", str(content_dir))
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def student(client):
    r = client.post("/api/login", json={"passcode": "student-pass"})
    assert r.status_code == 204, r.text
    return client


@pytest.fixture
def admin(client):
    r = client.post("/api/admin/login", json={"passcode": "admin-pass"})
    assert r.status_code == 204, r.text
    return client

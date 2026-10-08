from __future__ import annotations

import ipaddress
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))

import build_fixture  # noqa: E402


# ---------------------------------------------------------------- no network
# The suite never talks to a real service: models, embeddings, voices and storage are fakes.
# This guard turns any outbound connection (or DNS lookup) to a non-loopback host into a test
# failure, so a missing fake cannot quietly call a provider or spend money. Loopback stays open
# for the browser tests' local static server.

class NetworkBlocked(RuntimeError):
    pass


_LOOPBACK_NAMES = {"localhost", "localhost.localdomain", "testserver", ""}


def _is_loopback(host) -> bool:
    if host is None:
        return True  # getaddrinfo(None, port): the local wildcard, used by bind()
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    host = str(host).strip("[]")
    if host in _LOOPBACK_NAMES:
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_loopback or addr.is_unspecified


def _guard_address(address) -> None:
    if isinstance(address, (str, bytes)):  # AF_UNIX path
        return
    if isinstance(address, tuple) and address and not _is_loopback(address[0]):
        raise NetworkBlocked(f"tests may not open network connections (tried {address[0]!r})")


_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_getaddrinfo = socket.getaddrinfo


def _blocked_connect(self, address):
    _guard_address(address)
    return _real_connect(self, address)


def _blocked_connect_ex(self, address):
    _guard_address(address)
    return _real_connect_ex(self, address)


def _blocked_getaddrinfo(host, *args, **kwargs):
    if not _is_loopback(host):
        raise NetworkBlocked(f"tests may not look up network hosts (tried {host!r})")
    return _real_getaddrinfo(host, *args, **kwargs)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--e2e", action="store_true", default=False,
                     help="also run the browser tests in tests/e2e (needs requirements-e2e.txt and chromium)")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "e2e: browser test of public/ with ?mock=1 (skipped unless --e2e)")
    socket.socket.connect = _blocked_connect
    socket.socket.connect_ex = _blocked_connect_ex
    socket.getaddrinfo = _blocked_getaddrinfo


def pytest_unconfigure(config: pytest.Config) -> None:
    socket.socket.connect = _real_connect
    socket.socket.connect_ex = _real_connect_ex
    socket.getaddrinfo = _real_getaddrinfo


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--e2e"):
        return
    skip = pytest.mark.skip(reason="browser test: pass --e2e to run it")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip)

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
    "DAILY_FREE_VOICE_CHAR_CAP",
    "EDGE_TTS_RATE",
    "EDGE_TTS_PITCH",
    "VERCEL",
    "VERCEL_ENV",
    "VERCEL_REGION",
    "VERCEL_URL",
    "FT_ENV",
    "FT_LOCAL_DEV",
    "DAILY_LLM_CALL_CAP",
    "DAILY_EMBED_CAP",
    "LLM_MAX_PROMPT_PRICE_PER_MTOK",
    "LLM_MAX_COMPLETION_PRICE_PER_MTOK",
    "PUBLIC_SITE_URL",
]


@pytest.fixture
def content_dir(tmp_path: Path) -> Path:
    return build_fixture.build(tmp_path / "content_root")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    for key in KEYS_TO_CLEAR:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret-0123456789abcdef")
    monkeypatch.setenv("AUDIO_SIGNING_SECRET", "test-audio-secret-0123456789abcdef")
    monkeypatch.setenv("STUDENT_PASSCODE", "student-pass")
    monkeypatch.setenv("ADMIN_PASSCODE", "admin-pass")
    monkeypatch.delenv("CONTENT_DIR", raising=False)

    from app import edge_voice, limits, playlist, prompts, settings_store, speech, storage

    storage.store.reset()
    settings_store.clear_cache()
    prompts.clear_local()
    limits.reset_memory()
    playlist.clear_hidden_cache()
    speech.clear_cache()
    edge_voice.clear_cache()
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

"""Browser tests: public/ served on 127.0.0.1, driven by headless Chromium with ?mock=1.

No backend runs. public/dev/mock.js answers every /api/ call with canned data whose shapes
tests/test_contract_mock.py keeps in line with the real API. Skipped unless pytest gets --e2e.

    uv run --no-project --with-requirements requirements.txt --with-requirements requirements-test.txt \
        --with-requirements requirements-e2e.txt python -m pytest -q --e2e tests/e2e
"""

from __future__ import annotations

import functools
import threading
from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PUBLIC = Path(__file__).resolve().parents[2] / "public"


class QuietHandler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".js": "text/javascript", ".mjs": "text/javascript"}

    def log_message(self, *args) -> None:  # keep test output clean
        pass


@pytest.fixture(scope="session")
def base_url() -> Iterator[str]:
    handler = functools.partial(QuietHandler, directory=str(PUBLIC))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="module")
def browser(request):
    """One Chromium per test file. Module scope, not session: Playwright's sync API keeps an event loop
    running while it is open, which would break later tests that call asyncio.run()."""
    if not request.config.getoption("--e2e"):
        pytest.skip("browser test: pass --e2e to run it")
    sync_api = pytest.importorskip("playwright.sync_api", reason="install requirements-e2e.txt")
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required", "--mute-audio"])
        yield b
        b.close()


@pytest.fixture
def page(browser):
    """A fresh context per test (its own sessionStorage, so the mock's login starts signed out).

    Uncaught page errors fail the test: a broken render is the main thing these tests guard against.
    """
    context = browser.new_context(viewport={"width": 1280, "height": 860})
    context.set_default_timeout(10_000)
    pg = context.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda exc: errors.append(str(exc)))
    yield pg
    context.close()
    assert not errors, f"uncaught errors on the page: {errors}"

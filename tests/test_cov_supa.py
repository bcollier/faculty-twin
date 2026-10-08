"""app/supa.py against a fake Supabase (httpx.MockTransport): URLs, headers, bodies, and errors.

No network: every request goes to an in-process handler that records it.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app import supa

BASE = "https://proj.supabase.test"
KEY = "service-role-test-key"


class FakeSupabase:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: dict[tuple[str, str], httpx.Response | Exception] = {}

    def on(self, method: str, path: str, response) -> None:
        self.routes[(method, path)] = response

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        hit = self.routes.get((request.method, request.url.path))
        if hit is None:
            return httpx.Response(404, json={"error": "not found"})
        if isinstance(hit, Exception):
            raise hit
        return hit

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeSupabase:
    monkeypatch.setenv("SUPABASE_URL", BASE + "/")  # trailing slash is trimmed
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
    f = FakeSupabase()
    monkeypatch.setattr(supa, "_client", lambda: supa._Client(transport=httpx.MockTransport(f.handler)))
    return f


def test_not_configured_raises_before_any_request(monkeypatch):
    calls = []
    monkeypatch.setattr(supa, "_client", lambda: supa._Client(transport=httpx.MockTransport(
        lambda r: calls.append(r) or httpx.Response(200, json=[]))))
    with pytest.raises(supa.SupabaseError, match="not configured"):
        supa.select("settings")
    monkeypatch.setenv("SUPABASE_URL", BASE)  # URL alone is not enough
    with pytest.raises(supa.SupabaseError, match="not configured"):
        supa.download("content/index.json")
    assert calls == []


def test_select_sends_service_key_and_filters(fake):
    fake.on("GET", "/rest/v1/settings", httpx.Response(200, json=[{"key": "model", "value": "x"}]))
    rows = supa.select("settings", {"select": "key,value"})
    assert rows == [{"key": "model", "value": "x"}]
    req = fake.last
    assert req.headers["apikey"] == KEY
    assert req.headers["authorization"] == f"Bearer {KEY}"
    assert req.url.params["select"] == "key,value"


def test_insert_plain_and_upsert(fake):
    fake.on("POST", "/rest/v1/question_log", httpx.Response(201, json=[{"id": 1}]))
    assert supa.insert("question_log", {"question": "q"}) == [{"id": 1}]
    assert fake.last.headers["prefer"] == "return=representation"
    assert "on_conflict" not in fake.last.url.params
    assert json.loads(fake.last.content) == {"question": "q"}

    supa.insert("question_log", [{"question": "a"}], upsert_on="key")
    assert fake.last.headers["prefer"] == "return=representation,resolution=merge-duplicates"
    assert fake.last.url.params["on_conflict"] == "key"


def test_update_patches_matching_rows(fake):
    fake.on("PATCH", "/rest/v1/sources", httpx.Response(200, json=[{"id": 5, "status": "ready"}]))
    out = supa.update("sources", {"id": "eq.5"}, {"status": "ready"})
    assert out[0]["status"] == "ready"
    assert fake.last.url.params["id"] == "eq.5"
    assert json.loads(fake.last.content) == {"status": "ready"}


def test_rpc_posts_args(fake):
    fake.on("POST", "/rest/v1/rpc/increment_counter", httpx.Response(200, json=7))
    assert supa.rpc("increment_counter", {"k": "x", "n": 1}) == 7
    assert json.loads(fake.last.content) == {"k": "x", "n": 1}


def test_http_error_is_readable_and_never_echoes_the_key(fake):
    fake.on("GET", "/rest/v1/settings", httpx.Response(500, text="database is down"))
    with pytest.raises(supa.SupabaseError) as err:
        supa.select("settings")
    assert "500" in str(err.value) and "database is down" in str(err.value)
    assert KEY not in str(err.value)


def test_transport_error_becomes_supabase_error(fake):
    fake.on("GET", "/rest/v1/settings", httpx.ConnectError("boom"))
    with pytest.raises(supa.SupabaseError, match="ConnectError"):
        supa.select("settings")


def test_download_uses_bucket_and_quotes_path(fake, monkeypatch):
    monkeypatch.setenv("SUPABASE_BUCKET", "my-bucket")
    fake.on("GET", "/storage/v1/object/my-bucket/slides/a b.webp", httpx.Response(200, content=b"IMG"))
    assert supa.download("/slides/a b.webp") == b"IMG"
    assert fake.last.url.raw_path.endswith(b"/slides/a%20b.webp")


def test_download_optional_missing_is_none_and_errors_raise(fake):
    fake.on("GET", "/storage/v1/object/twin-content/a.json", httpx.Response(400, json={"error": "not_found"}))
    assert supa.download_optional("a.json") is None
    fake.on("GET", "/storage/v1/object/twin-content/b.json", httpx.Response(200, content=b"{}"))
    assert supa.download_optional("b.json") == b"{}"
    fake.on("GET", "/storage/v1/object/twin-content/c.json", httpx.Response(503, text="busy"))
    with pytest.raises(supa.SupabaseError, match="503"):
        supa.download_optional("c.json")


def test_upload_headers(fake):
    fake.on("POST", "/storage/v1/object/twin-content/evals/x.json", httpx.Response(200, json={}))
    supa.upload("evals/x.json", b"{}", "application/json")
    assert fake.last.headers["x-upsert"] == "false"
    assert "cache-control" not in fake.last.headers
    assert fake.last.content == b"{}"
    supa.upload("evals/x.json", b"{}", "application/json", upsert=True, cache_control="no-cache, max-age=0")
    assert fake.last.headers["x-upsert"] == "true"
    assert fake.last.headers["cache-control"] == "no-cache, max-age=0"
    assert fake.last.headers["content-type"] == "application/json"


def test_upload_failure_raises(fake):
    fake.on("POST", "/storage/v1/object/twin-content/x", httpx.Response(413, text="too large"))
    with pytest.raises(supa.SupabaseError, match="413"):
        supa.upload("x", b"1", "text/plain")


def test_list_objects_strips_prefix_and_skips_nameless(fake):
    fake.on("POST", "/storage/v1/object/list/twin-content",
            httpx.Response(200, json=[{"name": "b.json"}, {"name": None}, {"id": "x"}, {"name": "a.json"}]))
    assert supa.list_objects("/prompts/history/") == ["b.json", "a.json"]
    body = json.loads(fake.last.content)
    assert body["prefix"] == "prompts/history"
    assert body["limit"] == 100 and body["sortBy"]["order"] == "desc"


def test_object_exists(fake):
    fake.on("HEAD", "/storage/v1/object/twin-content/yes", httpx.Response(200))
    assert supa.object_exists("yes") is True
    assert supa.object_exists("no") is False


def test_sign_urls_builds_absolute_links_and_drops_errors(fake):
    assert supa.sign_urls([], 60) == {}
    assert fake.requests == []
    fake.on("POST", "/storage/v1/object/sign/twin-content", httpx.Response(200, json=[
        {"path": "slides/1.webp", "signedURL": "/object/sign/twin-content/slides/1.webp?token=a"},
        {"path": "slides/2.webp", "signedUrl": "/object/sign/twin-content/slides/2.webp?token=b"},
        {"path": "slides/3.webp", "signedURL": "/x", "error": "Either the object does not exist"},
        {"path": "slides/4.webp", "signedURL": None},
    ]))
    out = supa.sign_urls(["slides/1.webp", "slides/2.webp", "slides/3.webp", "slides/4.webp"], 3600)
    assert out == {
        "slides/1.webp": f"{BASE}/storage/v1/object/sign/twin-content/slides/1.webp?token=a",
        "slides/2.webp": f"{BASE}/storage/v1/object/sign/twin-content/slides/2.webp?token=b",
    }
    assert json.loads(fake.last.content)["expiresIn"] == 3600


def test_create_upload_url(fake):
    fake.on("POST", "/storage/v1/object/upload/sign/twin-content/inbox/a.pdf",
            httpx.Response(200, json={"url": "/object/upload/sign/twin-content/inbox/a.pdf?token=t"}))
    url = supa.create_upload_url("inbox/a.pdf")
    assert url == f"{BASE}/storage/v1/object/upload/sign/twin-content/inbox/a.pdf?token=t"
    assert fake.last.headers["x-upsert"] == "true"
    supa.create_upload_url("inbox/a.pdf", upsert=False)
    assert fake.last.headers["x-upsert"] == "false"


def test_create_upload_url_without_url_is_an_error(fake):
    fake.on("POST", "/storage/v1/object/upload/sign/twin-content/inbox/a.pdf", httpx.Response(200, json={}))
    with pytest.raises(supa.SupabaseError, match="no url"):
        supa.create_upload_url("inbox/a.pdf")


def test_real_client_factory_is_a_supabase_client():
    c = supa._client()
    try:
        assert isinstance(c, supa._Client)
    finally:
        c.close()

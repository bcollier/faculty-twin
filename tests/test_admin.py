"""Settings page API, with Supabase mocked where a route needs it."""

from __future__ import annotations

import json

import pytest

from app import settings_store, supa
from app.main import app, get_completer


def test_admin_login(client):
    assert client.post("/api/admin/login", json={"passcode": "student-pass"}).status_code == 401
    r = client.post("/api/admin/login", json={"passcode": "admin-pass"})
    assert r.status_code == 204 and "ft_admin=" in r.headers["set-cookie"]


def test_admin_routes_need_admin_cookie(student):
    for path in ["/api/admin/settings", "/api/admin/status", "/api/admin/log", "/api/admin/courses",
                 "/api/admin/voices", "/api/admin/sources", "/api/admin/models?provider=openrouter"]:
        assert student.get(path).status_code == 401, path


def test_settings_get_and_put(admin):
    s = admin.get("/api/admin/settings").json()
    assert s["provider"] == "anthropic" and s["model"] == "claude-sonnet-5-5"
    assert s["voice_id"] is None and s["providers"] == ["anthropic", "openai", "openrouter"]

    s = admin.put("/api/admin/settings", json={"provider": "openrouter", "model": "openai/gpt-6-luna"}).json()
    assert (s["provider"], s["model"]) == ("openrouter", "openai/gpt-6-luna")
    s = admin.put("/api/admin/settings", json={"provider": "openai"}).json()
    assert (s["provider"], s["model"]) == ("openai", "gpt-6.1-sol")
    s = admin.put("/api/admin/settings", json={"voice_id": "abc123", "daily_voice_char_cap": 5000}).json()
    assert s["voice_id"] == "abc123" and s["voice_source"] == "settings" and s["daily_voice_char_cap"] == 5000
    s = admin.put("/api/admin/settings", json={"voice_id": "none"}).json()
    assert s["voice_id"] == "none" and s["voice_source"] == "none"
    assert settings_store.voice_id() is None
    s = admin.put("/api/admin/settings", json={"voice_id": None}).json()
    assert s["voice_id"] is None and s["voice_source"] == "none"  # no ELEVENLABS_VOICE_ID in tests
    r = admin.put("/api/admin/settings", json={"student_passcode": "a-new-passcode"})
    assert "a-new-passcode" not in r.text and "pbkdf2" not in r.text

    assert admin.put("/api/admin/settings", json={"provider": "gemini"}).status_code == 400
    assert admin.put("/api/admin/settings", json={"model": "bad model id!"}).status_code == 400
    assert admin.put("/api/admin/settings", json={"daily_voice_char_cap": -1}).status_code == 400
    assert admin.put("/api/admin/settings", json={"student_passcode": "123"}).status_code == 400
    assert admin.put("/api/admin/settings", json={}).status_code == 400


def test_status_reports_booleans_only(admin, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test-value-xyz")
    r = admin.get("/api/admin/status")
    assert r.status_code == 200
    assert "ant-test-value-xyz" not in r.text and "admin-pass" not in r.text
    body = r.json()
    assert body["keys"]["ANTHROPIC_API_KEY"] is True and body["keys"]["OPENAI_API_KEY"] is False
    assert body["content"]["loaded"] is True and body["content"]["slides"] == 7
    assert body["today"]["voice_chars"] == 0 and body["today"]["voice_char_cap"] == 20000


def test_models_route(admin):
    r = admin.get("/api/admin/models?provider=anthropic")
    assert r.status_code == 200 and r.json()["models"][0]["id"] == "claude-sonnet-5-5"
    assert admin.get("/api/admin/models?provider=nope").status_code == 400


def test_model_test_uses_sample_slides_until_retrieval_exists(admin, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    seen = {}

    def fake(system, user, max_tokens, provider=None, model=None):
        seen["pm"] = (provider, model)
        ids = [s["slide_id"] for s in json.loads(user.split("shown:\n", 1)[1])]
        return json.dumps({"segments": [{"slide_id": i, "narration": "Fine."} for i in ids], "follow_ups": []})

    app.dependency_overrides[get_completer] = lambda: fake
    r = admin.post("/api/admin/test", json={"question": "what is an apple", "provider": "openai", "model": "gpt-6-luna"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["narration_source"] == "llm" and body["note"]
    assert seen["pm"] == ("openai", "gpt-6-luna")
    assert len(body["playlist"]["segments"]) == 3
    assert body["narration"]["segments"][0]["narration"] == "Fine."


def test_model_test_needs_key(admin):
    r = admin.post("/api/admin/test", json={"question": "hi", "provider": "openrouter"})
    assert r.status_code == 400 and "OPENROUTER_API_KEY" in r.json()["detail"]


def test_voices_needs_key(admin):
    assert admin.get("/api/admin/voices").status_code == 503


def test_courses_from_index_without_supabase(admin):
    courses = admin.get("/api/admin/courses").json()["courses"]
    a = next(c for c in courses if c["course"] == "70445")
    s1 = a["sessions"][0]
    assert s1["id"] == "70445-s01" and s1["slides_indexed"] == 5 and s1["clips"] == 1
    assert s1["sources"] == {"slides": None, "transcript": None, "video": None, "notebook": None}
    assert s1["has"] == {"slides": True, "transcript": True, "video": True, "clips": True, "indexed": True}


def test_supabase_routes_503_without_supabase(admin):
    assert admin.post("/api/admin/courses", json={"course": "70445", "title": "x"}).status_code == 503
    r = admin.post("/api/admin/uploads", json={"course": "70445", "session": 1, "kind": "slides",
                                               "filename": "a.pdf", "size": 10})
    assert r.status_code == 503


# ---------------------------------------------------------------- with a fake Supabase

class FakeSupa:
    """In-memory stand-in for app.supa (tables only)."""

    def __init__(self):
        self.tables = {"courses": [{"code": "70445", "title": "Fake Course A", "term": "F26"}],
                       "sessions": [], "sources": [], "settings": [], "question_log": [], "counters": []}
        self.objects: set[str] = set()
        self.next_id = 1

    def _match(self, row, params):
        for k, v in params.items():
            if k in ("select", "order", "limit", "on_conflict"):
                continue
            want = v.split(".", 1)[1]
            if str(row.get(k)).lower() != want.lower():
                return False
        return True

    def select(self, table, params=None):
        return [dict(r) for r in self.tables[table] if self._match(r, params or {})]

    def insert(self, table, row, upsert_on=None):
        rows = row if isinstance(row, list) else [row]
        out = []
        for r in rows:
            existing = next((x for x in self.tables[table] if upsert_on and x.get(upsert_on) == r.get(upsert_on)), None)
            if existing:
                existing.update(r)
                out.append(dict(existing))
            else:
                r = dict(r)
                if table == "sources":
                    r["id"] = self.next_id
                    self.next_id += 1
                if table == "sessions":
                    r.setdefault("visible", True)
                self.tables[table].append(r)
                out.append(dict(r))
        return out

    def update(self, table, match, values):
        rows = [r for r in self.tables[table] if self._match(r, match)]
        for r in rows:
            r.update(values)
        return [dict(r) for r in rows]

    def rpc(self, fn, args):
        assert fn == "ft_increment"
        row = next((r for r in self.tables["counters"] if r["key"] == args["p_key"]), None)
        if row is None:
            row = {"key": args["p_key"], "count": 0}
            self.tables["counters"].append(row)
        cap = args["p_cap"]
        if cap is not None and row["count"] + args["p_amount"] > cap:
            return [{"allowed": False, "count": row["count"]}]
        row["count"] += args["p_amount"]
        return [{"allowed": True, "count": row["count"]}]

    def create_upload_url(self, path, upsert=True):
        return f"https://example.supabase.co/storage/v1/object/upload/sign/twin-content/{path}?token=tok"

    def object_exists(self, path):
        return path in self.objects


@pytest.fixture
def fake_supa(monkeypatch):
    fake = FakeSupa()
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-test")
    for name in ("select", "insert", "update", "rpc", "create_upload_url", "object_exists"):
        monkeypatch.setattr(supa, name, getattr(fake, name))
    return fake


def test_upload_flow(admin, fake_supa):
    r = admin.post("/api/admin/uploads", json={"course": "70445", "session": 6, "kind": "slides",
                                               "filename": "../Week 6 Slides (final).PDF", "size": 1234})
    assert r.status_code == 200, r.text
    up = r.json()
    assert up["path"] == "inbox/70445/s06/slides/Week_6_Slides_final_.PDF"
    assert up["upload_url"].startswith("https://example.supabase.co/storage/v1/object/upload/sign/")
    assert up["method"] == "PUT" and up["headers"]["Content-Type"] == "application/pdf"
    assert fake_supa.tables["sessions"][0]["id"] == "70445-s06"
    sid = up["source_id"]

    # not arrived yet
    assert admin.post(f"/api/admin/sources/{sid}/complete").status_code == 409
    rows = admin.get("/api/admin/sources").json()["sources"]
    assert rows[0]["status"] == "pending_upload"
    # arrives; listing promotes it
    fake_supa.objects.add(up["path"])
    rows = admin.get("/api/admin/sources?course=70445&session=6").json()["sources"]
    assert rows[0]["status"] == "uploaded"
    fake_supa.tables["sources"][0]["status"] = "error"
    assert admin.post(f"/api/admin/sources/{sid}/rerun").json()["status"] == "uploaded"
    assert admin.post("/api/admin/sources/999/rerun").status_code == 404


def test_upload_validation(admin, fake_supa):
    base = {"course": "70445", "session": 6, "kind": "slides", "filename": "a.pdf", "size": 10}
    assert admin.post("/api/admin/uploads", json={**base, "kind": "audio"}).status_code == 400
    assert admin.post("/api/admin/uploads", json={**base, "filename": "a.exe"}).status_code == 400
    assert admin.post("/api/admin/uploads", json={**base, "size": 10**12}).status_code == 400
    assert admin.post("/api/admin/uploads", json={**base, "course": "45884"}).status_code == 400  # course not added
    assert admin.post("/api/admin/uploads", json={**base, "session": 0}).status_code == 400


def test_courses_and_sessions(admin, fake_supa):
    assert admin.post("/api/admin/courses", json={"course": "4588", "title": "x"}).status_code == 400
    r = admin.post("/api/admin/courses", json={"course": "45884", "title": "Fake Course B", "term": "F26 M1"})
    assert r.status_code == 200 and r.json()["course"] == "45884"
    r = admin.post("/api/admin/sessions", json={"course": "45884", "session": 3, "date": "2026-09-10", "title": "T"})
    assert r.status_code == 200 and r.json()["id"] == "45884-s03"
    assert admin.post("/api/admin/sessions", json={"course": "45884", "session": 4, "date": "Sept 10"}).status_code == 400
    r = admin.patch("/api/admin/sessions/70445-s01", json={"visible": False})
    assert r.status_code == 200 and r.json()["visible"] is False
    assert admin.patch("/api/admin/sessions/bad-id", json={"visible": False}).status_code == 400

    courses = admin.get("/api/admin/courses").json()["courses"]
    s1 = next(c for c in courses if c["course"] == "70445")["sessions"][0]
    assert s1["visible"] is False and s1["slides_indexed"] == 5
    # students no longer see the hidden session
    assert all(c["course"] != "70445" for c in admin.get("/api/courses").json())


def test_settings_and_counters_through_supabase(admin, fake_supa):
    s = admin.put("/api/admin/settings", json={"voice_id": "abc123"}).json()
    assert s["voice_id"] == "abc123"
    assert {"key": "voice_id", "value": "abc123"} in fake_supa.tables["settings"]
    settings_store.clear_cache()
    assert settings_store.voice_id() == "abc123"
    from app import limits

    assert limits.take_voice_chars(10, cap=15) and not limits.take_voice_chars(10, cap=15)
    assert limits.today_counters()["voice_chars"] == 10

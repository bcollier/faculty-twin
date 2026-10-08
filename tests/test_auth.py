from __future__ import annotations

import time

from app import auth, limits, settings_store


def test_token_round_trip_and_tamper():
    token = auth.make_token("s", "visitor1", 60, "gen1")
    session = auth.read_token(token, "s", "gen1")
    assert session is not None and session.visitor == "visitor1"
    body, sig = token.split(".")
    assert auth.read_token(body + "." + sig[:-2] + "AA", "s", "gen1") is None
    assert auth.read_token(token, "a", "gen1") is None  # student token is not an admin token
    assert auth.read_token(token, "s", "gen2") is None  # passcode rotated
    assert auth.read_token("garbage", "s", "gen1") is None


def test_token_expires():
    token = auth.make_token("s", "v", 10, "g", now=time.time() - 100)
    assert auth.read_token(token, "s", "g") is None


def test_passcode_hash():
    stored = auth.hash_passcode("correct horse")
    assert auth.verify_passcode_hash("correct horse", stored)
    assert not auth.verify_passcode_hash("wrong", stored)
    assert not auth.verify_passcode_hash("x", "not-a-hash")


def test_routes_need_cookie(client):
    assert client.get("/api/health").json() == {"ok": True}
    for r in [client.get("/api/courses"), client.get("/api/topics"), client.post("/api/ask", json={"question": "hi"})]:
        assert r.status_code == 401
        assert "passcode" in r.json()["detail"]


def test_login_wrong_and_right(client):
    r = client.post("/api/login", json={"passcode": "nope"})
    assert r.status_code == 401
    r = client.post("/api/login", json={"passcode": "student-pass"})
    assert r.status_code == 204
    cookie = r.headers["set-cookie"]
    assert "ft_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert client.get("/api/courses").status_code == 200


def test_admin_login_rate_limited_tighter(client):
    for _ in range(5):
        assert client.post("/api/admin/login", json={"passcode": "nope"}).status_code == 401
    assert client.post("/api/admin/login", json={"passcode": "admin-pass"}).status_code == 429


def test_login_rate_limited(client):
    for _ in range(limits.LOGIN_MAX_ATTEMPTS["student"]):
        assert client.post("/api/login", json={"passcode": "nope"}).status_code == 401
    r = client.post("/api/login", json={"passcode": "student-pass"})
    assert r.status_code == 429


def test_secure_cookie_on_vercel(client, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    r = client.post("/api/login", json={"passcode": "student-pass"})
    assert "Secure" in r.headers["set-cookie"]


def test_student_cookie_is_not_admin(student):
    assert student.get("/api/admin/settings").status_code == 401


def test_admin_cookie_works_for_student_routes(admin):
    assert admin.get("/api/courses").status_code == 200
    assert admin.get("/api/admin/settings").status_code == 200


def test_rotating_passcode_signs_students_out(student, admin):
    r = admin.put("/api/admin/settings", json={"student_passcode": "brand-new-code"})
    assert r.status_code == 200
    assert r.json()["student_passcode_source"] == "settings"
    assert settings_store.get("student_passcode_hash").startswith("pbkdf2_sha256$")
    student.cookies.delete("ft_admin")
    assert student.get("/api/courses").status_code == 401
    assert student.post("/api/login", json={"passcode": "student-pass"}).status_code == 401
    assert student.post("/api/login", json={"passcode": "brand-new-code"}).status_code == 204
    assert student.get("/api/courses").status_code == 200


def _supabase(monkeypatch, rows):
    """TEST FAKE: the settings table behind supa.select; `rows` None means Supabase is down."""
    from app import supa

    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")

    def select(table, params=None):
        if rows.get("down"):
            raise supa.SupabaseError("settings read failed: connection refused")
        return [{"key": k, "value": v} for k, v in rows.get("settings", {}).items()]

    def unreachable(*a, **k):
        raise supa.SupabaseError("unreachable")

    monkeypatch.setattr(supa, "select", select)
    for name in ("insert", "update", "rpc"):
        monkeypatch.setattr(supa, name, unreachable)
    settings_store.clear_cache()


def test_cold_start_with_supabase_down_never_falls_back_to_the_env_passcode(client, monkeypatch):
    # Oct 8 code review: after a rotation the hash lives in Supabase. A cold instance that could not read
    # it used to fall back to STUDENT_PASSCODE, so the old, rotated-away passcode worked again.
    _supabase(monkeypatch, {"down": True})
    r = client.post("/api/login", json={"passcode": "student-pass"})  # the env bootstrap value
    assert r.status_code == 503 and "try again" in r.json()["detail"]
    old_cookie = auth.make_token("s", "v", 60, auth._generation("env:student-pass"))
    client.cookies.set(auth.STUDENT_COOKIE, old_cookie)
    assert client.get("/api/courses").status_code == 401  # nor does a cookie from the env passcode


def test_a_warm_instance_keeps_using_its_last_good_copy(client, monkeypatch):
    rows = {"settings": {"student_passcode_hash": auth.hash_passcode("rotated-code")}}
    _supabase(monkeypatch, rows)
    assert client.post("/api/login", json={"passcode": "rotated-code"}).status_code == 204
    rows["down"] = True
    settings_store._cache_at = 0.0  # the 30 s cache ran out while Supabase is down
    assert client.post("/api/login", json={"passcode": "rotated-code"}).status_code == 204
    assert client.post("/api/login", json={"passcode": "student-pass"}).status_code == 401


def test_without_supabase_the_env_passcode_still_works(client):
    assert client.post("/api/login", json={"passcode": "student-pass"}).status_code == 204


def test_missing_passcode_config_is_503(client, monkeypatch):
    monkeypatch.delenv("STUDENT_PASSCODE")
    r = client.post("/api/login", json={"passcode": "anything"})
    assert r.status_code == 503

"""Admin auth: login, the admin cookie, wrong passcodes, the login limit, and token edge cases."""

from __future__ import annotations

import json
import time

import pytest
from fastapi import HTTPException

from app import auth, settings_store


def test_admin_login_sets_a_locked_down_cookie(client):
    r = client.post("/api/admin/login", json={"passcode": "  admin-pass  "})  # surrounding spaces are trimmed
    assert r.status_code == 204
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{auth.ADMIN_COOKIE}=")
    low = cookie.lower()
    assert "httponly" in low and "samesite=lax" in low and "path=/" in low
    assert f"max-age={auth.ADMIN_TTL}" in low
    assert "secure" not in low  # only on Vercel
    assert client.get("/api/admin/status").status_code == 200


@pytest.mark.parametrize("passcode", ["wrong", "", "   ", "student-pass", "x" * 201])
def test_admin_login_wrong_passcode(client, passcode):
    r = client.post("/api/admin/login", json={"passcode": passcode})
    assert r.status_code == 401 and "admin passcode" in r.json()["detail"]
    assert auth.ADMIN_COOKIE not in r.cookies
    assert client.get("/api/admin/status").status_code == 401


def test_admin_login_rate_limit_counts_wrong_and_right(client):
    for _ in range(5):
        client.post("/api/admin/login", json={"passcode": "nope"})
    r = client.post("/api/admin/login", json={"passcode": "admin-pass"})
    assert r.status_code == 429 and "Too many tries" in r.json()["detail"]


def test_student_cookie_cannot_reach_admin_routes(student):
    r = student.get("/api/admin/status")
    assert r.status_code == 401 and "admin passcode" in r.json()["detail"]


def test_admin_logout_clears_the_cookie(admin):
    r = admin.post("/api/admin/logout")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert auth.ADMIN_COOKIE in r.headers["set-cookie"]
    admin.cookies.clear()
    assert admin.get("/api/admin/status").status_code == 401


def test_rotating_admin_passcode_signs_admin_out(admin, monkeypatch):
    assert admin.get("/api/admin/status").status_code == 200
    monkeypatch.setenv("ADMIN_PASSCODE", "a-new-admin-pass")
    assert admin.get("/api/admin/status").status_code == 401


def test_admin_passcode_missing_is_503(client, monkeypatch):
    monkeypatch.delenv("ADMIN_PASSCODE")
    with pytest.raises(HTTPException) as err:
        auth.check_admin_passcode("anything")
    assert err.value.status_code == 503
    assert client.post("/api/admin/login", json={"passcode": "x"}).status_code == 503
    assert auth.admin_generation() is None


def test_admin_cookie_from_another_secret_is_refused(admin, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "another-session-secret-0123456789abcdef")
    assert admin.get("/api/admin/status").status_code == 401


# ---------------------------------------------------------------- token edge cases

def test_read_token_rejects_malformed_and_wrong_kind():
    gen = "g1"
    good = auth.make_token("a", "visitor", 60, gen)
    assert auth.read_token(good, "a", gen).visitor == "visitor"
    assert auth.read_token(good, "s", gen) is None  # an admin token is not a student token
    assert auth.read_token(good, "a", None) is None  # no passcode configured: nobody is signed in
    assert auth.read_token(None, "a", gen) is None
    assert auth.read_token("no-dot", "a", gen) is None
    assert auth.read_token("!!!.???", "a", gen) is None
    body, _, sig = good.partition(".")
    payload = auth._b64e(b"not json")
    assert auth.read_token(f"{payload}.{auth._b64e(auth._mac(b'not json'))}", "a", gen) is None
    forged = json.dumps({"k": "a", "v": "x", "exp": int(time.time()) + 999, "g": gen}).encode()
    assert auth.read_token(f"{auth._b64e(forged)}.{sig}", "a", gen) is None


def test_verify_passcode_hash_rejects_other_formats():
    stored = auth.hash_passcode("pw", salt=b"0" * 16)
    assert auth.verify_passcode_hash("pw", stored)
    assert not auth.verify_passcode_hash("pw", stored.replace("pbkdf2_sha256", "md5"))
    assert not auth.verify_passcode_hash("pw", "only$three$parts")
    assert not auth.verify_passcode_hash("pw", "pbkdf2_sha256$notanint$salt$digest")


def test_stored_student_hash_wins_over_env(client):
    settings_store.put({"student_passcode_hash": auth.hash_passcode("rotated-pass", salt=b"1" * 16)})
    assert client.post("/api/login", json={"passcode": "student-pass"}).status_code == 401
    assert client.post("/api/login", json={"passcode": "rotated-pass"}).status_code == 204
    assert client.get("/api/topics").status_code == 200


def test_visitor_key_is_hashed_and_stable():
    s = auth.Session(kind="s", visitor="raw-visitor-id", exp=0)
    key = auth.visitor_key(s)
    assert key == auth.visitor_key(s) and len(key) == 24 and "raw-visitor-id" not in key

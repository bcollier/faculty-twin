"""Open access: Settings can open the student page to everyone, without the passcode, until a set time
(docs/SPEC.md, "Open access"). Each visitor gets their own cookie, and closing ends every open session."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import auth, settings_store
from app.main import app


@pytest.fixture
def visitor(client):
    """A second browser with no passcode and no admin cookie (the `client` fixture sets CONTENT_DIR)."""
    with TestClient(app) as c:
        yield c


def test_closed_by_default(admin, visitor):
    assert admin.get("/api/admin/settings").json()["open_access_until"] is None
    assert visitor.get("/api/courses").status_code == 401


def test_open_lets_a_visitor_in_with_their_own_cookie(admin, visitor):
    until = int(time.time()) + 3600
    r = admin.put("/api/admin/settings", json={"open_access_until": until})
    assert r.status_code == 200 and r.json()["open_access_until"] == until
    first = visitor.get("/api/courses")
    assert first.status_code == 200
    cookie = first.headers["set-cookie"]
    assert "ft_session=" in cookie and "HttpOnly" in cookie
    max_age = int(cookie.split("Max-Age=")[1].split(";")[0])
    assert 3500 < max_age <= 3600, "the cookie ends when open access ends"
    again = visitor.get("/api/topics")
    assert again.status_code == 200 and "set-cookie" not in again.headers, "the same visitor keeps one cookie"
    assert visitor.post("/api/ask", json={"question": "hi"}).status_code != 401


def test_closing_signs_open_visitors_out_but_not_students(admin, visitor):
    admin.put("/api/admin/settings", json={"open_access_until": int(time.time()) + 3600})
    assert visitor.get("/api/courses").status_code == 200
    with TestClient(app) as student:
        assert student.post("/api/login", json={"passcode": "student-pass"}).status_code == 204
        r = admin.put("/api/admin/settings", json={"open_access_until": 0})
        assert r.status_code == 200 and r.json()["open_access_until"] is None
        assert visitor.get("/api/courses").status_code == 401
        assert student.get("/api/courses").status_code == 200


def test_open_access_ends_on_its_own(admin, visitor, monkeypatch):
    until = int(time.time()) + 600
    admin.put("/api/admin/settings", json={"open_access_until": until})
    assert visitor.get("/api/courses").status_code == 200
    real = time.time
    monkeypatch.setattr(auth.time, "time", lambda: real() + 601)
    assert auth.open_access_until() == 0.0
    assert visitor.get("/api/courses").status_code == 401


def test_an_open_cookie_is_not_a_student_or_admin_cookie(admin, visitor):
    admin.put("/api/admin/settings", json={"open_access_until": int(time.time()) + 3600})
    visitor.get("/api/courses")
    assert visitor.get("/api/admin/settings").status_code == 401
    token = visitor.cookies.get("ft_session")
    assert auth.read_token(token, "s", auth.student_generation()) is None


@pytest.mark.parametrize("until", [time.time() - 10, time.time() + 49 * 3600])
def test_settings_refuses_past_or_too_long_windows(admin, until):
    r = admin.put("/api/admin/settings", json={"open_access_until": until})
    assert r.status_code == 400 and "48 hours" in r.json()["detail"]
    assert not settings_store.get("open_access_until")


def test_a_bad_stored_value_means_closed(monkeypatch):
    monkeypatch.setattr(settings_store, "get", lambda key, default=None: "not-a-number")
    assert auth.open_access_until() == 0.0


def test_students_cannot_open_it(student):
    student.cookies.delete("ft_admin")
    r = student.put("/api/admin/settings", json={"open_access_until": int(time.time()) + 3600})
    assert r.status_code == 401

"""Passcode login and HMAC-signed session cookies.

Two cookies, both httpOnly, SameSite=Lax, Secure on Vercel:
- `ft_session` (students, 7 days): issued after the course passcode.
- `ft_admin` (Settings page, 12 hours): issued after ADMIN_PASSCODE.

Cookie value: base64url(JSON payload) + "." + base64url(HMAC-SHA256(payload)).
The payload carries a random visitor id (used, hashed, as the rate-limit key),
an expiry, and a "generation" derived from the current passcode, so rotating
the passcode signs everyone out.

The student passcode can be rotated from Settings: a PBKDF2 hash is stored in
the `settings` table (key `student_passcode_hash`). Until then the
STUDENT_PASSCODE env var is the passcode.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request, Response

from . import config, settings_store

STUDENT_COOKIE = "ft_session"
ADMIN_COOKIE = "ft_admin"
STUDENT_TTL = 7 * 24 * 3600
ADMIN_TTL = 12 * 3600
PBKDF2_ITERATIONS = 200_000


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(data: bytes) -> bytes:
    return hmac.new(config.session_secret(), data, hashlib.sha256).digest()


# ---------------------------------------------------------------- passcode hashing

def hash_passcode(passcode: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", passcode.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${_b64e(salt)}${_b64e(digest)}"


def verify_passcode_hash(passcode: str, stored: str) -> bool:
    try:
        algo, iters, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", passcode.encode(), _b64d(salt), int(iters))
        return hmac.compare_digest(calc, _b64d(digest))
    except (ValueError, TypeError):
        return False


def _generation(material: str) -> str:
    return hashlib.sha256(("gen:" + material).encode()).hexdigest()[:12]


def student_generation() -> str | None:
    stored = settings_store.get("student_passcode_hash")
    if stored:
        return _generation(str(stored))
    env_code = config.env("STUDENT_PASSCODE")
    return _generation("env:" + env_code) if env_code else None


def admin_generation() -> str | None:
    code = config.env("ADMIN_PASSCODE")
    return _generation("admin:" + code) if code else None


def check_student_passcode(passcode: str) -> bool:
    stored = settings_store.get("student_passcode_hash")
    if stored:
        return verify_passcode_hash(passcode, str(stored))
    env_code = config.env("STUDENT_PASSCODE")
    if not env_code:
        raise HTTPException(503, "The course passcode is not set up yet.")
    return hmac.compare_digest(passcode.encode(), env_code.encode())


def check_admin_passcode(passcode: str) -> bool:
    code = config.env("ADMIN_PASSCODE")
    if not code:
        raise HTTPException(503, "The admin passcode is not set up yet.")
    return hmac.compare_digest(passcode.encode(), code.encode())


# ---------------------------------------------------------------- cookies

@dataclass
class Session:
    kind: str  # "s" student, "a" admin
    visitor: str  # random id, never shown or stored raw
    exp: int


def make_token(kind: str, visitor: str, ttl: int, generation: str, now: float | None = None) -> str:
    payload = json.dumps(
        {"k": kind, "v": visitor, "exp": int((now or time.time()) + ttl), "g": generation},
        separators=(",", ":"),
    ).encode()
    return f"{_b64e(payload)}.{_b64e(_mac(payload))}"


def read_token(token: str | None, kind: str, generation: str | None, now: float | None = None) -> Session | None:
    if not token or "." not in token or not generation:
        return None
    body, _, sig = token.partition(".")
    try:
        payload = _b64d(body)
        if not hmac.compare_digest(_mac(payload), _b64d(sig)):
            return None
        data = json.loads(payload)
    except (ValueError, TypeError):
        return None
    if data.get("k") != kind or data.get("g") != generation:
        return None
    if int(data.get("exp", 0)) < (now or time.time()):
        return None
    return Session(kind=kind, visitor=str(data.get("v", "")), exp=int(data["exp"]))


def set_cookie(response: Response, name: str, token: str, ttl: int) -> None:
    response.set_cookie(
        name,
        token,
        max_age=ttl,
        httponly=True,
        samesite="lax",
        secure=config.is_production(),
        path="/",
    )


def issue_student(response: Response) -> None:
    gen = student_generation()
    token = make_token("s", secrets.token_urlsafe(12), STUDENT_TTL, gen or "")
    set_cookie(response, STUDENT_COOKIE, token, STUDENT_TTL)


def issue_admin(response: Response) -> None:
    gen = admin_generation()
    token = make_token("a", secrets.token_urlsafe(12), ADMIN_TTL, gen or "")
    set_cookie(response, ADMIN_COOKIE, token, ADMIN_TTL)


def visitor_key(session: Session) -> str:
    """Hashed visitor id for counters. The raw id never leaves the cookie."""
    return hmac.new(config.session_secret(), ("visitor:" + session.visitor).encode(), hashlib.sha256).hexdigest()[:24]


# ---------------------------------------------------------------- FastAPI dependencies

def admin_session(request: Request) -> Session | None:
    return read_token(request.cookies.get(ADMIN_COOKIE), "a", admin_generation())


def require_student(request: Request) -> Session:
    """Student cookie, or an admin cookie (so Ben can preview the student view)."""
    session = read_token(request.cookies.get(STUDENT_COOKIE), "s", student_generation())
    if session is None:
        session = admin_session(request)
    if session is None:
        raise HTTPException(401, "Please enter the course passcode first.")
    return session


def require_admin(request: Request) -> Session:
    session = admin_session(request)
    if session is None:
        raise HTTPException(401, "Please sign in to Settings with the admin passcode.")
    return session

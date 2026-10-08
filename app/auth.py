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

Fail closed (added Oct 8, code review): when Supabase is configured but this
instance has never read the settings table (a cold start while Supabase is
down), the stored hash cannot be known, so login answers 503 and student
cookies are not accepted. Falling back to STUDENT_PASSCODE there would let the
old passcode back in after a rotation. A warm instance keeps its last good copy.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request, Response

from . import b64url, config, settings_store

STUDENT_COOKIE = "ft_session"
ADMIN_COOKIE = "ft_admin"
STUDENT_TTL = 7 * 24 * 3600
ADMIN_TTL = 12 * 3600
PBKDF2_ITERATIONS = 600_000  # OWASP 2023 guidance for PBKDF2-HMAC-SHA256


_b64e = b64url.encode
_b64d = b64url.decode


def _mac(data: bytes) -> bytes:
    return hmac.new(config.session_secret(), data, hashlib.sha256).digest()


# ---------------------------------------------------------------- passcode hashing

def hash_passcode(passcode: str, salt: bytes | None = None) -> str:
    """A salted PBKDF2-SHA256 hash of the student passcode, as stored in settings."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", passcode.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${_b64e(salt)}${_b64e(digest)}"


def verify_passcode_hash(passcode: str, stored: str) -> bool:
    """Whether `passcode` matches a stored hash. A malformed hash never matches."""
    try:
        algo, iters, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", passcode.encode(), _b64d(salt), int(iters))
        return hmac.compare_digest(calc, _b64d(digest))
    except (ValueError, TypeError):
        return False


def _generation(material: str) -> str:
    """Short tag that changes when the passcode changes.

    Keyed with SESSION_SECRET: the cookie payload is readable by whoever holds
    the cookie, and an unkeyed hash of the passcode there would let anyone with
    a copied cookie brute-force the passcode offline.
    """
    return hmac.new(config.session_secret(), ("gen:" + material).encode(), hashlib.sha256).hexdigest()[:16]


SIGN_IN_UNAVAILABLE = "Sign-in is not available right now. Please try again in a minute."


def _stored_student_hash() -> str | None:
    """The rotated passcode hash, None when there is none. Raises SettingsUnavailable when it cannot be read."""
    stored = settings_store.get_required("student_passcode_hash")
    return str(stored) if stored else None


def student_generation() -> str | None:
    """A tag of the current student passcode: rotating the passcode changes it, so old cookies stop working."""
    try:
        stored = _stored_student_hash()
    except settings_store.SettingsUnavailable:
        return None  # no cookie is valid until the hash can be read
    if stored:
        return _generation(str(stored))
    env_code = config.env("STUDENT_PASSCODE")
    return _generation("env:" + env_code) if env_code else None


def admin_generation() -> str | None:
    code = config.env("ADMIN_PASSCODE")
    return _generation("admin:" + code) if code else None


def check_student_passcode(passcode: str) -> bool:
    """Whether this is the student passcode (the stored hash, else STUDENT_PASSCODE)."""
    try:
        stored = _stored_student_hash()
    except settings_store.SettingsUnavailable as exc:
        config.log.warning("student login refused: the settings table could not be read")
        raise HTTPException(503, SIGN_IN_UNAVAILABLE) from exc
    if stored:
        return verify_passcode_hash(passcode, stored)
    env_code = config.env("STUDENT_PASSCODE")
    if not env_code:
        raise HTTPException(503, "The course passcode is not set up yet.")
    return hmac.compare_digest(passcode.encode(), env_code.encode())


def check_admin_passcode(passcode: str) -> bool:
    """Whether this is ADMIN_PASSCODE, compared in constant time."""
    code = config.env("ADMIN_PASSCODE")
    if not code:
        raise HTTPException(503, "The admin passcode is not set up yet.")
    return hmac.compare_digest(passcode.encode(), code.encode())


# ---------------------------------------------------------------- cookies

@dataclass
class Session:
    """A signed-in visitor, read from a valid cookie."""

    kind: str  # "s" student, "a" admin
    visitor: str  # random id, never shown or stored raw
    exp: int


def make_token(kind: str, visitor: str, ttl: int, generation: str, now: float | None = None) -> str:
    """A signed cookie value: kind, a random visitor id, expiry and the passcode generation."""
    payload = json.dumps(
        {"k": kind, "v": visitor, "exp": int((now or time.time()) + ttl), "g": generation},
        separators=(",", ":"),
    ).encode()
    return f"{_b64e(payload)}.{_b64e(_mac(payload))}"


def read_token(token: str | None, kind: str, generation: str | None, now: float | None = None) -> Session | None:
    """The session in a cookie value, or None if it is unsigned, expired, the wrong kind or from an old passcode."""
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
    """Set an HttpOnly, SameSite=Lax cookie (Secure in production)."""
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
    """Sign the visitor in as a student for STUDENT_TTL."""
    gen = student_generation()
    token = make_token("s", secrets.token_urlsafe(12), STUDENT_TTL, gen or "")
    set_cookie(response, STUDENT_COOKIE, token, STUDENT_TTL)


def issue_admin(response: Response) -> None:
    """Sign Ben in to Settings for ADMIN_TTL."""
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
    """FastAPI dependency: the admin session, or 401."""
    session = admin_session(request)
    if session is None:
        raise HTTPException(401, "Please sign in to Settings with the admin passcode.")
    return session

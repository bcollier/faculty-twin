"""Signing in to Settings and out again: the admin passcode in, the 12-hour `ft_admin` cookie out.

Used by the sign-in form on the Settings page (`public/admin.html`) and its sign-out link.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from .. import auth, limits
from ..main import PasscodeBody

router = APIRouter(prefix="/api/admin")


@router.post("/login", status_code=204)
def admin_login(body: PasscodeBody, request: Request, response: Response) -> None:
    """Check the admin passcode (rate limited per address) and set the 12-hour Settings cookie."""
    limits.check_login_rate(request, "admin")
    code = (body.passcode or "").strip()
    if not code or len(code) > 200 or not auth.check_admin_passcode(code):
        raise HTTPException(401, "That admin passcode is not right.")
    auth.issue_admin(response)


@router.post("/logout")
def admin_logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(auth.ADMIN_COOKIE, path="/")
    return {"ok": True}

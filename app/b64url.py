"""URL-safe base64 without padding, for the signed values the server hands out.

The student and admin cookies (app/auth.py) and the signed audio links (app/speech.py)
both carry bytes this way, so a value survives a URL or a cookie unchanged.
"""

from __future__ import annotations

import base64


def encode(raw: bytes) -> str:
    """URL-safe base64 with the "=" padding dropped."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode(text: str) -> bytes:
    """The bytes of `encode`'s output (padding restored). Raises binascii.Error or ValueError on bad input."""
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))

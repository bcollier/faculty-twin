"""The source of a page script together with the modules it is split into, for tests that read a page's code.

A page script is an ES module that imports its sections from a folder of its own (`public/app.js` from
`public/student/`, `public/admin.js` from `public/settings/`). A test that checks the page's code as text
(a copy string, a guard, an allow-list) reads the page as a whole, wherever in the folder the code sits.
"""

from __future__ import annotations

from pathlib import Path

PUBLIC = Path(__file__).resolve().parents[2] / "public"
PAGE_FOLDERS = {"app.js": "student", "admin.js": "settings"}


def page_source(page: str) -> str:
    """`public/<page>` followed by every module in its folder, in name order, joined by newlines."""
    parts = [PUBLIC / page]
    folder = PUBLIC / PAGE_FOLDERS[page]
    if folder.is_dir():
        parts += sorted(folder.glob("*.js"))
    return "\n".join(p.read_text(encoding="utf-8") for p in parts)

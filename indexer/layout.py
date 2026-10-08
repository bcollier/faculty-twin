"""Where the content pipeline's inputs and outputs live, and how its JSON files are written.

Every stage in `indexer/` reads the private Lecture Archive and writes to its `_build/`
folder. The archive layout, the two course folders, the sessions that may never be cut
into clips, and the small file helpers (atomic JSON writes, change fingerprints) are
defined once here, so a privacy rule such as "no clips from the Tesla vs Waymo case"
cannot drift between stages.

Standard library only, on purpose: stages 1 to 4 (`slides.py`, `deidentify.py`,
`align.py`, `clips.py`) run with a handful of `uv run --with` packages and no httpx,
so they cannot import `indexer/common.py` (which holds the Supabase client).
`common.py` re-exports these names for the stages that already import it.

Nothing here prints or logs course content or roster data.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_ARCHIVE = Path(os.environ.get("LECTURE_ARCHIVE", "~/Lecture Archive")).expanduser()
TERM = "2026 Fall"

# Archive folder name per course code.
COURSE_FOLDERS = {
    "70445": "70-445 AI for Business Leaders",
    "45884": "45-884 AI Methods for Social and Visual Data",
}

# Sessions whose slides are indexed but whose class video must never be cut into clips
# (the Tesla vs Waymo case stays private).
NO_CLIP_SESSIONS = frozenset({("45884", 11), ("45884", 12)})

# Slide flags (set by indexer/slides.py) that rule out a clip. A tuple, not a set: clips.py
# reports the first matching flag as the rejection reason, so the order must be stable.
NO_CLIP_FLAGS = (
    "student_names_possible",
    "in_the_news",
    "student_presentation_possible",
    "no_clips_private_case",
)

# Slide flags that keep a slide out of the index entirely.
EXCLUDE_FLAGS = frozenset({"student_names_possible"})

# The text fields of a slides.json row, in the order the text matchers read them.
SLIDE_TEXT_FIELDS = ("title", "text", "notes", "ocr_text")


# ---------------------------------------------------------------- paths


def archive_dir(override: str | Path | None = None) -> Path:
    """The Lecture Archive: `override` when given, else $LECTURE_ARCHIVE or ~/Lecture Archive."""
    return Path(override).expanduser() if override else DEFAULT_ARCHIVE


def build_dir(archive: Path) -> Path:
    """The private build folder every stage writes to."""
    return archive / "_build"


def roster_dir(archive: Path) -> Path:
    """The private roster CSVs (they never leave the build machine)."""
    return archive / "_private" / "rosters"


def session_tag(session: int) -> str:
    """The `s<NN>` folder and file name for a session number."""
    return f"s{int(session):02d}"


def slide_text(row: dict[str, Any]) -> str:
    """A slide's searchable text (title, text, notes, OCR text), one field per line."""
    return "\n".join(str(row.get(k) or "") for k in SLIDE_TEXT_FIELDS)


# ---------------------------------------------------------------- files


def write_bytes_atomic(path: Path, data: bytes) -> None:
    """Write through a temporary file, so a crash never leaves a half-written output."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def dump_json(obj: Any) -> bytes:
    """The pipeline's JSON encoding: indent 1, UTF-8, non-ASCII kept as is."""
    return json.dumps(obj, indent=1, ensure_ascii=False).encode("utf-8")


def write_json(path: Path, obj: Any) -> None:
    """Write `obj` as pipeline JSON, atomically."""
    write_bytes_atomic(path, dump_json(obj))


def read_json(path: Path, default: Any = None) -> Any:
    """Parsed JSON, or `default` when the file is missing or unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def file_fingerprint(*paths: Path | None, include_missing: bool = False) -> list[list[Any]]:
    """[name, size, mtime] per existing file: a cheap "have the inputs changed" cache key.

    With `include_missing`, a missing file adds [name, None, None], so a file that appears
    later also changes the key. Stages store these lists in their outputs, so the format
    must not change (a different key would rebuild every session).
    """
    out: list[list[Any]] = []
    for path in paths:
        if path is not None and path.exists():
            st = path.stat()
            out.append([path.name, st.st_size, int(st.st_mtime)])
        elif include_missing:
            out.append([path.name if path else None, None, None])
    return out

"""Shared helpers for the index, upload, pregenerate, and worker stages.

Paths, `.env` loading, course and session facts from the inventory, hashing,
atomic writes, and a tiny Supabase REST client (Storage + PostgREST) that takes
an injectable `httpx.Client`, so tests run against a fake.

Nothing here prints or logs course content, roster data, or key values.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote

import httpx

REPO = Path(__file__).resolve().parents[1]
DEFAULT_ARCHIVE = Path(os.environ.get("LECTURE_ARCHIVE", "~/Lecture Archive")).expanduser()
TERM = "2026 Fall"

# Archive folder names (the slides stage uses the same table).
COURSE_FOLDERS = {
    "70445": "70-445 AI for Business Leaders",
    "45884": "45-884 AI Methods for Social and Visual Data",
}
# Sessions whose slides are indexed but must never get class clips (Tesla vs Waymo case).
NO_CLIP_SESSIONS = {("45884", 11), ("45884", 12)}
# Slide flags (from indexer/slides.py) that rule out a clip.
NO_CLIP_FLAGS = {
    "student_names_possible",
    "in_the_news",
    "student_presentation_possible",
    "no_clips_private_case",
}
# Slide flags that keep a slide out of the index entirely.
EXCLUDE_FLAGS = {"student_names_possible"}


def archive_dir(override: str | Path | None = None) -> Path:
    return Path(override).expanduser() if override else DEFAULT_ARCHIVE


def build_dir(archive: Path) -> Path:
    return archive / "_build"


def roster_dir(archive: Path) -> Path:
    return archive / "_private" / "rosters"


def session_tag(session: int) -> str:
    return f"s{int(session):02d}"


# ---------------------------------------------------------------- env

def load_env(path: Path | None = None) -> list[str]:
    """Load KEY=VALUE lines from the repo's git-ignored `.env` without overriding the environment.

    Returns the names that were set (never the values). `FT_ENV_FILE` points elsewhere.
    """
    path = Path(os.environ.get("FT_ENV_FILE") or path or (REPO / ".env")).expanduser()
    if not path.is_file():
        return []
    loaded = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if not re.match(r"^[A-Z_][A-Z0-9_]*$", key) or not value:
            continue
        if not os.environ.get(key, "").strip():
            os.environ[key] = value
            loaded.append(key)
    return loaded


def env(name: str, default: str | None = None) -> str | None:
    value = (os.environ.get(name) or "").strip()
    return value or default


# ---------------------------------------------------------------- hashing and files

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def dump_json(obj: Any) -> bytes:
    return json.dumps(obj, indent=1, ensure_ascii=False).encode("utf-8")


def write_json(path: Path, obj: Any) -> None:
    write_bytes_atomic(path, dump_json(obj))


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


# ---------------------------------------------------------------- course facts

def course_meta(archive: Path) -> tuple[dict[str, str], dict[tuple[str, int], dict[str, str]]]:
    """Course titles and per-session {date, title} from `_inventory/f26_inventory.json`.

    Returns ({"70445": "AI for Business Leaders"}, {("70445", 6): {"date": ..., "title": ...}}).
    """
    data = read_json(archive / "_inventory" / "f26_inventory.json", {}) or {}
    titles: dict[str, str] = {}
    sessions: dict[tuple[str, int], dict[str, str]] = {}
    for name, course in (data.get("courses") or {}).items():
        m = re.match(r"^(\d{2})-?(\d{3})\s+(.+)$", name.strip())
        if not m:
            continue
        code = m.group(1) + m.group(2)
        titles[code] = m.group(3).strip()
        for s in course.get("sessions") or []:
            try:
                sessions[(code, int(s["number"]))] = {"date": s.get("date"), "title": s.get("title")}
            except (KeyError, TypeError, ValueError):
                continue
    for code, folder in COURSE_FOLDERS.items():
        titles.setdefault(code, folder.split(" ", 1)[1])
    return titles, sessions


def session_folder(archive: Path, course: str, session: int) -> Path | None:
    """The archive folder `<course folder>/2026 Fall/<NN> <date> <title>/`, if it exists."""
    name = COURSE_FOLDERS.get(course)
    roots = [archive / name / TERM] if name else list(archive.glob(f"{course[:2]}-{course[2:]} */{TERM}"))
    for root in roots:
        for folder in sorted(root.glob(f"{int(session):02d} *")):
            if folder.is_dir():
                return folder
    return None


# ---------------------------------------------------------------- Supabase REST

class SupabaseError(RuntimeError):
    pass


class Supabase:
    """Storage + PostgREST with the service role key. Pass `client` to inject a fake transport."""

    def __init__(
        self,
        url: str | None = None,
        key: str | None = None,
        bucket: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        url = url or env("SUPABASE_URL")
        key = key or env("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            raise SupabaseError("Supabase is not configured: set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env")
        self.base = url.rstrip("/")
        self._key = key
        self.bucket = bucket or env("SUPABASE_BUCKET", "twin-content") or "twin-content"
        self.client = client or httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0))

    @classmethod
    def configured(cls) -> bool:
        return bool(env("SUPABASE_URL") and env("SUPABASE_SERVICE_ROLE_KEY"))

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {"apikey": self._key, "Authorization": f"Bearer {self._key}"}
        h.update(extra or {})
        return h

    def _obj(self, path: str) -> str:
        return f"{self.base}/storage/v1/object/{self.bucket}/{quote(path.lstrip('/'), safe='/')}"

    def _req(self, method: str, url: str, what: str, **kw) -> httpx.Response:
        try:
            resp = self.client.request(method, url, **kw)
        except httpx.HTTPError as exc:
            raise SupabaseError(f"{what}: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            # The body is Supabase's error JSON; it never holds our key.
            raise SupabaseError(f"{what} failed ({resp.status_code}): {resp.text[:200]}")
        return resp

    # Storage
    def upload(self, path: str, data: bytes, content_type: str, sha256: str, cache_seconds: int = 3600) -> None:
        meta = json.dumps({"sha256": sha256}).encode()
        self._req(
            "POST",
            self._obj(path),
            f"upload {path}",
            content=data,
            headers=self._headers(
                {
                    "Content-Type": content_type,
                    "x-upsert": "true",
                    "cache-control": f"max-age={cache_seconds}",
                    "x-metadata": base64.b64encode(meta).decode(),
                }
            ),
        )

    def download(self, path: str) -> bytes | None:
        """Object bytes, or None when it does not exist."""
        try:
            resp = self.client.get(self._obj(path), headers=self._headers())
        except httpx.HTTPError as exc:
            raise SupabaseError(f"download {path}: {type(exc).__name__}") from exc
        if resp.status_code in (400, 404):
            return None
        if resp.status_code >= 400:
            raise SupabaseError(f"download {path} failed ({resp.status_code})")
        return resp.content

    def download_to(self, path: str, dest: Path) -> int:
        """Stream an object to a local file (class videos are large). Returns bytes written."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        n = 0
        try:
            with self.client.stream("GET", self._obj(path), headers=self._headers()) as resp:
                if resp.status_code >= 400:
                    raise SupabaseError(f"download {path} failed ({resp.status_code})")
                with open(tmp, "wb") as fh:
                    for chunk in resp.iter_bytes(1 << 20):
                        fh.write(chunk)
                        n += len(chunk)
        except httpx.HTTPError as exc:
            raise SupabaseError(f"download {path}: {type(exc).__name__}") from exc
        os.replace(tmp, dest)
        return n

    def delete(self, paths: list[str]) -> None:
        if paths:
            self._req(
                "DELETE",
                f"{self.base}/storage/v1/object/{self.bucket}",
                "delete objects",
                json={"prefixes": paths},
                headers=self._headers({"Content-Type": "application/json"}),
            )

    # PostgREST
    def select(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        return self._req("GET", f"{self.base}/rest/v1/{table}", f"select {table}", params=params, headers=self._headers()).json()

    def update(self, table: str, match: dict[str, str], values: dict[str, Any]) -> list[dict[str, Any]]:
        return self._req(
            "PATCH",
            f"{self.base}/rest/v1/{table}",
            f"update {table}",
            params=match,
            json=values,
            headers=self._headers({"Prefer": "return=representation", "Content-Type": "application/json"}),
        ).json()

    def upsert(self, table: str, rows: list[dict[str, Any]], on: str) -> list[dict[str, Any]]:
        return self._req(
            "POST",
            f"{self.base}/rest/v1/{table}",
            f"upsert {table}",
            params={"on_conflict": on},
            json=rows,
            headers=self._headers(
                {"Prefer": "return=representation,resolution=merge-duplicates", "Content-Type": "application/json"}
            ),
        ).json()


def iter_session_files(root: Path, pattern: str) -> Iterator[tuple[str, int, Path]]:
    """Yield (course, session, path) under `<root>/<course>/`, e.g. pattern "s[0-9][0-9]/slides.json"."""
    for course_dir in sorted(p for p in root.glob("[0-9]" * 5) if p.is_dir()):
        for path in sorted(course_dir.glob(pattern)):
            m = re.match(r"^s(\d{2})", path.relative_to(course_dir).parts[0])
            if m:
                yield course_dir.name, int(m.group(1)), path

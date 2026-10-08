"""Course content: the index, its embeddings, and links to slide images and clips.

Where content comes from:
- `CONTENT_DIR` set (local dev, tests): read `<CONTENT_DIR>/content/index.json`
  and `<CONTENT_DIR>/content/embeddings.npy`; images and clips are served by the
  dev-only `/api/files/...` route, which still checks the student cookie.
- otherwise: download the same two files from the private Supabase bucket
  (`SUPABASE_BUCKET`, default `twin-content`) at cold start, and mint
  short-lived signed URLs for images and clips.

The repo never holds course content. `index.json` is either a list of records or
`{"records": [...], ...meta}`; row i of `embeddings.npy` belongs to records[i].

Reload: when the Settings page bumps `settings.index_version` (the local worker
does this after rebuilding the index), every warm instance notices within about
60 seconds and reloads.

Optional extras the backend reads if present (missing files are fine):
- `topics/topics.json`: suggested questions, `[{question, course, playlist?}]`
- `clips/manifest.json`: `[{slide_id, start, end, ...}]` for clip time windows
- `content/info_index.json` + `content/info_embeddings.npy`: the course-info index
  (Canvas syllabus, policies, assignments, FAQ doc), one record per chunk,
  `{id, course, title, kind, canvas_url, due_at, text}`; row i of the matrix
  belongs to record i. Both files missing (or not matching) turns the
  course-info answers off; nothing else changes. See app/course_info.py.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import numpy as np
from fastapi import HTTPException

from . import config, settings_store, supa

SIGNED_URL_SECONDS = 3600
VERSION_CHECK_SECONDS = 60.0


class ContentUnavailable(RuntimeError):
    pass


@dataclass
class Content:
    records: list[dict[str, Any]]
    matrix: np.ndarray  # float32, shape (len(records), dim)
    meta: dict[str, Any] = field(default_factory=dict)
    topics: list[dict[str, Any]] = field(default_factory=list)
    clip_windows: dict[str, tuple[float, float]] = field(default_factory=dict)
    source: str = ""  # "local" or "supabase"
    version: str | None = None
    by_id: dict[str, int] = field(default_factory=dict)
    # Course-info index (Canvas pages, syllabus, assignments). Empty means the feature is off.
    info_records: list[dict[str, Any]] = field(default_factory=list)
    info_matrix: np.ndarray | None = None

    def __post_init__(self) -> None:
        self.by_id = {r["id"]: i for i, r in enumerate(self.records)}

    def record(self, rec_id: str) -> dict[str, Any] | None:
        i = self.by_id.get(rec_id)
        return None if i is None else self.records[i]


# ---------------------------------------------------------------- loading

def _parse(index_bytes: bytes, emb_bytes: bytes) -> tuple[list[dict[str, Any]], np.ndarray, dict[str, Any]]:
    data = json.loads(index_bytes)
    if isinstance(data, dict):
        records = data.get("records", [])
        meta = {k: v for k, v in data.items() if k != "records"}
    else:
        records, meta = data, {}
    matrix = np.load(io.BytesIO(emb_bytes), allow_pickle=False).astype(np.float32, copy=False)
    if matrix.ndim != 2 or matrix.shape[0] != len(records):
        raise ContentUnavailable(
            f"embeddings.npy has shape {matrix.shape} but index.json has {len(records)} records"
        )
    return records, matrix, meta


def _optional_json(read) -> Any:
    try:
        return json.loads(read())
    except Exception:  # missing or malformed optional file
        return None


def _clip_windows(manifest: Any) -> dict[str, tuple[float, float]]:
    out: dict[str, tuple[float, float]] = {}
    for row in manifest or []:
        try:
            out[row["slide_id"]] = (float(row["start"]), float(row["end"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _optional_info(read_index, read_emb) -> tuple[list[dict[str, Any]], np.ndarray | None]:
    """The course-info index, or ([], None) when its files are missing or do not match."""
    try:
        index_bytes, emb_bytes = read_index(), read_emb()
    except Exception:  # missing optional files: the feature is off
        return [], None
    try:
        records, matrix, _meta = _parse(index_bytes, emb_bytes)
    except Exception as exc:
        config.log.warning("course-info index ignored: %s", str(exc)[:200])
        return [], None
    if not records:
        return [], None
    return records, matrix


def load_local(root: Path) -> Content:
    index_path = root / "content" / "index.json"
    emb_path = root / "content" / "embeddings.npy"
    if not index_path.exists() or not emb_path.exists():
        raise ContentUnavailable(f"No index found under {root}/content/")
    records, matrix, meta = _parse(index_path.read_bytes(), emb_path.read_bytes())
    topics = _optional_json(lambda: (root / "topics" / "topics.json").read_bytes()) or []
    manifest = _optional_json(lambda: (root / "clips" / "manifest.json").read_bytes())
    info_records, info_matrix = _optional_info(
        lambda: (root / "content" / "info_index.json").read_bytes(),
        lambda: (root / "content" / "info_embeddings.npy").read_bytes(),
    )
    return Content(records, matrix, meta, topics, _clip_windows(manifest), source="local",
                   info_records=info_records, info_matrix=info_matrix)


def load_supabase() -> Content:
    try:
        index_bytes = supa.download("content/index.json")
        emb_bytes = supa.download("content/embeddings.npy")
    except supa.SupabaseError as exc:
        raise ContentUnavailable(str(exc)) from exc
    records, matrix, meta = _parse(index_bytes, emb_bytes)
    topics = _optional_json(lambda: supa.download("topics/topics.json")) or []
    manifest = _optional_json(lambda: supa.download("clips/manifest.json"))
    info_records, info_matrix = _optional_info(
        lambda: supa.download("content/info_index.json"),
        lambda: supa.download("content/info_embeddings.npy"),
    )
    return Content(records, matrix, meta, topics, _clip_windows(manifest), source="supabase",
                   info_records=info_records, info_matrix=info_matrix)


STALE_RELOAD_TRIES = 3  # reloads of a download whose files are older than settings.index_version


class Store:
    """Process-wide holder for the loaded content, with lazy load and version reload."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._content: Content | None = None
        self._checked_at = 0.0
        self._stale_tries: dict[str, int] = {}

    def reset(self) -> None:
        with self._lock:
            self._content = None
            self._checked_at = 0.0
            self._stale_tries = {}

    def set(self, content: Content) -> None:
        """Tests and tools: install content directly."""
        with self._lock:
            self._content = content
            self._checked_at = time.monotonic()

    def _load(self) -> Content:
        # Read the version before downloading: a bump that lands mid-download must look new next time,
        # not be stamped on files fetched before it (Oct 8 code review).
        expected = settings_store.index_version()
        root = config.content_dir()
        try:
            if root is not None:
                content = load_local(root)
            elif config.supabase_configured():
                content = load_supabase()
            else:
                raise ContentUnavailable("No content source: set CONTENT_DIR or the Supabase variables")
        except ContentUnavailable:
            raise
        except Exception as exc:  # a truncated or half-uploaded file: keep what is loaded, or 503
            raise ContentUnavailable(f"the index files could not be read ({type(exc).__name__})") from exc
        content.version = self._label(content, expected)
        config.log.info("loaded %d records (%d course-info chunks) from %s",
                        len(content.records), len(content.info_records), content.source)
        return content

    def _label(self, content: Content, expected: str | None) -> str | None:
        """The version this download counts as.

        index.json carries the version it was built as (`index_version`), and indexer/upload.py sets
        settings.index_version to that plus "+info-<hash>". Bucket reads go through a CDN that may
        serve a copy cached before the upload finished; such a download gets a label that does not
        match, so the next check (60 s) loads again. After STALE_RELOAD_TRIES it is accepted, so a
        version set by hand cannot make every instance reload forever.
        """
        built = str(content.meta.get("index_version") or "")
        if content.source != "supabase" or not expected or not built or expected.split("+info-")[0] == built:
            self._stale_tries = {}
            return expected
        tries = self._stale_tries.get(expected, 0) + 1
        self._stale_tries = {expected: tries}
        if tries > STALE_RELOAD_TRIES:
            config.log.warning("index.json says %s but settings.index_version is %s; using it anyway", built, expected)
            return expected
        config.log.warning("downloaded index %s is older than settings.index_version %s; reloading soon", built, expected)
        return f"stale:{built}"

    def get(self) -> Content:
        now = time.monotonic()
        with self._lock:
            content = self._content
            due = now - self._checked_at >= VERSION_CHECK_SECONDS
        if content is not None and not due:
            return content
        if content is not None:
            with self._lock:
                self._checked_at = now
            if settings_store.index_version() == content.version:
                return content
            config.log.info("index_version changed; reloading content")
        try:
            fresh = self._load()
        except ContentUnavailable:
            if content is not None:
                config.log.warning("reload failed; keeping the loaded index")
                return content
            raise
        with self._lock:
            self._content, self._checked_at = fresh, time.monotonic()
        return fresh

    def get_or_503(self) -> Content:
        try:
            return self.get()
        except ContentUnavailable as exc:
            config.log.warning("content unavailable: %s", exc)
            raise HTTPException(503, "Course content is not loaded yet. Please try again shortly.") from exc

    @property
    def loaded(self) -> Content | None:
        return self._content


store = Store()


# ---------------------------------------------------------------- media links

def _dev_sig(path: str, exp: int) -> str:
    return hmac.new(config.session_secret(), f"file:{path}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]


def verify_dev_link(path: str, exp: int, sig: str) -> bool:
    if exp < time.time():
        return False
    return hmac.compare_digest(_dev_sig(path, exp).encode(), sig.encode("utf-8", "replace"))


def is_media_path(path: str) -> bool:
    """Only slide images, clips, and pre-generated audio may be linked to."""
    clean = path.lstrip("/")
    return ".." not in clean and clean.split("/", 1)[0] in {"slides", "clips", "audio"}


def media_urls(paths: list[str | None], expires_in: int = SIGNED_URL_SECONDS) -> dict[str, str]:
    """Signed, short-lived links for media paths. Unknown or failed paths are left out."""
    wanted = sorted({p.lstrip("/") for p in paths if p and is_media_path(p)})
    if not wanted:
        return {}
    if config.content_dir() is not None:
        exp = int(time.time()) + expires_in
        return {p: f"/api/files/{quote(p)}?exp={exp}&sig={_dev_sig(p, exp)}" for p in wanted}
    if config.supabase_configured():
        try:
            return supa.sign_urls(wanted, expires_in)
        except supa.SupabaseError as exc:
            config.log.warning("signing media URLs failed: %s", exc)
    return {}


def local_file(path: str) -> Path | None:
    root = config.content_dir()
    if root is None or not is_media_path(path):
        return None
    target = (root / path.lstrip("/")).resolve()
    if root.resolve() not in target.parents or not target.is_file():
        return None
    return target

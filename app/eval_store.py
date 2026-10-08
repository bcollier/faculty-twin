"""Where Settings > Evals keeps its data: the private bucket, under `evals/` (docs/SPEC.md, "Evals").

    evals/questions.jsonl            de-identified question set (scripts/upload_eval_questions.py)
    evals/index.json                 {"runs": [run summary, ...]}: what the runs list and report card read
    evals/runs/<run_id>/run.json     config, status, progress, the chosen questions, summary
    evals/runs/<run_id>/results.jsonl  one row per question x generator
    evals/calibration.json           latest calibration result per judge

No SQL table and no DDL: plain JSON objects in the bucket, read and written by
the function with the service role key. These paths are never signed for a
browser (`storage.is_media_path` allows only slides/, clips/ and audio/), so
question text is reachable only through the admin routes.

Backends, in the same order `app/storage.py` picks content from:
- `CONTENT_DIR` set (local dev): files under `<CONTENT_DIR>/evals/`
- Supabase configured: the private bucket
- neither: an in-process dict (tests, a bare local run)
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Protocol

from . import config, supa

PREFIX = "evals/"
QUESTIONS = "evals/questions.jsonl"
INDEX = "evals/index.json"
CALIBRATION = "evals/calibration.json"
RUN_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z(?:-[a-z0-9]{1,12})?$|^baseline-[0-9]{8}$")


class StoreError(RuntimeError):
    pass


class Bucket(Protocol):
    def get(self, path: str) -> bytes | None: ...

    def put(self, path: str, data: bytes, content_type: str = "application/json") -> None: ...


def _check_path(path: str) -> str:
    clean = path.lstrip("/")
    if not clean.startswith(PREFIX) or ".." in clean.split("/"):
        raise StoreError(f"eval data lives under {PREFIX}")
    return clean


class SupabaseBucket:
    """The private Supabase bucket (SUPABASE_BUCKET)."""

    def get(self, path: str) -> bytes | None:
        try:
            return supa.download_optional(_check_path(path))
        except supa.SupabaseError as exc:
            raise StoreError(str(exc)) from exc

    def put(self, path: str, data: bytes, content_type: str = "application/json") -> None:
        try:
            supa.upload(_check_path(path), data, content_type, upsert=True)
        except supa.SupabaseError as exc:
            raise StoreError(str(exc)) from exc


class LocalBucket:
    """Files under a local folder (local dev with CONTENT_DIR)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _file(self, path: str) -> Path:
        target = (self.root / _check_path(path)).resolve()
        if self.root.resolve() not in target.parents:
            raise StoreError("path escapes the content folder")
        return target

    def get(self, path: str) -> bytes | None:
        f = self._file(path)
        return f.read_bytes() if f.is_file() else None

    def put(self, path: str, data: bytes, content_type: str = "application/json") -> None:
        f = self._file(path)
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(f)


class MemoryBucket:
    """A dict. Tests use it as the fake bucket; also the fallback with no storage configured."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self._lock = threading.Lock()

    def get(self, path: str) -> bytes | None:
        with self._lock:
            return self.objects.get(_check_path(path))

    def put(self, path: str, data: bytes, content_type: str = "application/json") -> None:
        with self._lock:
            self.objects[_check_path(path)] = bytes(data)


_memory = MemoryBucket()


def default_bucket() -> Bucket:
    root = config.content_dir()
    if root is not None:
        return LocalBucket(root)
    if config.supabase_configured():
        return SupabaseBucket()
    return _memory


# ---------------------------------------------------------------- JSON helpers

def read_json(bucket: Bucket, path: str, default: Any = None) -> Any:
    raw = bucket.get(path)
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise StoreError(f"{path} is not valid JSON") from exc


def write_json(bucket: Bucket, path: str, value: Any) -> None:
    bucket.put(path, json.dumps(value, ensure_ascii=False, indent=1).encode("utf-8"))


def read_jsonl(bucket: Bucket, path: str) -> list[dict[str, Any]]:
    raw = bucket.get(path)
    if raw is None:
        return []
    out = []
    for line in raw.decode("utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError as exc:
                raise StoreError(f"{path} has a line that is not JSON") from exc
    return out


def write_jsonl(bucket: Bucket, path: str, rows: list[dict[str, Any]]) -> None:
    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    bucket.put(path, text.encode("utf-8"), "application/x-ndjson")


def check_run_id(run_id: str) -> str:
    if not RUN_ID_RE.match(run_id or ""):
        raise StoreError("Run ids look like 20261007T222857Z.")
    return run_id


def run_path(run_id: str, name: str) -> str:
    return f"evals/runs/{check_run_id(run_id)}/{name}"


# ---------------------------------------------------------------- the pieces

def read_questions_text(bucket: Bucket) -> str | None:
    raw = bucket.get(QUESTIONS)
    return None if raw is None else raw.decode("utf-8")


def write_questions_text(bucket: Bucket, text: str) -> None:
    bucket.put(QUESTIONS, text.encode("utf-8"), "application/x-ndjson")


def read_index(bucket: Bucket) -> list[dict[str, Any]]:
    data = read_json(bucket, INDEX, {"runs": []})
    runs = data.get("runs") if isinstance(data, dict) else None
    return list(runs or [])


def upsert_index(bucket: Bucket, entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Replace this run's entry (by id) or add it. Newest first."""
    runs = [r for r in read_index(bucket) if r.get("id") != entry["id"]]
    runs.append(entry)
    runs.sort(key=lambda r: (str(r.get("created_at") or ""), str(r.get("id"))), reverse=True)
    write_json(bucket, INDEX, {"runs": runs})
    return runs


def read_run(bucket: Bucket, run_id: str) -> dict[str, Any] | None:
    return read_json(bucket, run_path(run_id, "run.json"))


def write_run(bucket: Bucket, run: dict[str, Any]) -> None:
    write_json(bucket, run_path(run["id"], "run.json"), run)


def read_results(bucket: Bucket, run_id: str) -> list[dict[str, Any]]:
    return read_jsonl(bucket, run_path(run_id, "results.jsonl"))


def write_results(bucket: Bucket, run_id: str, rows: list[dict[str, Any]]) -> None:
    write_jsonl(bucket, run_path(run_id, "results.jsonl"), rows)


def read_calibration(bucket: Bucket) -> dict[str, Any]:
    data = read_json(bucket, CALIBRATION, {})
    return data if isinstance(data, dict) else {}


def write_calibration(bucket: Bucket, data: dict[str, Any]) -> None:
    write_json(bucket, CALIBRATION, data)

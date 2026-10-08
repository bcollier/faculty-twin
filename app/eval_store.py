"""Where Settings > Evals keeps its data: the private bucket, under `evals/` (docs/SPEC.md, "Evals").

    evals/questions.jsonl                  de-identified question set (scripts/upload_eval_questions.py)
    evals/questions/edits/<set>/<UTC>-<n>.json  one question added or edited in Settings, written once;
                                           <set> is the first 12 hex of the uploaded set's sha256
    evals/index.json                       {"runs": [run summary, ...]}: what the runs list, the report card
                                           and Settings > Analytics read (rebuilt from evals/index/)
    evals/index/<run_id>.json              one run's summary entry (the source evals/index.json is built from)
    evals/runs/<run_id>/run.json           config, status, progress, the chosen questions, summary
    evals/runs/<run_id>/rows/<pair>.json   one result row per question x generator, written once
    evals/runs/<run_id>/results.jsonl      all rows in one file (rebuilt from rows/ after every step)
    evals/runs/<run_id>/cancelled.json     present once the run is cancelled
    evals/runs/<run_id>/finished.json      present once the run has finished
    evals/calibration/<judge>.json         latest calibration result per judge (cases under <judge>/<attempt>/)

No SQL table and no DDL: plain JSON objects in the bucket, read and written by
the function with the service role key. These paths are never signed for a
browser (`storage.is_media_path` allows only slides/, clips/ and audio/), so
question text is reachable only through the admin routes.

Why rows are separate, write-once objects: Supabase serves object reads
through a CDN, and a read right after an overwrite can briefly return the
old copy (seen in a live check on Oct 7). So nothing a step depends on is a
read-modify-write of one object: which pairs are done comes from the bucket's
list API (a database query, never cached), each row is written once and never
changed, and the aggregate files are rebuilt as the union of what they held
and what the listing says exists, so a stale read can never drop a row or a run.

The question set follows the same rule (Oct 8 code review). Adding or editing a
question in Settings used to read evals/questions.jsonl, change one line and
write it back, so a stale read lost the previous edit. Now each add or edit is
its own write-once object under evals/questions/edits/<set>/, and the question
list is the uploaded set with those edits applied in name order. A new upload
(a different set hash) starts a fresh, empty edit list.

The runs index is rebuilt incrementally: a step of a running run writes only
its own evals/index/<run_id>.json; evals/index.json is rebuilt when a run
leaves the running state (finished, cancelled or excluded), from its own last copy plus any
listed entry it is missing (one list call, usually no extra reads).

Backends, in the same order `app/storage.py` picks content from:
- `CONTENT_DIR` set (local dev): files under `<CONTENT_DIR>/evals/`
- Supabase configured: the private bucket
- neither: an in-process dict (tests, a bare local run)
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from . import config, supa

PREFIX = "evals/"
QUESTIONS = "evals/questions.jsonl"
QUESTION_EDITS = "evals/questions/edits/"
EDIT_RE = re.compile(r"^[0-9]{8}T[0-9]{6}\.[0-9]{6}Z-[0-9a-f]{8}\.json$")
INDEX = "evals/index.json"
INDEX_DIR = "evals/index/"
RUN_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z(?:-[a-z0-9]{1,12})?$|^baseline-[0-9]{8}$")
ROW_RE = re.compile(r"^([0-9]{4})\.json$")
CACHE_CONTROL = "no-cache, max-age=0"
LIST_LIMIT = 1000


class StoreError(RuntimeError):
    pass


class Bucket(Protocol):
    """Where eval files live: the private Supabase bucket, a local folder, or memory (tests)."""

    def get(self, path: str) -> bytes | None: ...

    def put(self, path: str, data: bytes, content_type: str = "application/json") -> None: ...

    def list(self, prefix: str) -> list[str]:
        """Names directly under the folder `prefix` (files and sub-folders), uncached."""
        ...


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
            supa.upload(_check_path(path), data, content_type, upsert=True, cache_control=CACHE_CONTROL)
        except supa.SupabaseError as exc:
            raise StoreError(str(exc)) from exc

    def list(self, prefix: str) -> list[str]:
        try:
            return supa.list_objects(_check_path(prefix), limit=LIST_LIMIT)
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
        """Write `data` whole: to a temporary file first, then renamed, so a reader never sees half a file."""
        f = self._file(path)
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(f)

    def list(self, prefix: str) -> list[str]:
        folder = self._file(prefix.rstrip("/") + "/x").parent
        return sorted(p.name for p in folder.iterdir() if not p.name.endswith(".tmp")) if folder.is_dir() else []


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

    def list(self, prefix: str) -> list[str]:
        folder = _check_path(prefix.rstrip("/") + "/")
        with self._lock:
            names = {key[len(folder):].split("/", 1)[0] for key in self.objects if key.startswith(folder)}
        return sorted(names)


_memory = MemoryBucket()


def default_bucket() -> Bucket:
    """CONTENT_DIR as a local folder when set, else the private Supabase bucket, else memory (tests)."""
    root = config.content_dir()
    if root is not None:
        return LocalBucket(root)
    if config.supabase_configured():
        return SupabaseBucket()
    return _memory


# ---------------------------------------------------------------- JSON helpers

def read_json(bucket: Bucket, path: str, default: Any = None) -> Any:
    """Parsed JSON at a bucket path, or `default` when it is missing. Malformed JSON is a StoreError."""
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
    """Rows of a JSON Lines object ([] when missing). A malformed line is a StoreError."""
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
    """The run id when it is safe as a path segment, else a StoreError."""
    if not RUN_ID_RE.match(run_id or ""):
        raise StoreError("Run ids look like 20261007T222857Z.")
    return run_id


def run_path(run_id: str, name: str) -> str:
    return f"evals/runs/{check_run_id(run_id)}/{name}"


def bucket_names(bucket: Bucket, prefix: str) -> set[str]:
    return set(bucket.list(prefix))


def _fetch_many(bucket: Bucket, paths: list[str]) -> list[Any]:
    if not paths:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(paths))) as pool:
        return list(pool.map(lambda p: read_json(bucket, p), paths))


# ---------------------------------------------------------------- questions

def _set_folder(base: str) -> str:
    return f"{QUESTION_EDITS}{hashlib.sha256(base.encode('utf-8')).hexdigest()[:12]}/"


def _apply_edit(lines: list[str], edit: Any) -> None:
    if not isinstance(edit, dict) or not isinstance(edit.get("record"), dict):
        return
    line = json.dumps(edit["record"], ensure_ascii=False)
    n = edit.get("line")
    if edit.get("op") == "edit" and isinstance(n, int) and 1 <= n <= len(lines):
        lines[n - 1] = line
    elif edit.get("op") == "add":
        lines.append(line)


def read_question_lines(bucket: Bucket, pending: tuple[str, dict[str, Any]] | None = None) -> list[str] | None:
    """The uploaded set with every Settings add or edit applied, oldest first. None when there is nothing.

    Which edits exist comes from the list API (never cached); each edit object is written once, so a
    stale read cannot return an old version of it. `pending` is an edit this request just wrote: kept even
    when the listing has not caught up yet.
    """
    raw = bucket.get(QUESTIONS)
    base = "" if raw is None else raw.decode("utf-8")
    folder = _set_folder(base)
    names = sorted(n for n in bucket.list(folder) if EDIT_RE.match(n))
    if pending is not None and pending[0] not in names:
        names.append(pending[0])
    if raw is None and not names:
        return None
    to_fetch = [n for n in names if pending is None or n != pending[0]]
    by_name = dict(zip(to_fetch, _fetch_many(bucket, [folder + n for n in to_fetch])))
    if pending is not None:
        by_name[pending[0]] = pending[1]
    lines = base.splitlines()
    for name in sorted(by_name):
        _apply_edit(lines, by_name[name])
    return lines


def read_questions_text(bucket: Bucket) -> str | None:
    lines = read_question_lines(bucket)
    return None if lines is None else "\n".join(lines) + ("\n" if lines else "")


def write_question_edit(bucket: Bucket, op: str, record: dict[str, Any],
                        line: int | None = None) -> tuple[str, dict[str, Any]]:
    """Record one Settings add (`op` "add") or edit (`op` "edit", 1-based `line`) as a new object. Returns it."""
    if op not in ("add", "edit"):
        raise StoreError("op must be add or edit")
    raw = bucket.get(QUESTIONS)
    folder = _set_folder("" if raw is None else raw.decode("utf-8"))
    name = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ") + f"-{secrets.token_hex(4)}.json"
    edit = {"op": op, "line": line, "record": record, "at": datetime.now(UTC).isoformat(timespec="seconds")}
    write_json(bucket, folder + name, edit)
    return name, edit


def write_questions_text(bucket: Bucket, text: str) -> None:
    """Upload a whole question set (scripts/upload_eval_questions.py). Its edit list starts empty."""
    bucket.put(QUESTIONS, text.encode("utf-8"), "application/x-ndjson")


# ---------------------------------------------------------------- the runs index

def _sort_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(runs, key=lambda r: (str(r.get("created_at") or ""), str(r.get("id"))), reverse=True)


def read_index(bucket: Bucket) -> list[dict[str, Any]]:
    """Every run's entry, newest first.

    The per-run entries under evals/index/ come first (they are written with no-cache); evals/index.json
    only fills in runs that have no entry object (a copy of it can lag behind on the CDN).
    """
    listed = [n[:-5] for n in bucket.list(INDEX_DIR) if n.endswith(".json") and RUN_ID_RE.match(n[:-5])]
    runs = [e for e in _fetch_many(bucket, [f"{INDEX_DIR}{rid}.json" for rid in listed]) if isinstance(e, dict)]
    have = {r.get("id") for r in runs}
    data = read_json(bucket, INDEX, {"runs": []})
    legacy = [r for r in ((data.get("runs") if isinstance(data, dict) else data) or []) if isinstance(r, dict)]
    runs += [r for r in legacy if r.get("id") not in have]
    return _sort_runs(runs)


RUNNING = "running"


def upsert_index(bucket: Bucket, entry: dict[str, Any], rebuild: bool | None = None) -> None:
    """Save this run's entry; then, unless the run is still running, refresh evals/index.json with it.

    Changed Oct 8 (code review): every step used to read every run's entry to rebuild evals/index.json.
    Readers use the per-run entries (`read_index`), so the aggregate only needs refreshing when a run
    changes state. The refresh is incremental: its own last copy, this entry, and only the listed entries
    it does not have yet. It never drops a run (the listing is never cached).
    """
    check_run_id(entry["id"])
    write_json(bucket, f"{INDEX_DIR}{entry['id']}.json", entry)
    if rebuild is None:
        rebuild = entry.get("status") != RUNNING
    if not rebuild:
        return
    data = read_json(bucket, INDEX, {"runs": []})
    old = (data.get("runs") if isinstance(data, dict) else data) or []
    by_id = {r["id"]: r for r in old if isinstance(r, dict) and r.get("id")}
    by_id[entry["id"]] = entry
    listed = [n[:-5] for n in bucket.list(INDEX_DIR) if n.endswith(".json") and RUN_ID_RE.match(n[:-5])]
    missing = [rid for rid in listed if rid not in by_id]
    for found in _fetch_many(bucket, [f"{INDEX_DIR}{rid}.json" for rid in missing]):
        if isinstance(found, dict) and found.get("id"):
            by_id[found["id"]] = found
    write_json(bucket, INDEX, {"runs": _sort_runs(list(by_id.values()))})


# ---------------------------------------------------------------- one run

def read_run(bucket: Bucket, run_id: str) -> dict[str, Any] | None:
    return read_json(bucket, run_path(run_id, "run.json"))


def write_run(bucket: Bucket, run: dict[str, Any]) -> None:
    write_json(bucket, run_path(run["id"], "run.json"), run)


def row_path(run_id: str, pair: int) -> str:
    return run_path(run_id, f"rows/{int(pair):04d}.json")


def write_row(bucket: Bucket, run_id: str, row: dict[str, Any]) -> None:
    """Record one question x generator result. Written once; a repeat of the same pair overwrites it."""
    write_json(bucket, row_path(run_id, row["pair"]), row)


def row_pairs(bucket: Bucket, run_id: str) -> set[int]:
    """Which pairs have a result, from the list API (never cached)."""
    out = set()
    for name in bucket.list(run_path(run_id, "rows/")):
        m = ROW_RE.match(name)
        if m:
            out.add(int(m.group(1)))
    return out


def read_results(bucket: Bucket, run_id: str) -> list[dict[str, Any]]:
    """Every result row of a run: the results.jsonl aggregate, plus any written row it does not have yet."""
    rows = read_jsonl(bucket, run_path(run_id, "results.jsonl"))
    have = {r.get("pair") for r in rows}
    missing = sorted(p for p in row_pairs(bucket, run_id) if p not in have)
    rows += [r for r in _fetch_many(bucket, [row_path(run_id, p) for p in missing]) if isinstance(r, dict)]
    rows.sort(key=lambda r: (r.get("pair") is None, r.get("pair") or 0))
    return rows


def write_results(bucket: Bucket, run_id: str, rows: list[dict[str, Any]]) -> None:
    write_jsonl(bucket, run_path(run_id, "results.jsonl"), rows)


def mark_finished(bucket: Bucket, run_id: str, status: str, at: str) -> None:
    write_json(bucket, run_path(run_id, "finished.json"), {"status": status, "at": at})


def finished_status(bucket: Bucket, run_id: str) -> str | None:
    """"done" or "cancelled" when the run's write-once marker exists (from the listing), else None."""
    names = set(bucket.list(run_path(run_id, "")))
    if "cancelled.json" in names:
        return "cancelled"
    if "finished.json" in names:
        return "done"
    return None


def mark_cancelled(bucket: Bucket, run_id: str, at: str) -> None:
    write_json(bucket, run_path(run_id, "cancelled.json"), {"at": at})


def is_cancelled(bucket: Bucket, run_id: str) -> bool:
    return "cancelled.json" in bucket.list(run_path(run_id, ""))


# ---------------------------------------------------------------- calibration
#
#   evals/calibration/<judge>.json                     the latest result for that judge (rebuilt each step)
#   evals/calibration/<judge>/<attempt>/<case>.json    one case's result, written once
#
# <judge> is provider:model with anything outside [A-Za-z0-9._-] replaced by "_".

CALIBRATION_DIR = "evals/calibration/"
ATTEMPT_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{4}$")
CASE_RE = re.compile(r"^[A-Za-z0-9_-]{1,60}$")


def judge_slug(judge: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", judge)[:150]


def write_calibration_row(bucket: Bucket, judge: str, attempt: str, row: dict[str, Any]) -> None:
    """Store one calibration case's result for one judge's attempt."""
    if not ATTEMPT_RE.match(attempt) or not CASE_RE.match(str(row["cid"])):
        raise StoreError("bad calibration attempt or case id")
    write_json(bucket, f"{CALIBRATION_DIR}{judge_slug(judge)}/{attempt}/{row['cid']}.json", row)


def calibration_rows(bucket: Bucket, judge: str, attempt: str) -> dict[str, dict[str, Any]]:
    """The case results of one attempt, by case id (listed, so never a cached copy)."""
    if not ATTEMPT_RE.match(attempt or ""):
        raise StoreError("bad calibration attempt")
    folder = f"{CALIBRATION_DIR}{judge_slug(judge)}/{attempt}/"
    names = [n for n in bucket.list(folder) if n.endswith(".json")]
    rows = _fetch_many(bucket, [folder + n for n in names])
    return {r["cid"]: r for r in rows if isinstance(r, dict) and "cid" in r}


def write_calibration_entry(bucket: Bucket, judge: str, entry: dict[str, Any]) -> None:
    write_json(bucket, f"{CALIBRATION_DIR}{judge_slug(judge)}.json", entry)


def read_calibration(bucket: Bucket) -> dict[str, Any]:
    """The latest calibration result per judge, keyed by provider:model."""
    names = [n for n in bucket.list(CALIBRATION_DIR) if n.endswith(".json")]
    out: dict[str, Any] = {}
    for entry in _fetch_many(bucket, [CALIBRATION_DIR + n for n in names]):
        if isinstance(entry, dict) and entry.get("judge"):
            out[entry["judge"]] = entry
    return out

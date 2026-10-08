"""Pipeline stage 5: build the index the backend loads (content/index.json + embeddings.npy).

Reads (all private, under ~/Lecture Archive/_build/ unless noted):
  slides/<course>/s<NN>/slides.json   slide text, notes, OCR text, flags (indexer/slides.py)
  align/<course>/s<NN>.json           instructor transcript per slide (indexer/align.py); optional
  code/<course>/s<NN>.json            notebook code cells (indexer/slides.py)
  clips/manifest.json + clips/*.mp4   class clips (indexer/clips.py); optional
  ~/Lecture Archive/_inventory/f26_inventory.json   course titles, session titles, dates
  indexer/code_map.json (repo)        optional hand-written {slide_id: [code ids]}

Writes:
  content/index.json        {index_version, ..., records: [...]} (docs/SPEC.md, Data)
  content/embeddings.npy    float32, row i = records[i]; only when every record has an embedding
  content/manifest.json     index_version, per-source sha256, counts (what went into this version)
  content/versions/<index_version>.json   a copy of each manifest, for tracing an answer back
  content/embed_cache/<model>/<hash>.npy  one cached Voyage vector per distinct embedding text

Slides flagged `student_names_possible` are left out. A session with no
alignment file still builds (its transcripts are empty).

Index hygiene (added Oct 8). A slide with no text, notes, OCR text or
transcript is marked `thin: true` (every other record `thin: false`). Thin
slides stay in the index: Ben's segment selection fills a one-slide gap from
the records it is given, so a picture-only slide between two chosen slides must
be there to be filled in. No row of embeddings.npy is ever all zeros (or not
finite): a cached zero vector is not trusted, a zero vector from Voyage is
re-embedded once with the record's placeholder text, and if that is zero too
the build stops before writing anything. Embeddings are cached
by a hash of the model and the text, so a re-run only embeds records that
changed. Without VOYAGE_API_KEY, everything except embeddings.npy is written
and the script exits 3 with the one command to run once the key is in `.env`.

Run from the repo root:
  uv run --no-project --with-requirements requirements.txt python -m indexer.build_index
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
import numpy as np

if __package__ in (None, ""):  # allow `python indexer/build_index.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from indexer import assessment_filter, common  # noqa: E402
from indexer.leakcheck import RosterChecker, RosterMissing  # noqa: E402

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
DEFAULT_MODEL = "voyage-3.5"
INPUT_TYPE = "document"
BATCH_SIZE = 128
BATCH_CHARS = 400_000  # about 100k tokens, well under Voyage's per-request token cap
MAX_RETRIES = 6

# Characters kept from each part of the embedding text (about 3,500 tokens in all).
LIMITS = {"title": 300, "text": 3000, "notes": 3000, "ocr_text": 1500, "transcript": 6000, "source": 4000}
EMBED_MAX_CHARS = 14000

RUN_CMD = "uv run --no-project --with-requirements requirements.txt python -m indexer.build_index"
EXIT_OK, EXIT_PENDING, EXIT_LEAK = 0, 3, 4


class EmbeddingError(RuntimeError):
    pass


class ReducedLimits(EmbeddingError):
    """Voyage accounts without a payment method get 3 requests and 10K tokens per minute."""


# Paced mode for reduced-limit accounts: small batches, one request at a time, waits between them.
SLOW_BATCH_CHARS = 20_000  # at 2.5+ characters per token, under 8K tokens per request
SLOW_TPM = 10_000
SLOW_RPM = 3


# ---------------------------------------------------------------- records

def _clip_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    return cut[: space if space > limit * 0.8 else limit].rstrip()


THIN_FIELDS = ("text", "notes", "ocr_text", "transcript")
ZERO_NORM = 1e-6


def placeholder_text(rec: dict[str, Any]) -> str:
    """What a record is embedded as when it has nothing else: where it sits in the course."""
    kind = rec.get("kind")
    if kind not in ("slide", "code"):  # a course-info chunk (indexer/build_info_index.py)
        return str(rec.get("title") or rec.get("id") or "Course material")
    where = f"slide {rec.get('slide_number')}" if kind == "slide" else f"cell {rec.get('cell_number')}"
    return f"{rec.get('course_title') or rec.get('course')}, session {rec.get('session')}: {rec.get('session_title') or ''}, {where}"


def is_thin(rec: dict[str, Any]) -> bool:
    """A slide with no text, notes, OCR text or transcript: nothing to explain but its picture and title."""
    return rec.get("kind") == "slide" and not any(str(rec.get(k) or "").strip() for k in THIN_FIELDS)


def usable(vec: np.ndarray | None) -> bool:
    """A vector rank() can divide by: finite, and not all zeros."""
    return vec is not None and bool(np.isfinite(vec).all()) and float(np.linalg.norm(vec)) > ZERO_NORM


def embed_text(rec: dict[str, Any]) -> str:
    """Title, slide text, notes, OCR text, then what Ben said (code: title, markdown, source)."""
    keys = ("title", "text", "notes", "ocr_text", "transcript") if rec["kind"] == "slide" else ("title", "text", "source")
    parts = [_clip_text(rec.get(k), LIMITS.get(k, 3000)) for k in keys]
    text = _clip_text("\n\n".join(p for p in parts if p), EMBED_MAX_CHARS)
    return text or placeholder_text(rec)  # an image-only slide with no OCR text: Voyage rejects empty input


def _load_align(path: Path) -> dict[str, str]:
    rows = common.read_json(path, []) or []
    out: dict[str, str] = {}
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("slide_id"):
            out[str(row["slide_id"])] = str(row.get("transcript") or "").strip()
    return out


def _load_clips(build: Path) -> dict[str, dict[str, Any]]:
    rows = common.read_json(build / "clips" / "manifest.json", []) or []
    out = {}
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("slide_id"):
            out[str(row["slide_id"])] = row
    return out


def _load_code_map(path: Path) -> dict[str, list[str]]:
    data = common.read_json(path, {}) or {}
    if not isinstance(data, dict):
        return {}
    return {str(k): [str(x) for x in v] for k, v in data.items() if isinstance(v, list) and not str(k).startswith("_")}


def collect(build: Path, archive: Path, code_map_path: Path | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Merge the stage outputs into index records. Returns (records, stats with source hashes)."""
    titles, sessions = common.course_meta(archive)
    clips = _load_clips(build)
    code_map_path = code_map_path or common.REPO / "indexer" / "code_map.json"
    code_map = _load_code_map(code_map_path)
    sources: dict[str, str] = {}
    by_course: dict[str, dict[str, Any]] = {}
    excluded = 0
    code_redactions = 0  # access-code slides left out plus sentences replaced

    def note(path: Path) -> None:
        if path.exists():
            try:
                key = str(path.relative_to(build))
            except ValueError:
                key = str(path.relative_to(archive)) if archive in path.parents else path.name
            sources[key] = common.sha256_file(path)

    def sess_stats(course: str, session: int) -> dict[str, int]:
        c = by_course.setdefault(course, {"title": titles.get(course), "sessions": {}})
        return c["sessions"].setdefault(
            common.session_tag(session),
            {"slides": 0, "code": 0, "with_transcript": 0, "with_clip": 0, "excluded": 0, "align_file": False},
        )

    def facts(course: str, session: int, deck: dict[str, Any]) -> dict[str, Any]:
        meta = sessions.get((course, session), {})
        return {
            "course": course,
            "course_title": titles.get(course),
            "session": session,
            "session_title": meta.get("title") or deck.get("session_title"),
            "date": meta.get("date") or deck.get("date"),
        }

    note(archive / "_inventory" / "f26_inventory.json")
    note(build / "clips" / "manifest.json")
    if code_map_path.exists():
        sources["indexer/code_map.json"] = common.sha256_file(code_map_path)

    slides: list[dict[str, Any]] = []
    for course, session, path in common.iter_session_files(build / "slides", "s[0-9][0-9]/slides.json"):
        note(path)
        deck = common.read_json(path.parent / "deck.json", {}) or {}
        align_path = build / "align" / course / f"{common.session_tag(session)}.json"
        note(align_path)
        transcripts = _load_align(align_path)
        st = sess_stats(course, session)
        st["align_file"] = align_path.exists()
        base = facts(course, session, deck)
        no_clip_session = (course, session) in common.NO_CLIP_SESSIONS
        for row in sorted(common.read_json(path, []) or [], key=lambda r: int(r.get("slide_number") or 0)):
            flags = set(row.get("flags") or [])
            if flags & common.EXCLUDE_FLAGS:
                excluded += 1
                st["excluded"] += 1
                continue
            sid = str(row["slide_id"])
            if assessment_filter.slide_announces_code(row.get("title"), row.get("text"), row.get("ocr_text")):
                # The slide image shows a quiz or survey access code: leave the whole slide out.
                excluded += 1
                st["excluded"] += 1
                code_redactions += 1
                continue
            tag = common.session_tag(session)
            clip = None
            if sid in clips and not no_clip_session and not (flags & common.NO_CLIP_FLAGS):
                if (build / "clips" / f"{sid}.mp4").is_file():
                    clip = f"clips/{sid}.mp4"
            transcript, n_code = assessment_filter.redact(transcripts.get(sid, ""))
            text, n_text = assessment_filter.redact(str(row.get("text") or "").strip())
            notes, n_notes = assessment_filter.redact(str(row.get("notes") or "").strip())
            if n_code:
                clip = None  # the clip's audio may say the code
            code_redactions += n_code + n_text + n_notes
            rec = {
                "id": sid,
                "kind": "slide",
                **base,
                "slide_number": int(row["slide_number"]),
                "title": str(row.get("title") or "").strip(),
                "text": text,
                "notes": notes,
                "ocr_text": assessment_filter.redact(str(row.get("ocr_text") or "").strip())[0],
                "transcript": transcript,
                "image": row.get("image") or f"slides/{course}/{tag}/{sid}.webp",
                "thumb": row.get("thumb") or f"slides/{course}/{tag}/{sid}-thumb.webp",
                "clip": clip,
                "related_code": [],
                "flags": sorted(flags),
            }
            rec["thin"] = is_thin(rec)
            slides.append(rec)
            st["slides"] += 1
            st["with_transcript"] += bool(transcript)
            st["with_clip"] += bool(clip)

    code: list[dict[str, Any]] = []
    for course, session, path in common.iter_session_files(build / "code", "s[0-9][0-9].json"):
        note(path)
        st = sess_stats(course, session)
        base = facts(course, session, {})
        for cell in common.read_json(path, []) or []:
            if not isinstance(cell, dict) or set(cell.get("flags") or []) & common.EXCLUDE_FLAGS:
                continue
            source = str(cell.get("source") or "").rstrip()
            if not source.strip():
                continue
            notebook = str(cell.get("notebook") or "notebook.ipynb")
            number = int(cell.get("cell_index", 0)) + 1
            code.append(
                {
                    "id": str(cell["cell_id"]),
                    "kind": "code",
                    **base,
                    "slide_number": None,
                    "notebook": notebook,
                    "cell_number": number,
                    "title": f"{notebook.rsplit('.', 1)[0]}, cell {number}",
                    "text": str(cell.get("markdown_above") or "").strip(),
                    "notes": "",
                    "transcript": "",
                    "source": source,
                    "mark_lines": [],
                    "image": None,
                    "thumb": None,
                    "clip": None,
                    "related_code": [],
                    "flags": sorted(cell.get("flags") or []),
                    "thin": False,
                }
            )
            st["code"] += 1

    code_ids = {r["id"] for r in code}
    for rec in slides:
        rec["related_code"] = [cid for cid in code_map.get(rec["id"], []) if cid in code_ids]

    records = slides + code
    for rec in records:
        rec["hash"] = common.sha256_bytes(embed_text(rec).encode("utf-8"))
    stats = {
        "records": len(records),
        "slides": len(slides),
        "code": len(code),
        "excluded_student_names": excluded,
        "access_code_redactions": code_redactions,
        "with_transcript": sum(1 for r in slides if r["transcript"]),
        "with_clip": sum(1 for r in slides if r["clip"]),
        "with_related_code": sum(1 for r in slides if r["related_code"]),
        "thin": sum(1 for r in slides if r["thin"]),
        "by_course": by_course,
    }
    return records, {"counts": stats, "sources": dict(sorted(sources.items()))}


# ---------------------------------------------------------------- embeddings

def cache_key(model: str, text: str) -> str:
    return common.sha256_bytes(f"{model}\x00{INPUT_TYPE}\x00{text}".encode("utf-8"))


class EmbedCache:
    def __init__(self, root: Path, model: str) -> None:
        self.dir = root / model.replace("/", "_")
        self.model = model

    def path(self, text: str) -> Path:
        return self.dir / f"{cache_key(self.model, text)}.npy"

    def get(self, text: str) -> np.ndarray | None:
        p = self.path(text)
        if not p.exists():
            return None
        try:
            return np.load(p, allow_pickle=False).astype(np.float32)
        except (OSError, ValueError):
            return None

    def put(self, text: str, vec: np.ndarray) -> None:
        p = self.path(text)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp.npy")
        np.save(tmp, np.asarray(vec, dtype=np.float32))
        tmp.replace(p)


def voyage_embed(
    texts: list[str],
    model: str,
    key: str,
    client: httpx.Client,
    sleep: Callable[[float], None] = time.sleep,
    usage: dict[str, int] | None = None,
) -> list[np.ndarray]:
    """One Voyage call with retry and backoff on 429, 5xx, and network errors."""
    body = {"input": texts, "model": model, "input_type": INPUT_TYPE}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    last = ""
    for attempt in range(MAX_RETRIES):
        try:
            resp = client.post(VOYAGE_URL, json=body, headers=headers)
        except httpx.HTTPError as exc:
            last = type(exc).__name__
        else:
            if resp.status_code < 400:
                payload = resp.json()
                if usage is not None:
                    usage["tokens"] = usage.get("tokens", 0) + int((payload.get("usage") or {}).get("total_tokens") or 0)
                data = sorted(payload.get("data", []), key=lambda d: d.get("index", 0))
                if len(data) != len(texts):
                    raise EmbeddingError(f"Voyage returned {len(data)} embeddings for {len(texts)} inputs")
                return [np.asarray(d["embedding"], dtype=np.float32) for d in data]
            last = f"HTTP {resp.status_code}: {resp.text[:300]}"
            if resp.status_code == 429 and "reduced rate limits" in resp.text:
                raise ReducedLimits(last)
            if resp.status_code not in (408, 409, 425, 429) and resp.status_code < 500:
                raise EmbeddingError(f"Voyage returned {resp.status_code}: {resp.text[:200]}")
            retry_after = resp.headers.get("retry-after")
            if retry_after and retry_after.replace(".", "", 1).isdigit():
                sleep(min(60.0, float(retry_after)))
                continue
        sleep(min(60.0, 2.0**attempt + random.random()))
    raise EmbeddingError(f"Voyage failed after {MAX_RETRIES} tries ({last})")


def batches(items: list[str], max_chars: int = BATCH_CHARS) -> list[list[str]]:
    out: list[list[str]] = []
    cur: list[str] = []
    size = 0
    for t in items:
        if cur and (len(cur) >= BATCH_SIZE or size + len(t) > max_chars):
            out.append(cur)
            cur, size = [], 0
        cur.append(t)
        size += len(t)
    if cur:
        out.append(cur)
    return out


def embed_records(
    records: list[dict[str, Any]],
    cache: EmbedCache,
    key: str | None,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
    usage: dict[str, int] | None = None,
    text_of: Callable[[dict[str, Any]], str] | None = None,
) -> tuple[np.ndarray | None, int, int]:
    """Return (matrix or None, cached count, newly embedded count). Adds Voyage tokens to `usage`.

    `text_of` picks the text to embed per record (default `embed_text`; the course-info index passes its own).
    """
    texts = [(text_of or embed_text)(r) for r in records]
    # A cached zero (or broken) vector is not trusted: it is embedded again (Oct 8).
    vecs: list[np.ndarray | None] = [_trusted(cache.get(t)) for t in texts]
    cached = sum(v is not None for v in vecs)
    todo = sorted({t for t, v in zip(texts, vecs) if v is None})
    if todo and not key:
        return None, cached, 0
    if todo:
        own = client is None
        client = client or httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0))
        slow = bool(common.env("VOYAGE_SLOW"))
        try:
            groups = batches(todo, SLOW_BATCH_CHARS if slow else BATCH_CHARS)
            done = 0
            while groups:
                group = groups.pop(0)
                before = usage.get("tokens", 0) if usage is not None else 0
                try:
                    got = voyage_embed(group, cache.model, key or "", client, sleep, usage)
                except ReducedLimits:
                    if slow and len(group) == 1:
                        raise
                    if not slow:
                        rest = group + [t for g in groups for t in g]
                        est = sum(map(len, rest)) // 3
                        log(
                            "  Voyage says this account has reduced limits (no payment method yet): 3 requests and\n"
                            f"  10K tokens per minute. Switching to paced mode: about {est // SLOW_TPM + 1} minutes for the rest.\n"
                            "  Adding a payment method at https://dashboard.voyageai.com lifts the limit."
                        )
                        slow, groups = True, batches(rest, SLOW_BATCH_CHARS)
                    else:  # still too big for one minute's budget: split it
                        half = len(group) // 2
                        groups = [group[:half], group[half:]] + groups
                    sleep(61.0)
                    continue
                for text, vec in zip(group, got):
                    if usable(vec):  # a zero vector is never cached; the placeholder retry below handles it
                        cache.put(text, vec)
                done += 1
                log(f"  embedded batch {done} ({len(group)} texts, {len(groups)} batches left)")
                if slow and groups:
                    spent = (usage.get("tokens", 0) - before) if usage is not None else 0
                    spent = spent or sum(map(len, group)) // 3
                    sleep(max(60.0 / SLOW_RPM, 60.0 * spent / SLOW_TPM) + 1.0)
        finally:
            if own:
                client.close()
        asked = set(todo)
        vecs = [_trusted(cache.get(t)) for t in texts]
        for i, vec in enumerate(vecs):
            if vec is None and texts[i] in asked:
                vecs[i] = _placeholder_vector(records[i], cache, key, client, sleep, log, usage)
    if any(v is None for v in vecs):
        raise EmbeddingError("some embeddings are missing from the cache after embedding")
    if not all(usable(v) for v in vecs):  # the guarantee rank() relies on: no zero-norm row
        raise EmbeddingError("an embedding row is all zeros or not finite")
    dims = {v.shape[0] for v in vecs if v is not None}
    if len(dims) != 1:
        raise EmbeddingError(f"embeddings have mixed dimensions {sorted(dims)}; clear the cache for this model")
    return np.stack(vecs).astype(np.float32), cached, len(todo)


def _trusted(vec: np.ndarray | None) -> np.ndarray | None:
    return vec if usable(vec) else None


def _placeholder_vector(
    rec: dict[str, Any],
    cache: EmbedCache,
    key: str | None,
    client: httpx.Client | None,
    sleep: Callable[[float], None],
    log: Callable[[str], None],
    usage: dict[str, int] | None,
) -> np.ndarray:
    """Voyage gave a zero vector for this record's text: embed its placeholder text instead, or stop."""
    text = placeholder_text(rec)
    log(f"  Voyage returned a zero vector for {rec.get('id')}; re-embedding it with its placeholder text")
    vec = _trusted(cache.get(text))
    if vec is None:
        own = client is None
        client = client or httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0))
        try:
            vec = voyage_embed([text], cache.model, key or "", client, sleep, usage)[0]
        finally:
            if own:
                client.close()
    if not usable(vec):
        raise EmbeddingError(f"Voyage returned a zero vector for {rec.get('id')}, and for its placeholder text too")
    cache.put(text, vec)
    return np.asarray(vec, dtype=np.float32)


# ---------------------------------------------------------------- versions and output

def content_hash(records: list[dict[str, Any]], model: str) -> str:
    return common.sha256_bytes(common.dump_json({"model": model, "records": records}))


def new_version(chash: str, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-{chash[:8]}"


def build(
    archive: Path,
    build_root: Path | None = None,
    key: str | None = None,
    model: str = DEFAULT_MODEL,
    client: httpx.Client | None = None,
    code_map: Path | None = None,
    roster: Path | None = None,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> int:
    build_root = build_root or common.build_dir(archive)
    out = build_root / "content"
    records, info = collect(build_root, archive, code_map)
    if not records:
        log(f"No slide or code records found under {build_root}. Run indexer/slides.py first.")
        return 1
    chash = content_hash(records, model)
    cache = EmbedCache(out / "embed_cache", model)
    usage: dict[str, int] = {"tokens": 0}
    try:
        matrix, cached, fresh = embed_records(records, cache, key, client, sleep, log, usage)
    except EmbeddingError as exc:
        log(f"Embedding failed: {exc}. Re-run the same command; finished batches are cached.")
        return 2
    complete = matrix is not None
    # Same records, same model, same embedding state: keep the version, so an
    # unchanged re-run uploads nothing and does not make the backend reload.
    previous = common.read_json(out / "manifest.json", {}) or {}
    same = previous.get("content_hash") == chash and previous.get("embeddings") == ("complete" if complete else "pending")
    version = previous["index_version"] if same and previous.get("index_version") else new_version(chash)

    now = (same and previous.get("built_at")) or datetime.now(timezone.utc).isoformat(timespec="seconds")
    index = {
        "index_version": version,
        "built_at": now,
        "embedding_model": model,
        "embedding_input_type": INPUT_TYPE,
        "embedding_dim": int(matrix.shape[1]) if complete else None,
        "record_count": len(records),
        "records": records,
    }
    index_bytes = common.dump_json(index)
    common.write_bytes_atomic(out / "index.json", index_bytes)
    emb_path = out / "embeddings.npy"
    if complete:
        tmp = out / "embeddings.tmp.npy"
        np.save(tmp, matrix)
        tmp.replace(emb_path)
    elif emb_path.exists():
        emb_path.unlink()  # never leave a matrix that does not match index.json

    manifest = {
        "index_version": version,
        "built_at": now,
        "content_hash": chash,
        "embedding_model": model,
        "embedding_dim": index["embedding_dim"],
        "embeddings": "complete" if complete else "pending",
        "embeddings_cached": cached + fresh if complete else cached,
        "counts": info["counts"],
        "sources": info["sources"],
        "outputs": {
            "content/index.json": common.sha256_bytes(index_bytes),
            **({"content/embeddings.npy": common.sha256_file(emb_path)} if complete else {}),
        },
    }
    common.write_json(out / "manifest.json", manifest)
    common.write_json(out / "versions" / f"{version}.json", manifest)

    c = info["counts"]
    log(
        f"index {version}: {c['records']} records ({c['slides']} slides, {c['code']} code cells); "
        f"{c['excluded_student_names']} slides left out for student names; "
        f"{c['with_transcript']} slides with class transcript; {c['with_clip']} with a clip"
    )
    if complete and fresh:
        log(f"Voyage usage this run: {usage['tokens']:,} tokens ({fresh} new texts)")
    leak = run_leak_check(roster or common.roster_dir(archive), index, log)
    if leak == EXIT_LEAK:
        return EXIT_LEAK
    if not complete:
        need = len(records) - cached
        log(
            f"Embeddings pending: {cached} of {len(records)} records are cached, {need} need Voyage.\n"
            f"VOYAGE_API_KEY is not set. Put it in .env (repo root), then run:\n  {RUN_CMD}"
        )
        return EXIT_PENDING
    log(f"embeddings.npy: {matrix.shape[0]} x {matrix.shape[1]} ({fresh} newly embedded, {cached} from cache)")
    return EXIT_OK


def run_leak_check(roster: Path, index: dict[str, Any], log: Callable[[str], None]) -> int:
    try:
        checker = RosterChecker.from_dir(roster)
    except RosterMissing as exc:
        log(f"leak check skipped here: {exc}. indexer/upload.py will refuse to upload without it.")
        return EXIT_OK
    hits = checker.check_index(index)
    log(hits.summary())
    return EXIT_LEAK if hits.total else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build content/index.json and content/embeddings.npy")
    ap.add_argument("--archive", help="Lecture Archive folder (default ~/Lecture Archive or $LECTURE_ARCHIVE)")
    ap.add_argument("--build", help="build folder (default <archive>/_build)")
    ap.add_argument("--code-map", help="slide -> code map (default indexer/code_map.json)")
    ap.add_argument("--no-embed", action="store_true", help="write the index without calling Voyage")
    a = ap.parse_args(argv)
    common.load_env()
    archive = common.archive_dir(a.archive)
    key = None if a.no_embed else common.env("VOYAGE_API_KEY")
    model = common.env("VOYAGE_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL
    return build(
        archive,
        Path(a.build).expanduser() if a.build else None,
        key=key,
        model=model,
        code_map=Path(a.code_map).expanduser() if a.code_map else common.REPO / "indexer" / "code_map.json",
    )


if __name__ == "__main__":
    sys.exit(main())

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
alignment file still builds (its transcripts are empty). Embeddings are cached
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
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
    """Voyage failed or returned something unusable. Finished batches stay cached for the re-run."""


class ReducedLimits(EmbeddingError):
    """Voyage accounts without a payment method get 3 requests and 10K tokens per minute."""


# Paced mode for reduced-limit accounts: small batches, one request at a time, waits between them.
SLOW_BATCH_CHARS = 20_000  # at 2.5+ characters per token, under 8K tokens per request
SLOW_TPM = 10_000
SLOW_RPM = 3


# ---------------------------------------------------------------- records

SLIDE_EMBED_FIELDS = ("title", "text", "notes", "ocr_text", "transcript")
CODE_EMBED_FIELDS = ("title", "text", "source")


def _clip_text(value: Any, limit: int) -> str:
    """At most `limit` characters, cut at a space when one falls in the last fifth."""
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    return cut[: space if space > limit * 0.8 else limit].rstrip()


def embed_text(rec: dict[str, Any]) -> str:
    """Title, slide text, notes, OCR text, then what Ben said (code: title, markdown, source)."""
    keys = SLIDE_EMBED_FIELDS if rec["kind"] == "slide" else CODE_EMBED_FIELDS
    parts = [_clip_text(rec.get(k), LIMITS.get(k, 3000)) for k in keys]
    text = _clip_text("\n\n".join(p for p in parts if p), EMBED_MAX_CHARS)
    if not text:  # an image-only slide with no OCR text: Voyage rejects empty input
        where = f"slide {rec.get('slide_number')}" if rec["kind"] == "slide" else f"cell {rec.get('cell_number')}"
        course = rec.get("course_title") or rec.get("course")
        text = f"{course}, session {rec.get('session')}: {rec.get('session_title') or ''}, {where}"
    return text


def _rows_by_slide_id(rows: Any) -> dict[str, dict[str, Any]]:
    """{slide_id: row} for a list of JSON rows; anything malformed is skipped."""
    out: dict[str, dict[str, Any]] = {}
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("slide_id"):
            out[str(row["slide_id"])] = row
    return out


def _load_align(path: Path) -> dict[str, str]:
    """{slide_id: instructor transcript} from one session's alignment file ({} when there is none)."""
    rows = _rows_by_slide_id(common.read_json(path, []) or [])
    return {sid: str(row.get("transcript") or "").strip() for sid, row in rows.items()}


def _load_clips(build: Path) -> dict[str, dict[str, Any]]:
    """{slide_id: clip manifest row} from clips/manifest.json."""
    return _rows_by_slide_id(common.read_json(build / "clips" / "manifest.json", []) or [])


def _load_code_map(path: Path) -> dict[str, list[str]]:
    """{slide_id: [code cell id]} from the hand-checked map; keys starting with "_" are notes."""
    data = common.read_json(path, {}) or {}
    if not isinstance(data, dict):
        return {}
    return {str(k): [str(x) for x in v] for k, v in data.items() if isinstance(v, list) and not str(k).startswith("_")}


class _Collector:
    """Folds the stage outputs into index records, counting what it kept and hashing what it read."""

    def __init__(self, build: Path, archive: Path) -> None:
        self.build, self.archive = build, archive
        self.titles, self.sessions = common.course_meta(archive)
        self.clips = _load_clips(build)
        self.sources: dict[str, str] = {}  # every input file -> sha256, for the manifest
        self.by_course: dict[str, dict[str, Any]] = {}
        self.excluded = 0
        self.code_redactions = 0  # access-code slides left out plus sentences replaced

    def note(self, path: Path) -> None:
        """Record an input file's hash (keyed by its path under the build folder or archive)."""
        if path.exists():
            try:
                key = str(path.relative_to(self.build))
            except ValueError:
                key = str(path.relative_to(self.archive)) if self.archive in path.parents else path.name
            self.sources[key] = common.sha256_file(path)

    def session_stats(self, course: str, session: int) -> dict[str, int]:
        """The per-session counters in the manifest, created on first use."""
        c = self.by_course.setdefault(course, {"title": self.titles.get(course), "sessions": {}})
        return c["sessions"].setdefault(
            common.session_tag(session),
            {"slides": 0, "code": 0, "with_transcript": 0, "with_clip": 0, "excluded": 0, "align_file": False},
        )

    def facts(self, course: str, session: int, deck: dict[str, Any]) -> dict[str, Any]:
        """Course and session fields every record carries (the inventory wins over deck.json)."""
        meta = self.sessions.get((course, session), {})
        return {
            "course": course,
            "course_title": self.titles.get(course),
            "session": session,
            "session_title": meta.get("title") or deck.get("session_title"),
            "date": meta.get("date") or deck.get("date"),
        }

    def slide_records(self) -> list[dict[str, Any]]:
        """One record per slide that may be indexed, in deck order, session by session."""
        slides: list[dict[str, Any]] = []
        build = self.build
        for course, session, path in common.iter_session_files(build / "slides", "s[0-9][0-9]/slides.json"):
            self.note(path)
            deck = common.read_json(path.parent / "deck.json", {}) or {}
            align_path = build / "align" / course / f"{common.session_tag(session)}.json"
            self.note(align_path)
            transcripts = _load_align(align_path)
            st = self.session_stats(course, session)
            st["align_file"] = align_path.exists()
            base = self.facts(course, session, deck)
            for row in sorted(common.read_json(path, []) or [], key=lambda r: int(r.get("slide_number") or 0)):
                rec = self.slide_record(row, course, session, base, transcripts)
                if rec is None:
                    st["excluded"] += 1
                    continue
                slides.append(rec)
                st["slides"] += 1
                st["with_transcript"] += bool(rec["transcript"])
                st["with_clip"] += bool(rec["clip"])
        return slides

    def slide_record(self, row: dict[str, Any], course: str, session: int, base: dict[str, Any],
                     transcripts: dict[str, str]) -> dict[str, Any] | None:
        """The index record for one slides.json row, or None when the slide must stay out."""
        flags = set(row.get("flags") or [])
        if flags & common.EXCLUDE_FLAGS:
            self.excluded += 1
            return None
        sid = str(row["slide_id"])
        if assessment_filter.slide_announces_code(row.get("title"), row.get("text"), row.get("ocr_text")):
            # The slide image shows a quiz or survey access code: leave the whole slide out.
            self.excluded += 1
            self.code_redactions += 1
            return None
        tag = common.session_tag(session)
        transcript, n_code = assessment_filter.redact(transcripts.get(sid, ""))
        text, n_text = assessment_filter.redact(str(row.get("text") or "").strip())
        notes, n_notes = assessment_filter.redact(str(row.get("notes") or "").strip())
        self.code_redactions += n_code + n_text + n_notes
        # The clip's audio may say an access code its transcript had, so a redacted transcript loses its clip.
        clip = None if n_code else self.clip_path(sid, course, session, flags)
        return {
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

    def clip_path(self, sid: str, course: str, session: int, flags: set[str]) -> str | None:
        """The slide's class clip, unless its session or flags rule clips out or the file is missing."""
        if sid not in self.clips or (course, session) in common.NO_CLIP_SESSIONS:
            return None
        if any(flag in flags for flag in common.NO_CLIP_FLAGS):
            return None
        return f"clips/{sid}.mp4" if (self.build / "clips" / f"{sid}.mp4").is_file() else None

    def code_records(self) -> list[dict[str, Any]]:
        """One record per notebook code cell that is not flagged for student names."""
        code: list[dict[str, Any]] = []
        for course, session, path in common.iter_session_files(self.build / "code", "s[0-9][0-9].json"):
            self.note(path)
            st = self.session_stats(course, session)
            base = self.facts(course, session, {})
            for cell in common.read_json(path, []) or []:
                if not isinstance(cell, dict) or set(cell.get("flags") or []) & common.EXCLUDE_FLAGS:
                    continue
                source = str(cell.get("source") or "").rstrip()
                if not source.strip():
                    continue
                code.append(_code_record(cell, source, base))
                st["code"] += 1
        return code


def _code_record(cell: dict[str, Any], source: str, base: dict[str, Any]) -> dict[str, Any]:
    """The index record for one notebook code cell."""
    notebook = str(cell.get("notebook") or "notebook.ipynb")
    number = int(cell.get("cell_index", 0)) + 1
    return {
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
    }


def collect(build: Path, archive: Path,
            code_map_path: Path | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Merge the stage outputs into index records. Returns (records, stats with source hashes)."""
    collector = _Collector(build, archive)
    code_map_path = code_map_path or common.REPO / "indexer" / "code_map.json"
    code_map = _load_code_map(code_map_path)
    collector.note(archive / "_inventory" / "f26_inventory.json")
    collector.note(build / "clips" / "manifest.json")
    if code_map_path.exists():
        collector.sources["indexer/code_map.json"] = common.sha256_file(code_map_path)

    slides = collector.slide_records()
    code = collector.code_records()
    code_ids = {r["id"] for r in code}
    for rec in slides:
        rec["related_code"] = [cid for cid in code_map.get(rec["id"], []) if cid in code_ids]

    records = slides + code
    for rec in records:
        rec["hash"] = common.sha256_bytes(embed_text(rec).encode())
    stats = {
        "records": len(records),
        "slides": len(slides),
        "code": len(code),
        "excluded_student_names": collector.excluded,
        "access_code_redactions": collector.code_redactions,
        "with_transcript": sum(1 for r in slides if r["transcript"]),
        "with_clip": sum(1 for r in slides if r["clip"]),
        "with_related_code": sum(1 for r in slides if r["related_code"]),
        "by_course": collector.by_course,
    }
    return records, {"counts": stats, "sources": dict(sorted(collector.sources.items()))}


# ---------------------------------------------------------------- embeddings

def cache_key(model: str, text: str) -> str:
    """The cache file name for one text: a hash of the model, the input type and the text."""
    return common.sha256_bytes(f"{model}\x00{INPUT_TYPE}\x00{text}".encode())


class EmbedCache:
    """One .npy file per embedded text, so a re-run only pays for records that changed."""

    def __init__(self, root: Path, model: str) -> None:
        self.dir = root / model.replace("/", "_")
        self.model = model

    def path(self, text: str) -> Path:
        return self.dir / f"{cache_key(self.model, text)}.npy"

    def get(self, text: str) -> np.ndarray | None:
        """The cached vector for a text, or None when it is missing or unreadable."""
        p = self.path(text)
        if not p.exists():
            return None
        try:
            return np.load(p, allow_pickle=False).astype(np.float32)
        except (OSError, ValueError):
            return None

    def put(self, text: str, vec: np.ndarray) -> None:
        """Cache a vector, written atomically."""
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
                    spent = int((payload.get("usage") or {}).get("total_tokens") or 0)
                    usage["tokens"] = usage.get("tokens", 0) + spent
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
    """Split texts into request-sized batches: at most BATCH_SIZE texts and `max_chars` characters."""
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
    The matrix is None when some texts are not cached and there is no key to embed them.
    """
    texts = [(text_of or embed_text)(r) for r in records]
    vecs: list[np.ndarray | None] = [cache.get(t) for t in texts]
    cached = sum(v is not None for v in vecs)
    todo = sorted({t for t, v in zip(texts, vecs) if v is None})
    if todo and not key:
        return None, cached, 0
    if todo:
        own = client is None
        client = client or httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0))
        try:
            _embed_into_cache(todo, cache, key or "", client, sleep, log, usage)
        finally:
            if own:
                client.close()
        vecs = [cache.get(t) for t in texts]
    if any(v is None for v in vecs):
        raise EmbeddingError("some embeddings are missing from the cache after embedding")
    dims = {v.shape[0] for v in vecs if v is not None}
    if len(dims) != 1:
        raise EmbeddingError(f"embeddings have mixed dimensions {sorted(dims)}; clear the cache for this model")
    return np.stack(vecs).astype(np.float32), cached, len(todo)


def _embed_into_cache(
    texts: list[str],
    cache: EmbedCache,
    key: str,
    client: httpx.Client,
    sleep: Callable[[float], None],
    log: Callable[[str], None],
    usage: dict[str, int] | None,
) -> None:
    """Embed `texts` batch by batch into the cache, so an interrupted run resumes where it stopped.

    Voyage accounts without a payment method answer 429 "reduced rate limits". Then the rest
    is re-batched small and paced to 3 requests and 10K tokens a minute (VOYAGE_SLOW=1 starts
    paced). A batch still too big for one minute's budget is halved until it fits.
    """
    slow = bool(common.env("VOYAGE_SLOW"))
    groups = batches(texts, SLOW_BATCH_CHARS if slow else BATCH_CHARS)
    done = 0
    while groups:
        group = groups.pop(0)
        before = usage.get("tokens", 0) if usage is not None else 0
        try:
            got = voyage_embed(group, cache.model, key, client, sleep, usage)
        except ReducedLimits:
            if slow and len(group) == 1:
                raise
            if slow:  # still too big for one minute's budget: split it
                half = len(group) // 2
                groups = [group[:half], group[half:]] + groups
            else:
                rest = group + [t for g in groups for t in g]
                log(_paced_mode_message(rest))
                slow, groups = True, batches(rest, SLOW_BATCH_CHARS)
            sleep(61.0)
            continue
        for text, vec in zip(group, got):
            cache.put(text, vec)
        done += 1
        log(f"  embedded batch {done} ({len(group)} texts, {len(groups)} batches left)")
        if slow and groups:
            spent = (usage.get("tokens", 0) - before) if usage is not None else 0
            spent = spent or sum(map(len, group)) // 3  # no usage reported: about 3 characters a token
            sleep(max(60.0 / SLOW_RPM, 60.0 * spent / SLOW_TPM) + 1.0)


def _paced_mode_message(rest: list[str]) -> str:
    est = sum(map(len, rest)) // 3
    return (
        "  Voyage says this account has reduced limits (no payment method yet): 3 requests and\n"
        f"  10K tokens per minute. Switching to paced mode: about {est // SLOW_TPM + 1} minutes for the rest.\n"
        "  Adding a payment method at https://dashboard.voyageai.com lifts the limit."
    )


# ---------------------------------------------------------------- versions and output

def content_hash(records: list[dict[str, Any]], model: str) -> str:
    """A hash of the records and the model: what decides whether an index changed."""
    return common.sha256_bytes(common.dump_json({"model": model, "records": records}))


def new_version(chash: str, now: datetime | None = None) -> str:
    """A version id: UTC build time plus the start of the content hash."""
    now = now or datetime.now(UTC)
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-{chash[:8]}"


def embedding_state(complete: bool) -> str:
    """The manifest's "embeddings" field."""
    return "complete" if complete else "pending"


def keep_or_new_version(previous: dict[str, Any], version_key: str, chash: str, complete: bool) -> tuple[str, str]:
    """(version, built_at) for this build.

    Same records, same model, same embedding state as the previous manifest: keep its version
    and time, so an unchanged re-run uploads nothing and does not make the backend reload.
    """
    same = previous.get("content_hash") == chash and previous.get("embeddings") == embedding_state(complete)
    version = previous[version_key] if same and previous.get(version_key) else new_version(chash)
    built_at = (same and previous.get("built_at")) or datetime.now(UTC).isoformat(timespec="seconds")
    return version, built_at


def save_matrix(path: Path, matrix: np.ndarray | None) -> None:
    """Write the embedding matrix atomically, or delete the old one when there is none.

    Never leaves a matrix that does not match the index written next to it.
    """
    if matrix is not None:
        tmp = path.with_name(f"{path.stem}.tmp.npy")
        np.save(tmp, matrix)
        tmp.replace(path)
    elif path.exists():
        path.unlink()


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
    """Build content/index.json, embeddings.npy and the manifest. Returns an EXIT_* code."""
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
    previous = common.read_json(out / "manifest.json", {}) or {}
    version, now = keep_or_new_version(previous, "index_version", chash, complete)
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
    save_matrix(emb_path, matrix)

    manifest = {
        "index_version": version,
        "built_at": now,
        "content_hash": chash,
        "embedding_model": model,
        "embedding_dim": index["embedding_dim"],
        "embeddings": embedding_state(complete),
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


def run_leak_check(
    roster: Path,
    index: dict[str, Any],
    log: Callable[[str], None],
    name: str = "content/index.json",
    strict_fields: tuple[str, ...] = ("transcript",),
) -> int:
    """Check a freshly built index against the rosters. EXIT_LEAK on any hit.

    Without rosters the check is skipped here (with a note), because upload.py runs it again
    and refuses to upload without them: the gate that matters is at the upload.
    """
    try:
        checker = RosterChecker.from_dir(roster)
    except RosterMissing as exc:
        log(f"leak check skipped here: {exc}. indexer/upload.py will refuse to upload without it.")
        return EXIT_OK
    hits = checker.check_index(index, name, strict_fields=strict_fields)
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

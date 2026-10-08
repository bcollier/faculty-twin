"""Build the course-info index from the Canvas import (content/info_index.json + info_embeddings.npy).

Reads (private):
  <archive>/_build/canvas/<course>/items.json   written by indexer/canvas_import.py

Writes (private, under <archive>/_build/content/):
  info_index.json        {info_version, built_at, embedding_model, ..., records: [...]}
                         one record per chunk: {id, course, title, kind, module, canvas_url, due_at, text}
  info_embeddings.npy    float32, row i = records[i]; Voyage, input_type document, same model as the
                         slide index (so the app can score both with one question embedding)
  info_manifest.json     version, content hash, counts (local only; never uploaded)

Embeddings are cached by a hash of model, input type and text in the same
`content/embed_cache/` the slide index uses, so a re-run only embeds chunks that
changed. Every chunk already went through de-identification, the PG filter,
the access-code filter and key redaction in canvas_import.py; the access-code
and key filters run again here as a belt and braces. The roster leak check runs
over the finished index (strict on title and text) and must report 0 hits.

Exit codes: 0 done, 1 nothing to index, 2 embedding error, 3 VOYAGE_API_KEY missing, 4 leak check hit.

Run from the repo root:
  uv run --no-project --with-requirements requirements.txt python -m indexer.build_info_index
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from indexer import assessment_filter, common  # noqa: E402
from indexer.build_index import (  # noqa: E402
    DEFAULT_MODEL,
    INPUT_TYPE,
    EmbedCache,
    EmbeddingError,
    embed_records,
    new_version,
)
from indexer.canvas_import import redact_secrets  # noqa: E402
from indexer.leakcheck import INFO_STRICT_FIELDS, RosterChecker, RosterMissing  # noqa: E402

KINDS = {"page", "file", "assignment", "link", "announcement", "syllabus"}
EXIT_OK, EXIT_EMPTY, EXIT_EMBED, EXIT_PENDING, EXIT_LEAK = 0, 1, 2, 3, 4
RUN_CMD = "uv run --no-project --with-requirements requirements.txt python -m indexer.build_info_index"


def collect(build: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One record per chunk of every imported Canvas item. Returns (records, counts)."""
    records: list[dict[str, Any]] = []
    counts: dict[str, Any] = {"items": 0, "by_course": {}}
    for path in sorted((build / "canvas").glob("[0-9]" * 5 + "/items.json")):
        course = path.parent.name
        items = common.read_json(path, []) or []
        by_kind: dict[str, int] = {}
        for item in items if isinstance(items, list) else []:
            kind = item.get("kind")
            if kind not in KINDS:
                continue
            counts["items"] += 1
            for n, chunk in enumerate(item.get("chunks") or [], 1):
                text, _ = assessment_filter.redact(str(chunk))
                text, _ = redact_secrets(text)
                text = text.strip()
                if not text:
                    continue
                records.append({
                    "id": f"{item['id']}-c{n:02d}",
                    "course": item.get("course") or course,
                    "title": item.get("title") or "",
                    "kind": kind,
                    "module": item.get("module"),
                    "canvas_url": item.get("canvas_url"),
                    "due_at": item.get("due_at"),
                    "text": text,
                })
                by_kind[kind] = by_kind.get(kind, 0) + 1
        counts["by_course"][course] = by_kind
    counts["records"] = len(records)
    return records, counts


def slide_index_dim(content: Path) -> int | None:
    meta = common.read_json(content / "manifest.json", {}) or {}
    return meta.get("embedding_dim")


def build(
    archive: Path,
    build_root: Path | None = None,
    key: str | None = None,
    model: str = DEFAULT_MODEL,
    client: httpx.Client | None = None,
    roster: Path | None = None,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> int:
    build_root = build_root or common.build_dir(archive)
    out = build_root / "content"
    records, counts = collect(build_root)
    if not records:
        log(f"No Canvas items under {build_root / 'canvas'}. Run indexer/canvas_import.py first.")
        return EXIT_EMPTY
    chash = common.sha256_bytes(common.dump_json({"model": model, "records": records}))
    usage = {"tokens": 0}
    try:
        matrix, cached, fresh = embed_records(records, EmbedCache(out / "embed_cache", model), key, client,
                                              sleep, log, usage, text_of=lambda r: r["text"])
    except EmbeddingError as exc:
        log(f"Embedding failed: {exc}. Re-run the same command; finished batches are cached.")
        return EXIT_EMBED
    complete = matrix is not None
    dim = int(matrix.shape[1]) if complete else None
    want = slide_index_dim(out)
    if complete and want and dim != want:
        log(f"Embedding dimension {dim} does not match the slide index ({want}). Use the same VOYAGE_MODEL.")
        return EXIT_EMBED

    previous = common.read_json(out / "info_manifest.json", {}) or {}
    same = previous.get("content_hash") == chash and previous.get("embeddings") == ("complete" if complete else "pending")
    version = previous["info_version"] if same and previous.get("info_version") else new_version(chash)
    now = (same and previous.get("built_at")) or datetime.now(timezone.utc).isoformat(timespec="seconds")
    index = {
        "info_version": version,
        "built_at": now,
        "embedding_model": model,
        "embedding_input_type": INPUT_TYPE,
        "embedding_dim": dim,
        "record_count": len(records),
        "records": records,
    }
    index_bytes = common.dump_json(index)
    common.write_bytes_atomic(out / "info_index.json", index_bytes)
    emb_path = out / "info_embeddings.npy"
    if complete:
        tmp = out / "info_embeddings.tmp.npy"
        np.save(tmp, matrix)
        tmp.replace(emb_path)
    elif emb_path.exists():
        emb_path.unlink()  # never leave a matrix that does not match info_index.json
    common.write_json(out / "info_manifest.json", {
        "info_version": version,
        "built_at": now,
        "content_hash": chash,
        "embedding_model": model,
        "embedding_dim": dim,
        "embeddings": "complete" if complete else "pending",
        "counts": counts,
        "outputs": {
            "content/info_index.json": common.sha256_bytes(index_bytes),
            **({"content/info_embeddings.npy": common.sha256_file(emb_path)} if complete else {}),
        },
    })
    log(f"info index {version}: {len(records)} chunks from {counts['items']} Canvas items; "
        + "; ".join(f"{c}: " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items()))
                    for c, kinds in sorted(counts["by_course"].items())))
    if complete:
        log(f"info_embeddings.npy: {matrix.shape[0]} x {matrix.shape[1]} ({fresh} newly embedded, {cached} from cache); "
            f"Voyage tokens this run: {usage['tokens']:,}")

    try:
        checker = RosterChecker.from_dir(roster or common.roster_dir(archive))
    except RosterMissing as exc:
        log(f"leak check skipped here: {exc}. indexer/upload.py will refuse to upload without it.")
    else:
        hits = checker.check_index(index, "content/info_index.json", strict_fields=INFO_STRICT_FIELDS)
        log(hits.summary())
        if hits.total:
            return EXIT_LEAK
    if not complete:
        log(f"Embeddings pending: VOYAGE_API_KEY is not set. Put it in .env, then run:\n  {RUN_CMD}")
        return EXIT_PENDING
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build content/info_index.json and info_embeddings.npy from Canvas items")
    ap.add_argument("--archive", help="Lecture Archive folder (default ~/Lecture Archive or $LECTURE_ARCHIVE)")
    ap.add_argument("--build", help="build folder (default <archive>/_build)")
    ap.add_argument("--no-embed", action="store_true", help="write the index without calling Voyage")
    a = ap.parse_args(argv)
    common.load_env()
    archive = common.archive_dir(a.archive)
    key = None if a.no_embed else common.env("VOYAGE_API_KEY")
    model = common.env("VOYAGE_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL
    return build(archive, Path(a.build).expanduser() if a.build else None, key=key, model=model)


if __name__ == "__main__":
    sys.exit(main())

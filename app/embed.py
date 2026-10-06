"""Question embeddings from Voyage AI.

API (checked against docs.voyageai.com/reference/embeddings-api, Oct 2026):
POST https://api.voyageai.com/v1/embeddings
  headers: Authorization: Bearer <VOYAGE_API_KEY>
  body:    {"input": [text], "model": "voyage-3.5", "input_type": "query"}
  returns: {"data": [{"embedding": [...], "index": 0}], "model": ..., "usage": {...}}

The indexer embeds slides with input_type "document" and the same model, so the
question and slide vectors live in the same space. VOYAGE_MODEL must match the
model the index was built with.
"""

from __future__ import annotations

import httpx
import numpy as np

from . import config

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"


class EmbeddingError(RuntimeError):
    pass


def model_name() -> str:
    return config.env("VOYAGE_MODEL", config.DEFAULT_VOYAGE_MODEL) or config.DEFAULT_VOYAGE_MODEL


def build_request(text: str) -> tuple[str, dict[str, str], dict]:
    key = config.env("VOYAGE_API_KEY")
    if not key:
        raise EmbeddingError("VOYAGE_API_KEY is not set")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = {"input": [text], "model": model_name(), "input_type": "query"}
    return VOYAGE_URL, headers, body


def parse_response(data: dict) -> np.ndarray:
    try:
        vec = data["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError) as exc:
        raise EmbeddingError("Voyage response had no embedding") from exc
    return np.asarray(vec, dtype=np.float32)


def embed_question(text: str, client: httpx.Client | None = None) -> np.ndarray:
    url, headers, body = build_request(text)
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0))
    try:
        resp = client.post(url, headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise EmbeddingError(f"Voyage request failed: {type(exc).__name__}") from exc
    finally:
        if own:
            client.close()
    if resp.status_code >= 400:
        raise EmbeddingError(f"Voyage returned {resp.status_code}: {resp.text[:200]}")
    return parse_response(resp.json())

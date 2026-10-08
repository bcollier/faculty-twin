"""Tiny Supabase client: PostgREST (tables, RPC) and Storage (objects, signed URLs).

Plain httpx with the service role key, server side only. The browser never sees
this key. Every function raises `SupabaseError` on failure so callers can decide
whether to degrade (local dev) or return a readable error (admin routes).

REST shapes used (Supabase Storage API, mirrored from storage-js):
- POST /storage/v1/object/sign/{bucket}/{path}   {expiresIn}        -> {signedURL}
- POST /storage/v1/object/sign/{bucket}          {expiresIn, paths} -> [{path, signedURL, error}]
- POST /storage/v1/object/upload/sign/{bucket}/{path}               -> {url: "/object/upload/sign/...?token=..."}
- GET  /storage/v1/object/{bucket}/{path}        (authenticated download; HEAD for existence)
- POST /storage/v1/object/{bucket}/{path}        body = the bytes, x-upsert        (upload a small object)
- POST /storage/v1/object/list/{bucket}          {prefix, limit, offset, sortBy} -> [{name, ...}]
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from . import config

TIMEOUT = httpx.Timeout(20.0, connect=5.0)


class SupabaseError(RuntimeError):
    pass


def _base() -> str:
    url = config.env("SUPABASE_URL")
    if not url or not config.env("SUPABASE_SERVICE_ROLE_KEY"):
        raise SupabaseError("Supabase is not configured (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)")
    return url.rstrip("/")


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    key = config.env("SUPABASE_SERVICE_ROLE_KEY") or ""
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    if extra:
        headers.update(extra)
    return headers


class _Client(httpx.Client):
    """httpx client whose transport errors surface as SupabaseError."""

    def request(self, *args, **kwargs):  # type: ignore[override]
        try:
            return super().request(*args, **kwargs)
        except httpx.HTTPError as exc:
            raise SupabaseError(f"Supabase request failed: {type(exc).__name__}") from exc


def _client() -> httpx.Client:
    # A fresh client per call keeps this safe across Vercel invocations; calls are few.
    return _Client(timeout=TIMEOUT)


def _check(resp: httpx.Response, what: str) -> httpx.Response:
    if resp.status_code >= 400:
        # Never echo headers; the body is Supabase's error JSON and holds no secrets.
        raise SupabaseError(f"{what} failed ({resp.status_code}): {resp.text[:300]}")
    return resp


def _obj_path(path: str) -> str:
    return quote(path.lstrip("/"), safe="/")


# ---------------------------------------------------------------- PostgREST

def select(table: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
    with _client() as c:
        r = c.get(f"{_base()}/rest/v1/{table}", params=params or {}, headers=_headers())
    return _check(r, f"select {table}").json()


def insert(table: str, row: dict[str, Any] | list[dict[str, Any]], upsert_on: str | None = None) -> list[dict[str, Any]]:
    prefer = "return=representation"
    params = {}
    if upsert_on:
        prefer += ",resolution=merge-duplicates"
        params["on_conflict"] = upsert_on
    with _client() as c:
        r = c.post(
            f"{_base()}/rest/v1/{table}",
            params=params,
            json=row,
            headers=_headers({"Prefer": prefer, "Content-Type": "application/json"}),
        )
    return _check(r, f"insert {table}").json()


def update(table: str, match: dict[str, str], values: dict[str, Any]) -> list[dict[str, Any]]:
    """PATCH rows matching PostgREST filters, e.g. match={"id": "eq.5"}."""
    with _client() as c:
        r = c.patch(
            f"{_base()}/rest/v1/{table}",
            params=match,
            json=values,
            headers=_headers({"Prefer": "return=representation", "Content-Type": "application/json"}),
        )
    return _check(r, f"update {table}").json()


def rpc(fn: str, args: dict[str, Any]) -> Any:
    with _client() as c:
        r = c.post(
            f"{_base()}/rest/v1/rpc/{fn}",
            json=args,
            headers=_headers({"Content-Type": "application/json"}),
        )
    return _check(r, f"rpc {fn}").json()


# ---------------------------------------------------------------- Storage

def download(path: str) -> bytes:
    with _client() as c:
        r = c.get(f"{_base()}/storage/v1/object/{config.bucket()}/{_obj_path(path)}", headers=_headers())
    return _check(r, f"download {path}").content


def download_optional(path: str) -> bytes | None:
    """Object bytes, or None when the object does not exist (Storage answers 400 or 404 for that)."""
    with _client() as c:
        r = c.get(f"{_base()}/storage/v1/object/{config.bucket()}/{_obj_path(path)}", headers=_headers())
    if r.status_code in (400, 404):
        return None
    return _check(r, f"download {path}").content


def upload(path: str, data: bytes, content_type: str, upsert: bool = False, cache_control: str | None = None) -> None:
    """Write one small object from the server (prompt history, eval runs). Large files go browser-direct instead.

    `cache_control` is stored with the object and tells Storage's CDN how long it may serve a copy
    (Settings > Evals passes "no-cache, max-age=0" for files it rewrites).
    """
    headers = {"Content-Type": content_type, "x-upsert": "true" if upsert else "false"}
    if cache_control:
        headers["cache-control"] = cache_control
    with _client() as c:
        r = c.post(
            f"{_base()}/storage/v1/object/{config.bucket()}/{_obj_path(path)}",
            content=data,
            headers=_headers(headers),
        )
    _check(r, f"upload {path}")


def list_objects(prefix: str, limit: int = 100) -> list[str]:
    """Names (not full paths) of the objects directly under `prefix`, a folder ending in "/"."""
    folder = prefix.strip("/")
    with _client() as c:
        r = c.post(
            f"{_base()}/storage/v1/object/list/{config.bucket()}",
            json={"prefix": folder, "limit": limit, "offset": 0, "sortBy": {"column": "name", "order": "desc"}},
            headers=_headers({"Content-Type": "application/json"}),
        )
    return [item["name"] for item in _check(r, f"list {folder}").json() if item.get("name")]


def object_exists(path: str) -> bool:
    with _client() as c:
        r = c.head(f"{_base()}/storage/v1/object/{config.bucket()}/{_obj_path(path)}", headers=_headers())
    return r.status_code == 200


def sign_urls(paths: list[str], expires_in: int) -> dict[str, str]:
    """Mint signed download URLs for many objects in one call. Returns {path: absolute URL}."""
    if not paths:
        return {}
    base = _base()
    with _client() as c:
        r = c.post(
            f"{base}/storage/v1/object/sign/{config.bucket()}",
            json={"expiresIn": expires_in, "paths": paths},
            headers=_headers({"Content-Type": "application/json"}),
        )
    out: dict[str, str] = {}
    for item in _check(r, "sign urls").json():
        signed = item.get("signedURL") or item.get("signedUrl")
        if signed and not item.get("error"):
            out[item["path"]] = f"{base}/storage/v1{signed}"
    return out


def create_upload_url(path: str, upsert: bool = True) -> str:
    """Mint a signed upload URL; the browser PUTs the file there directly."""
    base = _base()
    with _client() as c:
        r = c.post(
            f"{base}/storage/v1/object/upload/sign/{config.bucket()}/{_obj_path(path)}",
            headers=_headers({"x-upsert": "true" if upsert else "false"}),
        )
    data = _check(r, "create upload url").json()
    rel = data.get("url")
    if not rel:
        raise SupabaseError("create upload url: no url in response")
    return f"{base}/storage/v1{rel}"

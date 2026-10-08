"""Import the student-facing course information Ben posted on Canvas.

Students ask the twin things the Canvas site already answers ("when is Lab 3
due?", "can I use AI on the homework?", "where is the FAQ?"). This stage pulls
that material, read-only, so `indexer/build_info_index.py` can index it.

What is imported (published items only, per course):
- module items: pages, files Ben posted (PDF, docx, md, txt -> text), assignment
  and quiz descriptions with due dates, external links (title and link only;
  Google Docs and Sheets are exported as text or CSV with `gog`)
- every published page and assignment that is not in a module
- the syllabus body
- announcements Ben wrote

Never fetched: submissions, grades, enrollments, users other than `self`,
groups, sections, discussion replies. `CanvasClient` refuses those paths, and it
only ever sends GET.

Privacy (docs/SPEC.md, UPDATE 8 of the team brief):
- A Google Doc, Sheet or file that lists students (a presentation schedule,
  teams, sign-ups) is not imported as text. It becomes a stub that points to
  Canvas. Detection: roster names in the raw text (indexer/leakcheck.py), or a
  schedule / sign-up / teams word in the title of a doc, sheet or file.
- Every other text is de-identified with the transcript scrubber
  (indexer/deidentify.py: `[student]` / `[person]`, Ben's own names stay),
  kept PG (indexer/pg_filter.py), stripped of quiz access codes
  (indexer/assessment_filter.py) and of anything shaped like an API key.
- Files whose names say key, secret, token or password are skipped outright.
- Zoom recording links are kept as "recording posted on Canvas" records with no
  external URL.

Output (private, never committed or uploaded):
  <archive>/_build/canvas/<course>/items.json    the contract below
  <archive>/_build/canvas/<course>/report.json   counts, stubs and skips (titles only)
  <archive>/_build/canvas/<course>/cache/        fetched bodies, keyed by Canvas updated_at

items.json: [{id, course, module, position, title, kind: page|file|assignment|link|announcement|syllabus,
              canvas_url, source_url, due_at, updated_at, text, chunks: [text...], stub}]

The Canvas token is read from `<archive>/_private/canvas_token` and never
printed or logged.

Run from the repo root:
  uv run --no-project --with-requirements requirements.txt --with rapidfuzz --with nicknames \
      python -m indexer.canvas_import
"""

from __future__ import annotations

import argparse
import html
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from indexer import assessment_filter, common  # noqa: E402
from indexer.pg_filter import smooth as pg_smooth  # noqa: E402

BASE = "https://canvas.cmu.edu"
COURSES = {"45884": 54496, "70445": 55124}
COURSE_LABELS = {"45884": "45-884", "70445": "70-445"}
STUB_TEXT = "Schedule with student names; see it on Canvas."
GOOGLE_ACCOUNTS = ("ben@collier.phd", "bcollier@andrew.cmu.edu")
MIN_INTERVAL = 0.34  # seconds between Canvas requests: at most 3 per second
MAX_RETRIES = 6
TZ = ZoneInfo("America/New_York")

CHUNK_MIN_WORDS = 150
CHUNK_MAX_WORDS = 400

# Canvas paths this importer must never touch (student work, grades, people).
FORBIDDEN_PATH = re.compile(
    r"/(?:submissions|quiz_submissions|grades|gradebook|gradebook_history|enrollments|students|groups|"
    r"group_categories|sections|analytics|recipients|conversations|entries|replies|view)(?:/|$|\?)"
    r"|/users/(?!self(?:/|$|\?))",
    re.IGNORECASE,
)
FORBIDDEN_INCLUDE = re.compile(r"submission|grade|enrollment|student|user", re.IGNORECASE)

# Titles of docs, sheets and files that list students.
STUDENT_LIST_TITLE = re.compile(
    r"\b(?:schedule|sign[- ]?ups?|teams?|groups?|roster|partners?|presentation order)\b", re.IGNORECASE)
# Files never imported, not even as a title: they hold keys or passwords.
SECRET_FILE = re.compile(r"(?:key|secret|token|password|credential)", re.IGNORECASE)
# Slide decks are already in the slide index; the case file stays private.
SLIDES_FILE = re.compile(r"(?:^|[\s\-])slides?\b|\bcase\b", re.IGNORECASE)
TEXT_FILE_EXT = {".pdf", ".docx", ".doc", ".md", ".txt"}
# Anything shaped like an API key or token.
SECRET_TEXT = re.compile(
    r"\b(?:sk-(?:ant-|proj-)?[A-Za-z0-9_\-]{16,}|AIza[0-9A-Za-z_\-]{30,}|pa-[A-Za-z0-9_\-]{20,}|"
    r"gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[abprs]-[A-Za-z0-9\-]{10,}|"
    r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}|"
    r"(?<![/=?&#.:\w-])(?=[A-Za-z0-9_\-]{32,}\b)(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Z])(?=[A-Za-z0-9_\-]*[a-z])[A-Za-z0-9_\-]{32,})"
)
KEY_MARKER = "[key removed]"
GOOGLE_DOC = re.compile(r"https://docs\.google\.com/(document|spreadsheets|presentation)/d/([A-Za-z0-9_\-]{20,})")
ZOOM = re.compile(r"^https?://[^/]*zoom\.us/", re.IGNORECASE)


class CanvasError(RuntimeError):
    """A Canvas request failed. The message carries the path and status, never the token."""


class ForbiddenRequest(CanvasError):
    """A request for student data (submissions, grades, people) that the client refuses to send."""


# ---------------------------------------------------------------- Canvas client (GET only)

class CanvasClient:
    """Read-only Canvas REST client: GET only, Link pagination, at most 3 requests per second.

    The token goes only into the Authorization header of requests to the Canvas
    host. Error messages carry the path and status, never the header.
    """

    def __init__(self, token: str, base: str = BASE, client: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 min_interval: float = MIN_INTERVAL) -> None:
        self.base = base.rstrip("/")
        self.host = urlparse(self.base).netloc
        self._token = token
        self.http = client or httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0), follow_redirects=False)
        self.sleep, self.clock, self.min_interval = sleep, clock, min_interval
        self._last = -1e9
        self.requests = 0

    def _check(self, url: str, params: dict[str, Any] | None) -> None:
        path = urlparse(url).path
        if FORBIDDEN_PATH.search(path):
            raise ForbiddenRequest(f"refusing to fetch {path}: student data")
        for key, value in (params or {}).items():
            values = value if isinstance(value, (list, tuple)) else [value]
            if key.startswith("include") and any(FORBIDDEN_INCLUDE.search(str(v)) for v in values):
                raise ForbiddenRequest(f"refusing include {values}: student data")

    def _pace(self) -> None:
        wait = self.min_interval - (self.clock() - self._last)
        if wait > 0:
            self.sleep(wait)
        self._last = self.clock()

    def _get(self, url: str, params: dict[str, Any] | None = None, auth: bool = True) -> httpx.Response:
        self._check(url, params)
        headers = {"Authorization": f"Bearer {self._token}"} if auth and urlparse(url).netloc == self.host else {}
        last = ""
        for attempt in range(MAX_RETRIES):
            self._pace()
            self.requests += 1
            try:
                resp = self.http.request("GET", url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                last = type(exc).__name__
            else:
                # Canvas throttles with 403 "Rate Limit Exceeded" as well as 429.
                rate_limited = resp.status_code == 429 or (
                    resp.status_code == 403 and "rate limit" in resp.text.lower())
                if resp.status_code < 400:
                    return resp
                if not rate_limited and resp.status_code < 500:
                    raise CanvasError(f"GET {urlparse(url).path} returned {resp.status_code}")
                last = f"HTTP {resp.status_code}"
            self.sleep(min(30.0, 2.0 ** attempt))
        raise CanvasError(f"GET {urlparse(url).path} failed after {MAX_RETRIES} tries ({last})")

    def url(self, path: str) -> str:
        return path if path.startswith("http") else f"{self.base}/api/v1{path}"

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """JSON for one object, or every page of a list (follows rel="next")."""
        url = self.url(path)
        params = dict(params or {})
        out: list[Any] | None = None
        while url:
            resp = self._get(url, params)
            data = resp.json()
            if not isinstance(data, list):
                return data
            out = (out or []) + data
            nxt = resp.links.get("next", {}).get("url")
            if nxt and urlparse(nxt).netloc != self.host:
                raise CanvasError("pagination link points off Canvas")
            url, params = nxt, None
        return out or []

    def download(self, url: str) -> bytes:
        """A file's bytes. Canvas file URLs carry a verifier, so no token is sent (redirects go to S3)."""
        if not url.startswith("https://"):
            raise CanvasError("file URL is not https")
        resp = self._get(url, auth=False)
        hops = 0
        while resp.status_code in (301, 302, 303, 307, 308) and hops < 5:
            loc = resp.headers.get("location", "")
            if not loc.startswith("https://"):
                raise CanvasError("file redirect is not https")
            resp = self._get(loc, auth=False)
            hops += 1
        return resp.content


def read_token(archive: Path) -> str:
    """The read-only Canvas token from the private folder (never printed or logged)."""
    path = archive / "_private" / "canvas_token"
    try:
        token = path.read_text().strip()
    except OSError as exc:
        raise CanvasError(f"no Canvas token at {path}") from exc
    if not token:
        raise CanvasError(f"the Canvas token file {path} is empty")
    return token


# ---------------------------------------------------------------- text helpers

class _HTMLText(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6",
             "section", "article", "blockquote", "pre", "hr", "dt", "dd"}
    SKIP = {"script", "style", "noscript", "iframe"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.href: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self.skip += 1
            return
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")
        if tag in ("td", "th"):
            self.parts.append(" | ")
        if tag == "a":
            self.href.append(dict(attrs).get("href"))

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if tag == "a" and self.href:
            href = self.href.pop()
            # Keep outside links as text; Zoom recordings and Canvas's own pages are not worth citing.
            external = href and href.startswith("http") and urlparse(href).netloc != urlparse(BASE).netloc
            if external and not ZOOM.match(href):
                self.parts.append(f" ({href})")
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def html_to_text(raw: str | None) -> str:
    """Readable text from Canvas HTML: block tags become line breaks, outside links stay as (url)."""
    if not raw:
        return ""
    p = _HTMLText()
    p.feed(raw)
    p.close()
    return normalize("".join(p.parts))


def normalize(text: str) -> str:
    """Unescaped text with runs of spaces shortened and blank lines collapsed to one."""
    text = html.unescape(text or "").replace("\r", "").replace(" ", " ").replace("﻿", "")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


def redact_secrets(text: str) -> tuple[str, int]:
    """(text with key-shaped strings replaced by KEY_MARKER, how many were replaced)."""
    return SECRET_TEXT.subn(KEY_MARKER, text or "")


def fmt_due(due_at: str | None) -> str | None:
    """A Canvas timestamp as "Monday, October 6, 2026 at 11:59 PM Eastern"; None when missing or malformed."""
    if not due_at:
        return None
    try:
        dt = datetime.fromisoformat(due_at.replace("Z", "+00:00")).astimezone(TZ)
    except ValueError:
        return None
    return f"{dt.strftime('%A, %B')} {dt.day}, {dt.year} at {dt.strftime('%I:%M %p').lstrip('0')} Eastern"


def _words(s: str) -> int:
    return len(s.split())


def _split_long(unit: str, hi: int) -> list[str]:
    if _words(unit) <= hi:
        return [unit]
    out, cur = [], []
    for sent in re.split(r"(?<=[.!?])\s+", unit):
        for piece in ([sent] if _words(sent) <= hi else
                      [" ".join(sent.split()[i:i + hi]) for i in range(0, _words(sent), hi)]):
            if cur and _words(" ".join(cur + [piece])) > hi:
                out.append(" ".join(cur))
                cur = []
            cur.append(piece)
    if cur:
        out.append(" ".join(cur))
    return out


def chunk_text(title: str, text: str, lo: int = CHUNK_MIN_WORDS, hi: int = CHUNK_MAX_WORDS) -> list[str]:
    """~lo-hi word chunks on paragraph boundaries, each prefixed with the title."""
    units: list[str] = []
    for para in re.split(r"\n+", text or ""):
        para = para.strip()
        if para:
            units.extend(_split_long(para, hi))
    chunks: list[list[str]] = []
    cur: list[str] = []
    n = 0
    for u in units:
        w = _words(u)
        # A new question in a FAQ starts a new chunk once the current one is big enough.
        if cur and (n + w > hi or (n >= lo and re.match(r"^(?:Q\s*[:.)]|\d+[.)]\s|#)", u))):
            chunks.append(cur)
            cur, n = [], 0
        cur.append(u)
        n += w
    if cur:
        if chunks and n < lo // 2 and sum(map(_words, chunks[-1])) + n <= hi + hi // 4:
            chunks[-1].extend(cur)
        else:
            chunks.append(cur)
    title = (title or "").strip()
    out = ["\n".join(c) for c in chunks if c]
    if not out:
        return [title] if title else []
    return [f"{title}\n{c}" if title else c for c in out]


# ---------------------------------------------------------------- de-identification and filters

class Cleaner:
    """De-identify, keep PG, drop access codes and keys. Counts are labels only, never text."""

    def __init__(self, scrub: Callable[[str, Counter], str], checker: Any | None) -> None:
        self._scrub = scrub
        self.checker = checker  # indexer.leakcheck.RosterChecker, or None when the rosters are missing
        self.counts: Counter = Counter()
        self.rules: Counter = Counter()  # which de-identification rule fired (labels only)

    @classmethod
    def from_archive(cls, archive: Path) -> Cleaner:
        """A cleaner built from the archive's word lists, rosters and overrides."""
        from indexer import deidentify as d
        from indexer.leakcheck import RosterChecker

        english, given, _people, scrub, keep, _ = d.build_context(archive)
        # A roster word that is also an ordinary English word ("Green", "Will", "Master") is masked
        # only as part of a full roster name: Ben's prose uses those words as words ("Red, Yellow,
        # and Green"). The leak check treats them the same way. Every other roster token is masked.
        scrub.exact = {t for t in scrub.exact if t not in english}
        scrub.nick = {t for t in scrub.nick if t not in english}
        scrub.fuzzy_pool = [t for t in scrub.fuzzy_pool if t not in english]
        # Written prose, not speech: a given name that is also an ordinary word ("The", "Part", "Case")
        # is only masked when it is a roster name. Roster names are always masked.
        scrubber = d.Scrubber(scrub, english, given - english, keep)
        checker = RosterChecker.from_dir(common.roster_dir(archive))
        return cls(scrubber.scrub, checker)

    def lists_students(self, text: str) -> bool:
        """True when raw text names several roster students (a schedule, a team list).

        Three or more full names, Andrew IDs or emails; or single roster names that are dense
        (more than 2% of the words), which is what a list of first names looks like. A long
        class summary that mentions a few first names is de-identified instead.
        """
        if self.checker is None or not text:
            return False
        strong = self.checker.strong(text)
        strict = self.checker.strict(text)
        return strong >= 3 or (strict >= 6 and strict > 0.02 * max(1, len(text.split())))

    def clean_title(self, title: str) -> str:
        """Ben's item titles: fully de-identified only when a roster name is in them.

        The transcript scrubber reads "Check here with Questions" as a roll call, so a title
        with no roster word only gets the PG, access-code and key filters.
        """
        if self.checker is not None and self.checker.strict(title):
            return self.clean(title)
        return self._filter(title or "")

    def clean(self, text: str) -> str:
        """De-identify `text` (names masked), then apply the PG, access-code and key filters."""
        counts: Counter = Counter()
        out = self._scrub(text or "", counts)
        if self.checker is not None:
            # A roster word right next to a mask is part of the same name ("James [person]").
            def absorb(m: re.Match) -> str:
                if self.checker.is_single(m.group("w")):
                    counts["name_pair"] += 1
                    return m.group("mask")
                return m.group(0)

            out = re.sub(r"\b(?P<w>[A-Z][a-z]+)\s+(?P<mask>\[(?:student|person)\])", absorb, out)
            out = re.sub(r"(?P<mask>\[(?:student|person)\])\s+(?P<w>[A-Z][a-z]+)\b", absorb, out)
        self.counts["names_masked"] += sum(counts.values())
        self.rules.update(counts)
        return self._filter(out)

    def _filter(self, text: str) -> str:
        """The filters every stored string gets after de-identification: PG, access codes, keys."""
        out, n = pg_smooth(text)
        self.counts["pg_swaps"] += n
        out, n = assessment_filter.redact(out)
        self.counts["access_codes_removed"] += n
        out, n = redact_secrets(out)
        self.counts["keys_removed"] += n
        return normalize(out)


# ---------------------------------------------------------------- Google Docs and Sheets

def gog_export(file_id: str, fmt: str, accounts: tuple[str, ...] = GOOGLE_ACCOUNTS) -> str | None:
    """Export a Google Doc (txt) or Sheet (csv) with `gog`. None when no account can read it."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / f"export.{fmt}"
        for account in accounts:
            try:
                proc = subprocess.run(
                    ["gog", "-a", account, "drive", "download", file_id, "--format", fmt, "--out", str(out),
                     "--overwrite", "--no-input"],
                    capture_output=True, text=True, timeout=120,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if proc.returncode == 0 and out.exists():
                return out.read_text(encoding="utf-8", errors="replace")
    return None


def csv_to_text(raw: str) -> str:
    """A Google Sheet export as text: non-empty cells joined with " | ", one row per line."""
    import csv
    import io

    rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(raw))]
    rows = [r for r in rows if any(r)]
    return "\n".join(" | ".join(c for c in r if c) for r in rows)


# ---------------------------------------------------------------- files

def extract_file_text(path: Path) -> str:
    """Text of a PDF (pdftotext), Word file (LibreOffice), or Markdown / text file."""
    ext = path.suffix.lower()
    if ext in (".md", ".txt"):
        return path.read_text(encoding="utf-8", errors="replace")
    if ext == ".pdf":
        proc = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, timeout=180)
        if proc.returncode != 0:
            raise CanvasError(f"pdftotext failed on {path.name}")
        return re.sub(r" {3,}", "  ", proc.stdout)
    if ext in (".docx", ".doc"):
        soffice = Path("~/bin/soffice").expanduser()
        with tempfile.TemporaryDirectory() as tmp:
            # A private profile, so a running LibreOffice (or a stale lock) cannot block the conversion.
            profile = Path(tmp, "profile").as_uri()
            proc = subprocess.run(
                [str(soffice) if soffice.exists() else "soffice", f"-env:UserInstallation={profile}", "--headless",
                 "--norestore", "--convert-to", "txt:Text", "--outdir", tmp, str(path)],
                capture_output=True, text=True, timeout=180,
            )
            out = Path(tmp) / (path.stem + ".txt")
            if proc.returncode != 0 or not out.exists():
                raise CanvasError(f"LibreOffice could not convert {path.name}")
            return out.read_text(encoding="utf-8", errors="replace")
    raise CanvasError(f"no text extractor for {ext}")


# ---------------------------------------------------------------- the importer

@dataclass
class Report:
    """What one course import kept as stubs, skipped, and counted (titles cleaned, reasons only)."""

    counts: Counter = field(default_factory=Counter)
    stubs: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)

    def skip(self, title: str, why: str) -> None:
        self.skipped.append({"title": title, "why": why})


class Importer:
    """Walks one course's modules, then the pages, assignments, syllabus and announcements outside them."""

    def __init__(self, course: str, canvas: CanvasClient, cleaner: Cleaner, cache_dir: Path,
                 gdrive: Callable[[str, str], str | None] | None = gog_export,
                 extract: Callable[[Path], str] = extract_file_text,
                 log: Callable[[str], None] = print) -> None:
        self.course = course
        self.cid = COURSES[course]
        self.canvas, self.cleaner, self.cache = canvas, cleaner, cache_dir
        self.gdrive, self.extract, self.log = gdrive, extract, log
        self.items: dict[str, dict[str, Any]] = {}
        self.report = Report()
        self.pdf_stems: set[str] = set()
        # Filled by run() from the course's listings before any item is imported.
        self.pages: dict[str, dict[str, Any]] = {}
        self.by_id: dict[int, dict[str, Any]] = {}
        self.by_quiz: dict[int, dict[str, Any]] = {}

    def skip(self, title: str, why: str) -> None:
        self.report.skip(self.cleaner.clean_title(title or ""), why)

    def item_url(self, item: dict[str, Any]) -> str | None:
        """The module item page students click (Canvas frames external links and files there)."""
        if item.get("id") is None:
            return item.get("html_url")
        return f"{BASE}/courses/{self.cid}/modules/items/{item['id']}"

    # -- cache keyed by Canvas updated_at -------------------------------------
    def _cached(self, kind: str, key: str, updated_at: str | None, fetch: Callable[[], Any]) -> Any:
        path = self.cache / kind / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', str(key))}.json"
        hit = common.read_json(path)
        if hit and updated_at and hit.get("updated_at") == updated_at:
            self.report.counts["cache_hits"] += 1
            return hit["value"]
        value = fetch()
        common.write_json(path, {"updated_at": updated_at, "value": value})
        return value

    # -- record building --------------------------------------------------------
    def add(self, key: str, *, kind: str, title: str, raw: str, canvas_url: str | None, module: str | None = None,
            position: int | None = None, section: str | None = None, source_url: str | None = None,
            due_at: str | None = None, updated_at: str | None = None, student_list_title: bool = False,
            header: list[str] | None = None) -> None:
        """Clean and chunk one item, or store a stub when it lists students. Each key is added once."""
        if key in self.items:
            return
        title = normalize(title)
        stub_reason = None
        if student_list_title and STUDENT_LIST_TITLE.search(title):
            stub_reason = "title says it lists students (schedule, teams, sign-ups)"
        elif self.cleaner.lists_students(raw):
            stub_reason = "names several roster students"
        lines = list(header or [])
        if section:
            lines.insert(0, f"Class: {section}")
        if module:
            lines.insert(0, f"Module: {module}")
        if stub_reason:
            body = STUB_TEXT
            source_url = None
            self.report.stubs.append({"title": self.cleaner.clean_title(title), "why": stub_reason})
        else:
            body = self.cleaner.clean(raw)
        meta = self.cleaner.clean("\n".join(lines)) if lines else ""
        text = normalize(f"{meta}\n\n{body}" if meta else body)
        clean_title = self.cleaner.clean_title(title)
        self.items[key] = {
            "id": f"{self.course}-canvas-{key}",
            "course": self.course,
            "module": module,
            "position": position,
            "title": clean_title,
            "kind": kind,
            "canvas_url": canvas_url,
            "source_url": source_url,
            "due_at": due_at,
            "updated_at": updated_at,
            "text": text,
            "chunks": chunk_text(clean_title, text),
            "stub": bool(stub_reason),
        }
        self.report.counts[f"{kind}{'_stub' if stub_reason else ''}"] += 1

    # -- Canvas objects -----------------------------------------------------------
    def page(self, url_slug: str, meta: dict[str, Any] | None = None, **kw) -> None:
        """A published Canvas page, from the cache when Canvas says it has not changed."""
        key = f"page-{url_slug}"
        if key in self.items:
            return
        meta = meta or self.pages.get(url_slug)
        if meta is not None and not meta.get("published", True):
            self.skip(meta.get("title", url_slug), "unpublished page")
            return
        updated = (meta or {}).get("updated_at")
        body = self._cached("pages", url_slug, updated,
                            lambda: self.canvas.get(f"/courses/{self.cid}/pages/{url_slug}"))
        if not body.get("published", True):
            self.skip(body.get("title", url_slug), "unpublished page")
            return
        self.add(key, kind="page", title=body.get("title") or url_slug, raw=html_to_text(body.get("body")),
                 canvas_url=body.get("html_url") or f"{BASE}/courses/{self.cid}/pages/{url_slug}",
                 updated_at=body.get("updated_at"), **kw)

    def assignment(self, a: dict[str, Any], **kw) -> None:
        """A published assignment with its due date, points and close date as a header."""
        key = f"assignment-{a['id']}"
        if key in self.items:
            return
        if not a.get("published", True):
            self.skip(a.get("name", ""), "unpublished assignment")
            return
        header = []
        due = fmt_due(a.get("due_at"))
        header.append(f"Due: {due}" if due else "Due: no due date set on Canvas")
        if a.get("points_possible") is not None:
            header.append(f"Points: {a['points_possible']:g}")
        if a.get("lock_at"):
            header.append(f"Closes: {fmt_due(a['lock_at'])}")
        self.add(key, kind="assignment", title=a.get("name") or a.get("title") or "Assignment",
                 raw=html_to_text(a.get("description")), canvas_url=a.get("html_url"),
                 due_at=a.get("due_at"), updated_at=a.get("updated_at"), header=header, **kw)

    def quiz(self, quiz_id: int, item: dict[str, Any], **kw) -> None:
        """A quiz, through its assignment when it has one, else from the quizzes API."""
        a = self.by_quiz.get(int(quiz_id))
        if a is not None:
            self.assignment(a, **kw)
            return
        key = f"quiz-{quiz_id}"
        if key in self.items:
            return
        q = self._cached("quizzes", str(quiz_id), item.get("content_details", {}).get("updated_at") or None,
                         lambda: self.canvas.get(f"/courses/{self.cid}/quizzes/{quiz_id}"))
        if not q.get("published", True):
            self.skip(q.get("title", ""), "unpublished quiz")
            return
        q = {**q, "name": q.get("title"), "id": f"q{quiz_id}"}
        self.assignment(q, **kw)

    def file(self, item: dict[str, Any], **kw) -> None:
        """A course file: text for PDFs, Word and text files; a title-only link for decks and everything else."""
        title = item.get("title") or ""
        fid = item.get("content_id")
        key = f"file-{fid}"
        if key in self.items:
            return
        if SECRET_FILE.search(title):
            self.skip(title, "file name says it holds a key or password: never imported")
            return
        meta = self.canvas.get(item["url"]) if item.get("url") else self.canvas.get(f"/courses/{self.cid}/files/{fid}")
        name = meta.get("display_name") or title
        ext = Path(meta.get("filename") or name).suffix.lower()
        kind = "syllabus" if re.search(r"syllabus", f"{title} {name}", re.I) else "file"
        canvas_url = self.item_url(item) or f"{BASE}/courses/{self.cid}/files/{fid}"
        updated = meta.get("modified_at") or meta.get("updated_at")
        if SECRET_FILE.search(name):
            self.skip(title, "file name says it holds a key or password: never imported")
            return
        stem = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", title).strip().lower()
        if ext in (".docx", ".doc") and stem in self.pdf_stems:
            self.skip(title, "Word copy of a PDF that is imported")
            return
        if ext not in TEXT_FILE_EXT or ((SLIDES_FILE.search(name) or SLIDES_FILE.search(title)) and kind != "syllabus"):
            why = ("slide deck or case file (slides are in the slide index)" if ext in TEXT_FILE_EXT
                   else f"{ext or 'no'} file: title only")
            self.skip(title, why + "; kept as a link")
            self.add(key, kind="file", title=title, raw=f"File posted on Canvas: {name}", canvas_url=canvas_url,
                     updated_at=updated, **kw)
            return

        def fetch() -> str:
            data = self.canvas.download(meta["url"])
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp) / f"file{ext}"
                p.write_bytes(data)
                return self.extract(p)

        try:
            raw = self._cached("files", str(fid), updated, fetch)
        except (CanvasError, OSError, subprocess.SubprocessError) as exc:
            self.skip(title, f"could not read the file ({type(exc).__name__}); kept as a link")
            raw = f"File posted on Canvas: {name}"
        self.add(key, kind=kind, title=title, raw=raw, canvas_url=canvas_url, updated_at=updated,
                 student_list_title=True, **kw)

    def link(self, item: dict[str, Any], **kw) -> None:
        """An external link: Google Docs and Sheets are exported as text, Zoom links become a note."""
        url = item.get("external_url") or ""
        title = item.get("title") or url
        key = f"link-{item.get('id')}"
        canvas_url = self.item_url(item)
        g = GOOGLE_DOC.search(url)
        if ZOOM.match(url):
            self.add(key, kind="link", title=title, raw="Class recording. Watch it on Canvas (CMU login).",
                     canvas_url=canvas_url, **kw)
            return
        if g and g.group(1) in ("document", "spreadsheets"):
            if STUDENT_LIST_TITLE.search(title):
                # Never even download a doc whose title says it lists students.
                self.add(key, kind="link", title=title, raw="", canvas_url=canvas_url, student_list_title=True, **kw)
                return
            fmt = "txt" if g.group(1) == "document" else "csv"
            raw = self.gdrive(g.group(2), fmt) if self.gdrive else None
            if raw is None:
                self.skip(title, "Google file not readable by Ben's accounts; kept as a link")
                raw = f"Google {'Doc' if fmt == 'txt' else 'Sheet'} linked from Canvas."
            elif fmt == "csv":
                raw = csv_to_text(raw)
            self.add(key, kind="link", title=title, raw=raw, canvas_url=canvas_url, source_url=url,
                     student_list_title=True, **kw)
            return
        self.add(key, kind="link", title=title, raw=f"Link: {url}" if url else "", canvas_url=canvas_url,
                 source_url=url or None, **kw)

    # -- the run ----------------------------------------------------------------------
    def run(self) -> list[dict[str, Any]]:
        """Import every student-facing item once, module items first. Returns the item records."""
        cid = self.cid
        me = self.canvas.get("/users/self")
        self.pages = {p["url"]: p for p in self.canvas.get(f"/courses/{cid}/pages", {"per_page": 100})}
        assignments = self.canvas.get(f"/courses/{cid}/assignments", {"per_page": 100})
        self.by_id = {int(a["id"]): a for a in assignments}
        self.by_quiz = {int(a["quiz_id"]): a for a in assignments if a.get("quiz_id")}
        modules = self.canvas.get(f"/courses/{cid}/modules",
                                  {"include[]": ["items", "content_details"], "per_page": 100})
        for m in modules:  # Word files that have a PDF twin are not imported twice
            for it in m.get("items") or []:
                t = (it.get("title") or "").strip().lower()
                if it.get("type") == "File" and t.endswith(".pdf"):
                    self.pdf_stems.add(t[:-4].strip())
        for m in sorted(modules, key=lambda m: m.get("position") or 0):
            self._import_module(m)
        self._import_loose_items(assignments)
        self._import_syllabus()
        self._import_announcements(me)
        return list(self.items.values())

    def _import_module(self, m: dict[str, Any]) -> None:
        """Every published item of one published module, with its module and class section."""
        mname = normalize(m.get("name") or "")
        if not m.get("published", True):
            self.skip(mname, "unpublished module")
            return
        items = m.get("items")
        if items is None:
            items = self.canvas.get(f"/courses/{self.cid}/modules/{m['id']}/items",
                                    {"include[]": ["content_details"], "per_page": 100})
        section = None
        for it in items:
            typ, title = it.get("type"), it.get("title") or ""
            if typ == "SubHeader":
                section = normalize(title) if it.get("published", True) else section
                continue
            if not it.get("published", True):
                self.skip(title, "unpublished module item")
                continue
            kw = {"module": mname, "position": it.get("position"), "section": section}
            try:
                self._import_module_item(typ, title, it, kw)
            except CanvasError as exc:
                self.skip(title, f"Canvas error: {exc}")

    def _import_module_item(self, typ: str | None, title: str, it: dict[str, Any], kw: dict[str, Any]) -> None:
        """Dispatch one module item by its Canvas type. Discussions are never imported (student posts)."""
        if typ == "Page":
            self.page(it["page_url"], **kw)
        elif typ == "Assignment":
            a = self.by_id.get(int(it["content_id"]))
            if a is None:
                self.skip(title, "assignment not visible in the assignments list")
            else:
                self.assignment(a, **kw)
        elif typ == "Quiz":
            self.quiz(int(it["content_id"]), it, **kw)
        elif typ == "File":
            self.file(it, **kw)
        elif typ in ("ExternalUrl", "ExternalTool"):
            self.link(it, **kw)
        elif typ == "Discussion":
            self.skip(title, "discussion (student posts are never imported)")
        else:
            self.skip(title, f"module item type {typ}")

    def _import_loose_items(self, assignments: list[dict[str, Any]]) -> None:
        """Published pages and assignments that are not in a module."""
        for slug, meta in self.pages.items():
            if not meta.get("published", True) or meta.get("hide_from_students"):
                if f"page-{slug}" not in self.items:
                    self.skip(meta.get("title", slug), "unpublished page")
                continue
            try:
                self.page(slug, meta)
            except CanvasError as exc:
                self.skip(meta.get("title", slug), f"Canvas error: {exc}")
        for a in assignments:
            self.assignment(a)

    def _import_syllabus(self) -> None:
        course = self.canvas.get(f"/courses/{self.cid}", {"include[]": ["syllabus_body"]})
        syllabus = html_to_text(course.get("syllabus_body"))
        if syllabus:
            self.add("syllabus", kind="syllabus", title=f"Syllabus: {course.get('name') or COURSE_LABELS[self.course]}",
                     raw=syllabus, canvas_url=f"{BASE}/courses/{self.cid}/assignments/syllabus",
                     updated_at=course.get("updated_at"))

    def _import_announcements(self, me: dict[str, Any]) -> None:
        """Posted announcements written by Ben (the token's own user); anyone else's are counted, not read."""
        topics = self.canvas.get(f"/courses/{self.cid}/discussion_topics",
                                 {"only_announcements": "true", "per_page": 100})
        for t in topics:
            author = (t.get("author") or {}).get("id") or t.get("user_id")
            if str(author) != str(me.get("id")):
                self.report.counts["announcements_not_by_ben"] += 1
                continue
            if t.get("published") is False or t.get("delayed_post_at") and not t.get("posted_at"):
                self.skip(t.get("title", ""), "announcement not posted yet")
                continue
            self.add(f"announcement-{t['id']}", kind="announcement", title=t.get("title") or "Announcement",
                     raw=html_to_text(t.get("message")), canvas_url=t.get("html_url"),
                     updated_at=t.get("posted_at") or t.get("updated_at"),
                     header=[f"Posted: {fmt_due(t.get('posted_at'))}"] if t.get("posted_at") else None)


def import_course(course: str, archive: Path, canvas: CanvasClient, cleaner: Cleaner,
                  gdrive: Callable[[str, str], str | None] | None = gog_export,
                  extract: Callable[[Path], str] = extract_file_text,
                  log: Callable[[str], None] = print) -> tuple[list[dict[str, Any]], Report]:
    """Import one course, then write items.json and a counts-only report.json next to its cache."""
    out_dir = common.build_dir(archive) / "canvas" / course
    imp = Importer(course, canvas, cleaner, out_dir / "cache", gdrive, extract, log)
    items = imp.run()
    common.write_json(out_dir / "items.json", items)
    report = {
        "course": course,
        "items": len(items),
        "by_kind": dict(Counter(i["kind"] + ("_stub" if i["stub"] else "") for i in items)),
        "chunks": sum(len(i["chunks"]) for i in items),
        "stubs": imp.report.stubs,
        "skipped": imp.report.skipped,
        "filters": dict(cleaner.counts),
        "cache_hits": imp.report.counts.get("cache_hits", 0),
        "announcements_not_by_ben": imp.report.counts.get("announcements_not_by_ben", 0),
    }
    common.write_json(out_dir / "report.json", report)
    return items, imp.report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Import student-facing course info from Canvas (read-only)")
    ap.add_argument("--archive", help="Lecture Archive folder (default ~/Lecture Archive or $LECTURE_ARCHIVE)")
    ap.add_argument("--course", choices=sorted(COURSES), action="append", help="course code (default: both)")
    ap.add_argument("--no-gdrive", action="store_true", help="do not export linked Google Docs and Sheets")
    a = ap.parse_args(argv)
    archive = common.archive_dir(a.archive)
    canvas = CanvasClient(read_token(archive))
    cleaner = Cleaner.from_archive(archive)
    for course in a.course or sorted(COURSES):
        before = dict(cleaner.counts)
        items, rep = import_course(course, archive, canvas, cleaner, None if a.no_gdrive else gog_export)
        kinds = Counter(i["kind"] + ("_stub" if i["stub"] else "") for i in items)
        print(f"{course}: {len(items)} items, {sum(len(i['chunks']) for i in items)} chunks; "
              + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())))
        for s in rep.stubs:
            print(f"  stub: {s['title']} ({s['why']})")
        print(f"  skipped: {len(rep.skipped)}")
        for s in rep.skipped:
            print(f"    {s['title'][:90]}: {s['why']}")
        delta = {k: v - before.get(k, 0) for k, v in cleaner.counts.items()}
        print(f"  filters: {delta}")
    print(f"de-identification rules: {dict(cleaner.rules)}")
    print(f"Canvas requests: {canvas.requests}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

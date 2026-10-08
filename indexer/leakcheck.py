"""Roster leak check: the gate before anything leaves the local build machine.

Rosters are read from `~/Lecture Archive/_private/rosters/*.csv` into memory
only. This module never prints, logs, returns, or writes a name: callers get
hit counts and the record ids / object paths where hits were found.

Two levels, because slide text is deliberately not altered (docs/SPEC.md,
"Everyone else named, too") and a student's surname alone often collides with
a cited author:

- strong (every text object, every field): a roster full name ("first last",
  preferred or first given name), an Andrew ID, or an email address.
- strict (de-identified transcript text, where every person was already
  replaced): also any single roster first name or surname, capitalized, that
  is not an ordinary lowercase English word and is not one of Ben's own names.

Reviewed exceptions (added Oct 5). A roster word can also be a product, a
place, or an ordinary word ("Duolingo Max", "IBM Watson", "Austin, Texas").
After a person has looked at a hit and decided it is not a name, it goes in a
PRIVATE allowlist, `~/Lecture Archive/_private/leak_allowlist.json` (it holds
roster words, so it is never committed):

    [{"record": "70445-s02-015", "field": "transcript", "token": "Watson",
      "reason": "IBM Watson, the product"}]

An entry allows only that single-word strict hit, in that one record and
field. It never allows a full name, an Andrew ID, or an email, never applies to
another record, and a record id or token that changes makes the hit fail again.
Allowed hits are counted and reported (counts only) but do not block.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from indexer.roster import blank_institution_terms, person, read_people, roster_files

BEN = {"ben", "benjamin", "collier"}
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*[A-Za-z]|[A-Za-z]")
TOKEN_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+|[A-Za-z0-9][A-Za-z0-9._\-]*")


class RosterMissing(RuntimeError):
    pass


def _english_words() -> set[str]:
    words = Path("/usr/share/dict/words")
    if not words.exists():
        return set()
    return {w for w in words.read_text(errors="ignore").split() if w.islower()}


@dataclass
class Hits:
    total: int = 0
    where: dict[str, int] = field(default_factory=dict)  # "<object>#<record id>.<field>" -> count
    allowed: int = 0  # strict hits a person reviewed and allowlisted (not names); never block
    allowed_where: dict[str, int] = field(default_factory=dict)

    def add(self, where: str, n: int) -> None:
        if n:
            self.total += n
            self.where[where] = self.where.get(where, 0) + n

    def add_allowed(self, where: str, n: int) -> None:
        if n:
            self.allowed += n
            self.allowed_where[where] = self.allowed_where.get(where, 0) + n

    def summary(self, limit: int = 20) -> str:
        """Locations and counts only. Never the matched text."""
        note = (f" ({self.allowed} reviewed non-name hit(s) allowed in {len(self.allowed_where)} place(s) "
                f"by the private allowlist)") if self.allowed else ""
        if not self.total:
            return "leak check: 0 roster hits" + note
        lines = [f"leak check: {self.total} roster hit(s) in {len(self.where)} place(s)" + note]
        for k, n in sorted(self.where.items())[:limit]:
            lines.append(f"  {k}: {n}")
        if len(self.where) > limit:
            lines.append(f"  ... and {len(self.where) - limit} more")
        return "\n".join(lines)


# Fields of content/info_index.json (Canvas course info) that get the strict check.
INFO_STRICT_FIELDS = ("title", "text")

ALLOWLIST_NAME = "leak_allowlist.json"  # in _private/, next to rosters/


def load_allowlist(path: Path) -> dict[tuple[str, str], set[str]]:
    """{(record id, field): {lowercase token}} from the private allowlist; {} when there is none.

    Every entry needs record, field, token and a non-empty reason, or the file is refused.
    """
    if not Path(path).exists():
        return {}
    data = json.loads(Path(path).read_text())
    out: dict[tuple[str, str], set[str]] = {}
    for e in data:
        rec, fld, tok, why = (str(e.get(k) or "").strip() for k in ("record", "field", "token", "reason"))
        if not (rec and fld and tok and why) or " " in tok:
            raise ValueError("leak allowlist entries need record, field, a single-word token, and a reason")
        out.setdefault((rec, fld), set()).add(tok.lower())
    return out


class RosterChecker:
    def __init__(self, rows: list[dict[str, str]], english: set[str] | None = None,
                 allow: dict[tuple[str, str], set[str]] | None = None) -> None:
        self.allow = allow or {}
        english = english if english is not None else _english_words()
        full: set[str] = set()
        self._ids: set[str] = set()
        self._singles: set[str] = set()
        for r in rows:
            p = person(r)  # any roster header layout (indexer/roster.py)
            if p is None:
                continue
            first, last = p["first"], p["last"]
            if first and last:
                full.add(f"{first} {last}".lower())
                full.add(f"{first.split()[0]} {last}".lower())
            for tok in re.split(r"[\s\-]+", f"{first} {last}".lower()):
                tok = tok.strip(".'")
                if len(tok) >= 3 and tok not in english and tok not in BEN:
                    self._singles.add(tok)
            aid = p["andrew_id"]
            if len(aid) >= 3:
                self._ids.add(aid)
            email = p["email"]
            if email:
                self._ids.add(email)
                self._ids.add(email.split("@")[0])
        parts = sorted((re.escape(n).replace(r"\ ", r"\s+") for n in full if n.strip()), key=len, reverse=True)
        self._full = re.compile(r"(?<![A-Za-z])(?:" + "|".join(parts) + r")(?![A-Za-z])", re.I) if parts else None
        self.size = len(rows)

    @classmethod
    def from_dir(cls, path: Path, english: set[str] | None = None,
                 allowlist: Path | None = None) -> "RosterChecker":
        """Rosters from `path`; the reviewed allowlist from `path/../leak_allowlist.json` unless given."""
        if not roster_files(path):
            raise RosterMissing(f"No roster CSVs in {path}; the leak check cannot run")
        rows = read_people(path)
        allowlist = allowlist if allowlist is not None else Path(path).parent / ALLOWLIST_NAME
        return cls(rows, english, load_allowlist(allowlist))

    def is_single(self, token: str) -> bool:
        """True when a capitalized word is a roster first name or surname (the strict list)."""
        return re.sub(r"'s$", "", (token or "").lower()).strip("'") in self._singles

    # ------------------------------------------------------------ counting

    def strong(self, text: str) -> int:
        if not text:
            return 0
        n = len(self._full.findall(text)) if self._full else 0
        for tok in TOKEN_RE.findall(text):
            low = tok.lower().rstrip(".")
            if low in self._ids:
                n += 1
        return n

    def strict(self, text: str) -> int:
        return self.strict_allowing(text)[0]

    def strict_allowing(self, text: str, allow: set[str] | frozenset = frozenset()) -> tuple[int, int]:
        """(hits, allowed): single-word hits whose token is in `allow` are counted as allowed instead.

        Strong hits (full names, Andrew IDs, emails) are never allowed.
        """
        n, ok = self.strong(text), 0
        # "Andrew ID" and "andrew.cmu.edu" are CMU's account system, not a student named Andrew
        # (indexer/roster.py). Full names, Andrew IDs and emails were counted above on the whole text.
        for tok in WORD_RE.findall(blank_institution_terms(text or "")):
            low = re.sub(r"'s$", "", tok.lower()).strip("'")  # possessive: "Name's" is a hit too
            if tok[0].isupper() and low in self._singles:
                if low in allow:
                    ok += 1
                else:
                    n += 1
        return n, ok

    # ------------------------------------------------------------ objects

    def check_index(self, data: Any, name: str = "content/index.json", hits: Hits | None = None,
                    strict_fields: tuple[str, ...] = ("transcript",)) -> Hits:
        """Record-aware check: strict on transcripts (`strict_fields`), strong on every other string."""
        hits = hits or Hits()
        records = data.get("records", []) if isinstance(data, dict) else data
        if isinstance(data, dict):
            meta = {k: v for k, v in data.items() if k != "records"}
            hits.add(f"{name}#meta", self.strong(json.dumps(meta, ensure_ascii=False)))
        for i, rec in enumerate(records or []):
            rid = str(rec.get("id", i)) if isinstance(rec, dict) else str(i)
            if not isinstance(rec, dict):
                hits.add(f"{name}#{rid}", self.strong(json.dumps(rec, ensure_ascii=False)))
                continue
            for key, value in rec.items():
                text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                if key in strict_fields:
                    count, ok = self.strict_allowing(text, self.allow.get((rid, key), frozenset()))
                    hits.add_allowed(f"{name}#{rid}.{key}", ok)
                else:
                    count = self.strong(text)
                hits.add(f"{name}#{rid}.{key}", count)
        return hits

    def check_object(self, path: str, data: bytes, hits: Hits | None = None) -> Hits:
        hits = hits or Hits()
        text = data.decode("utf-8", errors="replace")
        if path.endswith("content/index.json"):
            try:
                return self.check_index(json.loads(text), path, hits)
            except ValueError:
                pass
        if path.endswith("content/info_index.json"):
            try:  # Canvas course info: de-identified prose, so titles and text get the strict check
                return self.check_index(json.loads(text), path, hits, INFO_STRICT_FIELDS)
            except ValueError:
                pass
        hits.add(path, self.strong(text))
        return hits

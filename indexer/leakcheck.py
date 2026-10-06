"""Roster leak check: the gate before anything leaves Ben's Mac.

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
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

FIRST_COLS = ("Preferred/First Name", "Preferred Name", "First Name")
LAST_COLS = ("Last Name",)
ID_COLS = ("Andrew ID",)
EMAIL_COLS = ("Email",)
BEN = {"ben", "benjamin", "collier"}
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*[A-Za-z]|[A-Za-z]")
TOKEN_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+|[A-Za-z0-9][A-Za-z0-9._\-]*")


class RosterMissing(RuntimeError):
    pass


def _first(row: dict[str, str], cols: Iterable[str]) -> str:
    for c in cols:
        v = (row.get(c) or "").strip()
        if v:
            return v
    return ""


def _english_words() -> set[str]:
    words = Path("/usr/share/dict/words")
    if not words.exists():
        return set()
    return {w for w in words.read_text(errors="ignore").split() if w.islower()}


@dataclass
class Hits:
    total: int = 0
    where: dict[str, int] = field(default_factory=dict)  # "<object>#<record id>.<field>" -> count

    def add(self, where: str, n: int) -> None:
        if n:
            self.total += n
            self.where[where] = self.where.get(where, 0) + n

    def summary(self, limit: int = 20) -> str:
        """Locations and counts only. Never the matched text."""
        if not self.total:
            return "leak check: 0 roster hits"
        lines = [f"leak check: {self.total} roster hit(s) in {len(self.where)} place(s)"]
        for k, n in sorted(self.where.items())[:limit]:
            lines.append(f"  {k}: {n}")
        if len(self.where) > limit:
            lines.append(f"  ... and {len(self.where) - limit} more")
        return "\n".join(lines)


class RosterChecker:
    def __init__(self, rows: list[dict[str, str]], english: set[str] | None = None) -> None:
        english = english if english is not None else _english_words()
        full: set[str] = set()
        self._ids: set[str] = set()
        self._singles: set[str] = set()
        for r in rows:
            first, last = _first(r, FIRST_COLS), _first(r, LAST_COLS)
            if first and last:
                full.add(f"{first} {last}".lower())
                full.add(f"{first.split()[0]} {last}".lower())
            for tok in re.split(r"[\s\-]+", f"{first} {last}".lower()):
                tok = tok.strip(".'")
                if len(tok) >= 3 and tok not in english and tok not in BEN:
                    self._singles.add(tok)
            aid = _first(r, ID_COLS).lower()
            if len(aid) >= 3:
                self._ids.add(aid)
            email = _first(r, EMAIL_COLS).lower()
            if email:
                self._ids.add(email)
                self._ids.add(email.split("@")[0])
        parts = sorted((re.escape(n).replace(r"\ ", r"\s+") for n in full if n.strip()), key=len, reverse=True)
        self._full = re.compile(r"(?<![A-Za-z])(?:" + "|".join(parts) + r")(?![A-Za-z])", re.I) if parts else None
        self.size = len(rows)

    @classmethod
    def from_dir(cls, path: Path, english: set[str] | None = None) -> "RosterChecker":
        files = sorted(Path(path).glob("*.csv")) if Path(path).is_dir() else []
        if not files:
            raise RosterMissing(f"No roster CSVs in {path}; the leak check cannot run")
        rows: list[dict[str, str]] = []
        for p in files:
            with open(p, newline="", encoding="utf-8-sig") as fh:
                rows.extend(csv.DictReader(fh))
        return cls(rows, english)

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
        n = self.strong(text)
        for tok in WORD_RE.findall(text or ""):
            if tok[0].isupper() and tok.lower().strip("'") in self._singles:
                n += 1
        return n

    # ------------------------------------------------------------ objects

    def check_index(self, data: Any, name: str = "content/index.json", hits: Hits | None = None) -> Hits:
        """Record-aware check: strict on transcripts, strong on every other string."""
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
                count = self.strict(text) if key == "transcript" else self.strong(text)
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
        hits.add(path, self.strong(text))
        return hits

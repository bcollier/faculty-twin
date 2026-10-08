"""One roster loader for every stage that needs the scrub list.

The rosters in `~/Lecture Archive/_private/rosters/` come from different places
and do not share a header. The CMU course roster has `Last Name`,
`Preferred/First Name`, `Andrew ID` and `Email`; a Canvas group export has
`name` ("Last, First") and `login_id` (an email); a Canvas gradebook export has
`Student` and `SIS Login ID`. Before Oct 5 each stage read only the course
roster columns, so a file with other headers was silently skipped.

`read_people(folder)` reads every CSV and returns one canonical dict per
person: `{"first", "last", "andrew_id", "email"}`. `person(row)` does the same
for one CSV row; it is idempotent (a canonical dict maps to itself), so callers
can pass raw rows or canonical ones.

Header matching ignores case, spaces, underscores and hyphens. Recognized:

- first name: Preferred/First Name, Preferred Name, First Name, first,
  preferred, given name
- last name: Last Name, last, surname, family name
- full name, used only when first and last are both missing: name, Student,
  Student Name, Full Name, Sortable Name. "Last, First" when it has a comma,
  otherwise the last word is the surname.
- Andrew ID: Andrew ID, login_id, Login ID, SIS Login ID, username. A value
  with "@" in it is treated as an email.
- email: Email, Email Address, E-mail

Placeholder rows that Canvas adds ("Points Possible", "Test Student") are
skipped, so their words never join the scrub list. Numeric ids (SIS User ID)
are not read: a number is not a name and would match ordinary text.

Two matching helpers live here too, because every roster matcher needs them:
`names_pattern(names)` (one regex for a set of full names) and
`lowercase_dictionary_words()` (ordinary English words, which are never treated
as a name on their own).

This module never prints, logs, or returns anything but the parsed rows to its
caller.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable
from pathlib import Path

FIRST_HEADERS = ("preferred/first name", "preferred name", "first name", "first", "preferred", "given name")
LAST_HEADERS = ("last name", "last", "surname", "family name")
FULL_HEADERS = ("name", "student", "student name", "full name", "sortable name")
ID_HEADERS = ("andrew id", "login id", "sis login id", "username")
EMAIL_HEADERS = ("email", "email address", "e mail")

PLACEHOLDER_NAMES = {"points possible", "test student", "student test", "student, test"}


def _key(header: str) -> str:
    return re.sub(r"[\s_\-]+", " ", (header or "").replace("﻿", "").strip().lower())


def _pick(row: dict[str, str], headers: Iterable[str]) -> str:
    for h in headers:
        v = row.get(h, "")
        if v:
            return v
    return ""


def split_full_name(full: str) -> tuple[str, str]:
    """("first", "last") from "Last, First Middle" or "First Middle Last"."""
    full = re.sub(r"\s+", " ", (full or "").strip())
    if not full:
        return "", ""
    if "," in full:
        last, first = (p.strip() for p in full.split(",", 1))
        return first, last
    parts = full.split(" ")
    if len(parts) == 1:
        return parts[0], ""
    return " ".join(parts[:-1]), parts[-1]


def person(row: dict[str, str]) -> dict[str, str] | None:
    """Canonical {first, last, andrew_id, email} for one roster row, or None when it names nobody."""
    norm: dict[str, str] = {}
    for k, v in (row or {}).items():
        key = _key(k or "")
        val = re.sub(r"\s+", " ", (v or "").strip()) if isinstance(v, str) else ""
        if key and val and key not in norm:
            norm[key] = val
    first, last = _pick(norm, FIRST_HEADERS), _pick(norm, LAST_HEADERS)
    if not first and not last:
        full = _pick(norm, FULL_HEADERS)
        if full.lower() in PLACEHOLDER_NAMES:
            return None
        first, last = split_full_name(full)
    if f"{first} {last}".strip().lower() in PLACEHOLDER_NAMES:
        return None
    andrew_id, email = _pick(norm, ID_HEADERS), _pick(norm, EMAIL_HEADERS)
    if "@" in andrew_id:
        email = email or andrew_id
        andrew_id = andrew_id.split("@", 1)[0]
    if not (first or last):
        return None
    return {"first": first, "last": last, "andrew_id": andrew_id.lower(), "email": email.lower()}


def read_csv_people(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return [p for p in (person(r) for r in csv.DictReader(fh)) if p]


def roster_files(roster_dir: Path) -> list[Path]:
    root = Path(roster_dir)
    return sorted(root.glob("*.csv")) if root.is_dir() else []


def read_people(roster_dir: Path) -> list[dict[str, str]]:
    """Every person in every CSV in `roster_dir`, canonical. [] when the folder is missing."""
    people: list[dict[str, str]] = []
    for path in roster_files(roster_dir):
        people.extend(read_csv_people(path))
    return people


# ---------------------------------------------------------------- matching helpers

DICTIONARY = Path("/usr/share/dict/words")


def lowercase_dictionary_words(path: Path = DICTIONARY) -> set[str]:
    """The lowercase entries of the system word list: ordinary words, not proper nouns.

    A roster first name or surname that is also an ordinary word ("Green", "Will") is
    only matched as part of a full name. Empty when the word list is missing.
    """
    if not path.exists():
        return set()
    return {w for w in path.read_text(errors="ignore").split() if w.islower()}


def names_pattern(names: Iterable[str], flags: int = re.IGNORECASE) -> re.Pattern[str] | None:
    """One regex that matches any of `names` as a whole word, longest first; None for no names.

    A space inside a name matches any run of whitespace, so a name wrapped across lines
    still matches.
    """
    parts = sorted((re.escape(n).replace(r"\ ", r"\s+") for n in names if n.strip()), key=len, reverse=True)
    if not parts:
        return None
    return re.compile(r"(?<![A-Za-z])(?:" + "|".join(parts) + r")(?![A-Za-z])", flags)

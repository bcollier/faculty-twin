"""Tests for indexer/roster.py: one loader for every roster header layout.

Every name here is INVENTED for the test. No real roster is read.

Run: uv run --no-project --with-requirements requirements.txt --with pytest --with nbformat \
       --with rapidfuzz --with nicknames python -m pytest tests/test_roster.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from indexer import roster  # noqa: E402
from indexer.leakcheck import RosterChecker  # noqa: E402

COURSE_ROSTER = (
    '"Semester","Course","Last Name","Preferred/First Name","MI","Andrew ID","Email","Primary Advisor"\n'
    '"F26","70445","Quenwick","Zorblat","","zquenwic","zquenwic@example.edu","Advisor Person"\n'
)
# Canvas group export: "Last, First" in `name`, an email in `login_id`.
GROUPS_EXPORT = (
    "name,login_id,group_name\n"
    '"Thrandell, Ysolde",ythrande@example.edu,Team 4\n'
    '"Mirabont, Kesh Oriel",kmirabon@example.edu,Team 4\n'
)
# Canvas gradebook export: `Student` and `SIS Login ID`, plus Canvas's placeholder rows.
GRADEBOOK_EXPORT = (
    "Student,ID,SIS User ID,SIS Login ID,Section\n"
    "    Points Possible,,,,\n"
    '"Plovinski, Arvette",111,22334455,aplovins,A\n'
    '"Student, Test",999,,,A\n'
)
# A hand-made list: `Student Name` as "First Last" and a plain `Email` column.
HAND_LIST = "Student Name,Email\nDorvessa Quillane,dquillan@example.edu\n"


def write(folder: Path, name: str, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture()
def roster_dir(tmp_path: Path) -> Path:
    d = tmp_path / "rosters"
    write(d, "CourseRoster_TEST.csv", COURSE_ROSTER)
    write(d, "FT Test Teams.csv", GROUPS_EXPORT)
    write(d, "gradebook_TEST.csv", "﻿" + GRADEBOOK_EXPORT)  # Excel BOM
    write(d, "hand_list.csv", HAND_LIST)
    return d


def test_person_course_roster_columns():
    row = {"Last Name": "Quenwick", "Preferred/First Name": "Zorblat", "Andrew ID": "zquenwic",
           "Email": "zquenwic@example.edu", "Primary Advisor": "Advisor Person"}
    assert roster.person(row) == {"first": "Zorblat", "last": "Quenwick", "andrew_id": "zquenwic",
                                  "email": "zquenwic@example.edu"}


def test_person_name_and_login_id():
    p = roster.person({"name": "Thrandell, Ysolde", "login_id": "ythrande@example.edu", "group_name": "Team 4"})
    assert p == {"first": "Ysolde", "last": "Thrandell", "andrew_id": "ythrande", "email": "ythrande@example.edu"}


@pytest.mark.parametrize("header", ["Student", "Student Name", "student_name", "Full Name", "name", "NAME"])
def test_person_full_name_headers(header):
    assert roster.person({header: "Dorvessa Quillane"})["last"] == "Quillane"
    assert roster.person({header: "Quillane, Dorvessa"})["first"] == "Dorvessa"


def test_person_sis_login_id_and_email_headers():
    p = roster.person({"Student": "Plovinski, Arvette", "SIS Login ID": "aplovins", "SIS User ID": "22334455"})
    assert p["andrew_id"] == "aplovins" and p["email"] == ""
    p = roster.person({"Student Name": "Dorvessa Quillane", "Email Address": "DQuillan@Example.edu"})
    assert p["email"] == "dquillan@example.edu" and p["andrew_id"] == ""


def test_person_skips_placeholders_and_empty_rows():
    assert roster.person({"Student": "    Points Possible"}) is None
    assert roster.person({"Student": "Student, Test"}) is None
    assert roster.person({"name": "", "login_id": "x@example.edu"}) is None
    assert roster.person({"Primary Advisor": "Advisor Person"}) is None  # advisors are not students


def test_person_is_idempotent():
    p = roster.person({"name": "Mirabont, Kesh Oriel", "login_id": "kmirabon@example.edu"})
    assert p == {"first": "Kesh Oriel", "last": "Mirabont", "andrew_id": "kmirabon", "email": "kmirabon@example.edu"}
    assert roster.person(p) == p


def test_split_full_name():
    assert roster.split_full_name("Mirabont, Kesh Oriel") == ("Kesh Oriel", "Mirabont")
    assert roster.split_full_name("Kesh Oriel Mirabont") == ("Kesh Oriel", "Mirabont")
    assert roster.split_full_name("  Ysolde  ") == ("Ysolde", "")


def test_read_people_reads_every_layout(roster_dir):
    people = roster.read_people(roster_dir)
    assert sorted(p["last"] for p in people) == ["Mirabont", "Plovinski", "Quenwick", "Quillane", "Thrandell"]
    assert roster.read_people(roster_dir / "missing") == []


def test_leak_check_sees_every_layout(roster_dir):
    checker = RosterChecker.from_dir(roster_dir, english=set(), allowlist=roster_dir / "none.json")
    assert checker.strong("Thanks Ysolde Thrandell") == 1          # groups export, full name
    assert checker.strong("mail ythrande@example.edu") == 1         # groups export, login_id email
    assert checker.strong("Kesh Mirabont presented") == 1           # first given name + surname
    assert checker.strong("aplovins") == 1                          # gradebook SIS Login ID
    assert checker.strong("Dorvessa Quillane") == 1                 # hand list
    assert checker.strict("I asked Thrandell to explain") == 1      # single surname, transcripts
    assert checker.strong("Points Possible and a Test Student") == 0
    assert checker.strong("Advisor Person") == 0


def test_slide_scrubber_sees_every_layout(roster_dir):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "indexer"))
    import slides  # noqa: PLC0415

    s = slides.NameScrubber.from_dir(roster_dir)
    text, flags = s.check("Team 4: Ysolde Thrandell and ythrande@example.edu")
    assert "Thrandell" not in text and "ythrande" not in text
    assert "student_names_possible" in flags


def test_deidentify_scrub_list_sees_every_layout(roster_dir):
    pytest.importorskip("rapidfuzz")
    from indexer import deidentify as d

    people = d.read_rosters(roster_dir)
    assert len(people) == 5
    scrub = d.build_scrub_list(people, english=set())
    assert {"thrandell", "ysolde", "mirabont", "kesh", "oriel", "plovinski", "quillane"} <= scrub.exact
    assert {"ythrande", "kmirabon", "aplovins"} <= scrub.ids
    assert "points" not in scrub.exact and "possible" not in scrub.exact

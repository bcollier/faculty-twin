"""Tests for indexer/suggest_code_map.py on synthetic slides and cells (invented names only).

Run: uv run --no-project --with-requirements requirements.txt --with pytest --with scikit-learn \
       python -m pytest tests/test_suggest_code_map.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
pytest.importorskip("sklearn")

from indexer import build_index  # noqa: E402
from indexer import suggest_code_map as S  # noqa: E402
from indexer.leakcheck import RosterChecker  # noqa: E402


def slide(session, n, title, text="", flags=()):
    return {"slide_id": f"45884-s{session:02d}-{n:03d}", "course": "45884", "session": session, "slide_number": n,
            "title": title, "text": text, "notes": "", "ocr_text": "", "flags": list(flags)}


def cell(session, idx, source, md=""):
    return {"cell_id": f"45884-s{session:02d}-nb1-c{idx:03d}", "course": "45884", "session": session,
            "notebook": "demo.ipynb", "cell_index": idx, "source": source, "markdown_above": md, "flags": []}


SLIDES = {"45884": [
    slide(5, 1, "Choosing k with the elbow method", "Plot inertia against k and look for the elbow in the curve"),
    slide(5, 2, "Silhouette score", "Silhouette compares cohesion and separation for each cluster"),
    slide(5, 3, "Frames", "A frame holds slots and fillers"),
    slide(5, 4, "Thanks for coming", "Questions"),
    slide(5, 5, "Team list", "Quenwick team", flags=["student_names_possible"]),
    slide(6, 1, "Elbow method again", "inertia elbow k"),  # no notebook in session 6: not a target
]}
CELLS = {"45884": [
    cell(5, 3, "inertias = []\nfor k in range(1, 10):\n    km = KMeans(n_clusters=k).fit(X)\n    inertias.append(km.inertia_)\nplot_elbow(inertias)",
         "## Elbow method: inertia for each k"),
    cell(5, 4, "plt.plot(range(1, 10), inertias)  # the elbow curve", "Plot the elbow"),
    cell(5, 5, "score = silhouette_score(X, labels)  # cohesion vs separation", "## Silhouette"),
    cell(5, 6, "class Frame:\n    def __init__(self, name):\n        self.slots = {}", ""),
    cell(4, 2, "inertia_by_k = {k: KMeans(k).fit(X).inertia_ for k in range(2, 8)}  # elbow", "Lab: elbow"),
    cell(5, 7, "# Zorblat's version of the silhouette plot\nsilhouette_score(X, labels)", ""),
]}
ROSTER = [{"Last Name": "Quenwick", "Preferred/First Name": "Zorblat", "Andrew ID": "zquenwic", "Email": "z@example.edu"}]


@pytest.fixture()
def result():
    checker = RosterChecker(ROSTER, english=set())
    return S.suggest(SLIDES, CELLS, checker, threshold=0.15)


def by_slide(rows):
    out = {}
    for r in rows:
        out.setdefault(r["slide_id"], []).append(r)
    return out


def test_pairs_match_topics_and_cap_per_slide(result):
    rows, _ = result
    m = by_slide(rows)
    assert m["45884-s05-001"][0]["cell_id"] in {"45884-s05-nb1-c003", "45884-s05-nb1-c004"}
    assert len(m["45884-s05-001"]) <= S.MAX_PER_SLIDE
    assert [r["cell_id"] for r in m["45884-s05-002"]][0] == "45884-s05-nb1-c005"
    assert "45884-s05-004" not in m  # nothing relates to the closing slide
    assert all(a["score"] >= b["score"] for rs in m.values() for a, b in zip(rs, rs[1:]))


def test_same_session_preferred(result):
    rows, _ = result
    elbow = [r["cell_id"] for r in by_slide(rows)["45884-s05-001"]]
    assert "45884-s04-nb1-c002" not in elbow  # the lab cell is as relevant but from another session


def test_class_named_like_title_gets_bonus(result):
    rows, _ = result
    assert by_slide(rows)["45884-s05-003"][0]["cell_id"] == "45884-s05-nb1-c006"
    assert S.defines_title("class Frame:\n    pass", "Frames", set())
    assert not S.defines_title("frame = 1", "Frames", set())


def test_name_check_and_flags_leave_things_out(result):
    rows, stats = result
    assert all(r["cell_id"] != "45884-s05-nb1-c007" for r in rows)  # a roster first name in a comment
    assert all(r["slide_id"] not in {"45884-s05-005", "45884-s06-001"} for r in rows)
    assert stats["cells_left_out_names"] == 1 and stats["slides_left_out"] == 1


def test_code_map_json_is_what_build_index_reads(tmp_path, result):
    rows, _ = result
    data = S.code_map_json(rows, 0.15)
    assert data["_source"] == "suggested by similarity; Ben reviews"
    path = tmp_path / "code_map.json"
    path.write_text(__import__("json").dumps(data))
    loaded = build_index._load_code_map(path)
    assert not any(k.startswith("_") for k in loaded)
    assert loaded["45884-s05-002"][0] == "45884-s05-nb1-c005"
    assert set(data["_scores"]) == set(loaded)


def test_review_markdown_and_first_line(result):
    rows, stats = result
    md = S.review_markdown(rows, 0.15, stats, near=[])
    assert "| Slide | Slide title | Cell | First code line | Score |" in md
    assert "45884-s05-nb1-c005" in md and "Zorblat" not in md and "Quenwick" not in md
    assert S.first_code_line("import os\n# setup\n!pip install x\nvalue = compute(a|b)") == "value = compute(a|b)"
    assert S.stem("retrieval") == S.stem("retrieve") == "retriev"

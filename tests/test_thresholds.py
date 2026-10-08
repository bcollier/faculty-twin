"""Answer thresholds as Settings (docs/SPEC.md, Settings page, "Answer thresholds").

The slide threshold's code default is Ben's hand-chosen value in app/retrieval.py;
these tests only read it. Ranking and embedding here are TEST FAKES; segment
selection is Ben's real `select_segments`, so the covered/not-covered flip is
the real cutoff at the overridden value.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from test_api import TEST_FAKE_embedder as FRUIT_EMBEDDER
from test_course_info import FakeModel, with_info  # noqa: F401  (pytest fixture)
from test_course_info import TEST_FAKE_embedder as INFO_EMBEDDER

from app import config, course_info, retrieval, settings_store, supa, thresholds
from app.main import app, get_completer, get_embedder, get_retriever

ROOT = Path(__file__).resolve().parents[1]
BEN_VALUE = 0.52  # Ben's NOT_COVERED_THRESHOLD; if he changes it by hand, update this number.


# ---------------------------------------------------------------- retrieval.py is Ben's and untouched

def test_bens_constant_is_still_his_value():
    assert retrieval.NOT_COVERED_THRESHOLD == BEN_VALUE


def test_retrieval_py_unchanged_against_main():
    """Guard for agent branches: this feature must not edit app/retrieval.py."""
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    base = subprocess.run(["git", "merge-base", "HEAD", "origin/main"], cwd=ROOT, capture_output=True, text=True)
    if base.returncode != 0:
        pytest.skip("origin/main is not available")
    diff = subprocess.run(
        ["git", "diff", "--quiet", base.stdout.strip(), "--", "app/retrieval.py"], cwd=ROOT, capture_output=True
    )
    assert diff.returncode == 0, "app/retrieval.py differs from main; only Ben edits it"


def test_override_never_writes_to_retrieval_module():
    settings_store.put({"slide_threshold": 0.7})
    assert thresholds.slide_threshold() == 0.7
    assert retrieval.NOT_COVERED_THRESHOLD == BEN_VALUE


# ---------------------------------------------------------------- precedence

def test_slide_threshold_precedence():
    assert thresholds.slide_effective() == (BEN_VALUE, "code")
    assert get_retriever().threshold == BEN_VALUE
    settings_store.put({"slide_threshold": 0.6})
    assert thresholds.slide_effective() == (0.6, "settings")
    assert get_retriever().threshold == 0.6  # read per request, not frozen at import
    settings_store.put({"slide_threshold": None})
    assert thresholds.slide_effective() == (BEN_VALUE, "code")


def test_info_threshold_precedence(monkeypatch):
    assert thresholds.info_effective() == (0.55, "code")
    monkeypatch.setenv("INFO_THRESHOLD", "0.62")
    assert thresholds.info_effective() == (0.62, "env")
    settings_store.put({"info_threshold": 0.7})
    assert thresholds.info_effective() == (0.7, "settings")
    assert course_info.threshold() == 0.7
    settings_store.put({"info_threshold": None})
    assert course_info.threshold() == 0.62
    monkeypatch.setenv("INFO_THRESHOLD", "not a number")
    assert thresholds.info_effective() == (0.55, "code")


@pytest.mark.parametrize("bad", ["abc", 0.1, 1.5, True, float("nan")])
def test_unusable_stored_override_is_ignored(bad):
    settings_store.put({"slide_threshold": bad, "info_threshold": bad})
    assert thresholds.slide_effective() == (BEN_VALUE, "code")
    assert thresholds.info_effective() == (0.55, "code")


# ---------------------------------------------------------------- the 30-second cache

class FakeClock:
    now = 1000.0

    @classmethod
    def monotonic(cls) -> float:
        return cls.now


def test_settings_cache_refreshes_within_30_seconds(monkeypatch):
    rows = {"slide_threshold": 0.6}
    monkeypatch.setattr(config, "supabase_configured", lambda: True)
    monkeypatch.setattr(supa, "select", lambda table, params: [{"key": k, "value": v} for k, v in rows.items()])
    monkeypatch.setattr(settings_store, "time", FakeClock)
    FakeClock.now = 1000.0
    assert thresholds.slide_threshold() == 0.6
    rows["slide_threshold"] = 0.7  # another instance saved a new value
    FakeClock.now += 10
    assert thresholds.slide_threshold() == 0.6  # still cached
    FakeClock.now += 25
    assert thresholds.slide_threshold() == 0.7
    assert get_retriever().threshold == 0.7


# ---------------------------------------------------------------- the Settings routes

def test_get_thresholds_view(admin):
    body = admin.get("/api/admin/thresholds").json()
    assert body["slide"] == {"value": BEN_VALUE, "default": BEN_VALUE, "source": "code", "override": None}
    assert body["info"]["value"] == 0.55 and body["info"]["source"] == "code"
    assert (body["min"], body["max"], body["history"]) == (0.3, 0.9, [])


@pytest.mark.parametrize("value", [0.29, 0.91, -1, 2, "abc"])
def test_put_rejects_out_of_range(admin, value):
    r = admin.put("/api/admin/thresholds", json={"slide_threshold": value})
    assert r.status_code == 400
    assert thresholds.slide_effective() == (BEN_VALUE, "code")


def test_put_bounds_are_inclusive_and_rounded(admin):
    assert admin.put("/api/admin/thresholds", json={"slide_threshold": 0.30}).json()["slide"]["value"] == 0.3
    assert admin.put("/api/admin/thresholds", json={"info_threshold": 0.90}).json()["info"]["value"] == 0.9
    body = admin.put("/api/admin/thresholds", json={"slide_threshold": 0.56789}).json()
    assert body["slide"] == {"value": 0.568, "default": BEN_VALUE, "source": "settings", "override": 0.568}
    assert admin.put("/api/admin/thresholds", json={}).status_code == 400


def test_history_records_each_change_newest_first(admin, monkeypatch):
    monkeypatch.setenv("INFO_THRESHOLD", "0.6")
    admin.put("/api/admin/thresholds", json={"slide_threshold": 0.55})
    admin.put("/api/admin/thresholds", json={"slide_threshold": 0.55})  # no change, no entry
    admin.put("/api/admin/thresholds", json={"info_threshold": 0.65})
    body = admin.put("/api/admin/thresholds", json={"slide_threshold": None}).json()  # reset
    hist = body["history"]
    assert [(h["setting"], h["old"], h["new"], h["source"]) for h in hist] == [
        ("slide_threshold", 0.55, BEN_VALUE, "code"),
        ("info_threshold", 0.6, 0.65, "settings"),
        ("slide_threshold", BEN_VALUE, 0.55, "settings"),
    ]
    assert all(h["who"] == "admin" and h["at"].endswith("Z") for h in hist)
    assert body["slide"]["source"] == "code" and body["info"]["source"] == "settings"


def test_history_keeps_the_last_twenty(admin):
    for i in range(25):
        admin.put("/api/admin/thresholds", json={"slide_threshold": 0.4 + i / 100})
    hist = admin.get("/api/admin/thresholds").json()["history"]
    assert len(hist) == 20 and hist[0]["new"] == 0.64


def test_thresholds_are_admin_only(client, student):
    assert student.get("/api/admin/thresholds").status_code == 401
    assert student.put("/api/admin/thresholds", json={"slide_threshold": 0.6}).status_code == 401
    client.cookies.clear()
    assert client.get("/api/admin/thresholds").status_code == 401
    assert thresholds.slide_effective() == (BEN_VALUE, "code")


def test_cross_site_put_is_refused(admin):
    r = admin.put("/api/admin/thresholds", json={"slide_threshold": 0.6}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert thresholds.slide_effective() == (BEN_VALUE, "code")
    r = admin.put("/api/admin/thresholds", json={"slide_threshold": 0.6}, headers={"Origin": "http://testserver"})
    assert r.status_code == 200 and r.json()["slide"]["value"] == 0.6


# ---------------------------------------------------------------- covered flips at the overridden value

def TEST_FAKE_rank(question_vec, matrix):
    """TEST FAKE: every row scores 0.60, no similarity math."""
    return [(i, 0.60) for i in range(matrix.shape[0])]


def _ask(client, question):
    r = client.post("/api/ask", json={"question": question})
    assert r.status_code == 200, r.text
    return r.json()


def test_slide_answer_flips_at_the_overridden_threshold(student, monkeypatch):
    # get_retriever stays real: Ben's select_segments with the effective threshold; only rank is faked.
    monkeypatch.setattr(retrieval, "rank", TEST_FAKE_rank)
    app.dependency_overrides[get_embedder] = lambda: FRUIT_EMBEDDER
    app.dependency_overrides[get_completer] = lambda: FakeModel()
    assert _ask(student, "what is an apple")["covered"] is True  # 0.60 >= 0.52
    settings_store.put({"slide_threshold": 0.65})
    assert _ask(student, "what is an apple")["covered"] is False  # 0.60 < 0.65
    settings_store.put({"slide_threshold": 0.60})
    assert _ask(student, "what is an apple")["covered"] is True  # at the threshold counts


def test_info_answer_flips_at_the_overridden_threshold(with_info, student, monkeypatch):  # noqa: F811
    monkeypatch.setenv("INFO_THRESHOLD", "0.9")
    app.dependency_overrides[get_embedder] = lambda: INFO_EMBEDDER
    app.dependency_overrides[get_completer] = lambda: FakeModel()
    # "Is the lab required?" scores 0.714 on the lab chunk and 0 on every slide.
    assert _ask(student, "Is the lab required?").get("kind") != "course_info"  # env 0.9
    settings_store.put({"info_threshold": 0.70})
    assert _ask(student, "Is the lab required?")["kind"] == "course_info"
    settings_store.put({"info_threshold": 0.75})
    assert _ask(student, "Is the lab required?").get("kind") != "course_info"


# ---------------------------------------------------------------- the course-info margin (added Oct 8)

def test_info_margin_precedence(monkeypatch):
    assert thresholds.info_margin_effective() == (0.05, "code")
    assert course_info.margin() == 0.05
    monkeypatch.setenv("INFO_MARGIN", "0.08")
    assert thresholds.info_margin_effective() == (0.08, "env")
    monkeypatch.setenv("INFO_MARGIN", "lots")
    assert thresholds.info_margin_effective() == (0.05, "code")
    settings_store.put({"info_margin": 0.1})
    assert thresholds.info_margin_effective() == (0.1, "settings")
    settings_store.put({"info_margin": 0.0})  # zero is a real value: Canvas then only has to beat the slides
    assert thresholds.info_margin_effective() == (0.0, "settings")
    settings_store.put({"info_margin": 0.5})  # out of range: ignored
    assert thresholds.info_margin_effective() == (0.05, "code")


def test_get_thresholds_view_has_the_margin(admin):
    body = admin.get("/api/admin/thresholds").json()
    assert body["margin"] == {"value": 0.05, "default": 0.05, "source": "code", "override": None,
                              "min": 0.0, "max": 0.3}


@pytest.mark.parametrize("value", [-0.01, 0.31, "abc"])
def test_put_margin_rejects_out_of_range(admin, value):
    assert admin.put("/api/admin/thresholds", json={"info_margin": value}).status_code == 400
    assert thresholds.info_margin_effective() == (0.05, "code")


def test_put_margin_saves_resets_and_records_history(admin):
    body = admin.put("/api/admin/thresholds", json={"info_margin": 0.0812}).json()
    assert body["margin"]["value"] == 0.081 and body["margin"]["source"] == "settings"
    body = admin.put("/api/admin/thresholds", json={"info_margin": 0}).json()
    assert body["margin"]["value"] == 0.0 and body["margin"]["source"] == "settings"
    body = admin.put("/api/admin/thresholds", json={"info_margin": None}).json()
    assert body["margin"]["source"] == "code"
    assert [(h["setting"], h["old"], h["new"]) for h in body["history"]] == [
        ("info_margin", 0.0, 0.05), ("info_margin", 0.081, 0.0), ("info_margin", 0.05, 0.081)]


def test_settings_page_has_the_margin_field():
    html = (ROOT / "public" / "admin.html").read_text()
    js = (ROOT / "public" / "admin.js").read_text()
    assert 'id="info-margin"' in html and 'id="info-margin-form"' in html
    assert "info_margin" in js

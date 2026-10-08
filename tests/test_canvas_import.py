"""indexer/canvas_import.py, build_info_index.py and the upload of the info index.

Every Canvas response here is SYNTHETIC JSON served by a TEST FAKE (httpx
MockTransport). Every name is invented; the roster has made-up students only.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import httpx
import numpy as np
import pytest

pytest.importorskip("rapidfuzz")

import pipeline_fixture as pf  # noqa: E402
from indexer import build_index, build_info_index, canvas_import as ci, common, deidentify as d, upload  # noqa: E402
from indexer.leakcheck import RosterChecker  # noqa: E402

CID = ci.COURSES["70445"]
API = f"/api/v1/courses/{CID}"
BEN_ID = 42
ROSTER = (
    '"Semester","Course","Last Name","Preferred/First Name","MI","Andrew ID","Email"\n'
    '"F26","70445","Quenwick","Zorblat","","zquenwic","zquenwic@example.edu"\n'
    '"F26","70445","Vantreek","Marisol","","mvantree","mvantree@example.edu"\n'
    '"F26","70445","Pellwick","Wilhelmina","","wpellwic","wpellwic@example.edu"\n'
)
FAKE_KEY = "s" + "k-" + "Zx9" * 10  # built at run time so the repo never holds a key-shaped string
LONG_PARAGRAPHS = "\n".join(
    f"<p>Paragraph {i}: " + " ".join(f"word{i}x{j}" for j in range(60)) + ".</p>" for i in range(20)
)


def page(slug, title, body, published=True, updated="2026-09-01T00:00:00Z"):
    return {"url": slug, "title": title, "body": body, "published": published, "updated_at": updated,
            "html_url": f"https://canvas.cmu.edu/courses/{CID}/pages/{slug}"}


PAGES = [
    page("welcome", "Welcome to the Course",
         "<p>Welcome! Office hours are Tuesdays.</p><p>Thanks to Marisol Vantreek for the damn good question.</p>"
         "<p>The quiz code is BANANA.</p><p>Use this key: " + FAKE_KEY + "</p>"),
    page("ai-use-policy", "AI Use Policy", "<p>You may use AI tools for coding, not for written reflections.</p>"),
    page("long-guide", "Long Guide", LONG_PARAGRAPHS),
    page("draft", "Draft page", "<p>not ready</p>", published=False),
]

ASSIGNMENTS = [
    {"id": 501, "name": "Homework 1", "description": "<p>Write a memo.</p>", "due_at": "2026-09-10T03:59:00Z",
     "points_possible": 10, "published": True, "html_url": f"https://canvas.cmu.edu/courses/{CID}/assignments/501",
     "updated_at": "2026-09-01T00:00:00Z"},
    {"id": 502, "name": "Quiz 1", "description": "<p>Covers sessions 1-2.</p>", "due_at": "2026-09-12T03:59:00Z",
     "points_possible": 5, "published": True, "quiz_id": 900,
     "html_url": f"https://canvas.cmu.edu/courses/{CID}/assignments/502", "updated_at": "2026-09-01T00:00:00Z"},
]

MODULES = [
    {"id": 1, "name": "Course Overview", "position": 1, "published": True, "items": [
        {"id": 11, "type": "SubHeader", "title": "Start here", "published": True, "position": 1},
        {"id": 12, "type": "Page", "title": "Welcome to the Course", "page_url": "welcome", "published": True,
         "position": 2, "html_url": f"https://canvas.cmu.edu/courses/{CID}/modules/items/12"},
        {"id": 13, "type": "ExternalUrl", "title": "Course FAQ", "published": True, "position": 3,
         "external_url": "https://docs.google.com/document/d/FAQDOC0000000000000000000/edit",
         "html_url": f"https://canvas.cmu.edu/courses/{CID}/modules/items/13"},
        {"id": 14, "type": "ExternalUrl", "title": "AI in the News Presentation Schedule", "published": True,
         "position": 4, "external_url": "https://docs.google.com/spreadsheets/d/SHEET00000000000000000000/edit",
         "html_url": f"https://canvas.cmu.edu/courses/{CID}/modules/items/14"},
        {"id": 15, "type": "ExternalUrl", "title": "Project Ideas", "published": True, "position": 5,
         "external_url": "https://docs.google.com/document/d/LISTDOC000000000000000000/edit",
         "html_url": f"https://canvas.cmu.edu/courses/{CID}/modules/items/15"},
        {"id": 16, "type": "File", "title": "Syllabus.txt", "content_id": 701, "published": True, "position": 6,
         "url": f"https://canvas.cmu.edu{API}/files/701",
         "html_url": f"https://canvas.cmu.edu/courses/{CID}/modules/items/16"},
        {"id": 17, "type": "File", "title": "secret_api_keys.txt", "content_id": 702, "published": True,
         "position": 7, "url": f"https://canvas.cmu.edu{API}/files/702"},
        {"id": 18, "type": "ExternalUrl", "title": "Monday Class Recording", "published": True, "position": 8,
         "external_url": "https://cmu.zoom.us/rec/share/abc", "html_url": f"https://canvas.cmu.edu/courses/{CID}/modules/items/18"},
        {"id": 19, "type": "Quiz", "title": "Quiz 1", "content_id": 900, "published": True, "position": 9},
        {"id": 20, "type": "Discussion", "title": "Gallery", "content_id": 3, "published": True, "position": 10},
        {"id": 21, "type": "Assignment", "title": "Homework 1", "content_id": 501, "published": False, "position": 11},
    ]},
]

FILES = {
    701: {"id": 701, "display_name": "Syllabus.txt", "filename": "Syllabus.txt", "updated_at": "2026-08-20T00:00:00Z",
          "url": "https://canvas.cmu.edu/files/701/download?download_frd=1&verifier=abc"},
}
FILE_BYTES = {"/files/701/download": b"Grading: quizzes 30%, homework 40%, participation 30%.\nNo late work without notice."}

ANNOUNCEMENTS = [
    {"id": 31, "title": "Room change", "message": "<p>We meet in room 101 on Thursday.</p>", "posted_at": "2026-09-02T12:00:00Z",
     "author": {"id": BEN_ID}, "html_url": f"https://canvas.cmu.edu/courses/{CID}/discussion_topics/31", "published": True},
    {"id": 32, "title": "TA note", "message": "<p>From a TA.</p>", "posted_at": "2026-09-03T12:00:00Z",
     "author": {"id": 7}, "html_url": f"https://canvas.cmu.edu/courses/{CID}/discussion_topics/32", "published": True},
]


class FakeCanvas:
    """TEST FAKE for the Canvas REST API. Records every request; serves pages two to a page with Link headers."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.auth: list[str | None] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, str(request.url)))
        self.auth.append(request.headers.get("authorization"))
        assert request.method == "GET"
        path, q = request.url.path, request.url.params
        if path == "/api/v1/users/self":
            return httpx.Response(200, json={"id": BEN_ID, "name": "Ben Collier"})
        if path == f"{API}/pages":
            pg = int(q.get("page", "1"))
            rows = [{k: v for k, v in p.items() if k != "body"} for p in PAGES]
            chunk = rows[(pg - 1) * 2: pg * 2]
            headers = {}
            if pg * 2 < len(rows):
                headers["link"] = f'<https://canvas.cmu.edu{API}/pages?page={pg + 1}&per_page=2>; rel="next"'
            return httpx.Response(200, json=chunk, headers=headers)
        if path.startswith(f"{API}/pages/"):
            slug = path.rsplit("/", 1)[1]
            return httpx.Response(200, json=next(p for p in PAGES if p["url"] == slug))
        if path == f"{API}/assignments":
            return httpx.Response(200, json=ASSIGNMENTS)
        if path == f"{API}/modules":
            assert "items" in q.get_list("include[]")
            return httpx.Response(200, json=MODULES)
        if path.startswith(f"{API}/files/"):
            return httpx.Response(200, json=FILES[int(path.rsplit("/", 1)[1])])
        if path in FILE_BYTES:
            return httpx.Response(200, content=FILE_BYTES[path])
        if path == API:
            return httpx.Response(200, json={"name": "AI for Business Leaders", "syllabus_body": "<p>See the syllabus file.</p>",
                                             "updated_at": "2026-08-01T00:00:00Z"})
        if path == f"{API}/discussion_topics":
            assert q.get("only_announcements") == "true"
            return httpx.Response(200, json=ANNOUNCEMENTS)
        return httpx.Response(404, json={"errors": [{"message": f"unexpected {path}"}]})


@pytest.fixture(scope="module")
def words():
    return d.load_english_words(), d.load_given_names()


def make_cleaner(tmp: Path, words) -> ci.Cleaner:
    english, given = words
    roster = tmp / "_private" / "rosters"
    roster.mkdir(parents=True, exist_ok=True)
    (roster / "CourseRoster_fake.csv").write_text(ROSTER)
    scrub = d.build_scrub_list(d.read_rosters(roster), english)
    return ci.Cleaner(d.Scrubber(scrub, english, given).scrub, RosterChecker.from_dir(roster))


class FakeDrive:
    """TEST FAKE for `gog drive download`: Google file id -> exported text."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.files = {
            "FAQDOC0000000000000000000": "Q: Can I use R?\nA: Yes, but Python gets TA support.",
            "LISTDOC000000000000000000": "Team 1: Zorblat Quenwick, Marisol Vantreek\nTeam 2: Wilhelmina Pellwick",
        }

    def __call__(self, file_id: str, fmt: str) -> str | None:
        self.calls.append(file_id)
        return self.files.get(file_id)


def run_import(tmp: Path, words, fake: FakeCanvas | None = None, drive: FakeDrive | None = None):
    fake = fake or FakeCanvas()
    drive = drive or FakeDrive()
    canvas = ci.CanvasClient("test-canvas-token", client=httpx.Client(transport=httpx.MockTransport(fake)),
                             sleep=lambda s: None)
    items, report = ci.import_course("70445", tmp, canvas, make_cleaner(tmp, words), drive, log=lambda s: None)
    return {i["id"]: i for i in items}, report, fake, drive


@pytest.fixture
def imported(tmp_path, words):
    return run_import(tmp_path, words) + (tmp_path,)


def test_follows_pagination_and_skips_unpublished(imported):
    items, report, fake, _, _ = imported
    page_calls = [u for m, u in fake.requests if f"{API}/pages?" in u or u.endswith(f"{API}/pages")]
    assert len(page_calls) == 2  # two pages of the list, via rel="next"
    assert "70445-canvas-page-ai-use-policy" in items and "70445-canvas-page-long-guide" in items
    assert "70445-canvas-page-draft" not in items
    assert any(s["why"] == "unpublished page" for s in report.skipped)


def test_only_get_and_never_student_data(imported):
    _, _, fake, _, _ = imported
    assert fake.requests and all(m == "GET" for m, _ in fake.requests)
    for _, url in fake.requests:
        assert "submission" not in url and "grade" not in url and "enrollment" not in url
        assert "/users/" not in url or url.endswith("/users/self")
    canvas = ci.CanvasClient("t", client=httpx.Client(transport=httpx.MockTransport(FakeCanvas())), sleep=lambda s: None)
    for bad in (f"/courses/{CID}/assignments/501/submissions", f"/courses/{CID}/students", f"/courses/{CID}/enrollments",
                "/users/77/profile", f"/courses/{CID}/discussion_topics/3/entries"):
        with pytest.raises(ci.ForbiddenRequest):
            canvas.get(bad)
    with pytest.raises(ci.ForbiddenRequest):
        canvas.get(f"/courses/{CID}/assignments", {"include[]": ["submission"]})


def test_token_only_sent_to_canvas(imported):
    _, _, fake, _, _ = imported
    for (_, url), auth in zip(fake.requests, fake.auth):
        if "/download" in url:
            assert auth is None  # file links carry a verifier; the token is never sent with them
        else:
            assert auth == "Bearer test-canvas-token"


def test_stubs_for_student_lists(imported):
    items, report, _, drive, _ = imported
    sched = items["70445-canvas-link-14"]
    assert sched["stub"] and sched["text"].endswith(ci.STUB_TEXT) and sched["source_url"] is None
    assert "SHEET00000000000000000000" not in drive.calls  # a schedule is never even downloaded
    teams = items["70445-canvas-link-15"]
    assert teams["stub"] and "Quenwick" not in json.dumps(teams)
    assert {s["why"] for s in report.stubs} == {
        "title says it lists students (schedule, teams, sign-ups)", "names several roster students"}
    faq = items["70445-canvas-link-13"]
    assert not faq["stub"] and "Python gets TA support" in faq["text"]
    assert faq["source_url"].startswith("https://docs.google.com/document/")


def test_deid_pg_access_code_and_key_filters(imported):
    items, _, _, _, tmp = imported
    w = items["70445-canvas-page-welcome"]
    blob = json.dumps(w)
    assert "Marisol" not in blob and "Vantreek" not in blob and "[student]" in w["text"]
    assert "damn" not in blob and "darn" in w["text"]
    assert "BANANA" not in blob and "[access code removed]" in w["text"]
    assert FAKE_KEY not in blob and ci.KEY_MARKER in w["text"]
    assert "Module: Course Overview" in w["text"] and "Class: Start here" in w["text"]
    assert w["canvas_url"].startswith("https://canvas.cmu.edu/")
    saved = (tmp / "_build" / "canvas" / "70445" / "items.json").read_text()
    assert "Vantreek" not in saved and "BANANA" not in saved and FAKE_KEY not in saved
    report = json.loads((tmp / "_build" / "canvas" / "70445" / "report.json").read_text())
    assert report["filters"]["access_codes_removed"] >= 1 and report["filters"]["keys_removed"] >= 1


def test_items_files_assignments_announcements(imported):
    items, report, _, _, _ = imported
    hw = items["70445-canvas-assignment-501"]
    assert hw["kind"] == "assignment" and hw["due_at"] == "2026-09-10T03:59:00Z"
    assert "Due: Wednesday, September 9, 2026 at 11:59 PM Eastern" in hw["text"]
    quiz = items["70445-canvas-assignment-502"]  # the Quiz module item maps to its assignment
    assert quiz["module"] == "Course Overview" and "Covers sessions" in quiz["text"]
    syl = items["70445-canvas-file-701"]
    assert syl["kind"] == "syllabus" and "quizzes 30%" in syl["text"]
    assert "70445-canvas-file-702" not in items  # a key file is never imported
    assert items["70445-canvas-syllabus"]["kind"] == "syllabus"
    rec = items["70445-canvas-link-18"]
    assert rec["source_url"] is None and "zoom" not in json.dumps(rec)
    assert "70445-canvas-announcement-31" in items and "70445-canvas-announcement-32" not in items
    assert any("discussion" in s["why"] for s in report.skipped)
    for item in items.values():
        assert set(item) >= {"id", "course", "module", "position", "title", "kind", "canvas_url", "source_url",
                             "due_at", "updated_at", "text", "chunks"}
        assert item["kind"] in {"page", "file", "assignment", "link", "announcement", "syllabus"}


def test_chunking(imported):
    items, _, _, _, _ = imported
    chunks = items["70445-canvas-page-long-guide"]["chunks"]
    assert len(chunks) >= 3
    sizes = [len(c.split("\n", 1)[1].split()) for c in chunks]
    assert all(c.startswith("Long Guide\n") for c in chunks)
    assert all(150 <= n <= 400 for n in sizes[:-1]) and 75 <= sizes[-1] <= 400
    joined = " ".join(c.split("\n", 1)[1] for c in chunks)
    assert "word0x0" in joined and "word19x59" in joined
    short = ci.chunk_text("T", "one two three")
    assert short == ["T\none two three"]


def test_chunking_splits_a_huge_paragraph():
    text = " ".join(f"w{i}." for i in range(1000))
    chunks = ci.chunk_text("Big", text)
    assert all(len(c.split()) <= 401 for c in chunks) and len(chunks) >= 3


def test_second_run_uses_the_cache(tmp_path, words):
    run_import(tmp_path, words)
    _, _, fake, _ = run_import(tmp_path, words)
    assert not any(f"{API}/pages/" in u for _, u in fake.requests)  # unchanged updated_at: no body refetch
    assert not any("/download" in u for _, u in fake.requests)


def test_rate_limit_and_retry():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(403, text="403 Forbidden (Rate Limit Exceeded)")
        return httpx.Response(200, json={"ok": True})

    sleeps: list[float] = []
    clock = iter(range(0, 1000))
    canvas = ci.CanvasClient("t", client=httpx.Client(transport=httpx.MockTransport(handler)),
                             sleep=sleeps.append, clock=lambda: next(clock) * 0.0)
    assert canvas.get("/courses/1") == {"ok": True}
    assert calls["n"] == 2 and sleeps  # waited between requests and backed off


def test_html_to_text_keeps_links_but_not_zoom():
    t = ci.html_to_text('<p>Read <a href="https://learning.oreilly.com/x">here</a> and '
                        '<a href="https://cmu.zoom.us/rec/1">recording</a></p><ul><li>one</li></ul><script>x()</script>')
    assert "(https://learning.oreilly.com/x)" in t and "zoom" not in t and "- one" in t and "x()" not in t


# ---------------------------------------------------------------- info index + upload

@pytest.fixture
def info_built(tmp_path, words):
    archive = pf.make_archive(tmp_path)
    assert build_index.build(archive, key="test-voyage-key", client=pf.FakeVoyage().client(),
                             code_map=pf.code_map(tmp_path), sleep=lambda s: None, log=lambda s: None) == 0
    canvas = ci.CanvasClient("test-canvas-token", client=httpx.Client(transport=httpx.MockTransport(FakeCanvas())),
                             sleep=lambda s: None)
    ci.import_course("70445", archive, canvas, make_cleaner(archive, words), FakeDrive(), log=lambda s: None)
    return archive


def test_build_info_index(info_built):
    lines: list[str] = []
    voyage = pf.FakeVoyage()
    code = build_info_index.build(info_built, key="test-voyage-key", client=voyage.client(),
                                  sleep=lambda s: None, log=lines.append)
    assert code == 0, "\n".join(lines)
    content = info_built / "_build" / "content"
    index = json.loads((content / "info_index.json").read_text())
    emb = np.load(content / "info_embeddings.npy")
    assert emb.shape == (len(index["records"]), pf.DIM)
    rec = index["records"][0]
    assert set(rec) >= {"id", "course", "title", "kind", "canvas_url", "due_at", "text"}
    assert any("0 roster hits" in line for line in lines)
    # unchanged re-run embeds nothing
    again = pf.FakeVoyage()
    assert build_info_index.build(info_built, key="test-voyage-key", client=again.client(),
                                  sleep=lambda s: None, log=lambda s: None) == 0
    assert again.calls == 0


def test_info_leak_blocks(info_built):
    path = info_built / "_build" / "canvas" / "70445" / "items.json"
    items = json.loads(path.read_text())
    items[0]["chunks"].append(f"Planted: {pf.FAKE_FULL_NAME}")
    path.write_text(json.dumps(items))
    code = build_info_index.build(info_built, key="test-voyage-key", client=pf.FakeVoyage().client(),
                                  sleep=lambda s: None, log=lambda s: None)
    assert code == build_info_index.EXIT_LEAK


def test_upload_sends_info_index_and_bumps_version(info_built):
    assert build_info_index.build(info_built, key="test-voyage-key", client=pf.FakeVoyage().client(),
                                  sleep=lambda s: None, log=lambda s: None) == 0
    fake = pf.FakeSupabase()
    sb = common.Supabase("https://fake.supabase.co", "service-test-key", "twin-content", client=fake.client())
    lines: list[str] = []
    code = upload.run(info_built / "_build", info_built / "_private" / "rosters", sb, log=lines.append)
    assert code == 0, "\n".join(lines)
    assert {"content/info_index.json", "content/info_embeddings.npy"} <= set(fake.uploads)
    assert fake.uploads[-4:-1] == ["content/embeddings.npy", "content/index.json", "content/manifest.json"]
    assert not any("canvas" in p or "items.json" in p for p in fake.uploads)
    version = json.loads((info_built / "_build" / "content" / "manifest.json").read_text())["index_version"]
    assert fake.settings["index_version"].startswith(version + "+info-")


def test_upload_leak_check_covers_info_index(info_built):
    assert build_info_index.build(info_built, key="test-voyage-key", client=pf.FakeVoyage().client(),
                                  sleep=lambda s: None, log=lambda s: None) == 0
    path = info_built / "_build" / "content" / "info_index.json"
    data = json.loads(path.read_text())
    data["records"][0]["text"] += f" {pf.FAKE_SURNAME} said hi"  # single surname: strict check on info text
    path.write_text(json.dumps(data))
    fake = pf.FakeSupabase()
    sb = common.Supabase("https://fake.supabase.co", "service-test-key", "twin-content", client=fake.client())
    code = upload.run(info_built / "_build", info_built / "_private" / "rosters", sb, log=lambda s: None)
    assert code == upload.EXIT_LEAK and fake.uploads == []


def test_cleaner_counts_are_labels_only(tmp_path, words):
    c = make_cleaner(tmp_path, words)
    out = c.clean("Zorblat Quenwick says the attendance code is PLUM.")
    assert "Quenwick" not in out and "PLUM" not in out
    assert all(isinstance(k, str) and "Quenwick" not in k for k in Counter(c.counts))

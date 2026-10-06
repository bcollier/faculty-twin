"""A tiny synthetic Lecture Archive with build outputs, for the index/upload/worker tests.

Every name and every string here is invented. The roster has one made-up
student ("Zorblat Quenwick", Andrew ID "zquenwic") so the leak check has
something to find when a test plants it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import numpy as np

FAKE_FULL_NAME = "Zorblat Quenwick"
FAKE_SURNAME = "Quenwick"
DIM = 4

ROSTER_CSV = (
    '"Semester","Course","Last Name","Preferred/First Name","MI","Andrew ID","Email","Primary Advisor"\n'
    '"F26","70445","Quenwick","Zorblat","","zquenwic","zquenwic@example.edu","Advisor Person"\n'
)


def _slide(course, session, n, title, text, flags=(), notes="", ocr=""):
    sid = f"{course}-s{session:02d}-{n:03d}"
    row = {
        "slide_id": sid,
        "course": course,
        "session": session,
        "slide_number": n,
        "title": title,
        "text": text,
        "notes": notes,
        "flags": list(flags),
        "image": f"slides/{course}/s{session:02d}/{sid}.webp",
        "thumb": f"slides/{course}/s{session:02d}/{sid}-thumb.webp",
    }
    if ocr:
        row["ocr_text"] = ocr
    return row


def make_archive(root: Path) -> Path:
    archive = Path(root) / "archive"
    build = archive / "_build"
    inv = {
        "courses": {
            "70-445 Fake Course A": {
                "term": "2026 Fall",
                "sessions": [
                    {"number": 1, "date": "2026-09-01", "title": "Fruit basics"},
                    {"number": 2, "date": "2026-09-03", "title": "More fruit"},
                ],
            }
        }
    }
    (archive / "_inventory").mkdir(parents=True)
    (archive / "_inventory" / "f26_inventory.json").write_text(json.dumps(inv))

    decks = {
        ("70445", 1): [
            _slide("70445", 1, 1, "What is an apple", "Apples grow on trees.", notes="Say apples are fruit."),
            _slide("70445", 1, 2, "Team list", "[student] and friends", flags=["student_names_possible"]),
            _slide("70445", 1, 3, "Apple chart", "", flags=["little_text"], ocr="APPLES 2026"),
        ],
        ("70445", 2): [_slide("70445", 2, 1, "Bananas", "Bananas are long.", flags=["in_the_news"])],
    }
    for (course, session), rows in decks.items():
        folder = build / "slides" / course / f"s{session:02d}"
        folder.mkdir(parents=True)
        (folder / "slides.json").write_text(json.dumps(rows))
        (folder / "deck.json").write_text(json.dumps({"course": course, "session": session}))
        (folder / "source_converted.pdf").write_bytes(b"%PDF-fake")
        for r in rows:
            (build / r["image"]).write_bytes(b"RIFFfakeWEBP" + r["slide_id"].encode())
            (build / r["thumb"]).write_bytes(b"RIFFthumbWEBP" + r["slide_id"].encode())

    (build / "align" / "70445").mkdir(parents=True)
    write_align(build, "Apples are my favorite example of a fruit.")
    (build / "code" / "70445").mkdir(parents=True)
    (build / "code" / "70445" / "s01.json").write_text(
        json.dumps(
            [
                {"cell_id": "70445-s01-nb1-c000", "course": "70445", "session": 1, "notebook": "Fruit Demo.ipynb",
                 "cell_index": 0, "source": "apples = ['fuji', 'gala']", "markdown_above": "List apples", "flags": []},
                {"cell_id": "70445-s01-nb1-c001", "course": "70445", "session": 1, "notebook": "Fruit Demo.ipynb",
                 "cell_index": 1, "source": "   ", "markdown_above": "", "flags": []},
            ]
        )
    )
    (build / "clips").mkdir(parents=True)
    (build / "clips" / "manifest.json").write_text(
        json.dumps(
            [
                {"slide_id": "70445-s01-001", "course": "70445", "session": 1, "start": 10.0, "end": 40.0, "reason_kept": "test"},
                {"slide_id": "70445-s02-001", "course": "70445", "session": 2, "start": 5.0, "end": 30.0, "reason_kept": "test"},
            ]
        )
    )
    (build / "clips" / "70445-s01-001.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42clip")
    (build / "clips" / "70445-s02-001.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42news")

    # Private and never-upload files that must stay on the laptop.
    (build / "transcripts" / "70445").mkdir(parents=True)
    (build / "transcripts" / "70445" / "s01.json").write_text(json.dumps({"cues": [{"text": "private"}]}))
    (build / "review").mkdir()
    (build / "review" / "70445-s01.txt").write_text("review notes")
    (build / "_private").mkdir()
    (build / "_private" / "notes.json").write_text("{}")

    roster = archive / "_private" / "rosters"
    roster.mkdir(parents=True)
    (roster / "CourseRoster_fake.csv").write_text(ROSTER_CSV)
    return archive


def write_align(build: Path, transcript: str) -> None:
    (build / "align" / "70445" / "s01.json").write_text(
        json.dumps([{"slide_id": "70445-s01-001", "windows": [[10.0, 40.0]], "transcript": transcript}])
    )


def code_map(root: Path) -> Path:
    path = Path(root) / "code_map.json"
    path.write_text(json.dumps({"70445-s01-003": ["70445-s01-nb1-c000", "70445-s99-nb1-c000"]}))
    return path


def fake_vector(text: str) -> list[float]:
    h = hashlib.sha256(text.encode()).digest()
    v = np.frombuffer(h[: DIM * 4], dtype=np.uint32).astype(np.float64)
    v = v / np.linalg.norm(v)
    return [float(x) for x in v]


class FakeVoyage:
    """TEST FAKE for https://api.voyageai.com/v1/embeddings (httpx MockTransport handler)."""

    def __init__(self, fail_first: int = 0, reduced_first: int = 0) -> None:
        self.calls = 0
        self.inputs: list[str] = []
        self.fail_first = fail_first
        self.reduced_first = reduced_first

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        assert request.url.path == "/v1/embeddings"
        assert request.headers["authorization"] == "Bearer test-voyage-key"
        if self.reduced_first:
            self.reduced_first -= 1
            return httpx.Response(429, json={"detail": "You have not yet added your payment method in the billing page and will have reduced rate limits of 3 RPM and 10K TPM."})
        if self.fail_first:
            self.fail_first -= 1
            return httpx.Response(429, headers={"retry-after": "0"}, json={"detail": "slow down"})
        body = json.loads(request.content)
        assert body["input_type"] == "document"
        self.inputs.extend(body["input"])
        data = [{"index": i, "embedding": fake_vector(t)} for i, t in enumerate(body["input"])]
        usage = {"total_tokens": sum(len(t.split()) for t in body["input"])}
        return httpx.Response(200, json={"data": data, "model": body["model"], "usage": usage})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


class FakeSupabase:
    """TEST FAKE for Supabase Storage + the settings upsert (httpx MockTransport handler)."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.uploads: list[str] = []
        self.metadata: dict[str, str] = {}
        self.deleted: list[str] = []
        self.settings: dict[str, object] = {}
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer service-test-key"
        path = request.url.path
        self.requests.append(f"{request.method} {path}")
        prefix = "/storage/v1/object/twin-content/"
        if path.startswith(prefix):
            obj = path[len(prefix):]
            if request.method == "GET":
                if obj in self.objects:
                    return httpx.Response(200, content=self.objects[obj])
                return httpx.Response(400, json={"error": "not_found"})
            if request.method == "POST":
                self.objects[obj] = request.content
                self.uploads.append(obj)
                self.metadata[obj] = request.headers.get("x-metadata", "")
                return httpx.Response(200, json={"Key": f"twin-content/{obj}"})
        if path == "/storage/v1/object/twin-content" and request.method == "DELETE":
            for p in json.loads(request.content)["prefixes"]:
                self.objects.pop(p, None)
                self.deleted.append(p)
            return httpx.Response(200, json=[])
        if path == "/rest/v1/settings" and request.method == "POST":
            for row in json.loads(request.content):
                self.settings[row["key"]] = row["value"]
            return httpx.Response(201, json=json.loads(request.content))
        return httpx.Response(404, json={"error": f"unexpected {request.method} {path}"})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

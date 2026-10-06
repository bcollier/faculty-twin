"""Write a tiny synthetic content folder (fake slides, no real course text).

Used by the tests (into a temp dir) and for a local smoke run:

    uv run --with numpy python tests/fixtures/build_fixture.py /tmp/ft-fixture
    CONTENT_DIR=/tmp/ft-fixture uv run --with-requirements requirements.txt --with uvicorn uvicorn app.main:app

Layout matches the shared contract: content/index.json, content/embeddings.npy,
slides/<course>/s<NN>/<slide_id>.webp, clips/<slide_id>.mp4, clips/manifest.json,
topics/topics.json. Embedding dimension is 8; FRUIT and WEATHER are the two
topic directions so a fake embedder can produce on- and off-topic questions.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

DIM = 8
FRUIT = np.eye(DIM, dtype=np.float32)[0]
WEATHER = np.eye(DIM, dtype=np.float32)[1]
OTHER = np.eye(DIM, dtype=np.float32)[2]

SLIDES = [
    # (course, course_title, session, session_title, date, slide_number, title, topic weight on FRUIT)
    ("70445", "Fake Course A", 1, "Fruit basics", "2026-09-01", 1, "Welcome", 0.1),
    ("70445", "Fake Course A", 1, "Fruit basics", "2026-09-01", 2, "What is an apple", 0.9),
    ("70445", "Fake Course A", 1, "Fruit basics", "2026-09-01", 3, "Apple varieties", 0.85),
    ("70445", "Fake Course A", 1, "Fruit basics", "2026-09-01", 4, "Peeling a banana", 0.8),
    ("70445", "Fake Course A", 1, "Fruit basics", "2026-09-01", 5, "Logistics", 0.0),
    ("45884", "Fake Course B", 2, "Weather words", "2026-09-03", 1, "Clouds", 0.0),
    ("45884", "Fake Course B", 2, "Weather words", "2026-09-03", 2, "Rain", 0.0),
]


def build(root: Path) -> Path:
    root = Path(root)
    (root / "content").mkdir(parents=True, exist_ok=True)
    (root / "clips").mkdir(parents=True, exist_ok=True)
    records, rows = [], []
    for course, ctitle, session, stitle, date, n, title, w in SLIDES:
        sid = f"{course}-s{session:02d}-{n:03d}"
        image = f"slides/{course}/s{session:02d}/{sid}.webp"
        (root / image).parent.mkdir(parents=True, exist_ok=True)
        (root / image).write_bytes(b"RIFF\x00\x00\x00\x00WEBPfake")
        clip = None
        if sid == "70445-s01-002":
            clip = f"clips/{sid}.mp4"
            (root / clip).write_bytes(b"\x00\x00\x00\x18ftypmp42fake")
        records.append(
            {
                "id": sid,
                "kind": "slide",
                "course": course,
                "course_title": ctitle,
                "session": session,
                "session_title": stitle,
                "date": date,
                "slide_number": n,
                "title": title,
                "text": f"Fake slide text for {title.lower()}.",
                "notes": f"Fake speaker notes about {title.lower()}." if n != 3 else "",
                "transcript": f"Fake class transcript about {title.lower()}.",
                "image": image,
                "clip": clip,
                "related_code": ["70445-s01-code-01"] if sid == "70445-s01-004" else [],
            }
        )
        vec = w * FRUIT + (1 - w) * (WEATHER if course == "45884" else OTHER)
        rows.append(vec / np.linalg.norm(vec))
    records.append(
        {
            "id": "70445-s01-code-01",
            "kind": "code",
            "course": "70445",
            "course_title": "Fake Course A",
            "session": 1,
            "session_title": "Fruit basics",
            "date": "2026-09-01",
            "slide_number": None,
            "title": "peel()",
            "text": "def peel(banana):\n    return banana.strip()\n",
            "source": "def peel(banana):\n    return banana.strip()\n",
            "mark_lines": [2],
            "notes": "",
            "transcript": "",
            "image": None,
            "clip": None,
            "related_code": [],
        }
    )
    rows.append(FRUIT * 0.5 + OTHER * 0.5)
    (root / "content" / "index.json").write_text(json.dumps({"embedding_model": "fake", "records": records}, indent=1))
    np.save(root / "content" / "embeddings.npy", np.stack(rows).astype(np.float32))
    (root / "topics").mkdir(parents=True, exist_ok=True)
    (root / "topics" / "topics.json").write_text(
        json.dumps(
            [
                {"question": "What is an apple?", "course": "70445"},
                {
                    "question": "Show me the banana slide",
                    "course": "70445",
                    "playlist": {
                        "segments": [
                            {
                                "slide_id": "70445-s01-004",
                                "narration": "Stored narration about peeling.",
                                "audio_path": "audio/voice123/fakehash.mp3",
                            }
                        ],
                        "follow_ups": ["What is an apple?"],
                    },
                },
            ]
        )
    )
    (root / "audio" / "voice123").mkdir(parents=True, exist_ok=True)
    (root / "audio" / "voice123" / "fakehash.mp3").write_bytes(b"ID3fake")
    (root / "clips" / "manifest.json").write_text(
        json.dumps([{"slide_id": "70445-s01-002", "course": "70445", "session": 1, "start": 12.0, "end": 40.5}])
    )
    return root


if __name__ == "__main__":
    out = build(Path(sys.argv[1] if len(sys.argv) > 1 else "ft-fixture"))
    print(out)

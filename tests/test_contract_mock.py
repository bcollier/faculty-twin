"""Contract tests: the dev mock (public/dev/mock.js) answers in the same shapes as the real API.

The frontend is mostly built and checked against `?mock=1`, so a mock that drifts from the
FastAPI app hides real bugs. For each scenario this file asks the real app (TestClient, the
synthetic fixture and the usual TEST FAKES for retrieval and models) and the mock (run in Node
by tests/contract/dump_mock.mjs), reduces both bodies to a shape (keys and JSON types, list
items merged), and requires the shapes to match. A null on either side matches any type, since
most fields are optional (no audio, no clip, no code). Values are never compared.

Needs `node` on PATH. CI always has it; locally the test skips without it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app import limits, retrieval, storage
from app.main import Retriever, app, get_completer, get_embedder, get_retriever
from app import admin_evals, eval_store  # noqa: E402  (after app.main: admin imports from it)

import test_admin_evals
import test_api
import test_course_info

ROOT = Path(__file__).resolve().parents[1]
DUMPER = ROOT / "tests" / "contract" / "dump_mock.mjs"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None and not os.environ.get("CI"), reason="node is not installed")


# ---------------------------------------------------------------- shapes

NULL = "null"


def shape(value: Any) -> Any:
    """Keys and JSON types only. Lists become a one-item list holding the merged shape of every item."""
    if value is None:
        return NULL
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        merged: Any = None
        for item in value:
            merged = merge(merged, shape(item))
        return [merged] if merged is not None else []
    if isinstance(value, dict):
        return {k: shape(v) for k, v in value.items()}
    raise TypeError(type(value))


def merge(a: Any, b: Any) -> Any:
    """Combine two shapes from the same list: union of keys, null widened by a concrete type."""
    if a is None or a == NULL:
        return b
    if b is None or b == NULL:
        return a
    if isinstance(a, dict) and isinstance(b, dict):
        return {k: merge(a.get(k), b.get(k)) if k in a and k in b else (a.get(k) if k in a else b[k])
                for k in {**a, **b}}
    if isinstance(a, list) and isinstance(b, list):
        return [merge(a[0] if a else None, b[0] if b else None)] if (a or b) else []
    return a if a == b else f"{a}|{b}"


# Objects keyed by data rather than by field name (prompt variables, per-model numbers): compare
# the shape of their values, not their keys.
MAPS = {
    "$.prompts[].variables",
    "$.runs[].metrics", "$.run.metrics", "$.metrics",
}


def collapse(s: Any) -> Any:
    """A map's values merged into one shape under the key "*"."""
    if not isinstance(s, dict):
        return s
    merged: Any = None
    for v in s.values():
        merged = merge(merged, v)
    return {"*": merged} if merged is not None else {}


def diff(real: Any, mock: Any, where: str = "$") -> list[str]:
    """Every place the mock's shape disagrees with the real one."""
    if real == NULL or mock == NULL:
        return []
    if where in MAPS:
        real, mock = collapse(real), collapse(mock)
        if not real or not mock:
            return []
    if isinstance(real, dict) and isinstance(mock, dict):
        out = []
        for k in sorted(set(real) - set(mock)):
            out.append(f"{where}.{k}: the real API sends it, the mock does not")
        for k in sorted(set(mock) - set(real)):
            out.append(f"{where}.{k}: the mock sends it, the real API does not")
        for k in sorted(set(real) & set(mock)):
            out += diff(real[k], mock[k], f"{where}.{k}")
        return out
    if isinstance(real, list) and isinstance(mock, list):
        if not real or not mock:
            return []  # an empty list says nothing about its items
        return diff(real[0], mock[0], f"{where}[]")
    if real != mock:
        return [f"{where}: real is {real}, mock is {mock}"]
    return []


def test_shape_helpers():
    assert shape({"a": [1, None, 2.5], "b": None}) == {"a": ["number"], "b": NULL}
    assert shape([{"a": 1}, {"b": "x"}]) == [{"a": "number", "b": "string"}]
    assert diff(shape({"a": 1, "b": "x"}), shape({"a": None, "c": 1})) == [
        "$.b: the real API sends it, the mock does not",
        "$.c: the mock sends it, the real API does not",
    ]
    assert diff(shape({"a": [{"x": 1}]}), shape({"a": [{"x": "1"}]})) == ["$.a[].x: real is number, mock is string"]
    assert diff(shape({"a": []}), shape({"a": [{"x": 1}]})) == []
    assert diff(shape({"prompts": [{"variables": {"a": "x"}}]}), shape({"prompts": [{"variables": {"b": "y"}}]})) == []
    assert merge("string", None) == "string" and merge(NULL, "number") == "number"


# ---------------------------------------------------------------- scenarios
# name -> (method, path, body for the mock, who is signed in). The real side of each is built below.

STUDENT = "student"
ADMIN = "admin"
SCENARIOS: dict[str, tuple[str, str, Any, Any]] = {
    "health": ("GET", "/api/health", None, None),
    "topics": ("GET", "/api/topics", None, STUDENT),
    "courses": ("GET", "/api/courses", None, STUDENT),
    "voice": ("GET", "/api/voice", None, STUDENT),
    "topics_signed_out": ("GET", "/api/topics", None, None),
    "login_wrong": ("POST", "/api/login", {"passcode": "nope"}, None),
    "ask_covered": ("POST", "/api/ask", {"question": "Explain the placeholder method"}, STUDENT),
    "ask_not_covered": ("POST", "/api/ask", {"question": "Who won the stanley cup?"}, STUDENT),
    "ask_faq": ("POST", "/api/ask", {"question": "faqmeet"}, STUDENT),
    "ask_faq_contacts": ("POST", "/api/ask", {"question": "faqta"}, STUDENT),
    "ask_logistics": ("POST", "/api/ask", {"question": "logistics"}, STUDENT),
    "ask_course_info": ("POST", "/api/ask", {"question": "canvasinfo"}, STUDENT),
    "ask_too_long": ("POST", "/api/ask", {"question": "x" * 301}, STUDENT),
    "admin_login_wrong": ("POST", "/api/admin/login", {"passcode": "nope"}, None),
    "admin_status": ("GET", "/api/admin/status", None, ADMIN),
    "admin_settings": ("GET", "/api/admin/settings", None, ADMIN),
    "admin_log": ("GET", "/api/admin/log", None, ADMIN),
    "admin_prompts": ("GET", "/api/admin/prompts", None, ADMIN),
    "admin_prompt_history": ("GET", "/api/admin/prompts/narration_system/history", None, ADMIN),
    "admin_signed_out": ("GET", "/api/admin/status", None, None),
    "admin_courses": ("GET", "/api/admin/courses", None, ADMIN),
    "evals_limits": ("GET", "/api/admin/evals/limits", None, ADMIN),
    "evals_questions": ("GET", "/api/admin/evals/questions", None, ADMIN),
    "evals_runs": ("GET", "/api/admin/evals/runs", None, ADMIN),
    "evals_report_card": ("GET", "/api/admin/evals/report-card", None, ADMIN),
}

# Real requests made first (same client), so lists the mock fills have rows on the real side too.
PREP = {
    "admin_log": [("POST", "/api/ask", {"question": "What is an apple?", "course": "70445"}),
                  ("POST", "/api/ask", {"question": "When are your office hours?"})],
}

# The real request for scenarios whose mock trigger word is not a real question.
REAL_QUESTIONS = {
    "ask_covered": {"question": "What is an apple?", "course": "70445"},
    "ask_not_covered": {"question": "who won the stanley cup"},
    "ask_faq": {"question": "When are your office hours?"},
    "ask_faq_contacts": {"question": "Can we reschedule our presentation?"},
    "ask_logistics": {"question": "Can I get a regrade on my fruit quiz?", "course": "70445"},
    "ask_course_info": {"question": "What does the syllabus say about AI tools?"},
    "ask_too_long": {"question": "x" * 301},
}


@pytest.fixture(scope="module")
def mock_responses() -> dict[str, dict[str, Any]]:
    if NODE is None:
        pytest.fail("node is required for the contract tests in CI")
    requests = [{"name": n, "method": m, "path": p, "body": b, "as": who} for n, (m, p, b, who) in SCENARIOS.items()]
    proc = subprocess.run([NODE, str(DUMPER)], input=json.dumps(requests), capture_output=True, text=True,
                          timeout=60, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.fixture
def real_responses(content_dir, monkeypatch) -> dict[str, dict[str, Any]]:
    from fastapi.testclient import TestClient

    monkeypatch.setenv("CONTENT_DIR", str(content_dir))
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice123")  # so segments carry audio links
    test_course_info.write_info(content_dir)
    storage.store.reset()
    model = test_course_info.FakeModel()
    app.dependency_overrides[get_retriever] = lambda: Retriever(retrieval.rank, test_api.TEST_FAKE_select, 0.5)
    app.dependency_overrides[get_embedder] = lambda: test_course_info.TEST_FAKE_embedder
    app.dependency_overrides[get_completer] = lambda: model
    bucket = eval_store.MemoryBucket()  # an uploaded eval question set, as in tests/test_admin_evals.py
    eval_store.write_questions_text(bucket, "\n".join(json.dumps(q) for q in test_admin_evals.QUESTIONS) + "\n")
    app.dependency_overrides[admin_evals.get_bucket] = lambda: bucket
    out: dict[str, dict[str, Any]] = {}
    try:
        for name, (method, path, body, who) in SCENARIOS.items():
            limits.reset_memory()  # rate limits are not what this test is about
            with TestClient(app) as c:
                if who in (STUDENT, ADMIN):
                    assert c.post("/api/login", json={"passcode": "student-pass"}).status_code == 204
                if who == ADMIN:
                    assert c.post("/api/admin/login", json={"passcode": "admin-pass"}).status_code == 204
                for m, pth, b in PREP.get(name, []):
                    assert c.request(m, pth, json=b).status_code == 200
                r = c.request(method, path, json=REAL_QUESTIONS.get(name, body))
                out[name] = {"status": r.status_code, "body": r.json() if r.content else None}
    finally:
        app.dependency_overrides.clear()
    return out


def test_the_real_side_answers_every_scenario_the_way_the_mock_claims(real_responses, mock_responses):
    """Same kind of answer first (status, covered, kind), so the shape test compares like with like."""
    for name in SCENARIOS:
        real, mock = real_responses[name], mock_responses[name]
        real_ok, mock_ok = 200 <= real["status"] < 300, isinstance(mock["status"], int) and 200 <= mock["status"] < 300
        assert real_ok == mock_ok, f"{name}: real {real['status']}, mock {mock['status']}"
        if real["status"] >= 400:
            assert real["status"] == mock["status"], f"{name}: real {real['status']}, mock {mock['status']}"
        if isinstance(real["body"], dict) and name.startswith("ask_") and real_ok:
            for key in ("covered", "kind"):
                assert real["body"].get(key) == mock["body"].get(key), f"{name}.{key}"


def test_mock_shapes_match_the_real_api(real_responses, mock_responses):
    problems = []
    for name in SCENARIOS:
        found = diff(shape(real_responses[name]["body"]), shape(mock_responses[name]["body"]))
        problems += [f"{name}: {p}" for p in found]
    assert not problems, "public/dev/mock.js drifted from the API:\n  " + "\n  ".join(problems)

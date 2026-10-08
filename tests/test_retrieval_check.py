"""evals/retrieval_check.py: routing and retrieval measured without judges or narration.

Synthetic fixture content, the real retrieval, a TEST FAKE embedder (fruit words point at the fruit slides)
and a TEST FAKE classifier model. No network, no keys.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from app import llm, retrieval, storage, usage
from evals import dataset, retrieval_check

E = np.eye(8, dtype=np.float32)


def TEST_FAKE_embedder(question: str) -> np.ndarray:
    return E[0].copy() if "apple" in question.lower() or "banana" in question.lower() else E[5].copy()


class FakeClassifier:
    """TEST FAKE: the route classifier model; answers "course content" and counts its calls."""

    def __init__(self):
        self.calls = 0

    def __call__(self, system, user, max_tokens, provider=None, model=None):
        self.calls += 1
        return json.dumps({"kind": "course_content", "reason": "a concept"})


def line(question, qtype, expected_kind, expected_slides=()):
    return {"month": "2026-10", "course": "70-445 Fake Course A", "category": "CONCEPT_QUESTION", "question": question,
            "reference_answer": None, "answerable_from_course_materials": qtype == "concept", "type": qtype,
            "expected_kind": expected_kind, "expected_slides": list(expected_slides)}


@pytest.fixture
def setup(content_dir, monkeypatch, tmp_path):
    monkeypatch.setenv("CONTENT_DIR", str(content_dir))
    path = tmp_path / "q.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in [
        line("Tell me about apple varieties please", "concept", "slides", ["70445-s01-003"]),
        line("How do you peel a banana", "concept", "slides", ["70445-s01-099"]),
        line("Who won the big game last night", "off_topic", "declined"),
    ]) + "\n")
    from app import main

    retriever = main.Retriever(retrieval.rank, retrieval.select_segments, retrieval.NOT_COVERED_THRESHOLD)
    return dataset.load(path), storage.store.get_or_503(), retriever


def test_measure_counts_routes_hits_and_copies(setup, tmp_path):
    questions, content, retriever = setup
    model = FakeClassifier()
    completer = retrieval_check.cached_classifier(model, tmp_path / "cache")
    out = retrieval_check.measure(questions, content, retriever, TEST_FAKE_embedder, completer)
    assert out["questions"] == 3
    assert out["right_route"] == [3, 3]
    assert out["retrieval_hit"] == [1, 2]  # the banana question expects a slide that does not exist
    assert out["one_course_only"] == [2, 2]
    assert out["answers_showing_a_slide_twice"] == [0, 2]
    assert out["slide_recall"] == 0.5
    assert out["mean_slides_per_answer"] == 3
    first = model.calls
    assert first >= 1  # the logistics classifier ran, through the fake
    retrieval_check.measure(questions, content, retriever, TEST_FAKE_embedder, completer)
    assert model.calls == first  # the second run is answered from the cache


def test_classifier_cache_refuses_every_other_model_call(tmp_path):
    model = FakeClassifier()
    completer = retrieval_check.cached_classifier(model, tmp_path)
    with usage.purpose("narration"), pytest.raises(llm.LLMError):
        completer("system", "user", 10)
    with usage.purpose("logistics"):
        assert json.loads(completer("system", "user", 10))["kind"] == "course_content"
        completer("system", "user", 10)
    assert model.calls == 1 and len(list(tmp_path.iterdir())) == 1


def test_stub_web_answer_is_a_web_reply():
    result = retrieval_check._stub_web("q", [], [], None)
    assert result.reply["kind"] == "web" and result.source == "llm"


def test_slide_words_and_table():
    assert retrieval_check.slide_words(None) == ""
    assert retrieval_check.slide_words({"title": "TF-IDF", "text": "Term frequency", "ocr_text": None}) == \
        "tf idf term frequency"
    results = {"a.jsonl": {"right_route": [3, 4], "retrieval_hit": [0, 0], "retrieval_hit_copy_counts": [0, 0],
                           "slide_precision": 0.5, "slide_precision_copy_counts": None, "slide_recall": 0.25,
                           "one_course_only": [2, 2], "answers_showing_a_slide_twice": [0, 2],
                           "mean_slides_per_answer": 3.5}}
    text = retrieval_check.table(results)
    assert "| Right route | 3/4 |" in text
    assert "| Retrieval hit | n/a |" in text
    assert "| Slide precision | 0.5 |" in text
    assert "| Slide precision (a copy counts) | n/a |" in text
    assert "| Slides per answer | 3.5 |" in text


def test_main_prints_a_table_and_json(setup, monkeypatch, tmp_path, capsys):
    questions, content, _ = setup
    from evals import compare, judges

    monkeypatch.setattr(retrieval_check, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(compare, "CachedEmbedder", lambda texts: TEST_FAKE_embedder)
    monkeypatch.setattr(compare, "offline_caps", lambda: None)
    monkeypatch.setattr(judges, "direct_complete", FakeClassifier())
    from app import web_answer

    monkeypatch.setattr(web_answer, "answer", web_answer.answer)  # main() stubs it; put it back afterwards
    qfile = tmp_path / "q.jsonl"
    assert retrieval_check.main(["--questions", str(qfile), "--env-file", str(tmp_path / "none.env")]) == 0
    assert "| Right route | 3/3 |" in capsys.readouterr().out
    assert len(list((tmp_path / "cache").iterdir())) >= 1  # classifier answers cached where CACHE points
    assert retrieval_check.main(["--questions", str(qfile), "--json", "--threshold", "1.5",
                                 "--env-file", str(tmp_path / "none.env")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["threshold"] == 1.5 and out["results"]["q.jsonl"]["one_course_only"] == [0, 0]

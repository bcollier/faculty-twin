"""Model comparison evals (docs/SPEC.md, Block 8b): new metrics, reliability statistics, the comparison
runner, its reports, and the import into Settings. TEST FAKES only: no model, no embedding, no network."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from app import eval_core, eval_store
from evals import compare, compare_report, dataset
from evals import reliability as rel
from evals.judges import Judge
from scripts import import_eval_history

ROOT = Path(__file__).resolve().parents[1]
COURSE_SET = ROOT / "evals" / "questions.course.jsonl"


# ---------------------------------------------------------------- reliability statistics

def test_icc_matches_shrout_and_fleiss():
    # Shrout and Fleiss (1979), Table 2: six targets, four judges. ICC(2,1) = 0.29.
    data = [[9, 2, 5, 8], [6, 1, 3, 2], [8, 4, 6, 8], [7, 1, 2, 6], [10, 5, 6, 9], [6, 2, 4, 7]]
    assert rel.icc_2_1(data) == pytest.approx(0.29, abs=0.005)
    assert rel.icc_2_1([[1, 1], [3, 3], [5, 5]]) == 1.0  # perfect repeat
    assert rel.icc_2_1([[4, 4], [4, 4]]) is None  # no variation: undefined
    assert rel.icc_2_1([[1, 2]]) is None  # one target is not enough
    assert rel.icc_2_1([[1, None], [2, 2], [3, 3], [4, 4]]) == 1.0  # incomplete rows are dropped


def test_kappa_spearman_and_ci():
    assert rel.cohen_kappa(["p", "p", "f", "f"], ["p", "f", "f", "f"]) == 0.5
    assert rel.cohen_kappa(["p", "p"], ["p", "p"]) == 1.0
    assert rel.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert rel.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0
    assert rel.spearman([1, 1, 2], [1, 2, 3]) == pytest.approx(0.866, abs=0.001)  # ties get average ranks
    c = rel.mean_ci([4, 5, 5, 5], 1, 5)
    assert c["mean"] == 4.75 and c["high"] == 5 and c["low"] < 4.75 and c["n"] == 4  # clipped to the scale
    assert rel.mean_ci([]) == {"mean": None, "low": None, "high": None, "n": 0}


def test_krippendorff_alpha_and_pairwise_agreement():
    assert rel.krippendorff_alpha_ordinal([[1, 1, 1], [3, 3, None], [5, 5, 5]]) == 1.0
    assert rel.krippendorff_alpha_ordinal([[1, 5], [5, 1], [1, 5], [5, 1]]) < 0  # systematic disagreement
    assert rel.krippendorff_alpha_ordinal([[4, 4], [4, 4]]) is None  # no variation
    mixed = rel.krippendorff_alpha_ordinal([[1, 2], [2, 2], [4, 5], [5, 5], [3, 3]])
    assert 0.5 < mixed < 1
    agree = rel.pairwise_agreement([[4, 4, 5], [1, 3, None]])
    assert agree == {"exact": round(1 / 4, 3), "within_one": round(3 / 4, 3), "pairs": 4}
    for key in ("icc", "kappa", "alpha", "flip", "ci"):
        assert rel.PLAIN[key]  # every statistic is explained in plain words


# ---------------------------------------------------------------- question format and metrics

def test_course_set_is_complete_and_checked():
    qs = dataset.load(COURSE_SET)
    assert Counter(q.qtype for q in qs) == {"concept": 20, "beyond": 8, "off_topic": 6, "logistics": 6}
    for q in qs:
        assert q.expected_kind, q.qid
        if q.qtype == "concept":
            assert q.expected_kind == ("slides",) and 1 <= len(q.expected_slides) <= 4 and q.reference_answer
            assert all(s[:5] in ("70445", "45884") for s in q.expected_slides)
        if q.qtype == "off_topic":
            assert q.expected_kind == ("declined",) and not q.answerable
        if q.qtype == "beyond":
            assert q.expected_kind == ("web",) and q.must_include and q.must_not
    text = COURSE_SET.read_text()
    assert "—" not in text  # no em dashes


def test_optional_fields_are_checked_without_echoing_text():
    base = {"category": "CONCEPT_QUESTION", "question": "What is a word embedding?"}
    q = eval_core.parse_record({**base, "type": "concept", "expected_kind": "slides",
                                "expected_slides": ["70445-s07-012"], "must_include": ["an example"]}, 1)
    assert q.expected_kind == ("slides",) and q.expected_slides == ("70445-s07-012",)
    assert q.raw()["expected_kind"] == "slides" and q.as_dict()["type"] == "concept"
    assert eval_core.parse_record(q.raw(), 1) == q  # round trip
    for bad, msg in (({"type": "quiz"}, "type"), ({"expected_kind": "voice"}, "expected_kind"),
                     ({"expected_slides": ["slide 3"]}, "expected_slides"),
                     ({"must_include": ["mail pat@example.edu"]}, "must_include")):
        with pytest.raises(eval_core.DatasetError) as err:
            eval_core.parse_record({**base, **bad}, 4)
        assert msg in str(err.value) and "pat@" not in str(err.value)
    old = eval_core.parse_record(base, 2)  # the old format still loads
    assert old.expected_kind == () and "type" not in old.raw()


def resp(outcome, slides=(), status=None, latency=1000, cost=0.01, source="llm", tokens_out=100):
    status = status or ("ok" if outcome in ("course_content", "course_info", "web") else "not_covered")
    return {"status": status, "outcome": outcome, "narration_source": source if status == "ok" else None,
            "segments": [{"n": i + 1, "slide_id": s, "narration": "x"} for i, s in enumerate(slides)],
            "latency_ms": latency, "usage": {"cost_usd": cost, "tokens_in": 10, "tokens_out": tokens_out}}


def test_answer_metrics_route_hit_precision_purity():
    concept = {"answerable": True, "expected_kind": ["slides"], "expected_slides": ["70445-s05-044", "70445-s05-054"]}
    m = eval_core.answer_metrics({**concept, "response": resp("course_content", ["70445-s05-044", "45884-s03-001"])})
    assert m["route"] == "slides" and m["route_ok"] and m["hit"] is True
    assert m["precision"] == 0.5 and m["pure"] is False and m["fallback"] is False and m["cost_usd"] == 0.01
    miss = eval_core.answer_metrics({**concept, "response": resp("not_covered")})
    assert miss["route"] == "declined" and miss["route_ok"] is False and miss["hit"] is False and miss["precision"] is None
    web_q = {"answerable": False, "expected_kind": ["web"], "expected_slides": ["70445-s10-011"]}
    declined = {**web_q, "response": resp("not_covered")}
    assert eval_core.answer_metrics(declined, web_path=True)["route_ok"] is False
    assert eval_core.answer_metrics(declined, web_path=False)["route_ok"] is True  # no web path yet: decline is right
    assert eval_core.answer_metrics(declined)["hit"] is None  # closest slides on a web question are not a retrieval test
    # Without expected_kind: answerable expects slides or Canvas; the rest anything but slides.
    assert eval_core.answer_metrics({"answerable": False, "response": resp("faq")})["route_ok"] is True
    assert eval_core.answer_metrics({"answerable": False, "response": resp("course_content", ["70445-s01-001"])})["route_ok"] is False
    assert eval_core.answer_metrics({"answerable": True, "response": resp(None, status="error")})["route_ok"] is None


def J(name, verdict, **scores):
    full = {d: None for d in eval_core.DIMENSIONS}
    full.update(scores)
    return {"judge": name, "verdict": verdict, "scores": full, "rationale": "", "issues": []}


def test_generator_metrics_add_route_cost_and_same_family_pass_rate():
    rows = [
        {"generator": "anthropic:claude-opus-5-5", "category": "CONCEPT_QUESTION", "answerable": True, "expected_kind": ["slides"],
         "expected_slides": ["70445-s05-044"], "response": resp("course_content", ["70445-s05-044"], cost=0.04),
         "judgements": [J("anthropic:claude-opus-5-5", "pass", good_teaching=5), J("openai:gpt-6.1-sol", "fail", good_teaching=3),
                        J("openrouter:google/gemini-3.8-flash", "pass", good_teaching=4)]},
        {"generator": "anthropic:claude-opus-5-5", "category": "OTHER", "answerable": False, "expected_kind": ["declined"],
         "response": resp("not_covered", cost=0.0, latency=50),
         "judgements": [J("anthropic:claude-opus-5-5", "pass"), J("openai:gpt-6.1-sol", "pass"),
                        J("openrouter:google/gemini-3.8-flash", "pass")]},
    ]
    m = eval_core.generator_metrics(rows)
    assert m["pass_rate"] == round(5 / 6, 2)
    assert m["pass_rate_excluding_same_family"] == 0.75 and m["judgements_excluding_same_family"] == 4
    assert m["route_accuracy"] == 1.0 and m["route_n"] == 2 and m["retrieval_hit_rate"] == 1.0 and m["hit_n"] == 1
    assert m["cost_per_answer"] == 0.02 and m["total_cost"] == 0.04
    assert m["scores"]["good_teaching"] == 4.0 and m["group_means"]["teaching"] == 4.0
    assert eval_core.model_family("openrouter:google/gemini-3.8-flash") == "gemini"
    assert eval_core.model_family("openai:gpt-5.6-sol") == eval_core.model_family("openai:gpt-6.1-sol") == "gpt"


def test_judge_prompt_has_the_teaching_rubric_and_web_answers():
    text = eval_core.system_prompt()
    for d in eval_core.TEACHING_DIMENSIONS + eval_core.WEB_DIMENSIONS:
        assert f"- {d}:" in text and f'"{d}"' in text
    assert "1 = " in eval_core.DIMENSIONS["good_teaching"] and "5 = " in eval_core.DIMENSIONS["good_teaching"]
    item = {"question": "How do I self-host n8n?", "category": "OTHER", "answerable": False, "reference_answer": None,
            "expected_kind": ["web"], "must_include": ["cites 2-4 web sources"], "must_not": ["use Ben's voice"],
            "response": {"status": "ok", "outcome": "web", "segments": [{"n": 1, "slide_id": "web", "narration": "Use Docker."}],
                         "links": [{"title": "n8n docs", "url": "https://docs.n8n.io"}], "label": "Beyond my slides: from the web"}}
    user = eval_core.build_user_prompt(item)
    assert "citing 2 to 4 sources" in user and "must include: cites 2-4 web sources" in user
    assert "answered from the web" in user and "n8n docs" in user and "Beyond my slides" in user
    parsed = eval_core.parse(json.dumps({"scores": {"answers_question": 4, "correct_scope": 5, "safety_tone": 5,
                                                    "cites_sources": 5, "labeled_beyond_slides": 4, "good_teaching": 3},
                                         "verdict": "pass", "rationale": "ok", "issues": []}))
    assert parsed["scores"]["cites_sources"] == 5 and parsed["scores"]["accurate"] is None


# ---------------------------------------------------------------- the comparison runner

GENS = [{"provider": "anthropic", "model": "claude-sonnet-5-5"}, {"provider": "openai", "model": "gpt-6.1-sol"}]


def small_set(tmp_path) -> list[dataset.Question]:
    lines = [json.loads(x) for x in COURSE_SET.read_text().splitlines()]
    pick = [lines[0], lines[1], lines[20], lines[28], lines[34]]  # 2 concept, beyond, off-topic, logistics
    path = tmp_path / "q.jsonl"
    path.write_text("".join(json.dumps(x) + "\n" for x in pick))
    return dataset.load(path)


def test_plan_reps_and_retest_types(tmp_path):
    qs = small_set(tmp_path)
    tasks = compare.plan_tasks(qs, GENS, 3, {"concept"})
    assert len(tasks) == 5 * 2 + 2 * 2 * 2  # run 1 for all; runs 2 and 3 for the two concept questions
    assert [t[2] for t in tasks][:10] == [1] * 10  # run 1 first
    est = compare.estimate(tasks, [{"provider": "openai", "model": "gpt-6.1-sol"}], 0.25, None)
    assert est["answers"] == 18 and est["judgements"] == 18 + round(18 * 0.25) and est["total_usd"] > 0


def test_cli_guards_generators_and_budget(tmp_path, capsys):
    many = sum((["--generator", f"anthropic:m{i}"] for i in range(7)), [])
    assert compare.main(["--questions", str(COURSE_SET), *many, "--judge", "openai:j", "--dry-run"]) == 2
    six = sum((["--generator", f"anthropic:claude-sonnet-5-{i}"] for i in range(6)), [])
    base = ["--questions", str(COURSE_SET), *six, "--judge", "openai:gpt-6.1-sol", "--dry-run"]
    assert compare.main(base + ["--budget", "1000"]) == 0  # six generators are allowed
    assert compare.main(base + ["--budget", "0.01"]) == 4  # over budget: refused before anything runs
    out = capsys.readouterr()
    assert "Estimated total" in out.out and "over the budget" in out.err
    assert compare.main(["--questions", str(COURSE_SET), "--generator", "anthropic:a", "--generator", "anthropic:a",
                         "--judge", "openai:j", "--dry-run"]) == 2


class FakeTarget:
    """TEST FAKE: concept questions get slides, the rest are declined."""

    def __init__(self, g):
        self.g = g

    def ask(self, question):
        if "AGI" in question or "Amara" in question:
            return resp("course_content", ["70445-s01-044"] if "AGI" in question else ["70445-s02-099"],
                        cost=0.02 if "sonnet" in self.g["model"] else 0.01, latency=9000)
        if "office hours" in question:
            return resp("faq", cost=0.0, latency=2)
        return resp("not_covered", cost=0.0, latency=40)


def fake_judges():
    def make(name, verdict):
        def call(system, user):
            return json.dumps({"scores": {"answers_question": 4, "correct_scope": 5, "safety_tone": 5, "good_teaching": 4,
                                          "accurate": 5, "grounded": 4}, "verdict": verdict, "rationale": "r", "issues": []})
        return Judge(*name.split(":", 1), call=call)
    return [make("anthropic:claude-opus-5-5", "pass"), make("openai:gpt-6.1-sol", "fail"),
            make("openrouter:google/gemini-3.8-flash", "pass")]


def test_run_resumes_retests_and_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "web_path_available", lambda: False)
    qs = small_set(tmp_path)
    out = tmp_path / "20261008T050000Z-course"
    spend = compare.Spend(None)
    rows = compare.run(qs, GENS, fake_judges(), 2, {"concept"}, 0.5, 50.0, out, FakeTarget, spend, seed=1,
                       concurrency=2, log=lambda m: None)
    assert len(rows) == 5 * 2 + 2 * 2
    assert sum(1 for r in rows if r.get("judgements_retest")) == round(10 * 0.5)
    assert all(len(r["judgements"]) == 3 for r in rows)
    assert spend.total == pytest.approx(2 * 0.02 * 2 + 2 * 0.01 * 2)  # answers only: fake judges report no tokens
    # Resume: nothing is asked again.
    again = compare.run(qs, GENS, fake_judges(), 2, {"concept"}, 0.0, 50.0, out,
                        lambda g: pytest.fail("asked again"), compare.Spend(None), log=lambda m: None)
    assert len(again) == len(rows)
    # Budget: a run stops before asking once the spend reaches it.
    stopped = compare.run(qs, GENS, fake_judges(), 1, None, 0.0, 0.0, tmp_path / "x", FakeTarget, compare.Spend(None),
                          log=lambda m: None)
    assert stopped == []

    meta = {"label": "Test", "run_id": out.name, "questions_file": "q.jsonl", "private": False,
            "generators": [compare.key(g) for g in GENS], "judges": [j.name for j in fake_judges()], "spend_usd": 0.1}
    a = compare_report.analyze(rows, meta, include_text=True)
    sonnet = a["models"]["anthropic:claude-sonnet-5-5"]
    assert sonnet["route_accuracy"] == 1.0 and sonnet["retrieval_hit_rate"] == 0.5  # one hit, one miss
    assert sonnet["pass_rate"] == round(2 / 3, 2) and sonnet["pass_rate_excluding_same_family"] == 0.5
    assert a["generator_retest"]["models"]["anthropic:claude-sonnet-5-5"]["n"] == 2
    assert a["generator_retest"]["models"]["anthropic:claude-sonnet-5-5"]["verdict_flip_rate"] == 0.0
    assert a["judge_retest"]["openai:gpt-6.1-sol"]["verdict_same"] == 1.0
    assert a["inter_judge"]["matrix"]["anthropic:claude-opus-5-5|openai:gpt-6.1-sol"]["same_verdict"] == 0.0
    kinds = {(s["generator"], s["judge"]): s["kind"] for s in a["self_grading"]}
    assert kinds[("openai:gpt-6.1-sol", "openai:gpt-6.1-sol")] == "self"
    assert kinds[("anthropic:claude-sonnet-5-5", "anthropic:claude-opus-5-5")] == "same family"

    (out / "meta.json").write_text(json.dumps(meta))
    public = tmp_path / "reports" / out.name
    written = compare_report.write_all(rows, meta, out, public)
    assert (public / "report.html").exists() and (out / "compare_summary.md").exists()
    page = (public / "report.html").read_text()
    assert page.startswith("<!doctype html>") and "<svg" in page and "prefers-color-scheme:dark" in page
    assert qs[0].question.split()[0] in page  # the committable set shows its questions
    md = (out / "compare.md").read_text()
    assert "Krippendorff" in md and "ICC" in md and "without same-family judges" in md
    assert "web path was not in the app" in md
    summary = (out / "compare_summary.md").read_text() + (out / "compare_summary.json").read_text()
    for q in qs:
        assert q.question not in summary
    assert len(written) == 7


def test_private_report_has_no_question_text(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "web_path_available", lambda: True)
    qs = small_set(tmp_path)
    out = tmp_path / "private" / "20261008T050000Z-private"
    rows = compare.run(qs, GENS, fake_judges(), 1, None, 0.0, 50.0, out, FakeTarget, compare.Spend(None),
                       log=lambda m: None)
    meta = {"run_id": out.name, "private": True, "generators": [compare.key(g) for g in GENS]}
    written = compare_report.write_all(rows, meta, out, None)
    page = (out / "report.html").read_text()
    shareable = page + (out / "compare_summary.md").read_text() + (out / "compare_summary.json").read_text()
    for q in qs:
        assert q.question not in shareable
    assert all("reports" not in str(w) for w in written)
    assert compare._is_private(Path("/x/evals/private/questions.jsonl")) and not compare._is_private(COURSE_SET)


def test_cached_embedder_embeds_each_question_once(tmp_path):
    import numpy as np

    calls = []

    def fake_many(texts):  # TEST FAKE
        calls.append(list(texts))
        return [np.ones(4, dtype=np.float32) * len(t) for t in texts]

    e = compare.CachedEmbedder(["a", "bb", "a"], cache_dir=tmp_path, embed_many=fake_many)
    assert calls == [["a", "bb"]] and e("bb")[0] == 2 and e.made == 2
    e2 = compare.CachedEmbedder(["a", "bb", "ccc"], cache_dir=tmp_path, embed_many=fake_many)
    assert calls[-1] == ["ccc"] and e2.made == 1


def test_import_compare_puts_the_run_on_the_report_card(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "web_path_available", lambda: False)
    qs = small_set(tmp_path)
    out = tmp_path / "20261008T050000Z-course"
    rows = compare.run(qs, GENS, fake_judges(), 2, {"concept"}, 0.0, 50.0, out, FakeTarget, compare.Spend(None),
                       log=lambda m: None)
    meta = {"label": "Course set", "run_id": out.name, "private": False, "web_path": False, "spend_usd": 1.5,
            "generators": [compare.key(g) for g in GENS], "judges": [j.name for j in fake_judges()],
            "questions_file": "q.jsonl", "finished_at": "2026-10-08T06:00:00+00:00"}
    (out / "meta.json").write_text(json.dumps(meta))
    compare_report.write_all(rows, meta, out, None)
    bucket, lines = eval_store.MemoryBucket(), []
    assert import_eval_history.import_compare(out, bucket, out=lines.append) == 0
    run = eval_store.read_run(bucket, out.name)
    assert run["status"] == "done" and len(run["generators"]) == 2 and run["pairs_done"] == 10  # run 1 only
    entry = next(e for e in eval_store.read_index(bucket) if e["id"] == out.name)
    m = entry["by_generator"]["openai:gpt-6.1-sol"]
    assert m["route_accuracy"] == 1.0 and m["cost_per_answer"] is not None
    assert any("web path" in n for n in run["notes"])
    assert not any(q.question in "\n".join(lines) for q in qs)  # prints counts only
    assert {(s["generator"], s["judge"]) for s in run["self_grading"]} == {("openai:gpt-6.1-sol", "openai:gpt-6.1-sol")}

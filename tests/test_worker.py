"""indexer/worker.py with a TEST FAKE Supabase and stubbed stages. Synthetic data only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from indexer import common, worker

COURSE_DIR = "70-445 AI for Business Leaders"


class FakeSB:
    """TEST FAKE: the few Supabase calls the worker makes, in memory."""

    def __init__(self, rows: list[dict[str, Any]], files: dict[str, bytes], sessions=None) -> None:
        self.rows = rows
        self.files = files
        self.sessions = sessions or []
        self.history: list[tuple[int, str, str | None]] = []

    @staticmethod
    def _match(row: dict[str, Any], params: dict[str, str]) -> bool:
        for key, cond in params.items():
            if key in ("select", "order", "limit"):
                continue
            if not cond.startswith("eq.") or str(row.get(key)) != cond[3:]:
                return False
        return True

    def select(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        if table == "sources":
            return [dict(r) for r in self.rows if self._match(r, params)]
        if table == "sessions":
            return [dict(s) for s in self.sessions if self._match(s, params)]
        return []

    def update(self, table: str, match: dict[str, str], values: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for r in self.rows:
            if self._match(r, match):
                r.update(values)
                self.history.append((r["id"], values["status"], values.get("message")))
                out.append(dict(r))
        return out

    def download_to(self, path: str, dest: Path) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.files[path])
        return len(self.files[path])


def row(id_: int, kind: str, filename: str, session: int = 1, status: str = "uploaded") -> dict[str, Any]:
    return {"id": id_, "course": "70445", "session": session, "kind": kind,
            "path": f"inbox/70445/s{session:02d}/{kind}/{filename}", "status": status, "message": None}


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    folder = tmp_path / "archive" / COURSE_DIR / "2026 Fall" / "01 2026-09-01 Fruit basics"
    folder.mkdir(parents=True)
    (folder / "transcript_raw.vtt").write_text("WEBVTT\n\nold")
    return tmp_path / "archive"


def test_transcript_upload_is_placed_processed_and_marked_ready(archive):
    sb = FakeSB([row(7, "transcript", "class_one.vtt")], {"inbox/70445/s01/transcript/class_one.vtt": b"WEBVTT\n\nnew"})
    stages: list[tuple[str, str, int]] = []
    globals_run: list[str] = []
    w = worker.Worker(sb, archive, lambda st, c, s: stages.append((st, c, s)), globals_run.append)
    assert w.poll_once() == 1

    folder = archive / COURSE_DIR / "2026 Fall" / "01 2026-09-01 Fruit basics"
    assert (folder / "transcript_raw.vtt").read_text().endswith("new")
    assert [p.read_text().endswith("old") for p in (folder / "_replaced").iterdir()] == [True]
    assert stages == [("deidentify", "70445", 1), ("align", "70445", 1), ("clips", "70445", 1)]
    assert globals_run == ["build_index", "upload"]
    assert [h[1] for h in sb.history] == ["processing", "ready"]
    assert sb.rows[0]["status"] == "ready"
    assert "class_one" not in (sb.rows[0]["message"] or "")


def test_missing_stage_marks_a_clear_error(archive, tmp_path, monkeypatch):
    fake_repo = tmp_path / "repo"
    (fake_repo / "indexer").mkdir(parents=True)
    monkeypatch.setattr(common, "REPO", fake_repo)  # no stage scripts at all
    sb = FakeSB([row(8, "video", "lecture.mp4")], {"inbox/70445/s01/video/lecture.mp4": b"\x00\x00\x00\x18ftyp"})
    globals_run: list[str] = []
    w = worker.Worker(sb, archive, global_runner=globals_run.append)
    w.poll_once()
    assert sb.rows[0]["status"] == "error"
    assert "stage align is not installed yet" in sb.rows[0]["message"]
    assert "lecture" not in sb.rows[0]["message"]
    assert globals_run == []  # nothing rebuilt from a half-processed session
    # The file is already in the archive, so Re-run works once the stage merges.
    assert (archive / COURSE_DIR / "2026 Fall" / "01 2026-09-01 Fruit basics" / "video.mp4").exists()


def test_failed_global_stage_marks_rows_error(archive):
    sb = FakeSB([row(9, "notebook", "demo.ipynb")], {"inbox/70445/s01/notebook/demo.ipynb": b"{}"})

    def global_runner(stage):
        if stage == "build_index":
            raise worker.WorkerError(worker.BUILD_MESSAGES[3])

    w = worker.Worker(sb, archive, lambda *a: None, global_runner)
    w.poll_once()
    assert sb.rows[0]["status"] == "error" and "VOYAGE_API_KEY" in sb.rows[0]["message"]
    assert (archive / COURSE_DIR / "2026 Fall" / "01 2026-09-01 Fruit basics" / "notebooks" / "demo.ipynb").exists()


def test_unexpected_crash_in_a_global_stage_marks_rows_error_and_keeps_polling(archive):
    # Oct 8 code review: only WorkerError was caught around build_index/upload, so an OSError or
    # KeyError from either one killed the worker and left every claimed row "processing".
    sb = FakeSB([row(10, "notebook", "demo.ipynb")], {"inbox/70445/s01/notebook/demo.ipynb": b"{}"})

    def global_runner(stage):
        raise KeyError("records")

    w = worker.Worker(sb, archive, lambda *a: None, global_runner)
    assert w.poll_once() == 1
    assert sb.rows[0]["status"] == "error"
    assert "KeyError" in sb.rows[0]["message"] and "records" not in sb.rows[0]["message"]


def test_loop_survives_an_unexpected_poll_error(archive):
    w = worker.Worker(FakeSB([], {}), archive, lambda *a: None, lambda st: None)
    polls = []

    def boom():
        polls.append(1)
        if len(polls) == 1:
            raise RuntimeError("disk full")
        w.stopper.stop.set()
        return 0

    w.poll_once = boom
    w.loop(interval=0)
    assert len(polls) == 2


def test_only_uploaded_rows_are_claimed(archive):
    rows = [row(1, "transcript", "a.vtt", status="pending_upload"), row(2, "transcript", "b.vtt", status="ready")]
    sb = FakeSB(rows, {})
    assert worker.Worker(sb, archive, lambda *a: None, lambda s: None).poll_once() == 0
    assert sb.history == []


def test_new_session_folder_comes_from_the_sessions_row(archive):
    sb = FakeSB(
        [row(3, "slides", "deck.pptx", session=12)],
        {"inbox/70445/s12/slides/deck.pptx": b"PK fake"},
        sessions=[{"course": "70445", "session": 12, "date": "2026-10-06", "title": "Ethics: a case/study"}],
    )
    stages: list[str] = []
    worker.Worker(sb, archive, lambda st, c, s: stages.append(st), lambda s: None).poll_once()
    folder = archive / COURSE_DIR / "2026 Fall" / "12 2026-10-06 Ethics- a case-study"
    assert (folder / "slides.pptx").exists()
    assert stages == ["slides", "deidentify", "align", "clips"]
    assert sb.rows[0]["status"] == "ready"


def test_restart_puts_processing_rows_back(archive):
    sb = FakeSB([row(4, "video", "v.mp4", status="processing")], {})
    assert worker.Worker(sb, archive, lambda *a: None, lambda s: None).recover() == 1
    assert sb.rows[0]["status"] == "uploaded"


def test_single_instance_lock(tmp_path):
    with worker.Lock(tmp_path / "worker.lock"), pytest.raises(worker.AlreadyRunning), \
            worker.Lock(tmp_path / "worker.lock"):
        pass
    with worker.Lock(tmp_path / "worker.lock"):  # free again after release
        pass


def test_hard_stop_releases_rows(archive):
    sb = FakeSB([row(5, "transcript", "c.vtt")], {"inbox/70445/s01/transcript/c.vtt": b"WEBVTT"})
    w = worker.Worker(sb, archive, lambda *a: None, lambda s: None)

    def stop_now(stage, course, session):
        w.stopper.stop.set()
        w.stopper.hard.set()
        raise worker.WorkerError("stage deidentify was stopped")

    w.session_runner = stop_now
    w.poll_once()
    assert sb.rows[0]["status"] == "uploaded" and "retry" in sb.rows[0]["message"]


# ---------------------------------------------------------------- the stages' own packages (Oct 8)

def test_every_stage_runs_with_the_indexer_requirements():
    # align and clips import pillow, scipy and scikit-learn, which the Vercel function's requirements.txt
    # does not (and must not) have.
    reqs = (common.REPO / "indexer" / "requirements.txt").read_text().lower()
    for pkg in ("pillow", "scipy", "scikit-learn", "rapidfuzz", "nicknames", "python-pptx", "pypdf", "nbformat"):
        assert pkg in reqs
    root = (common.REPO / "requirements.txt").read_text().lower()
    assert "scipy" not in root and "scikit-learn" not in root and "pillow" not in root
    for stage, (extras, _args) in worker.EXTERNAL.items():
        assert "indexer/requirements.txt" in extras, stage


def test_startup_says_which_stage_packages_are_missing(monkeypatch):
    import subprocess

    def fake_run(cmd, **kw):
        assert "indexer/requirements.txt" in cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="missing: scipy sklearn\n", stderr="")

    monkeypatch.setattr(worker.subprocess, "run", fake_run)
    problem = worker.stage_env_problem()
    assert "scipy" in problem and "sklearn" in problem and "indexer/requirements.txt" in problem
    monkeypatch.setattr(worker.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr=""))
    assert worker.stage_env_problem() is None
    monkeypatch.setattr(worker.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 2, stdout="", stderr="No solution found"))
    assert "could not install" in worker.stage_env_problem()

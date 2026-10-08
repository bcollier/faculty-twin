"""Local worker: process files uploaded through the Settings page (docs/SPEC.md, Block 8).

Runs on the local build machine (the computer that holds the private archive),
because the rosters, the archive, and ffmpeg are there.

Every 30 seconds it reads the `sources` table for rows with status `uploaded`
(kinds slides | transcript | video | notebook), claims each one by moving it to
`processing` (a conditional update, so a row is never claimed twice), then:

1. downloads `inbox/<course>/s<NN>/<kind>/<file>` from the private bucket into
   the archive session folder (`~/Lecture Archive/<course>/2026 Fall/<NN> <date> <title>/`,
   created from the `sessions` row if needed) as `slides.pdf` / `slides.pptx`,
   `transcript_raw.vtt`, `video.mp4`, or `notebooks/<file>`. A file it replaces
   is moved to `_replaced/` in that folder, never deleted;
2. runs the stages that upload affects, for that session:
     slides      slides, deidentify, align, clips, then build_index, upload
     transcript  deidentify, align, clips, then build_index, upload
     video       align, clips, then build_index, upload
     notebook    slides (it extracts notebooks), then build_index, upload
   build_index and upload run once per poll, after every claimed session;
3. marks each row `ready`, or `error` with a short message.

A stage whose script is not in `indexer/` yet (some are still on other
branches) fails the row with "stage <name> is not installed yet"; the file is
already in the archive, so "Re-run" in Settings retries it after the merge.

Privacy: status messages and log lines carry ids, course and session numbers,
kinds, stage names and exit codes only: never file names, file contents, or
student names. Each external stage's output goes to `_build/logs/worker/`,
which is private and never uploaded.

One instance at a time (a lock file in `_build/`). Ctrl-C or SIGTERM finishes
the current session and stops; a second signal stops the running stage and
puts its rows back to `uploaded`. On start, rows left in `processing` by a
crashed worker go back to `uploaded`.

Run by hand from the repo root:
  uv run --no-project --with-requirements requirements.txt python -m indexer.worker          # loop
  uv run --no-project --with-requirements requirements.txt python -m indexer.worker --once   # one poll

Run at login with launchd (template: indexer/com.collier.facultytwin.worker.plist):
  1. Copy the template to ~/Library/LaunchAgents/ and replace __REPO__ with the
     repo path (for example /Users/jarvis/Code/faculty-twin) and __HOME__ with
     your home folder.
  2. launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.collier.facultytwin.worker.plist
  3. Logs: ~/Library/Logs/facultytwin-worker.log. Stop it with
     launchctl bootout gui/$(id -u)/com.collier.facultytwin.worker
"""

from __future__ import annotations

import argparse
import fcntl
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from indexer import common  # noqa: E402

log = logging.getLogger("facultytwin.worker")

KINDS = ("slides", "transcript", "video", "notebook")
SESSION_STAGES = {
    "slides": ["slides", "deidentify", "align", "clips"],
    "transcript": ["deidentify", "align", "clips"],
    "video": ["align", "clips"],
    "notebook": ["slides"],
}
GLOBAL_STAGES = ["build_index", "upload"]
STAGE_ORDER = ["slides", "deidentify", "align", "clips"]

# External stage scripts: uv extras and arguments. One place to adjust when a stage's CLI changes.
EXTERNAL: dict[str, tuple[list[str], list[str]]] = {
    "slides": (
        ["--with", "python-pptx", "--with", "pillow", "--with", "pypdf", "--with", "nbformat"],
        ["--course", "{course}", "--session", "{session}"],
    ),
    "deidentify": (["--with", "rapidfuzz", "--with", "nicknames"], ["run"]),
    "align": (["--with-requirements", "requirements.txt"], ["--course", "{course}", "--session", "{session}"]),
    "clips": (["--with-requirements", "requirements.txt"], ["--course", "{course}", "--session", "{session}"]),
}
STAGE_TIMEOUT = 3 * 3600
POLL_SECONDS = 30


class WorkerError(RuntimeError):
    """A failure whose message is safe to store in sources.message."""


class StageMissing(WorkerError):
    """A stage script is not installed; the file stays saved so Re-run can finish it later."""


class AlreadyRunning(RuntimeError):
    """Another worker holds the lock file."""


def now_iso() -> str:
    """The current UTC time, ISO 8601, for updated_at columns."""
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------- lock and signals

class Lock:
    """An exclusive, non-blocking file lock, so only one worker polls at a time."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh = None

    def __enter__(self) -> Lock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+")
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._fh.close()
            raise AlreadyRunning(f"another worker holds {self.path}") from exc
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(str(os.getpid()))
        self._fh.flush()
        return self

    def __exit__(self, *exc) -> None:
        if self._fh:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()


class Stopper:
    """First signal: finish the current session, then stop. Second: also kill the running stage."""

    def __init__(self) -> None:
        self.stop = threading.Event()
        self.hard = threading.Event()
        self.child: subprocess.Popen | None = None

    def install(self) -> None:
        signal.signal(signal.SIGINT, self._handle)
        signal.signal(signal.SIGTERM, self._handle)

    def _handle(self, signum, frame) -> None:  # noqa: ARG002
        if self.stop.is_set():
            self.hard.set()
            log.warning("second stop signal: stopping the running stage")
            if self.child and self.child.poll() is None:
                self.child.terminate()
        else:
            log.info("stop requested: finishing the current session")
            self.stop.set()


# ---------------------------------------------------------------- placing files

def _safe(text: str, limit: int = 80) -> str:
    """Text that is safe as one folder or file name (no separators), at most `limit` characters."""
    text = re.sub(r"[/\\:\0]+", "-", str(text or "")).strip(" .")
    return text[:limit]


def ensure_session_folder(sb: common.Supabase, archive: Path, course: str, session: int) -> Path:
    """The session's archive folder, created from the courses and sessions tables when it is new."""
    found = common.session_folder(archive, course, session)
    if found:
        return found
    course_name = common.COURSE_FOLDERS.get(course)
    if not course_name:
        rows = sb.select("courses", {"select": "code,title", "code": f"eq.{course}"})
        title = _safe(rows[0].get("title") if rows else "") or "Course"
        course_name = f"{course[:2]}-{course[2:]} {title}"
    rows = sb.select("sessions", {"select": "date,title", "course": f"eq.{course}", "session": f"eq.{session}"})
    row = rows[0] if rows else {}
    day = str(row.get("date") or date.today().isoformat())[:10]
    title = _safe(row.get("title") or "") or f"Session {session:02d}"
    folder = archive / course_name / common.TERM / f"{session:02d} {day} {title}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def target_name(kind: str, filename: str) -> str:
    """The archive file name an upload of this kind replaces (slides.pdf, transcript_raw.vtt, ...)."""
    ext = Path(filename).suffix.lower()
    if kind == "slides" and ext in (".pdf", ".pptx"):
        return f"slides{ext}"
    if kind == "transcript" and ext == ".vtt":
        return "transcript_raw.vtt"
    if kind == "video" and ext == ".mp4":
        return "video.mp4"
    if kind == "notebook" and ext == ".ipynb":
        return f"notebooks/{_safe(Path(filename).name, 120)}"
    raise WorkerError(f"a {kind} upload must be one of the Settings file types")


def _set_aside(folder: Path, rel: str) -> None:
    """Move a file about to be replaced into _replaced/ with a time stamp, never delete it."""
    path = folder / rel
    if path.exists():
        dest = folder / "_replaced" / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{path.name}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest))


def place(sb: common.Supabase, row: dict[str, Any], folder: Path, keep_pdf: bool = False) -> Path:
    """Download one inbox object into the session folder. Returns the archive path."""
    rel = target_name(row["kind"], row["path"])
    tmp = folder / f".incoming-{row['id']}{Path(rel).suffix}"
    try:
        size = sb.download_to(row["path"], tmp)
    except common.SupabaseError as exc:
        raise WorkerError("could not download the file from the bucket") from exc
    if size == 0:
        tmp.unlink(missing_ok=True)
        raise WorkerError("the uploaded file is empty")
    _set_aside(folder, rel)
    if rel == "slides.pptx" and not keep_pdf:
        _set_aside(folder, "slides.pdf")  # a new pptx is the newer deck: rebuild from it
    dest = folder / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp, dest)
    with open(folder / "SOURCES.md", "a", encoding="utf-8") as fh:
        fh.write(f"\n- {now_iso()[:16]}Z: {rel} replaced by a Settings upload (source {row['id']}).\n")
    return dest


# ---------------------------------------------------------------- stages

def run_external(stage: str, course: str, session: int, logs: Path, stopper: Stopper | None = None) -> None:
    """Run one per-session stage script under uv, logging to a file. Raises WorkerError on failure."""
    script = common.REPO / "indexer" / f"{stage}.py"
    if not script.exists():
        raise StageMissing(
            f"stage {stage} is not installed yet (indexer/{stage}.py); the file is saved, use Re-run later")
    extras, args = EXTERNAL[stage]
    uv = shutil.which("uv") or "uv"
    cmd = [uv, "run", "--no-project", *extras, "python", str(script)]
    cmd += [a.format(course=course, session=session) for a in args]
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{course}-s{session:02d}-{stage}.log"
    with open(log_path, "w") as out:
        proc = subprocess.Popen(cmd, cwd=common.REPO, stdout=out, stderr=subprocess.STDOUT)
        if stopper:
            stopper.child = proc
        try:
            code = proc.wait(timeout=STAGE_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            raise WorkerError(f"stage {stage} timed out") from exc
        finally:
            if stopper:
                stopper.child = None
    if stopper and stopper.hard.is_set():
        raise WorkerError(f"stage {stage} was stopped")
    if code != 0:
        raise WorkerError(f"stage {stage} failed (exit {code}); see the local worker log")


BUILD_MESSAGES = {
    3: "index built, but embeddings need VOYAGE_API_KEY in the local .env",
    4: "the leak check found a roster name in the index; see the local build log",
}
UPLOAD_MESSAGES = {
    2: "the index is not ready to upload (embeddings missing or out of date)",
    4: "upload blocked by the roster leak check; nothing was uploaded",
    5: "some objects failed to upload; Re-run to retry",
    6: "Supabase is not configured in the local .env",
}


def run_global(stage: str, archive: Path) -> None:
    """Run build_index or upload in-process, turning its exit code into a message Settings can show."""
    if stage == "build_index":
        from indexer import build_index

        code = build_index.main(["--archive", str(archive)])
        if code:
            raise WorkerError(BUILD_MESSAGES.get(code, f"stage build_index failed (exit {code})"))
    elif stage == "upload":
        from indexer import upload

        code = upload.main(["--archive", str(archive)])
        if code:
            raise WorkerError(UPLOAD_MESSAGES.get(code, f"stage upload failed (exit {code})"))
    else:
        raise StageMissing(f"unknown stage {stage}")


# ---------------------------------------------------------------- one poll

class Worker:
    """One poll: claim uploaded rows, process them by session, rebuild once, mark each row ready or error."""

    def __init__(
        self,
        sb,
        archive: Path,
        session_runner: Callable[[str, str, int], None] | None = None,
        global_runner: Callable[[str], None] | None = None,
        stopper: Stopper | None = None,
    ) -> None:
        self.sb = sb
        self.archive = archive
        self.stopper = stopper or Stopper()
        logs = common.build_dir(archive) / "logs" / "worker"
        self.session_runner = session_runner or (lambda st, c, s: run_external(st, c, s, logs, self.stopper))
        self.global_runner = global_runner or (lambda st: run_global(st, archive))

    def status(self, row_id: int, status: str, message: str | None) -> None:
        """Set one sources row's status and the message Settings shows next to it."""
        values = {"status": status, "message": message, "updated_at": now_iso()}
        self.sb.update("sources", {"id": f"eq.{row_id}"}, values)

    def recover(self) -> int:
        """Put rows a crashed or killed worker left "processing" back in the queue."""
        rows = self.sb.update(
            "sources",
            {"status": "eq.processing"},
            {"status": "uploaded", "message": "Worker restarted; retrying", "updated_at": now_iso()},
        )
        if rows:
            log.info("put %d interrupted rows back to uploaded", len(rows))
        return len(rows or [])

    def claim(self) -> list[dict[str, Any]]:
        """Move up to 20 uploaded rows to processing. The status match makes a claim atomic."""
        rows = self.sb.select(
            "sources", {"select": "*", "status": "eq.uploaded", "order": "updated_at.asc", "limit": "20"}
        )
        claimed = []
        for row in rows:
            if row.get("kind") not in KINDS:
                self.status(row["id"], "error", "unknown kind; expected slides, transcript, video, or notebook")
                continue
            got = self.sb.update(
                "sources",
                {"id": f"eq.{row['id']}", "status": "eq.uploaded"},
                {"status": "processing", "message": "Processing locally", "updated_at": now_iso()},
            )
            if got:
                claimed.append(got[0])
                log.info("source %s: %s s%02d %s -> processing",
                         row["id"], row["course"], int(row["session"]), row["kind"])
        return claimed

    def poll_once(self) -> int:
        """Process every uploaded row once. Returns how many rows were claimed."""
        claimed = self.claim()
        if not claimed:
            return 0
        done = self._run_session_groups(claimed)
        if done and not self.stopper.hard.is_set():
            self._rebuild_and_finish(done)
        elif done:
            self._release([r for r, _ in done])
        return len(claimed)

    def _run_session_groups(self, claimed: list[dict[str, Any]]) -> list[tuple[dict[str, Any], list[str]]]:
        """Place each session's files and run its stages. Returns (row, stages run) for the groups that succeeded.

        A failed group is marked error (or released when a hard stop interrupted it); the others carry on.
        """
        groups: dict[tuple[str, int], list[dict[str, Any]]] = {}
        for row in claimed:
            groups.setdefault((str(row["course"]), int(row["session"])), []).append(row)
        done: list[tuple[dict[str, Any], list[str]]] = []
        for (course, session), rows in groups.items():
            if self.stopper.hard.is_set():
                self._release(rows)
                continue
            try:
                stages = self._session(course, session, rows)
                done.extend((r, stages) for r in rows)
            except WorkerError as exc:
                if self.stopper.hard.is_set():
                    self._release(rows)
                else:
                    self._fail(rows, str(exc))
            except Exception as exc:  # never let one bad file stop the worker
                log.exception("source group %s s%02d crashed", course, session)
                self._fail(rows, f"unexpected {type(exc).__name__} in the worker; see the local worker log")
        return done

    def _rebuild_and_finish(self, done: list[tuple[dict[str, Any], list[str]]]) -> None:
        """Rebuild the index and upload once for every processed session, then mark those rows ready."""
        rows = [r for r, _ in done]
        try:
            for stage in GLOBAL_STAGES:
                log.info("running %s", stage)
                self.global_runner(stage)
        except WorkerError as exc:
            self._fail(rows, str(exc))
            return
        except Exception as exc:  # a crash in build_index or upload must not leave rows "processing"
            log.exception("global stage crashed")
            self._fail(rows, f"unexpected {type(exc).__name__} while rebuilding the index; see the local worker log")
            return
        for row, stages in done:
            self.status(row["id"], "ready", "Processed: " + ", ".join(stages + GLOBAL_STAGES))
            log.info("source %s -> ready", row["id"])

    def _session(self, course: str, session: int, rows: list[dict[str, Any]]) -> list[str]:
        folder = ensure_session_folder(self.sb, self.archive, course, session)
        has_pdf = any(r["kind"] == "slides" and r["path"].lower().endswith(".pdf") for r in rows)
        for row in sorted(rows, key=lambda r: r["path"].lower().endswith(".pptx")):
            place(self.sb, row, folder, keep_pdf=has_pdf)
        wanted = {st for r in rows for st in SESSION_STAGES[r["kind"]]}
        stages = [st for st in STAGE_ORDER if st in wanted]
        for stage in stages:
            log.info("%s s%02d: running %s", course, session, stage)
            self.session_runner(stage, course, session)
        return stages

    def _fail(self, rows: list[dict[str, Any]], message: str) -> None:
        for row in rows:
            self.status(row["id"], "error", message[:300])
            log.warning("source %s -> error: %s", row["id"], message)

    def _release(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            self.status(row["id"], "uploaded", "Worker stopped; it will retry")

    def loop(self, interval: float = POLL_SECONDS) -> None:
        """Poll until a stop signal. A failed poll is logged and the next one runs on schedule."""
        self.recover()
        log.info("worker started; polling every %ss", int(interval))
        while not self.stopper.stop.is_set():
            try:
                self.poll_once()
            except common.SupabaseError as exc:
                log.warning("poll failed: %s", exc)
            except Exception:  # keep polling; the rows involved were marked error where possible
                log.exception("poll crashed")
            self.stopper.stop.wait(interval)
        log.info("worker stopped")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Process Settings uploads on the local build machine")
    ap.add_argument("--archive", help="Lecture Archive folder (default ~/Lecture Archive or $LECTURE_ARCHIVE)")
    ap.add_argument("--once", action="store_true", help="poll once and exit")
    ap.add_argument("--interval", type=float, default=POLL_SECONDS)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    common.load_env()
    archive = common.archive_dir(a.archive)
    try:
        sb = common.Supabase()
    except common.SupabaseError as exc:
        log.error("%s", exc)
        return 6
    stopper = Stopper()
    stopper.install()
    try:
        with Lock(common.build_dir(archive) / "worker.lock"):
            worker = Worker(sb, archive, stopper=stopper)
            if a.once:
                worker.recover()
                worker.poll_once()
            else:
                worker.loop(a.interval)
    except AlreadyRunning as exc:
        log.error("not starting: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""scripts/live_smoke.py against a TEST FAKE deployed site (httpx.MockTransport, no network)."""

from __future__ import annotations

import json

import httpx
import pytest

from scripts import live_smoke

NO_ENV_FILE = "/nonexistent/faculty-twin-test.env"  # keeps the script from reading a real .env in tests
PASS = "smoke-secret-passcode"
ADMIN_PASS = "smoke-admin-secret"
STORAGE = "https://storage.test/object/sign/twin-content"
IMAGE_URL = f"{STORAGE}/slides/70445/s06/70445-s06-012.webp?token=signed-image-token"
AUDIO_URL = f"{STORAGE}/audio/edge-andrew/abc.mp3?token=signed-audio-token"
LIVE_AUDIO = "/api/audio?t=abc&s=sig&v=edge"
CHIP = "What is k-means?"


class FakeSite:
    """TEST FAKE deployed site. Records every request so tests can check what the script fetched."""

    def __init__(self, passcode=PASS, admin_passcode=ADMIN_PASS, broken_faq=False, chip_audio=AUDIO_URL,
                 image_type="image/webp", course_info_kind="course_info", topics=True):
        self.passcode, self.admin_passcode = passcode, admin_passcode
        self.broken_faq, self.chip_audio, self.image_type = broken_faq, chip_audio, image_type
        self.course_info_kind, self.topics = course_info_kind, topics
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, cookie = request.url.path, request.headers.get("cookie", "")
        if request.url.host == "storage.test":
            assert request.headers.get("range") == "bytes=0-1023"
            ctype = self.image_type if "/slides/" in path else "audio/mpeg"
            return httpx.Response(206, headers={"content-type": ctype}, content=b"\0" * 1024)
        if path == "/api/health":
            return httpx.Response(200, json={"ok": True})
        if path == "/api/login":
            if json.loads(request.content)["passcode"] != self.passcode:
                return httpx.Response(401, json={"detail": "That passcode is not right."})
            return httpx.Response(204, headers={"set-cookie": "ft_session=abc; Path=/; HttpOnly"})
        if path == "/api/admin/login":
            if json.loads(request.content)["passcode"] != self.admin_passcode:
                return httpx.Response(401, json={"detail": "That admin passcode is not right."})
            return httpx.Response(204, headers={"set-cookie": "ft_admin=xyz; Path=/; HttpOnly"})
        if path == "/api/admin/status":
            if "ft_admin=xyz" not in cookie:
                return httpx.Response(401, json={"detail": "Not signed in"})
            return httpx.Response(200, json={"keys": {"A": True, "B": True, "C": False},
                                             "content": {"loaded": True}, "today": {"questions": 7}})
        if "ft_session=abc" not in cookie:
            return httpx.Response(401, json={"detail": "Not signed in"})
        if path == "/api/topics":
            return httpx.Response(200, json=[{"question": CHIP, "course": "70445"}] if self.topics else [])
        if path == "/api/audio":
            raise AssertionError("the smoke check must never fetch a live voice link")
        if path == "/api/ask":
            q = json.loads(request.content)["question"]
            if q == live_smoke.COVERED_QUESTION:
                return httpx.Response(200, json={"covered": True, "segments": [
                    {"n": 1, "image": IMAGE_URL, "audio": LIVE_AUDIO}]})
            if q == live_smoke.FAQ_QUESTION and not self.broken_faq:
                return httpx.Response(200, json={"covered": False, "kind": "faq", "segments": []})
            if q == live_smoke.COURSE_INFO_QUESTION:
                return httpx.Response(200, json={"covered": True, "kind": self.course_info_kind,
                                                 "message": "From the syllabus.", "segments": []})
            if q == CHIP:
                return httpx.Response(200, json={"covered": True, "segments": [
                    {"n": 1, "image": IMAGE_URL, "audio": self.chip_audio}]})
            return httpx.Response(200, json={"covered": False, "segments": []})
        return httpx.Response(404)


@pytest.fixture
def secrets(monkeypatch):
    monkeypatch.setenv("STUDENT_PASSCODE", PASS)
    monkeypatch.setenv("ADMIN_PASSCODE", ADMIN_PASS)


def smoke(site: FakeSite, *extra: str) -> int:
    return live_smoke.main(["--base-url", "https://site.test", "--env-file", NO_ENV_FILE, *extra],
                           transport=site.transport())


def test_every_step_passes_and_no_secret_is_printed(secrets, capsys):
    site = FakeSite()
    code = smoke(site)
    out = capsys.readouterr().out
    assert code == 0 and "All steps passed." in out, out
    for name in ["health", "login", "topics", "ask covered", "slide image", "ask off-topic", "ask FAQ",
                 "ask course info", "ask chip", "chip audio", "admin login", "admin status"]:
        assert name in out, name
    assert "kind=faq" in out and "kind=course_info" in out and "covered=True" in out
    assert "keys 2/3 set" in out and "questions today=7" in out
    for secret in (PASS, ADMIN_PASS, "signed-image-token", "signed-audio-token", "ft_session", "ft_admin"):
        assert secret not in out, secret


def test_every_request_is_tagged_as_smoke_traffic(secrets):
    site = FakeSite()
    smoke(site)
    assert site.requests
    assert all(r.headers.get("x-ft-source") == "smoke" for r in site.requests if r.url.host == "site.test")
    asks = [json.loads(r.content)["question"] for r in site.requests if r.url.path == "/api/ask"]
    assert asks == [live_smoke.COVERED_QUESTION, live_smoke.OFF_TOPIC_QUESTION, live_smoke.FAQ_QUESTION,
                    live_smoke.COURSE_INFO_QUESTION, CHIP]
    assert len(asks) <= 5  # the per-visitor per-minute cap


def test_media_is_peeked_with_a_range_request_never_downloaded(secrets):
    site = FakeSite()
    smoke(site)
    media = [r for r in site.requests if r.url.host == "storage.test"]
    assert [r.url.path.rsplit("/", 1)[-1] for r in media] == ["70445-s06-012.webp", "abc.mp3"]
    assert all(r.headers["range"] == "bytes=0-1023" for r in media)
    assert not any(r.url.path == "/api/audio" for r in site.requests)


def test_live_voice_links_are_never_fetched(secrets, capsys):
    site = FakeSite(chip_audio=LIVE_AUDIO)
    assert smoke(site) == 0
    out = capsys.readouterr().out
    assert "live voice links only" in out
    assert not any(r.url.path == "/api/audio" for r in site.requests)


def test_captions_only_answer_skips_the_audio_check(secrets, capsys):
    assert smoke(FakeSite(chip_audio=None)) == 0
    assert "captions only" in capsys.readouterr().out


def test_wrong_media_type_fails(secrets, capsys):
    assert smoke(FakeSite(image_type="text/html")) == 1
    assert "FAIL slide image" in capsys.readouterr().out


def test_course_info_answered_another_way_fails(secrets, capsys):
    assert smoke(FakeSite(course_info_kind="faq")) == 1
    assert "FAIL ask course info" in capsys.readouterr().out


def test_no_suggested_questions_fails_the_chip_step(secrets, capsys):
    assert smoke(FakeSite(topics=False)) == 1
    assert "FAIL ask chip" in capsys.readouterr().out


def test_admin_step_is_skipped_without_the_admin_passcode(monkeypatch, capsys):
    monkeypatch.setenv("STUDENT_PASSCODE", PASS)
    monkeypatch.delenv("ADMIN_PASSCODE", raising=False)
    site = FakeSite()
    assert smoke(site) == 0
    out = capsys.readouterr().out
    assert "skipped: ADMIN_PASSCODE is not set" in out
    assert not any(r.url.path.startswith("/api/admin/") for r in site.requests)


def test_no_admin_flag_skips_settings(secrets):
    site = FakeSite()
    assert smoke(site, "--no-admin") == 0
    assert not any(r.url.path.startswith("/api/admin/") for r in site.requests)


def test_wrong_admin_passcode_fails_without_printing_it(monkeypatch, capsys):
    monkeypatch.setenv("STUDENT_PASSCODE", PASS)
    monkeypatch.setenv("ADMIN_PASSCODE", "a-wrong-admin-passcode")
    assert smoke(FakeSite()) == 1
    out = capsys.readouterr().out
    assert "FAIL admin login" in out and "a-wrong-admin-passcode" not in out


def test_json_output(secrets, capsys):
    assert smoke(FakeSite(), "--json") == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True and report["base_url"] == "https://site.test"
    names = [s["name"] for s in report["steps"]]
    assert names[:3] == ["health", "login", "topics"] and "chip audio" in names and "admin status" in names
    assert all(set(s) == {"name", "status", "ms", "ok", "note"} for s in report["steps"])
    assert PASS not in json.dumps(report) and ADMIN_PASS not in json.dumps(report)


def test_json_output_on_failure(monkeypatch, capsys):
    monkeypatch.setenv("STUDENT_PASSCODE", PASS)
    assert smoke(FakeSite(passcode="other"), "--json") == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False and report["steps"][-1] == {
        "name": "login", "status": 401, "ms": report["steps"][-1]["ms"], "ok": False,
        "note": "That passcode is not right."}


def test_reports_a_wrong_passcode(secrets, monkeypatch, capsys):
    code = smoke(FakeSite(passcode="other"))
    out = capsys.readouterr().out
    assert code == 1 and "FAIL login" in out and "401" in out and PASS not in out


def test_flags_a_wrong_answer(secrets, capsys):
    assert smoke(FakeSite(broken_faq=True)) == 1
    assert "FAIL ask FAQ" in capsys.readouterr().out


def test_without_a_passcode(monkeypatch, capsys):
    monkeypatch.delenv("STUDENT_PASSCODE", raising=False)
    assert smoke(FakeSite()) == 1
    assert "STUDENT_PASSCODE is not set" in capsys.readouterr().out


def test_unreachable_site(secrets, capsys):
    def down(request):
        raise httpx.ConnectError("down", request=request)

    code = live_smoke.main(["--base-url", "https://site.test", "--env-file", NO_ENV_FILE],
                           transport=httpx.MockTransport(down))
    out = capsys.readouterr().out
    assert code == 1 and "request failed: ConnectError" in out


def test_smoke_tag_and_legacy_questions_unchanged():
    from app import usage

    # Rows from before the `source` column are recognised by these three questions only.
    assert set(usage.SMOKE_QUESTIONS) == {live_smoke.COVERED_QUESTION, live_smoke.OFF_TOPIC_QUESTION,
                                          live_smoke.FAQ_QUESTION}

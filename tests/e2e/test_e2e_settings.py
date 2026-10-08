"""Settings (public/admin.html) in a real browser, against the ?mock=1 API: every section loads."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

expect = pytest.importorskip("playwright.sync_api").expect

SECTIONS = ["sec-model", "sec-voice", "sec-courses", "sec-limits", "sec-activity", "sec-prompts", "sec-analytics", "sec-evals"]


def sign_in(page, base_url):
    page.goto(f"{base_url}/admin.html?mock=1")
    expect(page.locator("#a-login")).to_be_visible()
    page.fill("#a-passcode", "wrong")
    page.click("#a-login-form button[type=submit]")
    expect(page.locator("#a-login-error")).to_contain_text("didn't work")
    page.fill("#a-passcode", "admin")
    page.click("#a-login-form button[type=submit]")
    expect(page.locator("#a-app")).to_be_visible()


def test_every_settings_section_loads(page, base_url):
    sign_in(page, base_url)
    for sec in SECTIONS:
        section = page.locator(f"#{sec}")
        expect(section).to_be_attached()
        page.click(f".admin-nav a[href='#{sec}']")
        expect(section).to_be_visible()
        expect(section.locator("h2").first).not_to_be_empty()


def test_courses_activity_and_prompts_render_mock_data(page, base_url):
    sign_in(page, base_url)
    # Courses: one block per course, with the "what exists" pills from the session's `has` flags.
    expect(page.locator("#course-blocks .course-block")).to_have_count(2)
    expect(page.locator("#course-blocks .pill.ok").first).to_be_visible()
    # Activity: the log table fills from {rows: [...]}.
    expect(page.locator("#log-body tr").first).to_be_visible()
    assert page.locator("#log-body tr").count() > 5
    # Prompts: the three mock prompts are listed.
    page.click(".admin-nav a[href='#sec-prompts']")
    expect(page.locator("#sec-prompts")).to_contain_text("Narration (what the voice says)")


def test_signed_out_settings_shows_the_login(page, base_url):
    page.goto(f"{base_url}/admin.html?mock=1")
    expect(page.locator("#a-login")).to_be_visible()
    expect(page.locator("#a-app")).to_be_hidden()

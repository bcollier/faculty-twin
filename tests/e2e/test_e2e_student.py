"""The student page (public/index.html) in a real browser, against the ?mock=1 API.

Scenario words come from the header of public/dev/mock.js (stanley, faqmeet, busy, ...).
"""

from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.e2e

expect = pytest.importorskip("playwright.sync_api").expect


def open_page(page, base_url, extra=""):
    page.goto(f"{base_url}/index.html?mock=1{extra}")


def sign_in(page, base_url, extra=""):
    open_page(page, base_url, extra)
    expect(page.locator("#screen-login")).to_be_visible()
    page.fill("#passcode", "demo")
    page.click("#login-form button[type=submit]")
    expect(page.locator("#screen-app")).to_be_visible()


def ask(page, question):
    """Ask from the idle box, or from the dock once an answer is on screen."""
    if page.locator("#idle-q").is_visible():
        page.fill("#idle-q", question)
        page.click("#idle-form button[type=submit]")
    else:
        page.fill("#dock-q", question)
        page.press("#dock-q", "Enter")


def stage_title(page):
    return page.locator("#stage-message-title")


def wait_for_player(page):
    expect(page.locator("#player")).to_be_visible(timeout=15_000)
    expect(page.locator("#dots button")).to_have_count(4)


def current_dot(page):
    dots = page.locator("#dots button")
    for i in range(dots.count()):
        if dots.nth(i).get_attribute("aria-current") == "step":
            return i
    return None


# ---------------------------------------------------------------- passcode

def test_wrong_passcode_shows_an_error_then_demo_gets_in(page, base_url):
    open_page(page, base_url)
    expect(page.locator("#screen-login")).to_be_visible()
    page.click("#login-form button[type=submit]")
    expect(page.locator("#login-error")).to_have_text("Enter the passcode first.")
    page.fill("#passcode", "not-the-passcode")
    page.click("#login-form button[type=submit]")
    expect(page.locator("#login-error")).to_contain_text("That passcode did not work")
    expect(page.locator("#screen-app")).to_be_hidden()
    page.fill("#passcode", "demo")
    page.click("#login-form button[type=submit]")
    expect(page.locator("#screen-app")).to_be_visible()
    expect(page.locator("#idle-chips .chip").first).to_be_visible()
    assert page.locator("#idle-chips .chip").count() <= 8  # the chip cap


def test_too_many_login_tries(page, base_url):
    open_page(page, base_url)
    page.fill("#passcode", "busy")
    page.click("#login-form button[type=submit]")
    expect(page.locator("#login-error")).to_contain_text("Too many tries")


# ---------------------------------------------------------------- answers

def test_chip_answer_plays_a_walkthrough(page, base_url):
    sign_in(page, base_url)
    chip = page.locator("#idle-chips .chip").first
    chip_text = chip.evaluate("n => n.firstChild.textContent")
    chip.click()
    wait_for_player(page)
    expect(page.locator("#stage")).to_be_visible()
    expect(page.locator("#slide-img")).to_have_attribute("src", re.compile(r"^data:image/svg\+xml"))
    expect(page.locator("#caption")).not_to_be_empty()
    assert current_dot(page) == 0
    # The question and a summary land in the chat log.
    expect(page.locator(".msg-user").first).to_have_text(chip_text)
    expect(page.locator(".msg-twin").first).to_contain_text("slide")


def test_next_advances_segment_by_segment_then_finishes(page, base_url):
    sign_in(page, base_url)
    ask(page, "Explain the placeholder method")
    wait_for_player(page)
    expect(page.locator("#btn-prev")).to_be_disabled()
    first_src = page.locator("#slide-img").get_attribute("src")

    page.click("#btn-next")
    expect(page.locator("#dots button").nth(1)).to_have_attribute("aria-current", "step")
    assert page.locator("#slide-img").get_attribute("src") != first_src
    expect(page.locator("#code-panel")).to_be_visible()  # part 2 has code from class
    expect(page.locator("#code-body")).to_contain_text("placeholder_score")
    expect(page.locator("#btn-prev")).to_be_enabled()

    page.click("#btn-prev")
    expect(page.locator("#dots button").nth(0)).to_have_attribute("aria-current", "step")
    expect(page.locator("#code-panel")).to_be_hidden()

    for i in (1, 2, 3):
        page.click("#btn-next")
        expect(page.locator("#dots button").nth(i)).to_have_attribute("aria-current", "step")
    expect(page.locator("#btn-next")).to_have_attribute("aria-label", "Finish")
    page.click("#btn-next")
    expect(page.locator("#btn-next")).to_be_disabled()
    expect(page.locator("#followups")).to_be_visible()
    expect(page.locator("#followup-chips .chip")).to_have_count(2)


def test_clip_swaps_the_slide_for_the_class_video_and_back(page, base_url):
    sign_in(page, base_url)
    ask(page, "Explain the placeholder method")
    wait_for_player(page)
    expect(page.locator("#clip-btn")).to_be_hidden()  # part 1 has no clip
    page.click("#btn-next")
    page.click("#btn-next")
    expect(page.locator("#dots button").nth(2)).to_have_attribute("aria-current", "step")
    expect(page.locator("#clip-btn")).to_be_visible()
    page.click("#clip-btn")
    expect(page.locator("#clip-video")).to_be_visible()
    expect(page.locator("#slide-img")).to_be_hidden()
    expect(page.locator("#clip-back")).to_be_visible()
    assert page.locator("#clip-video").get_attribute("src")
    page.click("#clip-back")
    expect(page.locator("#slide-img")).to_be_visible()
    expect(page.locator("#clip-video")).to_be_hidden()


def test_broken_clip_hides_the_button_and_keeps_the_slide(page, base_url):
    sign_in(page, base_url)
    ask(page, "badclip please")
    wait_for_player(page)
    page.click("#btn-next")
    page.click("#btn-next")
    clip_btn = page.locator("#clip-btn")
    if clip_btn.is_visible():
        clip_btn.click()
    expect(page.locator("#slide-img")).to_be_visible()
    expect(clip_btn).to_be_hidden()


def test_not_covered(page, base_url):
    sign_in(page, base_url)
    ask(page, "Who won the stanley cup?")
    expect(stage_title(page)).to_have_text("I don't have course material on that.")
    expect(page.locator("#player")).to_be_hidden()
    expect(page.locator("#stage-message-chips .chip").first).to_be_visible()  # topics to try instead


def test_faq_card_with_a_link_button(page, base_url):
    sign_in(page, base_url)
    ask(page, "faqmeet")
    expect(stage_title(page)).to_have_text("Meeting with me")
    expect(page.locator("#stage-message-text")).to_contain_text("Placeholder FAQ answer")
    expect(page.locator("#stage-message-actions button", has_text="Book a 30-minute meeting")).to_be_visible()
    expect(page.locator("#player")).to_be_hidden()


def test_faq_card_with_ta_contacts(page, base_url):
    sign_in(page, base_url)
    ask(page, "faqta")
    expect(stage_title(page)).to_have_text("Rescheduling a presentation")
    page.click("#stage-message-actions button:has-text('Contact the 45-884 TA')")
    dialog = page.locator("#contact-dialog")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("ta-two@example.edu")
    expect(dialog).to_contain_text("TA for 45-884")


def test_canvas_card(page, base_url):
    sign_in(page, base_url)
    ask(page, "canvasinfo")
    expect(page.locator("#stage-message-label")).to_have_text("From Canvas")
    expect(stage_title(page)).to_have_text("Syllabus: placeholder policy")
    expect(page.locator("#stage-message-actions button")).to_have_count(2)
    expect(page.locator("#player")).to_be_hidden()


def test_logistics_referral(page, base_url):
    sign_in(page, base_url)
    ask(page, "logistics")
    expect(stage_title(page)).to_have_text("That one is for me directly.")
    expect(page.locator("#stage-message-actions button", has_text="Contact the TA")).to_be_visible()


def test_answer_from_the_other_course_says_so(page, base_url):
    # Spec step 7c: the course filter had nothing, so the other course's slides answer, labeled plainly.
    sign_in(page, base_url)
    ask(page, "crosscourse")
    wait_for_player(page)
    note = page.locator("#cross-note")
    expect(note).to_be_visible()
    expect(note).to_have_text(re.compile(r"^My 45-884 slides don't cover that, but I taught it in 70-445"))
    expect(page.locator(".msg-twin").last).to_contain_text("My 45-884 slides don't cover that")
    # The next ordinary answer has no note.
    ask(page, "Explain the placeholder method")
    expect(page.locator(".msg-twin")).to_have_count(2)
    wait_for_player(page)
    expect(note).to_be_hidden()
    expect(page.locator(".msg-twin").last).to_contain_text("Here are 4 slides")


def test_web_card_with_and_without_closest_material(page, base_url):
    sign_in(page, base_url)
    ask(page, "webanswer")
    expect(page.locator("#stage-message-label")).to_have_text("Beyond my slides: from the web")
    expect(page.locator("#stage-message-extra h3", has_text="Closest material in my courses")).to_be_visible()
    expect(page.locator(".related-slides li")).to_have_count(3)
    # Spec step 7b: nothing cleared the related-slide floor, so the card shows its sources only.
    ask(page, "webanswer norelated")
    expect(page.locator(".msg-twin")).to_have_count(2)
    expect(page.locator("#stage-message-extra h3", has_text="Sources")).to_be_visible()
    expect(page.locator("#stage-message-extra h3", has_text="Closest material")).to_have_count(0)
    expect(page.locator(".related-slides")).to_have_count(0)
    expect(page.locator("#stage-message-text")).to_contain_text("Placeholder web answer")


# ---------------------------------------------------------------- error states

@pytest.mark.parametrize(
    "question,title,retry",
    [
        ("busy", "That's a lot of questions in a short time.", True),
        ("offline", "I can't reach the server right now.", True),
        ("unfinished", "This part isn't finished yet.", False),
    ],
)
def test_error_states(page, base_url, question, title, retry):
    sign_in(page, base_url)
    ask(page, question)
    expect(stage_title(page)).to_have_text(title)
    expect(page.locator("#player")).to_be_hidden()
    expect(page.locator("#stage-message-actions button", has_text="Try again")).to_have_count(1 if retry else 0)


def test_session_expiry_returns_to_the_passcode_and_asks_again(page, base_url):
    sign_in(page, base_url)
    ask(page, "expire")
    expect(page.locator("#screen-login")).to_be_visible()
    expect(page.locator("#login-note")).to_have_text("Your session ran out. Enter the passcode again to keep going.")


def test_server_unreachable_at_boot_then_retry(page, base_url):
    open_page(page, base_url, "&boot=offline")
    expect(page.locator("#screen-offline")).to_be_visible()
    page.click("[data-action=boot-retry]")
    expect(page.locator("#screen-login")).to_be_visible()


def test_audio_failure_falls_back_to_captions(page, base_url):
    sign_in(page, base_url)
    ask(page, "noaudio")
    wait_for_player(page)
    expect(page.locator("#caption")).not_to_be_empty()
    expect(page.locator("#btn-mute")).to_be_disabled()  # captions only: nothing to mute


# ---------------------------------------------------------------- read-along

def test_read_along_sweeps_the_words_and_lights_up_the_slide(page, base_url):
    sign_in(page, base_url)
    ask(page, "Explain the placeholder method")
    wait_for_player(page)
    expect(page.locator("#narration-tag")).to_have_text("Narration")
    expect(page.locator("#narration-voice")).to_have_text("AI voice made from my recordings.")
    words = page.locator("#caption .w")
    assert words.count() == len(page.evaluate("document.querySelector('#caption').textContent").split())
    # The spoken word moves along, the words before it are said, and the slide gets the spotlight.
    expect(page.locator("#caption .w.now")).to_have_count(1)
    expect(page.locator("#slide-frame")).to_have_class(re.compile(r"\bis-speaking\b"))
    expect(page.locator("#caption .w.said").nth(3)).to_be_attached(timeout=8_000)
    # "Set up the idea" is on the placeholder slide: a highlighter mark appears over it on the image.
    expect(page.locator("#slide-marks .slide-mark").first).to_be_attached(timeout=10_000)
    mark = page.locator("#slide-marks .slide-mark").first.bounding_box()
    frame = page.locator("#slide-frame").bounding_box()
    assert frame["x"] <= mark["x"] and mark["x"] + mark["width"] <= frame["x"] + frame["width"]
    assert frame["y"] <= mark["y"] and mark["y"] + mark["height"] <= frame["y"] + frame["height"]
    # Screen readers get the narration a sentence at a time.
    expect(page.locator("#narration-live")).not_to_be_empty()
    # Pausing takes the spotlight off.
    page.click("#btn-play")
    expect(page.locator("#slide-frame")).not_to_have_class(re.compile(r"\bis-speaking\b"))


def test_read_along_reduced_motion_marks_the_sentence(page, base_url):
    page.emulate_media(reduced_motion="reduce")
    sign_in(page, base_url)
    ask(page, "Explain the placeholder method")
    wait_for_player(page)
    expect(page.locator("#caption")).to_have_class(re.compile(r"\breduce\b"))
    expect(page.locator("#caption .w.in-sentence").first).to_be_attached(timeout=8_000)
    first_sentence = page.locator("#caption .w[data-s='0']").count()
    expect(page.locator("#caption .w.in-sentence")).to_have_count(first_sentence)


def test_read_along_in_captions_only(page, base_url):
    sign_in(page, base_url)
    ask(page, "noaudio")
    wait_for_player(page)
    expect(page.locator("#narration-voice")).to_have_text("Captions only")
    expect(page.locator("#caption .w.now")).to_have_count(1, timeout=8_000)


# ---------------------------------------------------------------- arriving from collier.phd

@pytest.mark.parametrize("motion", ["no-preference", "reduce"])
def test_arriving_from_collier_phd_shows_the_frame_then_the_passcode_screen(page, base_url, motion):
    page.emulate_media(reduced_motion=motion)
    open_page(page, base_url, "&from=collier.phd")
    # html.handoff is set before the first paint; the frame then slides away and is removed
    expect(page.locator("#screen-login")).to_be_visible()
    expect(page.locator("#handoff")).to_have_count(0, timeout=5_000)
    assert page.evaluate("location.search") == "?mock=1"  # replaceState dropped only the from parameter
    assert not page.evaluate("document.documentElement.classList.contains('handoff')")
    expect(page.locator(".ho-draw")).to_have_count(0, timeout=5_000)  # the pen outline cleans up after itself
    page.fill("#passcode", "demo")
    page.click("#login-form button[type=submit]")
    expect(page.locator("#screen-app")).to_be_visible()


def test_without_the_parameter_there_is_no_hand_off(page, base_url):
    open_page(page, base_url)
    expect(page.locator("#screen-login")).to_be_visible()
    assert not page.evaluate("document.documentElement.classList.contains('handoff')")
    expect(page.locator("#handoff")).to_be_hidden()

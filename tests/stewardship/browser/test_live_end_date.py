"""A live campaign's end date is checked, reviewed and applied in place (#912).

Campaign settings for a live campaign is served by ``settings_components``:
its one editable setting is the end date, whose Review is answered with the
review (or a refusal) and whose Apply the fixture server redirects to the
page naming the change. The date is checked as it is typed (#944): the live
check is answered, by the posted date, with the real check fragment for a
clear date, a past date, a date before the start or stranded mail, or
made to fail or answer late. A mark outside every region shows nothing
reloaded.
"""

import contextlib
from urllib.parse import parse_qs

import pytest

from .settings_components import (
    BEFORE_START,
    COMBINED_REVIEW,
    END_CHECKS,
    END_REFUSAL,
    LIVE_END,
    LIVE_END_PENDING,
    LIVE_END_REFUSED,
    LIVE_END_REFUSING,
    LIVE_END_REVIEW,
    PASSED,
    STRANDING,
)
from .test_in_place import MARK, MARKED, count_requests
from .test_settings_in_place import answer_reviews
from .waits import has_attribute, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

FORM_TOP = "document.getElementById('settings-form').getBoundingClientRect().top"
# Where Review sits on the page: typing may scroll the field into view.
REVIEW_TOP = (
    "document.querySelector('#settings-form button[type=submit]')"
    ".getBoundingClientRect().top + window.scrollY"
)
SAVED = "2054-10-31"
UNCHANGED = "Choose a different end date first."
EMPTY = "Choose the new end date first."
CHECKED = "/campaign/settings/end-date-check/"
COULD_NOT = (
    "Couldn't check this end date. Check your connection, then change the "
    "date or reload the page."
)
SIGNED_OUT = "Your sign-in has ended. Sign in again, then reload the page."
# A date whose check answers slowly, when a test asks (``held``).
HELD = {"2054-11-15"}
RESTORE = "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"


def answer_checks(page, origin, *, failing=None, held=None):
    """Answer the end date's live checks by the posted date.

    A date in ``failing`` fails instead: status 0 drops the connection, any
    other is answered with that status. A date in ``held`` is not answered
    yet: its route is appended to that list, for the test to answer late.
    Both may change as the test runs. Returns the dates checked, in order.
    """
    checked = []
    failing = {} if failing is None else failing

    def answer(route):
        """Serve the check fragment ``END_CHECKS`` names for the posted date."""
        typed = parse_qs(route.request.post_data or "")["end_date"][0]
        checked.append(typed)
        if typed in failing:
            if failing[typed]:
                route.fulfill(status=failing[typed], body="{}")
            else:
                route.abort()
            return
        if held is not None and typed in HELD:
            held.append(route)
            return
        fulfil(route, origin)

    page.route(lambda url: url.endswith(CHECKED), answer)
    return checked


def fulfil(route, origin):
    """Answer a held or current check with the fragment for its date."""
    typed = parse_qs(route.request.post_data or "")["end_date"][0]
    fetched = route.fetch(url=origin + END_CHECKS[typed], method="GET")
    route.fulfill(response=fetched)


def enabled(locator):
    """Wait until ``locator`` is enabled: the live check answers after a pause."""
    from playwright.sync_api import expect

    expect(locator).to_be_enabled()


def opened(page, origin, **answers):
    """Open the live campaign's settings, answering its checks; return them."""
    checked = answer_checks(page, origin, **answers)
    page.goto(origin + LIVE_END)
    page.evaluate(MARK)
    return checked


def test_review_and_apply_the_end_date_in_place(page, component_origin):
    """Review waits for a changed date that the check clears; Review and
    Apply answer in place, and the end-date form never moves."""
    reviews = answer_reviews(page, component_origin, LIVE_END_REVIEW, address=LIVE_END)
    posts = count_requests(page, "POST", LIVE_END)
    checked = opened(page, component_origin)
    field = page.locator("#end-date").get_by_label("Campaign end date")
    review = page.get_by_role("button", name="Review changes")
    hint = page.locator("#settings-complete-hint")
    # The saved date: nothing to review yet, and nothing wrong either.
    assert field.input_value() == SAVED and review.is_disabled()
    visible(hint)
    assert hint.inner_text() == UNCHANGED
    assert field.get_attribute("aria-invalid") != "true"
    # The settings themselves stay read-only beside it.
    assert page.locator("fieldset[disabled]").count() == 1
    field.fill("")
    visible(page.get_by_text(EMPTY))
    assert review.is_disabled()
    field.fill("2054-11-15")
    enabled(review)
    hidden(hint)
    assert checked == ["2054-11-15"]
    hidden(page.locator("#id_end_date_error"))
    before = page.evaluate(FORM_TOP)
    review.click()
    panel = page.locator("#settings-review")
    visible(panel.get_by_role("heading", name="Review your changes"))
    assert len(reviews) == 1 and "end_date=2054-11-15" in reviews[0]
    assert "Family codes and links already sent keep working" in panel.inner_text()
    assert page.evaluate(FORM_TOP) == before
    page.get_by_role("button", name="Apply changes").click()
    visible(panel.get_by_role("heading", name="Change status"))
    assert page.url == f"{component_origin}{LIVE_END_PENDING}#settings-review"
    assert page.evaluate(MARKED) == "kept"
    # Review and Apply; the live check posts elsewhere.
    assert len(posts) == 2


@pytest.mark.parametrize(
    ("typed", "message"),
    [("2026-10-05", PASSED), ("2054-09-30", BEFORE_START), ("2054-10-20", STRANDING)],
    ids=["past", "before-start", "stranded-mail"],
)
def test_a_date_that_cannot_be_reviewed_says_why_at_the_field(
    page, component_origin, typed, message
):
    """The check's words show in the field's reserved line, the field is
    marked in error, and Review stays unavailable; nothing moves, and a
    date the check clears takes it all back."""
    checked = opened(page, component_origin)
    field = page.locator("#end-date").get_by_label("Campaign end date")
    review = page.get_by_role("button", name="Review changes")
    error = page.locator("#id_end_date_error")
    top = page.evaluate(REVIEW_TOP)
    field.fill(typed)
    visible(error)
    assert message in error.inner_text()
    has_attribute(field, "aria-invalid", "true")
    assert "id_end_date_error" in field.get_attribute("aria-describedby").split()
    assert review.is_disabled()
    assert message in page.locator("#settings-complete-hint").inner_text()
    assert page.evaluate(REVIEW_TOP) == top
    link = error.get_by_role("link", name="Change the end date and its mailings")
    if message == STRANDING:
        # Mail is resolved with the date in the combined review.
        assert link.get_attribute("href") == COMBINED_REVIEW
    else:
        assert link.count() == 0
    field.fill("2054-11-15")
    hidden(error)
    enabled(review)
    has_attribute(field, "aria-invalid", "false")
    assert "id_end_date_error" not in (field.get_attribute("aria-describedby") or "")
    assert page.evaluate(REVIEW_TOP) == top
    assert checked == [typed, "2054-11-15"]
    assert page.evaluate(MARKED) == "kept"


def test_the_saved_date_again_needs_no_check(page, component_origin):
    """Going back to the saved date clears an error and holds Review as
    unchanged, without asking the server."""
    checked = opened(page, component_origin)
    field = page.locator("#end-date").get_by_label("Campaign end date")
    review = page.get_by_role("button", name="Review changes")
    field.fill("2026-10-05")
    visible(page.locator("#id_end_date_error"))
    field.fill(SAVED)
    hidden(page.locator("#id_end_date_error"))
    has_attribute(field, "aria-invalid", "false")
    assert review.is_disabled()
    assert page.locator("#settings-complete-hint").inner_text() == UNCHANGED
    assert checked == ["2026-10-05"]


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (0, COULD_NOT),
        (503, COULD_NOT),
        (409, COULD_NOT),
        (401, SIGNED_OUT),
        (403, SIGNED_OUT),
    ],
    ids=["network", "unavailable", "stale", "unauthenticated", "denied"],
)
def test_a_check_that_cannot_answer_keeps_review_held(
    page, component_origin, status, message
):
    """Review is offered only when the change is known to be possible, so a
    check that cannot answer holds it and says so at the field, without
    moving anything; the next edit and pageshow check again."""
    failing = {"2054-10-20": status}
    checked = opened(page, component_origin, failing=failing)
    field = page.locator("#end-date").get_by_label("Campaign end date")
    review = page.get_by_role("button", name="Review changes")
    error = page.locator("#id_end_date_error")
    hint = page.locator("#settings-complete-hint")
    top = page.evaluate(REVIEW_TOP)
    field.fill("2054-10-20")
    visible(error)
    assert error.inner_text() == message
    assert hint.inner_text() == message
    assert review.is_disabled()
    # Nothing is known to be wrong with the date itself.
    assert field.get_attribute("aria-invalid") != "true"
    assert "id_end_date_error" in field.get_attribute("aria-describedby").split()
    assert page.evaluate(REVIEW_TOP) == top
    # Showing the page again checks again, and now it answers.
    failing.clear()
    page.evaluate(RESTORE)
    has_attribute(field, "aria-invalid", "true")
    assert STRANDING in error.inner_text()
    assert review.is_disabled()
    # The next edit checks again too.
    failing["2054-11-15"] = status
    field.fill("2054-11-15")
    visible(error.get_by_text(message))
    assert review.is_disabled()
    failing.clear()
    field.fill("2054-10-20")
    visible(error.get_by_text(STRANDING))
    assert checked == ["2054-10-20", "2054-10-20", "2054-11-15", "2054-10-20"]
    assert page.evaluate(REVIEW_TOP) == top
    assert page.evaluate(MARKED) == "kept"


def test_a_new_date_takes_the_old_error_away_before_its_check(page, component_origin):
    """An error belongs to the date it was about: another date takes it away
    at once, while its check is out, so a check that then cannot answer
    shows only that. Typing clears a mark anyway (#592); a page restored
    from history gets its other date back without an edit, and only the
    live check's pageshow re-check sees it."""
    held = []
    opened(page, component_origin, held=held)
    field = page.locator("#end-date").get_by_label("Campaign end date")
    error = page.locator("#id_end_date_error")
    hint = page.locator("#settings-complete-hint")
    field.fill("2026-10-05")
    visible(error.get_by_text(PASSED))
    # As a browser restores a page's values: no input or change event.
    field.evaluate("(node) => { node.value = '2054-11-15'; }")
    page.evaluate(RESTORE)
    # While the new date's check is out, the old error is gone already.
    hidden(error)
    has_attribute(field, "aria-invalid", "false")
    assert hint.inner_text() == "Checking the end date…"
    waited(page, held)
    held[0].abort()
    visible(error.get_by_text(COULD_NOT))
    assert PASSED not in error.inner_text()
    assert field.get_attribute("aria-invalid") != "true"
    assert hint.inner_text() == COULD_NOT
    assert page.get_by_role("button", name="Review changes").is_disabled()


def waited(page, held):
    """Wait until the page has posted a check the test holds."""
    for _ in range(100):
        if held:
            return
        page.wait_for_timeout(50)
    raise AssertionError("The page never posted the held check.")


def test_a_late_answer_for_an_earlier_date_is_ignored(page, component_origin):
    """A slow answer for one date that arrives after another date was typed
    changes nothing: the later date's answer stands."""
    from playwright.sync_api import Error as PlaywrightError

    held = []
    checked = opened(page, component_origin, held=held)
    field = page.locator("#end-date").get_by_label("Campaign end date")
    review = page.get_by_role("button", name="Review changes")
    error = page.locator("#id_end_date_error")
    # The clear date's check waits; a past date is typed and answered first.
    field.fill("2054-11-15")
    waited(page, held)
    assert checked == ["2054-11-15"] and len(held) == 1
    field.fill("2026-10-05")
    visible(error.get_by_text(PASSED))
    # Now the clear date's answer arrives, too late to count.
    with contextlib.suppress(PlaywrightError):  # The page may have dropped it.
        fulfil(held[0], component_origin)
    # Give a wrongly accepted answer time to show; nothing must change.
    page.wait_for_timeout(600)
    assert PASSED in error.inner_text()
    assert review.is_disabled()
    assert field.get_attribute("aria-invalid") == "true"
    assert checked == ["2054-11-15", "2026-10-05"]


def test_the_answer_line_is_a_polite_live_region(page, component_origin):
    """The reserved line is a polite status region (announced, never moved)."""
    opened(page, component_origin)
    line = page.locator(".field-error-line:has(#id_end_date_error)")
    assert line.get_attribute("role") == "status"
    assert line.is_visible()


def test_the_server_still_refuses_a_review_the_check_cleared(page, component_origin):
    """The server checks every Review in full: a refusal the check did not
    foresee (the mail changed meanwhile) is shown in place, linking to the
    combined date-change review for stranded mail."""
    answer_reviews(page, component_origin, LIVE_END_REFUSED, 400, address=LIVE_END)
    opened(page, component_origin)
    page.locator("#end-date").get_by_label("Campaign end date").fill("2054-11-15")
    review = page.get_by_role("button", name="Review changes")
    enabled(review)
    review.click()
    summary = page.locator("#settings-review [data-error-summary]")
    visible(summary)
    assert "would no longer fit the campaign" in summary.inner_text()
    link = page.locator("#settings-review").get_by_role(
        "link", name="Change the end date and its mailings"
    )
    assert link.get_attribute("href") == COMBINED_REVIEW
    assert page.get_by_role("button", name="Apply changes").count() == 0
    assert page.evaluate(MARKED) == "kept"


def test_a_refused_change_settles_in_place_with_its_reason(page, component_origin):
    """The polled status says Not applied and why, without moving the form
    or reloading the page (#944)."""
    page.goto(component_origin + LIVE_END_REFUSING)
    page.evaluate(MARK)
    before = page.evaluate(FORM_TOP)
    panel = page.locator("#settings-review")
    visible(panel.get_by_role("heading", name="Not applied"))
    assert END_REFUSAL in panel.inner_text()
    assert page.evaluate(FORM_TOP) == before
    assert page.evaluate(MARKED) == "kept"
    assert page.url == component_origin + LIVE_END_REFUSING

"""A live campaign's end date is checked, reviewed and applied in place (#912).

Campaign settings for a live campaign is served by ``settings_components``:
its one editable setting is the end date, whose Review is answered with the
review (or a refusal) and whose Apply the fixture server redirects to the
page naming the change. The date is checked as it is typed (#944): the live
check is answered, by the posted date, with the real check fragment for a
clear date, a past date, a date before the start or stranded mail. A mark
outside every region shows nothing reloaded.
"""

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


def answer_checks(page, origin, *, fail=False):
    """Answer the end date's live checks by the posted date (or fail them).

    Returns the dates checked, in order.
    """
    checked = []

    def answer(route):
        """Serve the check fragment ``END_CHECKS`` names for the posted date."""
        typed = parse_qs(route.request.post_data or "")["end_date"][0]
        checked.append(typed)
        if fail:
            route.abort()
            return
        fetched = route.fetch(url=origin + END_CHECKS[typed], method="GET")
        route.fulfill(response=fetched)

    page.route(lambda url: url.endswith(CHECKED), answer)
    return checked


def enabled(locator):
    """Wait until ``locator`` is enabled: the live check answers after a pause."""
    from playwright.sync_api import expect

    expect(locator).to_be_enabled()


def opened(page, origin, *, fail=False):
    """Open the live campaign's settings, answering its checks; return them."""
    checked = answer_checks(page, origin, fail=fail)
    page.goto(origin + LIVE_END)
    page.evaluate(MARK)
    return checked


def test_review_and_apply_the_end_date_in_place(page, component_origin):
    """Review waits for a changed date that the check clears; Review and
    Apply answer in place, and the end-date form never moves."""
    reviews = answer_reviews(page, component_origin, LIVE_END_REVIEW, address=LIVE_END)
    posts = count_requests(page, "POST", LIVE_END)
    checked = opened(page, component_origin)
    field = page.get_by_label("Campaign end date")
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
    field = page.get_by_label("Campaign end date")
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
    field = page.get_by_label("Campaign end date")
    review = page.get_by_role("button", name="Review changes")
    field.fill("2026-10-05")
    visible(page.locator("#id_end_date_error"))
    field.fill(SAVED)
    hidden(page.locator("#id_end_date_error"))
    has_attribute(field, "aria-invalid", "false")
    assert review.is_disabled()
    assert page.locator("#settings-complete-hint").inner_text() == UNCHANGED
    assert checked == ["2026-10-05"]


def test_a_failed_check_leaves_the_server_refusal_to_explain(page, component_origin):
    """When the check cannot answer, Review is available and the server's
    own refusal explains in place, linking to the combined date-change
    review for stranded mail."""
    answer_reviews(page, component_origin, LIVE_END_REFUSED, 400, address=LIVE_END)
    checked = opened(page, component_origin, fail=True)
    page.get_by_label("Campaign end date").fill("2054-10-20")
    review = page.get_by_role("button", name="Review changes")
    enabled(review)
    assert checked == ["2054-10-20"]
    review.click()
    summary = page.locator("#settings-review [data-error-summary]")
    visible(summary)
    assert "would no longer fit the campaign" in summary.inner_text()
    link = page.locator("#settings-review").get_by_role(
        "link", name="Change the end date and its mailings"
    )
    assert link.get_attribute("href") == COMBINED_REVIEW
    assert page.get_by_role("button", name="Apply changes").count() == 0
    assert page.get_by_label("Campaign end date").input_value() == "2054-10-20"
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

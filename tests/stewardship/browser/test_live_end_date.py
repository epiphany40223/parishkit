"""A live campaign's end date is reviewed and applied in place (#912).

Campaign settings for a live campaign is served by ``settings_components``:
its one editable setting is the end date, whose Review is answered with the
review (or a refusal) and whose Apply the fixture server redirects to the
page naming the change. A mark outside every region shows nothing reloaded.
"""

import pytest

from .settings_components import (
    COMBINED_REVIEW,
    END_REFUSAL,
    LIVE_END,
    LIVE_END_PENDING,
    LIVE_END_REFUSED,
    LIVE_END_REFUSING,
    LIVE_END_REVIEW,
)
from .test_in_place import MARK, MARKED, count_requests
from .test_settings_in_place import answer_reviews
from .waits import hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

FORM_TOP = "document.getElementById('settings-form').getBoundingClientRect().top"


def test_review_and_apply_the_end_date_in_place(page, component_origin):
    """Review waits for a date; Review and Apply answer in place, and the
    end-date form never moves under the reader."""
    reviews = answer_reviews(page, component_origin, LIVE_END_REVIEW, address=LIVE_END)
    posts = count_requests(page, "POST", LIVE_END)
    page.goto(component_origin + LIVE_END)
    page.evaluate(MARK)
    field = page.get_by_label("Campaign end date")
    review = page.get_by_role("button", name="Review changes")
    assert field.input_value() == "2054-10-31" and review.is_enabled()
    # The settings themselves stay read-only beside it.
    assert page.locator("fieldset[disabled]").count() == 1
    field.fill("")
    visible(page.get_by_text("Choose the new end date first."))
    assert review.is_disabled()
    field.fill("2054-11-15")
    hidden(page.get_by_text("Choose the new end date first."))
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
    assert len(posts) == 2


def test_a_shortening_that_strands_mail_links_to_the_combined_review(
    page, component_origin
):
    """The refusal is explained in place and leads to Dates and mail
    schedules with the proposed date, where each mailing is resolved."""
    answer_reviews(page, component_origin, LIVE_END_REFUSED, 400, address=LIVE_END)
    page.goto(component_origin + LIVE_END)
    page.evaluate(MARK)
    page.get_by_label("Campaign end date").fill("2054-10-20")
    page.get_by_role("button", name="Review changes").click()
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

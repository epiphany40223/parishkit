"""Campaign form conveniences: period end dates, the commitment period's
start and long selections."""

import pytest

from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def financial(page, component_origin):
    """Open Create the campaign with financial stewardship turned on."""
    page.goto(component_origin + "/campaign-create-long")
    page.get_by_label("Financial stewardship").check()
    return page.locator("form[data-campaign-form]")


def value(page, name):
    """The current value of a named form control."""
    return page.locator(f'[name="{name}"]').input_value()


def test_period_start_fills_an_empty_end_one_year_minus_a_day(page, component_origin):
    """Both periods fill an empty end; a typed end is never replaced."""
    financial(page, component_origin)
    page.locator('[name="financial_start"]').fill("2027-01-01")
    assert value(page, "financial_end") == "2027-12-31"
    visible(page.get_by_text("End date filled in; change it if needed."))
    # A filled-in end follows further edits of the start.
    page.locator('[name="financial_start"]').fill("2027-07-01")
    assert value(page, "financial_end") == "2028-06-30"
    # Leap day: the anniversary is Feb 28, so the period ends Feb 27.
    page.locator('[name="comparison_start"]').fill("2028-02-29")
    assert value(page, "comparison_end") == "2029-02-27"
    # An end the person entered stays.
    page.locator('[name="comparison_end"]').fill("2029-01-15")
    page.locator('[name="comparison_start"]').fill("2028-03-01")
    assert value(page, "comparison_end") == "2029-01-15"


def test_the_commitment_start_follows_the_campaign_end(page, component_origin):
    """The commitment period's start must come after the campaign end
    (#2293): its minimum is the day after, so the browser finds a start on
    the campaign's last day as the server does, and the next day is
    allowed; no overlap confirmation is offered any more."""
    financial(page, component_origin)
    start = page.locator('[name="financial_start"]')
    page.locator('[name="start_date"]').fill("2026-10-01")
    page.locator('[name="end_date"]').fill("2026-10-31")
    start.fill("2026-07-01")
    assert start.get_attribute("min") == "2026-11-01"
    assert start.evaluate("input => input.validity.rangeUnderflow")
    # The boundary days: the campaign's last day is refused, the next is not.
    start.fill("2026-10-31")
    assert start.evaluate("input => input.validity.rangeUnderflow")
    start.fill("2026-11-01")
    assert not start.evaluate("input => input.validity.rangeUnderflow")
    # The day after a year's last day is the next year's first.
    page.locator('[name="end_date"]').fill("2026-12-31")
    assert start.get_attribute("min") == "2027-01-01"
    assert page.locator("[data-overlap-confirmation]").count() == 0


def test_schedule_window_ends_before_the_commitment_period(page, component_origin):
    """The schedules page has no acknowledgement either: its end date's
    maximum is the day before the commitment period starts (2055-01-01), and
    a later day is marked, as it is left, with the server's words in the
    end date's reserved error line (#2293)."""
    page.goto(component_origin + "/schedule-editor-financial")
    assert page.locator("[data-overlap-confirmation]").count() == 0
    end = page.locator('[name="window-end_date"]')
    assert end.get_attribute("max") == "2054-12-31"
    line = page.locator("#id_window-end_date_error")
    end.fill("2055-01-01")
    end.blur()
    assert end.evaluate("input => input.validity.rangeOverflow")
    assert end.get_attribute("aria-invalid") == "true"
    visible(line)
    assert line.inner_text() == (
        "The campaign must end before its commitment period starts on "
        "January 1, 2055: choose an earlier end date."
    )
    # The browser refuses the submission itself: no review is asked for.
    page.get_by_role("button", name="Review changes").click()
    assert not page.locator("#schedule-dates-review").evaluate("dialog => dialog.open")
    end.fill("2054-12-31")
    assert not end.evaluate("input => input.validity.rangeOverflow")
    assert end.get_attribute("aria-invalid") == "false"
    assert line.is_hidden()
    # No emails are saved yet: say so instead of offering an empty list.
    visible(page.get_by_text("No invitation or reminder emails are saved yet"))
    assert page.get_by_role("link", name="Create the emails").get_attribute("href") == (
        "/admin/campaign/content/"
    )


@pytest.mark.parametrize("width", [320, 1280])
def test_long_multi_selects_scroll_and_count_the_selection(
    page, component_origin, width
):
    """Hundreds of Ministries stay usable: a scroll bar, a count, no page scroll."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/campaign-create-long")
    page.get_by_label("Ministry stewardship").check()
    ministries = page.locator('select[name="ministry_duids"]')
    metrics = ministries.evaluate(
        """select => ({
            scroll: select.scrollHeight, client: select.clientHeight,
            overflow: getComputedStyle(select).overflowY,
        })"""
    )
    assert metrics["scroll"] > metrics["client"]
    assert metrics["overflow"] != "hidden"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    count = ministries.locator("xpath=following-sibling::p[@aria-live]")
    # A new campaign starts with every Ministry selected (data-new-campaign).
    assert count.inner_text() == "213 of 213 selected"
    ministries.select_option(["1", "5"])
    assert count.inner_text() == "2 of 213 selected"
    assert "Ctrl" in page.locator("#id_ministry_duids_helptext").inner_text()

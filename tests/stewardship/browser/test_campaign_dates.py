"""Campaign form conveniences: period end dates, overlap and long selections."""

import pytest

from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def financial(page, component_origin):
    """Open the first-campaign page with financial stewardship turned on."""
    page.goto(component_origin + "/setup-campaign")
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


def test_overlap_confirmation_appears_only_while_needed(page, component_origin):
    """Shown when the period overlaps the campaign; hidden and unchecked after."""
    financial(page, component_origin)
    group = page.locator("[data-overlap-confirmation]")
    box = group.locator('input[type="checkbox"]')
    assert group.is_hidden()
    page.locator('[name="start_date"]').fill("2026-10-01")
    page.locator('[name="end_date"]').fill("2026-10-31")
    page.locator('[name="financial_start"]').fill("2027-01-01")
    assert group.is_hidden()
    # The auto-filled end (2027-06-30) makes the period overlap October 2026.
    page.locator('[name="financial_start"]').fill("2026-07-01")
    assert value(page, "financial_end") == "2027-06-30"
    visible(group)
    box.check()
    page.locator('[name="financial_start"]').fill("2027-01-01")
    assert group.is_hidden() and not box.is_checked()


def test_schedule_window_overlap_follows_the_campaign_dates(page, component_origin):
    """The schedules page mirrors the same rule for its fixed financial period."""
    page.goto(component_origin + "/setup-schedules-financial")
    group = page.locator("[data-overlap-confirmation]")
    assert group.is_hidden()
    page.locator('[name="window-end_date"]').fill("2027-01-05")
    visible(group)
    group.locator('input[type="checkbox"]').check()
    page.locator('[name="window-end_date"]').fill("2026-12-31")
    assert group.is_hidden()
    assert not group.locator('input[type="checkbox"]').is_checked()
    # No emails are saved yet: say so instead of offering an empty list.
    visible(page.get_by_text("No invitation or reminder emails are saved yet"))
    assert page.get_by_role("link", name="Create the emails").get_attribute("href") == (
        "/admin/setup/content"
    )


@pytest.mark.parametrize("width", [320, 1280])
def test_long_multi_selects_scroll_and_count_the_selection(
    page, component_origin, width
):
    """Hundreds of Ministries stay usable: a scroll bar, a count, no page scroll."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/setup-campaign")
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
    assert count.inner_text() == "0 of 213 selected"
    ministries.select_option(["1", "5"])
    assert count.inner_text() == "2 of 213 selected"
    assert "Ctrl" in page.locator("#id_ministry_duids_helptext").inner_text()

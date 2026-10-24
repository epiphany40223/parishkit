"""Dates and mail schedules' suggested schedules (#1404 PR 6a).

Dates and mail schedules as the campaign wizard draws it for a campaign with
no schedule saved (``stepper_components.SUGGESTED``): the Suggested schedules
panel lists the starting set, each "Not saved yet", with send times in the
browser's zone; the action row holds Back, Continue without schedules for
now (secondary) and Add these schedules and continue (the primary, last),
which posts only the page's version and request key without a page load,
follows the change, then opens the next step, whose status line says the
schedules were added. A refusal stays on the page with its reason in the
reserved status line, moving nothing.

The panel also lists the suggested report emails (#2038), each ticked with
an empty time: Add is held, its reserved hint saying why, until each ticked
row has a time; unticking a row greys its time in place, so nothing moves;
the save posts the ticks, times and browser zone; and a refused save's
redrawn page marks the missing time at its field.
"""

import json
import re
from urllib.parse import parse_qs

import pytest

from .stepper_components import (
    CONTENT,
    SUGGESTED,
    SUGGESTED_PASSED,
    SUGGESTED_REFUSED,
)
from .test_campaign_subpage_direct_save import applied_line
from .test_components import axe_violations
from .waits import has_text, reads, same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def placed(*locators):
    """Each locator's box in page coordinates, read in one script turn each."""
    return [
        locator.evaluate(
            """(node) => {
                const box = node.getBoundingClientRect();
                return {x: box.x + window.scrollX, y: box.y + window.scrollY,
                        width: box.width, height: box.height};
            }"""
        )
        for locator in locators
    ]


def row(page):
    """The action row's Back, Continue without schedules and Add."""
    actions = page.locator("[data-campaign-step-actions]")
    return (
        actions.locator("[data-campaign-step-back]"),
        actions.locator("[data-campaign-continue] button"),
        actions.locator("[data-campaign-add]"),
    )


@pytest.mark.parametrize("width", [320, 1280])
def test_add_these_schedules_and_continue(page, component_origin, axe_source, width):
    """The suggested set is listed and Add saves it, then the next step opens."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.set_viewport_size({"width": width, "height": 900})
    sent = []

    def note(route):
        """Note the save's posted fields and let it through."""
        if route.request.method == "POST":
            sent.append(parse_qs(route.request.post_data or ""))
        route.continue_()

    page.route(lambda url: url.split("#")[0] == component_origin + SUGGESTED, note)
    page.goto(component_origin + SUGGESTED)
    panel = page.locator("#suggested-schedules")
    visible(panel)
    rows = panel.locator("table.schedule-table tbody tr")
    assert rows.count() == 2
    assert [rows.nth(index).locator("th").inner_text() for index in range(2)] == [
        "Initial invitation",
        "Reminder 1",
    ]
    # Two Family schedules and two report emails, each not saved yet.
    assert panel.locator(".status-badge").all_inner_texts() == ["Not saved yet"] * 4
    # Send times are drawn in the browser's zone, as every schedule's are.
    when = rows.nth(0).locator("time")
    assert re.search(r"\d{1,2}:\d{2}", when.inner_text())
    assert "T" not in when.inner_text()
    reports = page.locator("[data-suggested-report]")
    assert reports.count() == 2
    assert reports.locator("th").all_inner_texts() == ["Daily report", "Weekly report"]
    assert reports.nth(1).locator("td").first.inner_text().startswith("Every Monday")
    assert page.locator("#suggested-report_daily").is_checked()
    back, keep, add = row(page)
    # Each ticked report needs its time first; the reserved hint says so.
    assert add.is_disabled()
    hint = page.locator("#suggested-reports-hint")
    has_text(hint, "Enter the time Daily report sends, or untick it.")
    assert add.get_attribute("aria-describedby") == "suggested-reports-hint"
    page.locator("#suggested-report_daily_time").fill("7:00 AM")
    has_text(hint, "Enter the time Weekly report sends, or untick it.")
    page.locator("#suggested-report_weekly_time").fill("8:30 AM")
    has_text(hint, "")
    assert add.is_enabled()
    assert back.inner_text().startswith("Back")
    assert keep.inner_text() == "Continue without schedules for now"
    assert add.inner_text() == "Add these schedules and continue"
    assert "button-secondary" in (keep.get_attribute("class") or "")
    # One row, the primary last; nothing wider than the page.
    if width >= 1280:
        boxes = [node.bounding_box() for node in (back, keep, add)]
        assert boxes[0]["x"] < boxes[1]["x"] < boxes[2]["x"]
        assert same_place(boxes[1]["y"], boxes[2]["y"], tolerance=1)
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    assert axe_violations(page, axe_source) == []
    add.click()
    page.wait_for_url(component_origin + CONTENT)
    [body] = sent
    assert sorted(body) == [
        "base_digest",
        "csrfmiddlewaretoken",
        "report_daily",
        "report_daily_time",
        "report_weekly",
        "report_weekly_time",
        "request_key",
        "zone",
    ]
    # The time entry writes each time canonically once it is left.
    assert body["report_daily_time"] == ["07:00"]
    assert body["report_weekly"] == ["yes"]
    assert body["zone"] and body["zone"][0]
    applied_line(page, "Suggested schedules added.")
    assert failures == []


def test_a_refused_add_stays_and_moves_nothing(page, component_origin):
    """A refusal (a schedule saved meanwhile) is said in the status line; the
    page stays and the row does not move."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    refusal = {
        "message": "The suggested schedules no longer apply, so nothing was saved.",
        "fix": (
            "Reload the page to see the campaign's schedules, then add any you need."
        ),
    }

    def refuse(route):
        """Answer the save as a stale page (409, JSON)."""
        if route.request.method != "POST":
            route.continue_()
            return
        route.fulfill(
            status=409,
            content_type="application/json",
            body=json.dumps({"refusal": refusal}),
        )

    page.route(lambda url: url.split("#")[0] == component_origin + SUGGESTED, refuse)
    page.goto(component_origin + SUGGESTED)
    _, keep, add = row(page)
    # No report email this time, so Add is not held for their times.
    page.locator("#suggested-report_daily").uncheck()
    page.locator("#suggested-report_weekly").uncheck()
    # Scrolled into view first, so the click itself scrolls nothing.
    add.scroll_into_view_if_needed()
    before = placed(add, keep)
    add.click()
    line = page.locator("#campaign-step-status")
    has_text(line, f"{refusal['message']} {refusal['fix']}")
    assert page.url == component_origin + SUGGESTED
    assert same_place(placed(add, keep), before, tolerance=1)
    # Continue without schedules for now is available again.
    assert keep.is_enabled()
    assert not keep.evaluate("(node) => node.closest('form').inert")
    assert failures == []


def test_a_passed_initial_time_suggests_nothing(page, component_origin):
    """Once the Initial invitation's time has passed there is no panel and no
    Add (it would send at once); Continue without schedules for now stays."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + SUGGESTED_PASSED)
    visible(page.locator("[data-campaign-step-actions]"))
    assert page.locator("#suggested-schedules").count() == 0
    assert page.locator("[data-campaign-add]").count() == 0
    keep = page.locator("[data-campaign-step-actions] button", has_text="Continue")
    assert keep.inner_text() == "Continue without schedules for now"
    assert failures == []


def test_unticking_a_report_greys_its_time_in_place(page, component_origin):
    """An unticked report's time is greyed, not sent, and nothing moves."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    sent = []

    def note(route):
        """Note the save's posted fields and let it through."""
        if route.request.method == "POST":
            sent.append(parse_qs(route.request.post_data or "", keep_blank_values=True))
        route.continue_()

    page.route(lambda url: url.split("#")[0] == component_origin + SUGGESTED, note)
    page.goto(component_origin + SUGGESTED)
    daily = page.locator("#suggested-report_daily_time")
    weekly = page.locator("#suggested-report_weekly_time")
    _, keep, add = row(page)
    add.scroll_into_view_if_needed()
    before = placed(daily, weekly, add, keep)
    page.locator("#suggested-report_daily").uncheck()
    page.locator("#suggested-report_weekly").uncheck()
    assert daily.is_disabled() and weekly.is_disabled()
    assert add.is_enabled()
    assert same_place(placed(daily, weekly, add, keep), before, tolerance=1)
    # Ticked again, the time is asked for again.
    page.locator("#suggested-report_daily").check()
    assert daily.is_enabled() and add.is_disabled()
    page.locator("#suggested-report_daily").uncheck()
    add.click()
    page.wait_for_url(component_origin + CONTENT)
    [body] = sent
    assert sorted(body) == ["base_digest", "csrfmiddlewaretoken", "request_key", "zone"]
    assert failures == []


@pytest.mark.parametrize("width", [320, 1280])
def test_a_refused_report_time_is_marked_at_its_field(
    page, component_origin, axe_source, width
):
    """A save refused for a report's time stays, marking that field.

    The server answers with the page drawn again (400), as the real view
    does when a ticked report has no readable time; the time typed for the
    other report stays as typed.
    """
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + SUGGESTED_REFUSED)
    refused = page.content()

    def refuse(route):
        """Answer the save with the redrawn page (400)."""
        if route.request.method != "POST":
            route.continue_()
            return
        route.fulfill(status=400, content_type="text/html", body=refused)

    page.route(lambda url: url.split("#")[0] == component_origin + SUGGESTED, refuse)
    page.goto(component_origin + SUGGESTED)
    daily = page.locator("#suggested-report_daily_time")
    page.locator("#suggested-report_weekly_time").fill("8:30 AM")
    # The page's gate lets the save go; the server's own check is the one
    # refusing it here (the answer is fixed), as it refuses a time the page
    # could not have checked.
    daily.fill("7:00 AM")
    _, _keep, add = row(page)
    add.click()
    error = page.locator("#suggested-report_daily_time_error")
    has_text(error, "Enter the time it sends, or untick it.")
    assert daily.get_attribute("aria-invalid") == "true"
    assert page.url == component_origin + SUGGESTED
    assert page.locator("#suggested-report_weekly_time").input_value() == "08:30"
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    assert axe_violations(page, axe_source) == []
    assert failures == []


@pytest.mark.parametrize("width", [320, 1280])
def test_typing_a_report_time_moves_nothing(page, component_origin, width):
    """Typing a report's time moves nothing (#2038).

    The shared time entry's "Reads as" line, or a refusal, appears under
    the Daily report's Time as the Admin types (an Administrator's pk-local
    check saw it push the Weekly row and Add down). The cell reserves room
    for the longest of them, so the field itself, the Weekly row and Add
    stay under the pointer (ui-conventions "No layout shift").
    """
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + SUGGESTED)
    daily = page.locator("#suggested-report_daily_time")
    weekly = page.locator("#suggested-report_weekly_time")
    weekly_row = page.locator('[data-suggested-report="weekly"]')
    _, keep, add = row(page)
    # Shown first, as it is before a click: on a phone the table scrolls
    # sideways to bring a field into view, which is not the layout moving
    # (WebKit's focus() leaves that scroll to the first keystroke).
    daily.evaluate(
        "(node) => node.scrollIntoView({block: 'nearest', inline: 'nearest'})"
    )
    before = placed(daily, weekly, weekly_row, add, keep)
    reading = page.locator("#suggested-report_daily_time_reading")
    daily.press_sequentially("7")
    has_text(reading, reads("Reads as 7:00 AM"))
    assert same_place(placed(daily, weekly, weekly_row, add, keep), before, tolerance=1)
    # A refusal, shown once typing pauses, keeps the same reserved space:
    # the seconds one and a 32-character entry's (the field's limit) are
    # the longest. The complete gate's hint under the table says the
    # refusal again (#398), which on a phone can take another line there,
    # so below the table only the desktop rows are held still.
    shown = 5 if width >= 1280 else 3
    for entry, refusal in (
        ("9xx", "“9xx” isn't a time. Try 2:00 PM, 2pm, 14:00 or 1400."),
        ("07:00:30", "“07:00:30” has seconds: these times are to the minute, so"),
        ("x" * 32, f"“{'x' * 32}” isn't a time. Try 2:00 PM"),
    ):
        daily.fill("")
        daily.press_sequentially(entry)
        has_text(reading, re.compile(re.escape(refusal)))
        after = placed(daily, weekly, weekly_row, add, keep)
        assert same_place(after[:shown], before[:shown], tolerance=1)
    assert failures == []

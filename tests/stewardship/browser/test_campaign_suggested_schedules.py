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
"""

import json
import re
from urllib.parse import parse_qs

import pytest

from .stepper_components import CONTENT, SUGGESTED, SUGGESTED_PASSED
from .test_campaign_subpage_direct_save import applied_line
from .test_components import axe_violations
from .waits import has_text, same_place, visible

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
    rows = panel.locator("tbody tr")
    assert rows.count() == 2
    assert [rows.nth(index).locator("th").inner_text() for index in range(2)] == [
        "Initial invitation",
        "Reminder 1",
    ]
    assert panel.locator(".status-badge").all_inner_texts() == ["Not saved yet"] * 2
    # Send times are drawn in the browser's zone, as every schedule's are.
    when = rows.nth(0).locator("time")
    assert re.search(r"\d{1,2}:\d{2}", when.inner_text())
    assert "T" not in when.inner_text()
    back, keep, add = row(page)
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
    assert sorted(body) == ["base_digest", "csrfmiddlewaretoken", "request_key"]
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

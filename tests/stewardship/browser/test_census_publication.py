"""Publish to ParishSoft: accessible, Check ParishSoft acts in place (#528).

The page is served by ``publication_components`` at its real address. Check
ParishSoft posts in place and follows the redirect back to the page, so the
check's live status appears below the button with nothing above it moving;
when the poll finds the check finished, the page is re-read once in place
and shows the plan. A mark outside every region shows the page never
reloaded.
"""

import pytest

from .publication_components import (
    CHECK_FINISHED_STATUS,
    CHECK_STATUS,
    CHECKED,
    OFF,
    PUBLICATION,
    REQUESTED,
    RUNNING,
)
from .test_in_place import MARK, MARKED, VIEWPORT
from .test_live_status_in_place import answer_reads
from .waits import greyed, has_attribute, has_text, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_each_state_is_accessible_and_fits(page, component_origin, axe_source, width):
    """Off, ready, checking, checked and held all pass the accessibility
    scan and fit the window."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in (OFF, PUBLICATION, REQUESTED, CHECKED, RUNNING):
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert (
            page.evaluate("""async () => (await axe.run(document, {
            runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
        })).violations.map(({id,impact}) => ({id,impact}))""")
            == []
        )


def test_switched_off_offers_no_button(page, component_origin):
    """Off: the page says so, with no Check ParishSoft and no plan."""
    page.goto(component_origin + OFF)
    visible(page.locator("#publication-off"))
    assert page.get_by_role("button", name="Check ParishSoft").count() == 0
    assert page.get_by_role("heading", name="Newest check").count() == 0


def test_a_running_publication_holds_the_check_with_its_reason(page, component_origin):
    """Check ParishSoft is unavailable, named by its visible reason."""
    page.goto(component_origin + RUNNING)
    button = page.get_by_role("button", name="Check ParishSoft")
    greyed(button)
    has_attribute(button, "aria-describedby", "publication-check-reason")
    has_text(
        page.locator("#publication-check-reason"),
        "Publishing is running. Wait for it to finish.",
    )


def test_check_parishsoft_stays_on_the_page_and_shows_the_plan(page, component_origin):
    """Check ParishSoft posts its key in place; the check's status appears
    below the button, nothing above it moves, and once the check ends the
    page is re-read once, in place, with the plan's rows and counts."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + PUBLICATION)
    page.evaluate(MARK)
    button = page.get_by_role("button", name="Check ParishSoft")
    before = button.bounding_box()
    assert page.get_by_role("heading", name="Current status").count() == 0
    with page.expect_request(lambda request: request.method == "POST") as sent:
        button.click()
    assert "request_key=" in sent.value.post_data
    assert "csrfmiddlewaretoken=" in sent.value.post_data
    has_text(
        page.get_by_role("status").filter(has_text="Check requested."),
        "Check requested.",
    )
    region = page.locator("#publication-check")
    visible(region.get_by_role("heading", name="Current status"))
    visible(region.get_by_role("link", name="View this check's Background task"))
    assert page.evaluate(MARKED) == "kept"
    assert button.bounding_box()["y"] == before["y"]
    polls = answer_reads(
        page,
        component_origin,
        CHECK_STATUS + "?view=publication",
        CHECK_FINISHED_STATUS,
    )
    reads = answer_reads(page, component_origin, REQUESTED, CHECKED)
    visible(region.get_by_text("Finished successfully."))
    plan = page.locator("#publication-plan")
    visible(plan.get_by_text("1 change for 1 Member will be written."))
    visible(plan.get_by_role("cell", name="Held back"))
    visible(plan.get_by_role("cell", name="Could not be read"))
    visible(plan.get_by_text("Checked by admin@example.org at", exact=False))
    assert page.evaluate(MARKED) == "kept"
    assert len(polls) >= 1 and len(reads) == 1, (polls, reads)

"""The remaining live-status pages update themselves (#519 PR 7b, #1586).

Background task and Change status are served at their real addresses
(live_status_components), where their own reads lead; neither has a
Refresh link. Each test sets a mark outside every region, which only a full
page load could lose, and checks the mark, the fresh status, that focus did
not move, and what the page read.
"""

import pytest

from .live_status_components import (
    CHANGE,
    TASK_FINISHED_BY_ACTION,
    TASK_PAGE,
    TASK_STATUS,
)
from .test_in_place import MARK, MARKED
from .test_live_status_in_place import answer_reads, refresh_controls
from .waits import has_attribute, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

VIEWPORT = {"width": 1280, "height": 320}


def test_change_status_updates_itself(page, component_origin):
    """Change status follows the change until it is applied, by itself."""
    page.set_viewport_size(VIEWPORT)
    reads = answer_reads(page, component_origin, CHANGE, "/live-change-applied")
    page.goto(component_origin + CHANGE)
    page.evaluate(MARK)
    assert refresh_controls(page).count() == 0
    visible(page.get_by_role("heading", name="Applied: your change is saved"))
    assert page.evaluate(MARKED) == "kept"
    assert page.evaluate("document.activeElement === document.body")
    assert len(reads) == 1
    # One watcher, and so one status line, for the region.
    assert page.locator(".live-status-problem").count() <= 1


def test_finished_task_brings_its_history_in_place(page, component_origin):
    """When the run ends, the page brings the finished run and its history
    in by itself, without a reload; it has no Technical details (#2199)."""
    page.set_viewport_size(VIEWPORT)
    answer_reads(page, component_origin, TASK_STATUS, "/live-task-finished-status")
    page.goto(component_origin + TASK_PAGE)
    page.evaluate(MARK)
    assert refresh_controls(page).count() == 0
    assert page.get_by_text("Technical details").count() == 0
    reads = answer_reads(page, component_origin, TASK_PAGE, "/live-task-finished")
    visible(page.locator("#task-status").get_by_text("Finished successfully."))
    history = page.locator("[data-table-region]")
    visible(history.get_by_role("cell", name="Finished", exact=True))
    assert page.evaluate(MARKED) == "kept"
    assert len(reads) == 1


def test_finished_task_keeps_the_history_sort_and_a_reload_keeps_it(
    page, component_origin
):
    """After the history is sorted by Action in place, the address carries
    that sort, so a reload shows the same sorted history, and the page's own
    re-read when the run ends keeps it too."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + TASK_PAGE)
    heading = page.locator("th[data-sort-column='action']")
    heading.locator(".sort-link").click()
    has_attribute(heading, "aria-sort", "ascending")
    assert "sort=action" in page.url
    sorted_at = page.url
    page.reload()
    assert page.url == sorted_at
    has_attribute(heading, "aria-sort", "ascending")
    page.evaluate(MARK)
    reads = []

    def finished(route):
        """Answer the page's re-read of its sorted address with the end."""
        if route.request.resource_type == "document":
            route.continue_()
            return
        reads.append(route.request.url)
        route.fulfill(
            response=route.fetch(
                url=component_origin + TASK_FINISHED_BY_ACTION, method="GET"
            )
        )

    page.route(lambda url: "sort=action" in url, finished)
    answer_reads(page, component_origin, TASK_STATUS, "/live-task-finished-status")
    visible(page.locator("#task-status").get_by_text("Finished successfully."))
    history = page.locator("[data-table-region]")
    visible(history.get_by_role("cell", name="Finished", exact=True))
    has_attribute(heading, "aria-sort", "ascending")
    assert page.evaluate(MARKED) == "kept"
    assert len(reads) == 1 and "sort=action" in reads[0]

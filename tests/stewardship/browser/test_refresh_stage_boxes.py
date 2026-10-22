"""A ParishSoft refresh's Background task status draws the shared stage boxes (#1579).

ui-conventions "Stage boxes": the task page's Current status shows the
refresh's four phases as the same boxes as Load ParishSoft data. While the
run is watched, the boxes update in place from the passive status poll,
each change of state is announced once through the page's announcer (not on
every poll), and no box moves or changes size as its state changes.
"""

import pytest

from .live_status_components import (
    TASK_PAGE,
    TASK_QUEUED_PAGE,
    TASK_QUEUED_STATUS,
    TASK_STAGING_STATUS,
    TASK_STATUS,
)
from .test_components import axe_violations
from .test_in_place import MARK, MARKED
from .test_live_status_in_place import answer_reads
from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Every text the task page's announcer is given, in order, from now on.
RECORD_SPOKEN = """() => {
  const announcer = document.querySelector('[data-live-announcer="task"]');
  window.spokenTexts = [];
  new MutationObserver(() => window.spokenTexts.push(announcer.textContent))
    .observe(announcer, {childList: true, characterData: true, subtree: true});
}"""


def settled(box, state):
    """Wait for ``box`` to reach ``state`` through the fixture's slow polls.

    The fixture answers one read per poll and the finished state is the
    fourth, so a slower engine under a loaded runner needs more than
    Playwright's 5 s expect default (WebKit failed it in train t61).
    """
    from playwright.sync_api import expect

    expect(box).to_have_attribute("data-stage-state", state, timeout=30_000)


def places(boxes):
    """Each stage box's place and size, rounded to the pixel."""
    return [
        {key: round(value) for key, value in boxes.nth(i).bounding_box().items()}
        for i in range(boxes.count())
    ]


def states(boxes):
    """Each stage box's state, in order."""
    return [
        boxes.nth(i).get_attribute("data-stage-state") for i in range(boxes.count())
    ]


@pytest.mark.parametrize("width", [320, 1280])
def test_the_boxes_follow_the_run_in_place_and_announce_each_change_once(
    page, component_origin, axe_source, width
):
    """Downloading, then saving, then finished: the boxes say so in words,
    each change is announced once, and no box moves or resizes."""
    page.set_viewport_size({"width": width, "height": 900})
    polls = answer_reads(
        page,
        component_origin,
        TASK_STATUS,
        TASK_STAGING_STATUS,
        TASK_STAGING_STATUS,
        "/live-task-finished-status",
    )
    answer_reads(page, component_origin, TASK_PAGE, "/live-task-finished")
    page.goto(component_origin + TASK_PAGE)
    page.evaluate(MARK)
    page.evaluate(RECORD_SPOKEN)
    region = page.locator("#task-status [data-live-status=task]")
    boxes = region.get_by_role("list", name="Parts of the refresh").locator("li")
    # Measured at once: the first poll (after 2 s) may already have moved
    # the run on to saving, which must not move or resize any box either.
    before = places(boxes)
    assert len(before) == 5
    assert region.get_attribute("aria-live") is None
    assert "Step:" not in region.inner_text()
    settled(boxes.nth(2), "active")
    assert states(boxes) == ["done", "done", "active", "waiting", "waiting"]
    assert "In progress" in boxes.nth(2).inner_text()
    assert places(boxes) == before
    settled(boxes.nth(4), "done")
    assert states(boxes) == ["done"] * 5
    assert all("Done" in boxes.nth(i).inner_text() for i in range(5))
    visible(page.locator("#task-status").get_by_text("Finished successfully."))
    assert places(boxes) == before
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert axe_violations(page, axe_source) == []
    # The finished page's follow-up re-read in place, then nothing more.
    page.wait_for_timeout(3000)
    spoken = page.evaluate("window.spokenTexts.filter((text) => text)")
    assert spoken == [
        "Running. Save the downloaded records: In progress.",
        "Finished successfully.",
    ], spoken
    assert page.evaluate(MARKED) == "kept"
    assert len(polls) >= 3, polls


def test_the_boxes_draw_on_the_refresh_page_too(page, component_origin):
    """Refresh from ParishSoft draws the same region for the run it
    requested, with its announcer outside the in-place region."""
    page.goto(component_origin + "/live-refresh-finished")
    region = page.locator("#refresh-run")
    boxes = region.get_by_role("list", name="Parts of the refresh").locator("li")
    assert states(boxes) == ["done"] * 5
    announcer = page.locator('[data-live-announcer="task"]')
    assert announcer.count() == 1
    assert announcer.evaluate("node => !node.closest('#refresh-run')")


@pytest.mark.parametrize("width", [320, 1280])
def test_a_queued_refresh_starts_in_progress_and_hands_over_in_place(
    page, component_origin, width
):
    """While the run waits for a worker, Start the load is In progress with
    the spinner and the queued sentence, never Not started (#1947); once a
    worker takes it, Start the load is Done and the download In progress, in
    place, and no box moves or resizes."""
    page.set_viewport_size({"width": width, "height": 900})
    polls = answer_reads(
        page,
        component_origin,
        TASK_STATUS,
        TASK_QUEUED_STATUS,
        TASK_STATUS,
        "/live-task-finished-status",
    )
    answer_reads(page, component_origin, TASK_QUEUED_PAGE, "/live-task-finished")
    page.goto(component_origin + TASK_QUEUED_PAGE)
    page.evaluate(MARK)
    region = page.locator("#task-status [data-live-status=task]")
    boxes = region.get_by_role("list", name="Parts of the refresh").locator("li")
    before = places(boxes)
    assert states(boxes) == ["active", *["waiting"] * 4]
    assert boxes.nth(0).locator(".spinner").count() == 1
    assert "In progress" in boxes.nth(0).inner_text()
    queued = region.get_by_text("Waiting for the background service to pick up")
    visible(queued)
    settled(boxes.nth(1), "active")
    assert states(boxes) == ["done", "active", *["waiting"] * 3]
    assert boxes.nth(0).locator(".spinner").count() == 0
    assert boxes.nth(1).locator(".spinner").count() == 1
    assert queued.count() == 0
    assert places(boxes) == before
    settled(boxes.nth(4), "done")
    assert places(boxes) == before
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.evaluate(MARKED) == "kept"
    assert len(polls) >= 2, polls

"""The Go live page's Prepare step in a real browser (#1585 slice 3).

The step is served at its real address and in each state
(go_live_components). Every Prepare form posts to that address, and the
fixture server answers by the posted action as the view does: Retry and
Refresh and prepare again redirect back, Stop go-live's Review answers its
dialog and its Apply redirects to Check.
"""

import pytest

from .go_live_components import CONFIRM, GO_LIVE, PREPARE
from .test_components import axe_violations
from .test_in_place import MARK, MARKED
from .waits import focused, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Each state's page, its heading, the stage boxes' states and its one
# primary (None: nothing to do).
STATES = (
    (
        PREPARE,
        "Preparing to go live.",
        ["active", "waiting", "waiting"],
        None,
    ),
    (
        "/go-live-prepare-refresh",
        "Preparing to go live.",
        ["done", "active", "waiting"],
        None,
    ),
    (
        "/go-live-prepare-links",
        "Preparing to go live.",
        ["done", "done", "active"],
        None,
    ),
    (
        "/go-live-prepare-failed",
        "A step failed after its automatic retries.",
        ["failed", "waiting", "waiting"],
        "Retry",
    ),
    (
        "/go-live-prepare-again",
        "Going live needs a new ParishSoft refresh.",
        ["done", "done", "active"],
        "Refresh and prepare again",
    ),
    (
        "/go-live-prepare-ready",
        "Ready to confirm.",
        ["done", "done", "done"],
        "Continue to Confirm Production",
    ),
    (
        "/go-live-prepare-closed",
        "The campaign has ended, so it cannot go live.",
        ["done", "active", "waiting"],
        None,
    ),
)


def posted(page):
    """Collect the body of every POST to Prepare's address, in order."""
    bodies = []
    page.on(
        "request",
        lambda request: (
            bodies.append(request.post_data or "")
            if request.method == "POST" and PREPARE in request.url
            else None
        ),
    )
    return bodies


@pytest.mark.parametrize("width", [320, 1280])
def test_each_state_has_one_frame_three_boxes_and_at_most_one_primary(
    page, component_origin, axe_source, width
):
    """Heading, step bar, three stage boxes, two reserved lines and one row.

    Each state shows its own heading, the boxes in their states (in words,
    left to right), Stop go-live as the secondary and exactly its one
    primary (none while the work runs or once the campaign has ended), no
    Back and no Refresh link; it fits the width without scrolling sideways
    and passes the accessibility scan.
    """
    from playwright.sync_api import expect

    page.set_viewport_size({"width": width, "height": 900})
    for path, heading, states, primary in STATES:
        page.goto(component_origin + path)
        visible(page.get_by_role("heading", name="Go live", exact=True))
        expect(page.locator("#go-live-state")).to_have_text(heading)
        expect(page.locator(".setup-stepper-current")).to_have_text(
            "Step 2 of 4: Prepare"
        )
        boxes = page.locator("[data-go-live-stage]")
        assert [
            boxes.nth(index).get_attribute("data-stage-state") for index in range(3)
        ] == states, path
        expect(boxes.first).to_contain_text("Delete Testing data")
        expect(boxes.nth(2)).to_contain_text("Prepare Family links")
        visible(page.locator("[data-go-live-detail]"))
        visible(page.locator("[data-go-live-note]"))
        row = page.locator("[data-go-live-actions] [data-go-live-primary]")
        if primary is None:
            assert row.count() == 0, path
        else:
            assert row.count() == 1, path
            visible(row.get_by_text(primary, exact=True))
        visible(page.get_by_role("button", name="Stop go-live"))
        assert page.locator("[data-go-live-back]").count() == 0
        assert page.get_by_role("link", name="Refresh", exact=False).count() == 0
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        ), path
        assert axe_violations(page, axe_source) == [], path
    page.goto(component_origin + "/go-live-prepare-ready")
    expect(page.locator(".setup-stepper-count")).to_have_text("2 of 4 steps completed")
    proceed = page.get_by_role("link", name="Continue to Confirm Production")
    assert proceed.get_attribute("href") == CONFIRM
    assert page.locator(".setup-track a").nth(2).get_attribute("href") == CONFIRM


def test_stage_boxes_update_in_place_and_a_reload_resumes(page, component_origin):
    """The step re-reads itself: the boxes move on without a reload, nothing
    shifts, and a browser reload returns to the same step, still updating."""
    from playwright.sync_api import expect

    reads = []

    def later(route):
        """The first read is the page; every later one finds the links running."""
        reads.append(route.request.url)
        if len(reads) == 1 or route.request.method != "GET":
            route.continue_()
            return
        route.fulfill(
            response=route.fetch(
                url=component_origin + "/go-live-prepare-links", method="GET"
            )
        )

    page.route(component_origin + PREPARE, later)
    page.goto(component_origin + PREPARE)
    page.evaluate(MARK)
    row = page.locator("[data-go-live-actions]").bounding_box()
    last = page.locator("[data-go-live-stage]").nth(2)
    expect(last).to_have_attribute("data-stage-state", "active", timeout=15000)
    expect(page.locator("[data-go-live-detail]")).to_contain_text(
        "Preparing links: 812 of 1,031 Families"
    )
    assert page.evaluate(MARKED) == "kept"
    # Every state draws the same boxes and lines, so the row did not move.
    moved = page.locator("[data-go-live-actions]").bounding_box()
    assert abs(moved["y"] - row["y"]) <= 1
    seen = len(reads)
    page.reload()
    expect(page.locator("[data-go-live-detail]")).to_contain_text(
        "Preparing links: 812 of 1,031 Families"
    )
    expect(page.locator("#go-live-prepare")).to_have_attribute("data-live-pending", "")
    page.wait_for_timeout(2500)
    assert len(reads) > seen + 1


def test_stop_go_live_confirms_in_the_dialog_then_opens_check(page, component_origin):
    """Stop go-live opens the shared dialog: title first, the sentence for
    the state and what is never undone; Cancel and a reload change nothing;
    Apply posts the step's control once and opens Check."""
    from playwright.sync_api import expect

    page.goto(component_origin + "/go-live-prepare-ready")
    posts = posted(page)
    stop = page.get_by_role("button", name="Stop go-live")
    stop.click()
    dialog = page.get_by_role("dialog", name="Stop going live?")
    visible(dialog)
    focused(dialog.get_by_role("heading", name="Stop going live?"))
    expect(dialog).to_contain_text("The prepared Family links are discarded.")
    expect(dialog).to_contain_text("Testing data already deleted is not restored.")
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    focused(stop)
    stop.click()
    visible(dialog)
    page.reload()
    hidden(page.get_by_role("dialog", name="Stop going live?"))
    assert posts and all("action=review_stop" in body for body in posts)
    reviews = len(posts)
    page.get_by_role("button", name="Stop go-live").click()
    visible(dialog)
    with page.expect_navigation(url=component_origin + GO_LIVE):
        dialog.get_by_role("button", name="Apply").click()
    assert len(posts) == reviews + 2
    assert "action=stop" in posts[-1] and "control=synthetic-stop" in posts[-1]


def test_a_refused_stop_stays_in_the_dialog(page, component_origin):
    """Stopped by another Administrator since the page was drawn: said in
    the dialog, with the way to Check and no Apply."""
    from playwright.sync_api import expect

    def refuse(route):
        """Answer the Review with the refusal; let reads through."""
        if route.request.method != "POST":
            route.continue_()
            return
        answer = route.fetch(
            url=component_origin + "/go-live-prepare-review-stopped", method="GET"
        )
        route.fulfill(status=409, body=answer.text(), content_type="text/html")

    page.goto(component_origin + PREPARE)
    page.route(component_origin + PREPARE, refuse)
    page.get_by_role("button", name="Stop go-live").click()
    dialog = page.get_by_role("dialog", name="Stop going live?")
    visible(dialog)
    expect(dialog).to_contain_text("Another Administrator stopped go-live.")
    hidden(dialog.get_by_role("button", name="Apply"))
    visible(dialog.get_by_role("link", name="Open Check"))


def test_a_stop_elsewhere_moves_the_watching_page_to_check(page, component_origin):
    """Once go-live is stopped elsewhere, the step settles and opens Check."""

    def later(route):
        """The first read is the page; later ones find go-live stopped."""
        if route.request.method != "GET" or not later.seen:
            later.seen = True
            route.continue_()
            return
        route.fulfill(
            response=route.fetch(
                url=component_origin + "/go-live-prepare-stopped", method="GET"
            )
        )

    later.seen = False
    page.route(component_origin + PREPARE, later)
    page.goto(component_origin + PREPARE)
    page.wait_for_url(component_origin + GO_LIVE, timeout=20000)

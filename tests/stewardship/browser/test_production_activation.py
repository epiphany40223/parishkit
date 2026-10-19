"""Production activation, the Go live page's Activation step (#1585 slice 5).

The page is served at its real address and in each state
(go_live_components.production_pages, through the view's own context). Its
forms post to that address, and the fixture server answers by the posted
action as the view does: Retry redirects back; Cancel go-live's Review
answers the dialog with the counts and the reason field, and its Apply with
a reason opens Check, without one is refused inside the dialog.

Confirm Production itself is the Go live page's step, in its review dialog
(test_go_live_confirm.py, #1585 slice 4).
"""

import pytest

from .go_live_components import GO_LIVE, PRODUCTION
from .test_components import axe_violations
from .waits import focused, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Each state's page, its state heading, whether it draws the Go live step
# bar, its primary (None: nothing to do), whether Cancel go-live is offered,
# and the stage boxes' states (None: no boxes).
STATES = (
    (
        "/production-preparing",
        "Campaign active. Preparing initial campaign mail.",
        True,
        None,
        False,
        ["done", "active", "waiting"],
    ),
    (
        "/production-retrying",
        "Campaign active. Preparing initial campaign mail.",
        True,
        None,
        False,
        ["done", "active", "waiting"],
    ),
    (
        PRODUCTION,
        "Campaign active. Preparing initial campaign mail failed after its "
        "automatic retries.",
        True,
        "Retry",
        False,
        ["done", "failed", "waiting"],
    ),
    (
        "/production-prepared",
        "Campaign active. Initial campaign mail is prepared.",
        False,
        None,
        False,
        ["done", "done", "active"],
    ),
    ("/production-scheduled", "Campaign scheduled.", False, None, True, None),
    ("/production-scheduled-paused", "Campaign scheduled.", False, None, False, None),
    ("/production-started", "Campaign active.", False, None, False, None),
    ("/production-closed", "Campaign closed.", False, None, False, None),
)


def posted(page):
    """Collect the body of every POST to the page's address, in order."""
    bodies = []

    def record(request):
        """Keep a POST's body when it is to Production activation."""
        if request.method == "POST" and request.url.split("#")[0].endswith(PRODUCTION):
            bodies.append(request.post_data or "")

    page.on("request", record)
    return bodies


@pytest.mark.parametrize("width", [320, 1280])
def test_each_state_has_its_frame_and_at_most_one_primary(
    page, component_origin, axe_source, width
):
    """Heading, the step bar only while initial mail is prepared, the
    content, and one action row: Retry for a failure, Cancel go-live only
    for a scheduled campaign. No Refresh link, no numbered flow list; the
    page fits the width without scrolling sideways and passes the scan."""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": width, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    for path, heading, bar, primary, cancel, boxes in STATES:
        page.goto(component_origin + path)
        visible(page.get_by_role("heading", name="Production activation", exact=True))
        expect(page.locator("#production-state")).to_have_text(heading)
        current = page.locator(".setup-stepper-current")
        if bar:
            expect(current).to_have_text("Step 4 of 4: Activation")
        else:
            assert current.count() == 0, path
        row = page.locator("[data-production-actions] [data-production-primary]")
        if primary is None:
            assert row.count() == 0, path
        else:
            assert row.count() == 1, path
            visible(row.get_by_role("button", name=primary, exact=True))
        cancels = page.get_by_role("button", name="Cancel go-live")
        assert cancels.count() == (1 if cancel else 0), path
        drawn = page.locator("[data-production-stage]")
        if boxes is None:
            assert drawn.count() == 0, path
        else:
            assert [
                box.get_attribute("data-stage-state") for box in drawn.all()
            ] == boxes, path
        assert page.get_by_role("link", name="Refresh Production progress").count() == 0
        assert page.locator(".flow-steps").count() == 0, path
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        ), path
        assert axe_violations(page, axe_source) == [], path
    assert errors == []
    # Prepared: the Sending box is in progress, never done, and the line
    # under the boxes says where delivery shows.
    page.goto(component_origin + "/production-prepared")
    sending = page.locator('[data-production-stage="sending"]')
    expect(sending).to_contain_text("In progress")
    detail = page.locator("[data-production-detail]")
    visible(detail.get_by_role("link", name="Sent mail", exact=True))
    page.goto(component_origin + "/production-scheduled-paused")
    expect(page.locator("main")).to_contain_text(
        "Cancel go-live isn't available while live mail delivery is paused"
    )
    page.goto(component_origin + PRODUCTION)
    expect(page.locator("main")).to_contain_text("Items processed: 5,678.")


def test_the_lines_under_the_boxes_never_move_the_row(page, component_origin):
    """Every direct-activation state draws the same boxes and two lines, so
    the action row stays where it was between them (no layout shift) on a
    desktop width, where each line fits one row."""
    page.set_viewport_size({"width": 1280, "height": 900})
    tops = set()
    for path in ("/production-preparing", "/production-retrying"):
        page.goto(component_origin + path)
        tops.add(round(page.locator("[data-production-actions]").bounding_box()["y"]))
    assert len(tops) == 1


def test_cancel_go_live_waits_for_a_reason_in_the_dialog(page, component_origin):
    """The dialog: its counts, the caution and the reason field. Apply waits
    for a reason of more than spaces, saying why under the buttons, which
    never move; Apply then posts the reason with the Review's binding, and
    the page opens Check."""
    from playwright.sync_api import expect

    page.goto(component_origin + "/production-scheduled")
    posts = posted(page)
    page.get_by_role("button", name="Cancel go-live").click()
    dialog = page.get_by_role("dialog", name="Cancel go-live?")
    visible(dialog)
    focused(dialog.get_by_role("heading", name="Cancel go-live?"))
    expect(dialog).to_contain_text("Unsent messages to cancel")
    expect(dialog).to_contain_text("1,200")
    expect(dialog).to_contain_text(
        "Testing data deleted earlier is not restored; going live again starts "
        "from Check."
    )
    # Apply is the acknowledgement: there is no tick box.
    assert dialog.get_by_role("checkbox").count() == 0
    apply = dialog.get_by_role("button", name="Apply")
    status = dialog.locator("[data-confirm-status]")
    reason = dialog.get_by_label("Reason for cancelling go-live")
    hint = "Enter the reason for cancelling go-live."
    assert apply.is_disabled()
    expect(status).to_have_text(hint)
    where = apply.bounding_box()
    reason.fill("   ")
    assert apply.is_disabled()
    expect(status).to_have_text(hint)
    assert len(posts) == 1 and "action=review_cancel" in posts[0]
    reason.fill("Correct the schedule")
    assert apply.is_enabled()
    expect(status).to_have_text("")
    assert apply.bounding_box() == where
    with page.expect_navigation(url=component_origin + GO_LIVE):
        apply.click()
    assert len(posts) == 2
    assert "action=cancel" in posts[1] and "review=synthetic-cancel" in posts[1]
    assert "reason=Correct+the+schedule" in posts[1]


def test_cancel_and_a_reload_change_nothing(page, component_origin):
    """Cancel closes the dialog and returns focus to Cancel go-live; a
    reload while it is open closes it with nothing applied."""
    page.goto(component_origin + "/production-scheduled")
    posts = posted(page)
    opener = page.get_by_role("button", name="Cancel go-live")
    opener.click()
    dialog = page.get_by_role("dialog", name="Cancel go-live?")
    visible(dialog)
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    hidden(dialog)
    focused(opener)
    opener.click()
    visible(dialog)
    page.reload()
    hidden(page.get_by_role("dialog", name="Cancel go-live?"))
    assert posts and all("action=review_cancel" in body for body in posts)


def answering(page, component_origin, action, path):
    """Answer the dialog's ``action`` POST with the refusal fragment at
    ``path`` (a 409, as the view draws it); every other request goes
    through."""

    def refuse(route):
        """Answer the action with the refusal the view draws; pass the rest."""
        body = route.request.post_data or ""
        if route.request.method != "POST" or f"action={action}" not in body:
            route.continue_()
            return
        answer = route.fetch(url=component_origin + path, method="GET")
        route.fulfill(status=409, body=answer.text(), content_type="text/html")

    page.route(component_origin + PRODUCTION, refuse)


@pytest.mark.parametrize("width", [320, 1280])
def test_blocking_mail_is_refused_inside_the_dialog(
    page, component_origin, axe_source, width
):
    """Mail being sent blocks the cancellation: said inside the dialog, as
    drawn, with links to the pages that resolve it and no Apply; the open
    dialog fits the width and passes the scan."""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/production-scheduled")
    answering(
        page, component_origin, "review_cancel", "/production-cancel-review-blocking"
    )
    page.get_by_role("button", name="Cancel go-live").click()
    dialog = page.get_by_role("dialog", name="Cancel go-live?")
    visible(dialog)
    expect(dialog).to_contain_text("2 items of mail are being sent right now")
    visible(dialog.get_by_role("link", name="Pause and resume mail"))
    visible(dialog.get_by_role("link", name="Sent mail"))
    hidden(dialog.get_by_role("button", name="Apply"))
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert dialog.evaluate("node => node.scrollWidth <= node.clientWidth")
    assert axe_violations(page, axe_source) == []


@pytest.mark.parametrize(
    ("path", "words"),
    [
        ("/production-cancel-review-started", "The campaign has started"),
        ("/production-cancel-review-changed", "Mail work changed since this preview"),
    ],
)
def test_a_refused_apply_names_the_change_inside_the_dialog(
    page, component_origin, path, words
):
    """The campaign started, or its mail work changed, since the Review: the
    refusal is shown in the dialog in its own words, Apply stays
    unavailable, and Cancel closes it on the same page."""
    from playwright.sync_api import expect

    page.goto(component_origin + "/production-scheduled")
    answering(page, component_origin, "cancel", path)
    page.get_by_role("button", name="Cancel go-live").click()
    dialog = page.get_by_role("dialog", name="Cancel go-live?")
    dialog.get_by_label("Reason for cancelling go-live").fill("Fix dates")
    apply = dialog.get_by_role("button", name="Apply")
    apply.click()
    expect(dialog).to_contain_text(words)
    assert apply.is_disabled()
    dialog.get_by_label("Reason for cancelling go-live").fill("Fix the dates")
    assert apply.is_disabled()
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    hidden(dialog)
    assert page.url == component_origin + "/production-scheduled"

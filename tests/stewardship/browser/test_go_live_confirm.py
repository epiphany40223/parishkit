"""The Go live page's Confirm Production step in a real browser (#1585 slice 4).

The step is served at its real address and in each state
(go_live_components). Its dialog posts to that address, and the fixture
server answers by the posted action as the view does: Review answers the
dialog with the preview and the typed field; Apply with the typed word
opens Production activation, and without it is refused inside the dialog.
"""

import pytest

from .go_live_components import CONFIRM, PREPARE, PRODUCTION
from .test_components import axe_violations
from .waits import focused, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Each state's page, its heading and its one primary (None: nothing to do).
STATES = (
    (CONFIRM, "Ready to confirm Production.", "Confirm Production"),
    (
        "/go-live-confirm-scheduled",
        "Ready to confirm Production.",
        "Confirm Production",
    ),
    ("/go-live-confirm-problems", "Something needs attention before going live.", None),
    (
        "/go-live-confirm-hold",
        "ParishSoft refreshes resumed while this waited.",
        "Refresh and prepare again",
    ),
    ("/go-live-confirm-closed", "The campaign has ended, so it cannot go live.", None),
    (
        "/go-live-confirm-moved",
        "ParishSoft data changed, so new links are being prepared.",
        "Open Prepare",
    ),
)


def posted(page):
    """Collect the body of every POST to the step's address, in order."""
    bodies = []
    page.on(
        "request",
        lambda request: (
            bodies.append(request.post_data or "")
            if request.method == "POST" and request.url.endswith(CONFIRM)
            else None
        ),
    )
    return bodies


@pytest.mark.parametrize("width", [320, 1280])
def test_each_state_has_one_frame_and_at_most_one_primary(
    page, component_origin, axe_source, width
):
    """Heading, Step 3 of 4, the step's content and one action row.

    Stop go-live is the secondary while go-live is in progress (not once the
    step has moved back to Prepare); no Back and no Refresh link; the page
    fits the width without scrolling sideways and passes the scan.
    """
    from playwright.sync_api import expect

    page.set_viewport_size({"width": width, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    for path, heading, primary in STATES:
        page.goto(component_origin + path)
        visible(page.get_by_role("heading", name="Go live", exact=True))
        expect(page.locator("#go-live-state")).to_have_text(heading)
        expect(page.locator(".setup-stepper-current")).to_have_text(
            "Step 3 of 4: Confirm Production"
        )
        row = page.locator("[data-go-live-actions] [data-go-live-primary]")
        if primary is None:
            assert row.count() == 0, path
        else:
            assert row.count() == 1, path
            visible(row.get_by_text(primary, exact=True))
        stops = page.get_by_role("button", name="Stop go-live")
        assert stops.count() == (0 if path.endswith("moved") else 1), path
        assert page.locator("[data-go-live-back]").count() == 0
        assert page.get_by_role("link", name="Refresh", exact=True).count() == 0
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        ), path
        assert axe_violations(page, axe_source) == [], path
    assert errors == []
    page.goto(component_origin + CONFIRM)
    expect(page.locator(".setup-stepper-count")).to_have_text("2 of 4 steps completed")
    expect(page.locator("main")).to_contain_text("Family emails due at once")
    expect(page.locator("main")).to_contain_text("1,000 out of 1,500 (66.7%)")


def test_confirm_production_waits_for_the_typed_word_in_the_dialog(
    page, component_origin
):
    """The dialog: its question, the target and two counts, the caution and
    the typed field. Apply waits for "Production" (spaces around it allowed,
    as the server allows), saying why under the buttons, which never move;
    Enter applies once it holds the word, posting it with the Review's
    binding, and the page opens Production activation."""
    from playwright.sync_api import expect

    page.goto(component_origin + CONFIRM)
    posts = posted(page)
    page.get_by_role("button", name="Confirm Production").click()
    dialog = page.get_by_role("dialog", name="Go live now?")
    visible(dialog)
    focused(dialog.get_by_role("heading", name="Go live now?"))
    expect(dialog).to_contain_text("The campaign becomes active at once")
    expect(dialog).to_contain_text("Families can open the form")
    apply = dialog.get_by_role("button", name="Apply")
    status = dialog.locator("[data-confirm-status]")
    typed = dialog.get_by_label("Type Production to confirm")
    text = "Type Production exactly as shown to confirm."
    assert apply.is_disabled()
    expect(status).to_have_text(text)
    assert apply.get_attribute("aria-describedby") == status.get_attribute("id")
    where = apply.bounding_box()
    for value in ("production", "Productio", "Production!"):
        typed.fill(value)
        assert apply.is_disabled(), value
        expect(status).to_have_text(text)
        # Enter never sends the field while Apply waits.
        typed.press("Enter")
    assert len(posts) == 1 and "action=review" in posts[0]
    typed.fill(" Production ")
    assert apply.is_enabled()
    expect(status).to_have_text("")
    assert apply.get_attribute("aria-describedby") is None
    assert apply.bounding_box() == where
    with page.expect_navigation(url=component_origin + PRODUCTION):
        typed.press("Enter")
    assert len(posts) == 2
    assert "action=confirm" in posts[1] and "review=synthetic-review" in posts[1]
    assert "typed=+Production+" in posts[1]


def test_cancel_and_a_reload_change_nothing(page, component_origin):
    """Cancel closes the dialog and returns focus to Confirm Production; a
    reload while it is open closes it with nothing applied."""
    page.goto(component_origin + CONFIRM)
    posts = posted(page)
    confirm = page.get_by_role("button", name="Confirm Production")
    confirm.click()
    dialog = page.get_by_role("dialog", name="Go live now?")
    visible(dialog)
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    focused(confirm)
    confirm.click()
    visible(dialog)
    page.reload()
    hidden(page.get_by_role("dialog", name="Go live now?"))
    assert posts and all("action=review" in body for body in posts)


def test_a_refused_apply_names_the_change_inside_the_dialog(page, component_origin):
    """ParishSoft data changed since the Review: Apply's refusal is shown in
    the dialog, naming the change, Apply stays unavailable, Cancel closes."""
    from playwright.sync_api import expect

    def refuse(route):
        """Answer Apply with the refusal the view draws; let the rest through."""
        body = route.request.post_data or ""
        if route.request.method != "POST" or "action=confirm" not in body:
            route.continue_()
            return
        answer = route.fetch(
            url=component_origin + "/go-live-confirm-review-changed", method="GET"
        )
        route.fulfill(status=409, body=answer.text(), content_type="text/html")

    page.goto(component_origin + CONFIRM)
    page.route(component_origin + CONFIRM, refuse)
    page.get_by_role("button", name="Confirm Production").click()
    dialog = page.get_by_role("dialog", name="Go live now?")
    dialog.get_by_label("Type Production to confirm").fill("Production")
    apply = dialog.get_by_role("button", name="Apply")
    apply.click()
    expect(dialog).to_contain_text("ParishSoft data changed since this preview")
    assert apply.is_disabled()
    # Typing again never makes the refused Apply available.
    dialog.get_by_label("Type Production to confirm").fill("Production ")
    assert apply.is_disabled()
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    assert page.url == component_origin + CONFIRM


def test_a_scheduled_campaign_asks_to_schedule(page, component_origin):
    """A campaign whose start is ahead: the question is "Schedule the
    campaign?" and the page says nothing is sent until the start."""
    from playwright.sync_api import expect

    page.goto(component_origin + "/go-live-confirm-scheduled")
    expect(page.locator("[data-go-live-impact]")).to_contain_text(
        "nothing is sent until then"
    )

    def scheduled(route):
        """Answer the Review with the scheduled campaign's question."""
        if route.request.method != "POST":
            route.continue_()
            return
        answer = route.fetch(
            url=component_origin + "/go-live-confirm-review", method="GET"
        )
        text = answer.text().replace(
            'data-review-title="Go live now?"',
            'data-review-title="Schedule the campaign?"',
        )
        route.fulfill(status=200, body=text, content_type="text/html")

    page.route(component_origin + CONFIRM, scheduled)
    page.get_by_role("button", name="Confirm Production").click()
    visible(page.get_by_role("dialog", name="Schedule the campaign?"))


def test_links_prepared_again_move_the_watching_page_to_prepare(page, component_origin):
    """Once ParishSoft data changes, the step settles and opens Prepare."""

    def later(route):
        """The first read is the page; later ones find the links re-preparing."""
        if route.request.method != "GET" or not later.seen:
            later.seen = True
            route.continue_()
            return
        route.fulfill(
            response=route.fetch(
                url=component_origin + "/go-live-confirm-moved", method="GET"
            )
        )

    later.seen = False
    page.route(component_origin + CONFIRM, later)
    page.goto(component_origin + CONFIRM)
    page.wait_for_url(component_origin + PREPARE, timeout=20000)


def test_the_ready_step_keeps_updating_across_a_reload(page, component_origin):
    """The step re-reads itself while it waits (every 30 seconds on the
    server, quicker here), and a browser reload returns to the same state,
    still updating."""
    from playwright.sync_api import expect

    reads = []

    def counted(route):
        """Count every read of the step, then let it through."""
        if route.request.method == "GET":
            reads.append(route.request.url)
        route.continue_()

    page.route(component_origin + CONFIRM, counted)
    page.goto(component_origin + CONFIRM)
    region = page.locator("#go-live-confirm")
    expect(region).to_have_attribute("data-live-pending", "")
    page.wait_for_timeout(2500)
    assert len(reads) > 1
    seen = len(reads)
    page.reload()
    expect(page.locator("#go-live-state")).to_have_text("Ready to confirm Production.")
    page.wait_for_timeout(2500)
    assert len(reads) > seen + 1

"""The Go live page's Check step in a real browser (#1585 slice 2).

The step is served at its real address and in each state
(go_live_components). Every Check form posts to that address, and the
fixture server answers by the posted action as the view does: the check
redirects back (to the check passed), Review answers the Start go-live
dialog's review, and Apply redirects to the step in progress.
"""

import pytest
from django.urls import reverse

from .go_live_components import GO_LIVE, PREPARE
from .test_components import axe_violations
from .test_in_place import MARK, MARKED
from .waits import focused, hidden, visible


def posted(page):
    """Collect the body of every POST to Check's address, in order."""
    bodies = []
    page.on(
        "request",
        lambda request: (
            bodies.append(request.post_data or "")
            if request.method == "POST" and GO_LIVE in request.url
            else None
        ),
    )
    return bodies


pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Each state's page, its heading and its one primary (None: nothing to do).
STATES = (
    (
        "/go-live",
        "2 things to fix before going live",
        "Check readiness and public web address",
    ),
    (
        "/go-live-unchecked",
        "Everything else is ready.",
        "Check readiness and public web address",
    ),
    (GO_LIVE, "Everything is ready.", "Start go-live"),
    (
        "/go-live-closed",
        "The campaign has closed and cannot go live with these dates.",
        "Review campaign dates",
    ),
    ("/go-live-started", "Go-live has started.", "Continue to Prepare"),
    (
        "/go-live-stopping",
        "Stopping go-live. Testing data deletion is stopping at the next safe point.",
        None,
    ),
    (
        "/go-live-stop-failed",
        "Stopping go-live did not finish",
        "Retry stopping go-live",
    ),
)


@pytest.mark.parametrize("width", [320, 1280])
def test_each_state_has_one_frame_and_at_most_one_primary(
    page, component_origin, axe_source, width
):
    """Heading, step bar, the state's line and one action row, on a phone too.

    Each state shows its own heading and exactly its one primary control
    (none while stopping), fits the width without scrolling sideways and
    passes the accessibility scan. Once go-live has started, Check is
    completed and Prepare opens the step in progress.
    """
    from playwright.sync_api import expect

    page.set_viewport_size({"width": width, "height": 900})
    for path, heading, primary in STATES:
        page.goto(component_origin + path)
        visible(page.get_by_role("heading", name="Go live", exact=True))
        expect(page.locator("#go-live-state")).to_contain_text(heading)
        expect(page.locator(".setup-stepper-current")).to_have_text(
            "Step 1 of 4: Check"
        )
        row = page.locator("[data-go-live-actions] [data-go-live-primary]")
        if primary is None:
            assert row.count() == 0, path
        else:
            assert row.count() == 1, path
            visible(row.get_by_text(primary, exact=True))
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        ), path
        assert axe_violations(page, axe_source) == [], path
    page.goto(component_origin + "/go-live-started")
    expect(page.locator(".setup-stepper-count")).to_have_text("1 of 4 steps completed")
    prepare = page.locator(".setup-track a").nth(1)
    assert prepare.get_attribute("href") == PREPARE
    # Settings are locked, so Check has no Back once go-live has started.
    assert page.locator("[data-go-live-back]").count() == 0
    # Stop failed says when the disposal's last attempt failed.
    page.goto(component_origin + "/go-live-stop-failed")
    expect(page.locator("[data-go-live-stop-failed]")).to_contain_text(
        "Its last attempt failed at"
    )


def test_back_returns_to_preview_and_test_email(page, component_origin):
    """Back, first in the row, opens the Campaign setup step before Go live;
    the page draws only its own step bar."""
    page.goto(component_origin + "/go-live-back")
    back = page.locator("[data-go-live-actions] > *").first
    assert back.get_attribute("href") == "/admin/campaign/content/test/1/"
    assert "Back" in back.inner_text()
    assert page.locator("#campaign-stepper").count() == 0
    assert page.locator(".setup-track").count() == 1


@pytest.mark.parametrize("width", [320, 1280])
def test_check_lists_one_summary_line_per_setup_step(
    page, component_origin, axe_source, width
):
    """Campaign setup's summary: one line per earlier step (#1404 PR 7).

    Each line names its step, linking the step's page, and says what it
    holds, so no step needs a review dialog of its own. The list fits a
    phone and passes the accessibility scan.
    """
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/go-live-back")
    summary = page.get_by_role("list", name="Campaign setup")
    assert summary.locator("li").all_inner_texts() == [
        "Campaign settings: as created",
        "Load ParishSoft data: loaded",
        "Share options: kept the defaults",
        "Pages and emails: 3 edited, 2 kept as is, 14 default text",
        "Dates and mail schedules: 1 invitation, 3 reminders",
        "Campaign images: changed",
        "Preview and test email: test accepted",
    ]
    link = summary.get_by_role("link", name="Share options")
    assert link.get_attribute("href") == reverse("admin:share_settings")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    # Outside the stepper (any other Check page) there is no summary.
    page.goto(component_origin + "/go-live-unchecked")
    assert page.locator("[data-go-live-summary]").count() == 0


def test_check_readiness_is_in_place_and_survives_a_reload(page, component_origin):
    """The check posts once and follows the redirect in place; a reload of
    the address shows the same result and sends no form again.

    The fresh step offers Start go-live in place of the check, the result
    line and the overall result are said, the address is Check's own, and
    the mark set before the check survives, so the page was not loaded
    again.
    """
    from playwright.sync_api import expect

    loads = []

    def first_unchecked(route):
        """The first read of Check's address is the step before the check."""
        if route.request.method == "GET" and not loads:
            loads.append(route.request.url)
            unchecked = component_origin + "/go-live-unchecked"
            route.fulfill(response=route.fetch(url=unchecked, method="GET"))
            return
        route.continue_()

    page.route(component_origin + GO_LIVE, first_unchecked)
    page.goto(component_origin + GO_LIVE)
    page.evaluate(MARK)
    posts = posted(page)
    page.get_by_role("button", name="Check readiness and public web address").click()
    visible(page.get_by_role("button", name="Start go-live"))
    expect(page.locator("div.visually-hidden[role=status]")).to_contain_text(
        "Readiness checked: every check passed."
    )
    visible(page.get_by_text("The public web address passed", exact=False))
    assert page.evaluate(MARKED) == "kept"
    assert page.url.split("#")[0] == component_origin + GO_LIVE
    assert len(posts) == 1 and "action=verify" in posts[0]
    page.reload()
    visible(page.get_by_role("button", name="Start go-live"))
    assert len(posts) == 1


def test_start_go_live_reviews_in_the_dialog_then_opens_the_step(
    page, component_origin
):
    """Start go-live opens the shared review dialog: title first, what
    happens next, the counts and the caution; Cancel and a reload change
    nothing; Apply posts once and opens the step in progress."""
    from playwright.sync_api import expect

    page.goto(component_origin + GO_LIVE)
    posts = posted(page)
    start = page.get_by_role("button", name="Start go-live")
    start.click()
    dialog = page.get_by_role("dialog", name="Start going live?")
    visible(dialog)
    focused(dialog.get_by_role("heading", name="Start going live?"))
    expect(dialog).to_contain_text("Testing data is deleted.")
    expect(dialog).to_contain_text("Nothing reaches Families until you confirm.")
    expect(dialog).to_contain_text("Deleted Testing data cannot be restored")
    expect(dialog).to_contain_text("1,234")
    visible(dialog.get_by_role("link", name="View Testing responses"))
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    focused(start)
    # A reload while the dialog is open closes it with nothing applied.
    start.click()
    visible(dialog)
    page.reload()
    hidden(page.get_by_role("dialog", name="Start going live?"))
    assert posts and all("action=review" in body for body in posts)
    reviews = len(posts)
    page.get_by_role("button", name="Start go-live").click()
    visible(dialog)
    with page.expect_navigation(url=component_origin + PREPARE):
        dialog.get_by_role("button", name="Apply").click()
    # The second Review, then one Apply, which carries the signed preview.
    assert len(posts) == reviews + 2
    assert (
        "action=confirm" in posts[-1] and "preview_token=synthetic-start" in posts[-1]
    )


@pytest.mark.parametrize(
    "fixture, status, said",
    [
        (
            "/go-live-review-started",
            409,
            "Another Administrator already started go-live.",
        ),
        ("/go-live-review-stopping", 409, "Go-live is still stopping."),
        (
            "/go-live-review-origin",
            409,
            "The public web address check failed or timed out",
        ),
    ],
)
def test_refused_start_stays_in_the_dialog(
    page, component_origin, fixture, status, said
):
    """A refused Start is said inside the dialog, as drawn, without Apply:
    another Administrator's Start (with the way to the step in progress), a
    stop still running, or a failed public web address check."""
    from playwright.sync_api import expect

    def refuse(route):
        """Answer the Review with the refusal; let reads through."""
        if route.request.method != "POST":
            route.continue_()
            return
        answer = route.fetch(url=component_origin + fixture, method="GET")
        route.fulfill(status=status, body=answer.text(), content_type="text/html")

    page.goto(component_origin + GO_LIVE)
    page.route(component_origin + GO_LIVE, refuse)
    page.get_by_role("button", name="Start go-live").click()
    dialog = page.get_by_role("dialog", name="Start going live?")
    visible(dialog)
    expect(dialog).to_contain_text(said)
    hidden(dialog.get_by_role("button", name="Apply"))
    if fixture == "/go-live-review-started":
        visible(dialog.get_by_role("link", name="Continue to Prepare"))


def test_check_updates_itself_without_a_refresh_link(page, component_origin):
    """While go-live is stopping the step re-reads itself and shows the stop
    finished, in place: no Refresh link, no reload, the change announced."""
    from playwright.sync_api import expect

    reads = []

    def later(route):
        """The first read is the page; every later one finds the stop done."""
        reads.append(route.request.url)
        if len(reads) == 1:
            route.continue_()
            return
        route.fulfill(
            response=route.fetch(
                url=component_origin + "/go-live-stopped", method="GET"
            )
        )

    page.route(component_origin + "/go-live-stopping", later)
    page.goto(component_origin + "/go-live-stopping")
    page.evaluate(MARK)
    assert page.get_by_role("link", name="Refresh", exact=False).count() == 0
    expect(page.locator("[data-go-live-stopped]")).to_contain_text(
        "Go-live stopped at", timeout=15000
    )
    visible(page.get_by_role("button", name="Check readiness and public web address"))
    expect(page.locator('[data-live-announcer="go-live-check"]')).to_have_text(
        "Everything else is ready."
    )
    assert page.evaluate(MARKED) == "kept"

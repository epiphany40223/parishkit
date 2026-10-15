"""The Restore review page in a real browser (#537): in place, accessible.

Every step posts to the page itself and is swapped into the review region
without a reload; focus follows the change. Each POST is answered from a
rendered sample state, routed by its action, as the view would answer it.
"""

import pytest

from .restore_review_components import (
    BLOCKED,
    CHANGED,
    GROUP_PREVIEW,
    INERT_PREVIEW,
    LISTED,
    PAGE,
    RELEASE_PREVIEW,
    RESEND_PREVIEW,
    REVIEWING,
    SETTLED,
    STEP_UP,
    SUPERSEDED,
    TESTING,
)
from .waits import painted

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
REGION = "#restore-review"
AXE = """async () => (await axe.run(document, {
    runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
})).violations.map(({id,impact}) => ({id,impact}))"""


def expect(locator):
    """Load the optional browser assertion library only after explicit opt-in."""
    from playwright.sync_api import expect as browser_expect

    return browser_expect(locator)


def _routed(page, component_origin, answers, start=None, address=PAGE):
    """Answer the page's own POSTs by their action, all at the page's address.

    ``answers`` maps an action to ``(status, component path)``. ``start`` is
    the state the page opens in (a component path); without it the page
    opens as served, before anything was found. ``address`` is the page's
    own address (the Held emails page shares these helpers, #757).
    """
    from urllib.request import urlopen

    def body(path):
        """The rendered component at ``path``."""
        with urlopen(component_origin + path) as response:
            return response.read().decode()

    bodies = {
        action: (status, body(path)) for action, (status, path) in answers.items()
    }
    opening = body(start) if start else None

    def respond(route):
        """Fulfil a POST from ``answers``, and the opening GET from ``start``."""
        if route.request.method != "POST":
            if opening is None:
                route.continue_()
            else:
                route.fulfill(status=200, content_type="text/html", body=opening)
            return
        data = route.request.post_data or ""
        action = next(name for name in bodies if f"action={name}&" in data + "&")
        status, body = bodies[action]
        route.fulfill(status=status, content_type="text/html", body=body)

    page.route(f"{component_origin}{address}", respond)


def _not_reloaded(page):
    """Mark the document, so a reload or a rewritten document loses the mark."""
    page.evaluate("document.body.dataset.notReloaded = 'yes'")
    return lambda: page.evaluate("document.body.dataset.notReloaded === 'yes'")


def _top(locator):
    """The element's top in the document, so scrolling does not count as a move."""
    return locator.evaluate("e => e.getBoundingClientRect().top + window.scrollY")


@pytest.mark.parametrize("width", [320, 1280])
def test_review_pages_are_accessible_at_phone_and_desktop_widths(
    page, component_origin, axe_source, width
):
    """Each state passes axe and fits a phone without sideways scrolling."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in (
        PAGE,
        TESTING,
        LISTED,
        GROUP_PREVIEW,
        RELEASE_PREVIEW,
        STEP_UP,
        SUPERSEDED,
        BLOCKED,
    ):
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert page.evaluate(AXE) == [], path
        expect(
            page.get_by_role("heading", name="Restore review", level=1)
        ).to_be_visible()
        # The help is there, closed until asked for.
        assert page.locator("details.about-page").get_attribute("open") is None


def test_find_settle_and_preview_release_in_place(page, component_origin):
    """Find, preview, confirm and the release preview never reload the page."""
    _routed(
        page,
        component_origin,
        {
            "list": (200, LISTED),
            "group-preview": (200, GROUP_PREVIEW),
            "group-confirm": (200, SETTLED),
            "release-preview": (200, RELEASE_PREVIEW),
        },
    )
    page.goto(component_origin + PAGE)
    kept = _not_reloaded(page)
    region = page.locator(REGION)
    # Release is unavailable, with its visible reason, until the held emails
    # are found (prerequisite gates).
    expect(region.get_by_text("52 emails still need a hold")).to_be_visible()
    expect(
        region.get_by_role("button", name="Release the site", exact=True)
    ).to_be_disabled()
    region.get_by_role("button", name="Find emails that may have gone out").click()
    expect(region.get_by_text("Found 52 more emails")).to_be_visible()
    # Mid-hand-off emails are counted, not held: the delivery warning owns them.
    expect(region.get_by_text("2 emails were being handed")).to_be_visible()
    expect(
        region.get_by_role("rowheader", name="Invitation", exact=False)
    ).to_be_visible()
    region.get_by_role(
        "button", name="Assume these were sent", exact=True
    ).first.click()
    confirm = region.get_by_role("button", name="Confirm: assume sent")
    expect(confirm).to_be_visible()
    # Only Families that can be emailed are counted; the rest are named apart.
    expect(
        region.get_by_role("heading", name="Assume 10 emails were sent")
    ).to_be_visible()
    expect(region.get_by_text("This also settles 2 held emails")).to_be_visible()
    # The confirm waits for the note, saying so after the button.
    expect(confirm).to_be_disabled()
    expect(region.get_by_text("Enter a short note to confirm.")).to_be_visible()
    note = region.get_by_label("Why (kept in the System logs)")
    note.fill("The provider's log shows them.")
    expect(confirm).to_be_enabled()
    confirm.click()
    expect(region.get_by_text("12 held emails settled.")).to_be_visible()
    region.get_by_role("button", name="Release the site", exact=True).click()
    release = region.get_by_role("button", name="Confirm: release the site")
    expect(release).to_be_visible()
    expect(
        region.get_by_text("40 held emails you have not decided on stay held")
    ).to_be_visible()
    assert kept()
    assert page.evaluate(
        "document.getElementById('restore-review').contains(document.activeElement)"
    )


def test_a_note_is_required_before_settling(page, component_origin):
    """The confirm is unavailable until the note holds more than spaces."""
    page.goto(component_origin + GROUP_PREVIEW)
    note = page.get_by_label("Why (kept in the System logs)")
    confirm = page.get_by_role("button", name="Confirm: assume sent")
    assert note.evaluate("element => element.required")
    expect(confirm).to_be_disabled()
    note.fill("   ")
    expect(confirm).to_be_disabled()
    note.fill("Seen in the provider's log.")
    expect(confirm).to_be_enabled()


def test_refusals_answer_inside_the_region(page, component_origin):
    """A stale sign-in offers the step-up in place; a change is explained."""
    _routed(
        page,
        component_origin,
        {"release-preview": (403, STEP_UP), "group-preview": (409, CHANGED)},
        start=REVIEWING,
    )
    page.goto(component_origin + PAGE)
    kept = _not_reloaded(page)
    region = page.locator(REGION)
    release = region.get_by_role("button", name="Release the site", exact=True)
    resend = region.get_by_role("button", name="Send these again", exact=True).first
    before = (_top(release), _top(resend))
    release.click()
    expect(region.get_by_text("this step needs a Google sign-in")).to_be_visible()
    expect(region.get_by_role("button", name="Confirm with Google")).to_be_visible()
    # The refusal is drawn after the controls, so none of them moved.
    after = (_top(release), _top(resend))
    assert after == pytest.approx(before, abs=1)
    resend.click()
    expect(region.get_by_text("Something changed since you looked")).to_be_visible()
    assert _top(resend) == pytest.approx(before[1], abs=1)
    assert kept()


def test_a_superseded_resend_is_explained_in_place(page, component_origin):
    """A resend a later reminder overtook says to assume it sent instead."""
    _routed(
        page,
        component_origin,
        {"group-preview": (200, RESEND_PREVIEW), "group-confirm": (409, SUPERSEDED)},
        start=LISTED,
    )
    page.goto(component_origin + PAGE)
    kept = _not_reloaded(page)
    region = page.locator(REGION)
    region.get_by_role("button", name="Send these again", exact=True).first.click()
    region.get_by_label("Why (kept in the System logs)").fill("Not in the log.")
    region.get_by_role("button", name="Confirm: send again").click()
    expect(region.get_by_text("would arrive out of order")).to_be_visible()
    assert kept()


def test_a_send_with_only_inert_holds_says_so(page, component_origin):
    """No "Send 0 emails again": the heading names what is settled."""
    page.goto(component_origin + INERT_PREVIEW)
    expect(
        page.get_by_role(
            "heading", name="Settle 3 held emails that would not be sent now"
        )
    ).to_be_visible()
    expect(page.get_by_role("heading", name="Send 0 emails again")).to_have_count(0)


def test_a_blocked_resend_is_unavailable_with_its_reason(page, component_origin):
    """ "Send these again" is greyed out, named by a visible reason."""
    page.goto(component_origin + BLOCKED)
    region = page.locator(REGION)
    buttons = region.get_by_role("button", name="Send these again", exact=True)
    expect(buttons).to_have_count(2)
    for index in range(2):
        expect(buttons.nth(index)).to_be_disabled()
    expect(region.get_by_text("An earlier restore still holds")).to_be_visible()
    expect(region.get_by_text("can only be assumed sent")).to_be_visible()
    # A send whose schedule was removed has no due time to show.
    expect(region.get_by_text("schedule removed")).to_be_visible()
    reason = buttons.first.get_attribute("aria-describedby")
    assert region.locator(f"#{reason}").is_visible()
    # "Assume these were sent" stays available.
    expect(
        region.get_by_role("button", name="Assume these were sent", exact=True).first
    ).to_be_enabled()


def _open_preview(page, component_origin, close):
    """Open a send's preview on the routed page; returns (region, close button)."""
    page.set_viewport_size({"width": 1280, "height": 900})
    _routed(
        page,
        component_origin,
        {
            "group-preview": (200, GROUP_PREVIEW),
            "cancel": (200, REVIEWING),
            "group-confirm": (200, SETTLED),
        },
        start=REVIEWING,
    )
    page.goto(component_origin + PAGE)
    region = page.locator(REGION)
    region.get_by_role(
        "button", name="Assume these were sent", exact=True
    ).first.click()
    button = region.get_by_role(
        "button", name="Cancel" if close == "cancel" else "Confirm: assume sent"
    )
    expect(button).to_be_visible()
    if close == "confirm":
        region.get_by_label("Why (kept in the System logs)").fill("In the log.")
        # Firefox scrolls the typed field into view at its next refresh; a
        # scroll the test makes before then is undone (#1037).
        painted(page)
    return region, button


# Where the reader has scrolled to: the preview's top 40 px inside the
# window, as after reading down to it; or the page's foot.
PREVIEW_NEAR_THE_TOP = (
    "scrollY + document.querySelector('[data-restore-preview]')"
    ".getBoundingClientRect().top - 40"
)
THE_FOOT = (
    "document.documentElement.scrollHeight - document.documentElement.clientHeight"
)


def _scroll_and_hold(page, where, *, attempts=5):
    """Wheel the page to ``where`` (a script giving a scrollY), and keep it there.

    The reader scrolls with the wheel, not a script: Firefox can re-apply
    the page's last scroll into view (the swap's, or the typed note's caret)
    at a later refresh, undoing a scripted scroll made in between, and then
    keeps doing so while the note has focus. The test would measure from a
    place the page was about to leave, and a click on a button scrolled out
    of view makes Playwright scroll it back into view first (#1088). A
    wheel turn can also stop short, so the page is wheeled again until it
    holds across two settled checks. The page itself scrolls nothing here,
    so this changes no behaviour under test.
    """
    there = f"Math.abs(({where}) - scrollY) <= 1"
    # Over the main content, so the wheel scrolls the page, not the menu.
    page.mouse.move(900, 450)
    for _ in range(attempts):
        page.mouse.wheel(0, page.evaluate(f"({where}) - scrollY"))
        painted(page)
        if page.evaluate(there):
            painted(page)
            if page.evaluate(there):
                return
    raise AssertionError(f"the page did not stay at {where} after {attempts} turns")


@pytest.mark.parametrize("close", ["cancel", "confirm"])
def test_closing_a_preview_moves_nothing_above_it(page, component_origin, close):
    """Cancel or Confirm leaves the table above the preview where it was.

    The reader has the preview in view. Focus moves to the next control,
    "Check again…", which takes the preview's place, so nothing scrolls.
    """
    region, button = _open_preview(page, component_origin, close)
    kept = _not_reloaded(page)
    _scroll_and_hold(page, PREVIEW_NEAR_THE_TOP)
    # The settled row's own counts change; the table's top must not move.
    row = region.get_by_role("row").first
    before = row.bounding_box()["y"]
    button.click()
    expect(button).to_have_count(0)
    # Within a pixel: engines round layout differently after a swap.
    assert abs(row.bounding_box()["y"] - before) <= 1
    assert page.evaluate(
        "document.activeElement.textContent.includes('Check again for emails')"
    )
    assert kept()


@pytest.mark.parametrize("close", ["cancel", "confirm"])
def test_closing_a_preview_at_the_foot_keeps_the_page_height(
    page, component_origin, close
):
    """At the page's foot the region keeps its height (data-keep-height).

    Without it the shorter page would pull everything down under the
    pointer; the only move left is focus scrolling to the next control.
    """
    region, button = _open_preview(page, component_origin, close)
    _scroll_and_hold(page, THE_FOOT)
    height = page.evaluate("document.documentElement.scrollHeight")
    button.click()
    expect(button).to_have_count(0)
    assert page.evaluate("document.documentElement.scrollHeight") >= height - 1
    assert (
        page.evaluate(
            "getComputedStyle(document.querySelector('#restore-review')).minHeight"
        )
        != "0px"
    )
    focused = page.evaluate_handle("document.activeElement")
    assert "Check again for emails" in focused.text_content()

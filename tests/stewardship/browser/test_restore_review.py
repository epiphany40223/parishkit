"""The Restore review page in a real browser (#537): in place, accessible.

Every step posts to the page itself and is swapped into the review region
without a reload; focus follows the change. Each POST is answered from a
rendered sample state, routed by its action, as the view would answer it.
"""

import pytest

from .restore_review_components import (
    CHANGED,
    GROUP_PREVIEW,
    INERT_PREVIEW,
    LISTED,
    PAGE,
    RELEASE_PREVIEW,
    SETTLED,
    STEP_UP,
    TESTING,
)

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


def _routed(page, component_origin, answers, start=None):
    """Answer the page's own POSTs by their action, all at the page's address.

    ``answers`` maps an action to ``(status, component path)``. ``start`` is
    the state the page opens in (a component path); without it the page
    opens as served, before anything was found.
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

    page.route(f"{component_origin}{PAGE}", respond)


def _not_reloaded(page):
    """Mark the document, so a reload or a rewritten document loses the mark."""
    page.evaluate("document.body.dataset.notReloaded = 'yes'")
    return lambda: page.evaluate("document.body.dataset.notReloaded === 'yes'")


@pytest.mark.parametrize("width", [320, 1280])
def test_review_pages_are_accessible_at_phone_and_desktop_widths(
    page, component_origin, axe_source, width
):
    """Each state passes axe and fits a phone without sideways scrolling."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in (PAGE, TESTING, LISTED, GROUP_PREVIEW, RELEASE_PREVIEW, STEP_UP):
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
    # Release is not offered until the held emails are found.
    expect(region.get_by_text("52 emails still need a hold")).to_be_visible()
    expect(region.get_by_role("button", name="Release the site…")).to_have_count(0)
    region.get_by_role("button", name="Find emails that may have gone out").click()
    expect(region.get_by_text("Found 52 more emails")).to_be_visible()
    # Mid-hand-off emails are counted, not held: the delivery warning owns them.
    expect(region.get_by_text("2 emails were being handed")).to_be_visible()
    expect(
        region.get_by_role("rowheader", name="Invitation", exact=False)
    ).to_be_visible()
    region.get_by_role("button", name="Assume these were sent…").first.click()
    confirm = region.get_by_role("button", name="Confirm: assume sent")
    expect(confirm).to_be_visible()
    # Only Families that can be emailed are counted; the rest are named apart.
    expect(
        region.get_by_role("heading", name="Assume 10 emails were sent")
    ).to_be_visible()
    expect(region.get_by_text("This also settles 2 held emails")).to_be_visible()
    note = region.get_by_label("Why (kept in the System logs)")
    note.fill("The provider's log shows them.")
    confirm.click()
    expect(region.get_by_text("12 held emails settled.")).to_be_visible()
    region.get_by_role("button", name="Release the site…").click()
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
    """The browser keeps an empty note from being sent."""
    page.goto(component_origin + GROUP_PREVIEW)
    note = page.get_by_label("Why (kept in the System logs)")
    assert note.evaluate("element => element.required")
    assert note.evaluate("element => !element.checkValidity()")


def test_refusals_answer_inside_the_region(page, component_origin):
    """A stale sign-in offers the step-up in place; a change is explained."""
    _routed(
        page,
        component_origin,
        {"release-preview": (403, STEP_UP), "group-preview": (409, CHANGED)},
        start=LISTED,
    )
    page.goto(component_origin + PAGE)
    kept = _not_reloaded(page)
    region = page.locator(REGION)
    region.get_by_role("button", name="Release the site…").click()
    expect(region.get_by_text("confirm it's you with Google first")).to_be_visible()
    expect(region.get_by_role("button", name="Confirm with Google")).to_be_visible()
    region.get_by_role("button", name="Send these again…").first.click()
    expect(region.get_by_text("Something changed since you looked")).to_be_visible()
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

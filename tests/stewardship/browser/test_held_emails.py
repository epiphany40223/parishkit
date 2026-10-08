"""The Held emails page in a real browser (#757): in place, accessible."""

import pytest

from .held_emails_components import NONE, PAGE, PREVIEW, SETTLED, STEP_UP

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


def _routed(page, component_origin, answers):
    """Answer the page's own POSTs by their action; let GETs through."""
    from urllib.request import urlopen

    def body(path):
        """The rendered component at ``path``."""
        with urlopen(component_origin + path) as response:
            return response.read().decode()

    bodies = {
        action: (status, body(path)) for action, (status, path) in answers.items()
    }

    def respond(route):
        """Fulfil a POST from ``answers``."""
        if route.request.method != "POST":
            route.continue_()
            return
        data = (route.request.post_data or "") + "&"
        action = next(name for name in bodies if f"action={name}&" in data)
        status, text = bodies[action]
        route.fulfill(status=status, content_type="text/html", body=text)

    page.route(f"{component_origin}{PAGE}", respond)


@pytest.mark.parametrize("width", [320, 1280])
def test_held_email_pages_are_accessible(page, component_origin, axe_source, width):
    """Each state passes axe and fits a phone without sideways scrolling."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in (PAGE, PREVIEW, SETTLED, STEP_UP, NONE):
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert page.evaluate(AXE) == [], path
        expect(page.get_by_role("heading", name="Held emails", level=1)).to_be_visible()


def test_send_again_previews_and_confirms_in_place(page, component_origin):
    """Preview then confirm, each swapped into the region without a reload."""
    _routed(
        page,
        component_origin,
        {"group-preview": (200, PREVIEW), "group-confirm": (200, SETTLED)},
    )
    page.goto(component_origin + PAGE)
    page.evaluate("document.body.dataset.notReloaded = 'yes'")
    region = page.locator(REGION)
    expect(page.locator("[data-held-invitations]")).to_be_visible()
    expect(region.get_by_text("9 Families get no reminders")).to_be_visible()
    region.get_by_role("button", name="Send these again…").click()
    expect(region.get_by_role("heading", name="Send 9 emails again")).to_be_visible()
    expect(region.get_by_text("They are sent shortly")).to_be_visible()
    region.get_by_label("Why (kept in the System logs)").fill("Not in the log.")
    region.get_by_role("button", name="Confirm: send again").click()
    expect(region.get_by_text("12 held emails settled.")).to_be_visible()
    expect(region.get_by_text("No held emails are waiting")).to_be_visible()
    assert page.evaluate("document.body.dataset.notReloaded === 'yes'")


def test_a_stale_sign_in_is_answered_in_place(page, component_origin):
    """The step-up appears inside the region."""
    _routed(page, component_origin, {"group-preview": (403, STEP_UP)})
    page.goto(component_origin + PAGE)
    page.locator(REGION).get_by_role("button", name="Assume these were sent…").click()
    expect(page.get_by_role("button", name="Confirm with Google")).to_be_visible()

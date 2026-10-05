"""Admin automation pages in Chromium and WebKit (ADM-11).

Revoking a session on Automation access and acknowledging the dashboard's
automation notices act in place (#559): one POST, no reload (a mark outside
the regions survives), the reader's place kept, the change announced, and
the address set to the redirect's page. Acknowledging the last notice still
swaps, because the notices region is rendered even when empty. The approval
page offers "Confirm with Google" with its controls unavailable while the
sign-in is stale, and its review states what was asked in plain words.
"""

import pytest

from .automation_components import ACCESS, APPROVAL, HOME, LOCAL_SIGN_IN, REVOKE
from .waits import has_text


def contains(locator, text):
    """Assert that ``locator`` comes to contain ``text``, waiting."""
    from playwright.sync_api import expect

    expect(locator).to_contain_text(text)


pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

MARK = "document.querySelector('h1').dataset.mark = 'kept'"
MARKED = "document.querySelector('h1').dataset.mark"
AXE = """async () => (await axe.run(document, {
    runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
})).violations.map(({id,impact}) => ({id,impact}))"""


def posts_to(page, part):
    """Collect every POST whose URL contains ``part``."""
    seen = []
    page.on(
        "request",
        lambda request: (
            seen.append(request.url)
            if request.method == "POST" and part in request.url
            else None
        ),
    )
    return seen


@pytest.mark.parametrize(
    "path",
    [
        ACCESS,
        "/automation-access-stale",
        "/automation-access-stale-local",
        APPROVAL,
        "/automation-approval-stale-local",
        "/automation-approval-review",
    ],
)
def test_automation_pages_are_accessible(page, component_origin, axe_source, path):
    """No WCAG 2.2 AA violations, and no sideways scrolling on a phone."""
    page.set_viewport_size({"width": 320, "height": 900})
    page.goto(component_origin + path)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


def test_wide_session_tables_scroll_inside_their_regions(page, component_origin):
    """A table wider than a phone scrolls in its own region, never the page.

    Fonts differ between machines (CI's Linux fonts are wider than macOS's),
    so the tables are widened on purpose rather than relying on how close
    to the edge they happen to fit.
    """
    page.set_viewport_size({"width": 320, "height": 900})
    page.goto(component_origin + ACCESS)
    page.evaluate(
        "document.querySelectorAll('table').forEach(t => t.style.fontSize = '28px')"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    for region in ("automation-sessions", "automation-live"):
        scroller = page.locator(f"#{region} .table-scroll")
        assert scroller.evaluate("e => e.scrollWidth > e.clientWidth")


def test_revoking_a_session_acts_in_place(page, component_origin):
    """One POST; the session lists swap; the mark and scroll stay; it is said."""
    page.set_viewport_size({"width": 1280, "height": 400})
    page.goto(component_origin + ACCESS)
    page.evaluate(MARK)
    sessions = page.locator("#automation-sessions")
    # The label is shown as text, never interpreted as markup.
    assert sessions.get_by_text("launch <assistant>").count() == 1
    button = sessions.get_by_role("button", name="Revoke")
    button.scroll_into_view_if_needed()
    offset = page.evaluate("window.scrollY")
    posts = posts_to(page, REVOKE)
    button.click()
    contains(sessions, "Revoked by you")
    assert page.evaluate(MARKED) == "kept"
    # No jump to the top; the page may shrink a little under the reader, as
    # the revoked session leaves the list of live ones.
    assert offset > 0 and page.evaluate("window.scrollY") > 0
    assert abs(page.evaluate("window.scrollY") - offset) < 120
    has_text(
        page.get_by_role("status").filter(has_text="Session revoked."),
        "Session revoked.",
    )
    # The list of every live session swapped too: only the other one is left.
    assert page.locator("#automation-live").get_by_text("other job").count() == 1
    assert (
        page.locator("#automation-live").get_by_text("launch <assistant>").count() == 0
    )
    assert posts == [component_origin + REVOKE]
    assert page.url.startswith(component_origin + ACCESS + "?revoked=1")


def test_acknowledging_the_last_notice_acts_in_place(page, component_origin):
    """The region empties in place; nothing reloads."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + HOME)
    page.evaluate(MARK)
    region = page.locator("#automation-notices")
    contains(region, "An automation session was approved")
    assert region.get_by_text("launch <assistant>").count() == 1
    region.get_by_role("button", name="Acknowledge", exact=True).click()
    has_text(
        page.get_by_role("status").filter(has_text="Notice acknowledged."),
        "Notice acknowledged.",
    )
    assert page.locator("#automation-notices").inner_text().strip() == ""
    assert page.evaluate(MARKED) == "kept"


def test_acknowledge_all_acts_in_place_and_notices_read_plainly(page, component_origin):
    """Every notice is a plain sentence; Acknowledge all clears them in place."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + HOME + "?two=1")
    page.evaluate(MARK)
    region = page.locator("#automation-notices")
    contains(region, "A command was turned away")
    text = region.inner_text()
    assert "Use refused" not in text and "admin_cmd" not in text
    region.get_by_role("button", name="Acknowledge all").click()
    has_text(
        page.get_by_role("status").filter(has_text="Notices acknowledged."),
        "Notices acknowledged.",
    )
    assert page.locator("#automation-notices").inner_text().strip() == ""
    assert page.evaluate(MARKED) == "kept"


def step_up_returns_to(page, path):
    """Assert the visible re-sign-in control posts to the login route with
    ``next`` naming ``path``, so the sign-in comes back to that page."""
    button = page.get_by_role("button", name="Confirm with Google")
    assert button.count() == 1 and button.is_visible() and button.is_enabled()
    form = page.locator("form").filter(has=button)
    assert form.get_attribute("method") == "post"
    assert form.get_attribute("action") == "/admin/login"
    assert form.locator("input[name=next]").get_attribute("value") == path


@pytest.mark.parametrize("width", [320, 1280])
def test_a_stale_sign_in_offers_a_working_step_up(page, component_origin, width):
    """Both stale pages show a visible re-sign-in control that returns to the
    approval page; Continue, the code field and Approve a session wait."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/automation-approval-stale")
    step_up_returns_to(page, APPROVAL)
    assert page.get_by_role("button", name="Continue").is_disabled()
    assert page.get_by_role("textbox", name="User code").is_disabled()
    page.goto(component_origin + "/automation-access-stale")
    step_up_returns_to(page, APPROVAL)
    assert page.get_by_role("button", name="Approve a session").is_disabled()


STORED_STEP_UP = "JSON.parse(localStorage.getItem('pk-local-step-up') || 'null')"


@pytest.mark.parametrize(
    "path,says",
    [
        ("/automation-approval-stale-local", "brings you back to this page"),
        ("/automation-access-stale-local", "takes you to the approval page"),
    ],
)
def test_the_local_step_up_returns_to_the_approval_page(
    page, component_origin, path, says
):
    """LOCAL has no Google (#613): the page names the laptop command and where
    the sign-in goes, records the approval page, and the local sign-in sends
    it as "next", clearing it only when the form is submitted."""
    page.goto(component_origin + path)
    assert page.get_by_role("button", name="Confirm with Google").count() == 0
    step_up = page.locator("[data-local-step-up]")
    assert step_up.is_visible()
    contains(step_up, "tools/stewardship-local.sh sign-in --email E")
    contains(step_up, says)
    assert page.evaluate(STORED_STEP_UP)["next"] == APPROVAL
    page.goto(component_origin + LOCAL_SIGN_IN + "#" + "t" * 43)
    assert page.locator("#local-sign-in-next").get_attribute("value") == APPROVAL
    # Opening the page keeps it: a link opened but not submitted keeps it.
    assert page.evaluate(STORED_STEP_UP)["next"] == APPROVAL
    with page.expect_navigation():
        page.get_by_role("button", name="Sign in").click()
    assert page.evaluate(STORED_STEP_UP) is None


def test_an_old_local_step_up_is_not_used(page, component_origin):
    """A return path recorded more than ten minutes ago is dropped."""
    page.goto(component_origin + "/automation-approval-stale-local")
    page.evaluate(
        "localStorage.setItem('pk-local-step-up', JSON.stringify("
        "{next: '/admin/', at: Date.now() - 11 * 60 * 1000}))"
    )
    page.goto(component_origin + LOCAL_SIGN_IN + "#" + "t" * 43)
    assert page.locator("#local-sign-in-next").get_attribute("value") == ""


def test_the_review_states_the_request_plainly(page, component_origin):
    """Scope as words, the host prefix and the confused-deputy warning."""
    page.goto(component_origin + "/automation-approval-review")
    review = page.get_by_role("region", name="Review the request")
    contains(review, "Approve only a code you started in your own terminal")
    assert review.get_by_text("launch <assistant>").count() == 1
    assert review.locator("dd").filter(has_text="Full").count() == 1
    assert review.locator("code").filter(has_text="0123456789ab").count() == 1
    assert page.get_by_role("button", name="Approve").is_enabled()

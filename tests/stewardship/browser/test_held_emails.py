"""The Held emails page in a real browser (#757): in place, accessible.

Every step posts to the page itself and is swapped into its region without a
reload; refusals and outcomes are drawn after the table, so nothing moves
under the pointer. Each POST is answered from a rendered sample state, routed
by its action, as the view would answer it.
"""

import pytest

from .held_emails_components import (
    BLOCKED,
    CHANGED,
    NONE,
    PAGE,
    PREVIEW,
    SETTLED,
    STEP_UP,
)
from .test_restore_review import AXE, _not_reloaded, _routed, _top, expect

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
REGION = "#held-emails"


def _route(page, component_origin, answers, start=None):
    """Answer this page's own POSTs by their action (see _routed)."""
    _routed(page, component_origin, answers, start, address=PAGE)


@pytest.mark.parametrize("width", [320, 1280])
def test_held_email_pages_are_accessible_at_phone_and_desktop_widths(
    page, component_origin, axe_source, width
):
    """Each state passes axe and fits a phone without sideways scrolling."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in (PAGE, PREVIEW, SETTLED, STEP_UP, BLOCKED, NONE):
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert page.evaluate(AXE) == [], path
        expect(page.get_by_role("heading", name="Held emails", level=1)).to_be_visible()
        # The help is there, closed until asked for.
        assert page.locator("details.about-page").get_attribute("open") is None


@pytest.mark.parametrize("width", [320, 1280])
def test_preview_and_settle_in_place(page, component_origin, width):
    """Preview, the note-gated confirm and the outcome never reload the page.

    At phone width too, the blocked-reminders line (up to three lines there,
    empty once settled) keeps its space, so the row's buttons never move.
    """
    page.set_viewport_size({"width": width, "height": 900})
    _route(
        page,
        component_origin,
        {"group-preview": (200, PREVIEW), "group-confirm": (200, SETTLED)},
    )
    page.goto(component_origin + PAGE)
    kept = _not_reloaded(page)
    region = page.locator(REGION)
    summary = region.locator("[data-held-summary]")
    expect(summary).to_contain_text("10 Families get no reminders until their held")
    # The line fits the lines it reserves, even on a phone.
    assert summary.evaluate(
        "e => e.scrollHeight <= Math.ceil(parseFloat(getComputedStyle(e).minHeight))"
    )
    assume = region.get_by_role(
        "button", name="Assume these were sent", exact=True
    ).first
    before = _top(assume)
    assume.click()
    confirm = region.get_by_role("button", name="Confirm: assume sent")
    expect(confirm).to_be_visible()
    expect(
        region.get_by_role("heading", name="Assume 10 emails were sent")
    ).to_be_visible()
    # The row keeps its buttons while the preview is open, in place.
    assert _top(assume) == pytest.approx(before, abs=1)
    expect(confirm).to_be_disabled()
    region.get_by_label("Why (kept in the System logs)").fill("In the sent log.")
    expect(confirm).to_be_enabled()
    confirm.click()
    expect(region.get_by_text("12 held emails settled.")).to_be_visible()
    # The send stays listed, decided; its buttons stay where they were.
    expect(region.get_by_text("Every email of this send is decided.")).to_be_visible()
    assert _top(assume) == pytest.approx(before, abs=1)
    assert kept()
    assert page.evaluate(
        "document.getElementById('held-emails').contains(document.activeElement)"
    )


def test_refusals_answer_inside_the_region(page, component_origin):
    """A stale sign-in offers the step-up in place; a change is explained."""
    _route(
        page,
        component_origin,
        {"group-preview": (403, STEP_UP), "group-confirm": (409, CHANGED)},
    )
    page.goto(component_origin + PAGE)
    kept = _not_reloaded(page)
    region = page.locator(REGION)
    resend = region.get_by_role("button", name="Send these again", exact=True).first
    assume = region.get_by_role(
        "button", name="Assume these were sent", exact=True
    ).nth(1)
    before = (_top(resend), _top(assume))
    resend.click()
    expect(region.get_by_text("this step needs a Google sign-in")).to_be_visible()
    expect(region.get_by_role("button", name="Confirm with Google")).to_be_visible()
    # The refusal is drawn after the table, so no control moved.
    assert (_top(resend), _top(assume)) == pytest.approx(before, abs=1)
    assert kept()


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
    expect(
        region.get_by_role("button", name="Assume these were sent", exact=True).first
    ).to_be_enabled()


def test_without_holds_the_page_says_so(page, component_origin):
    """No held email: a short line, no table."""
    page.goto(component_origin + NONE)
    expect(page.get_by_text("No held emails.")).to_be_visible()
    expect(page.locator(f"{REGION} table")).to_have_count(0)

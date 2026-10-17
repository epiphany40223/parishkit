"""Clear the halt on System health in a real browser (ADM-13 PR 4b, #530).

The control sits in the Mail tile's details while a mail sender is halted.
Its preview says what is halted and why and shows the mailbox check
running; the check's outcome and Confirm follow the polled status in place,
without a reload and without moving anything; Confirm's answer is drawn in
the same region.
"""

import pytest

from .system_health_components import (
    BACKUP_PAGE,
    CLEAR_DONE,
    CLEAR_PAGE,
    CLEAR_PREVIEW,
)
from .test_system_health import _away, _axe, _not_reloaded, _places, _routed
from .waits import visible


def expect(locator):
    """Load the optional browser assertion library only after explicit opt-in."""
    from playwright.sync_api import expect as browser_expect

    return browser_expect(locator)


pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def _open(page, component_origin):
    """The halted page at its real address, its steps answered as the view
    would; returns the #clear-halt region."""
    _routed(
        page,
        component_origin,
        {"clear_preview": (200, CLEAR_PREVIEW), "clear_confirm": (200, CLEAR_DONE)},
        shown=CLEAR_PAGE,
    )
    page.goto(component_origin + BACKUP_PAGE + "#health-mail")
    return page.locator("#clear-halt")


def _top(locator):
    """Where ``locator`` is in the document, in pixels from its top."""
    return locator.evaluate("e => e.getBoundingClientRect().top + window.scrollY")


def test_clear_the_halt_checks_the_mailbox_and_confirms_in_place(
    page, component_origin, axe_source
):
    """Preview, the mailbox check's outcome, then Confirm, each in
    #clear-halt in the Mail tile; Confirm waits for the check with its
    reason; nothing above moves and the page is never reloaded."""
    region = _open(page, component_origin)
    kept = _not_reloaded(page)
    heading = region.get_by_role("heading", name="Clear the halt", exact=True)
    expect(heading).to_be_visible()
    # Right after the Mail tile's problems, which name the halt.
    assert region.evaluate(
        """e => e.parentElement.matches('[data-live-keyed="clear-halt"]')
            && e.parentElement.parentElement.id === 'health-mail-details'"""
    )
    places = _places(page, heading)
    region.get_by_role("button", name="Clear the halt", exact=True).click()
    confirm = region.get_by_role("button", name="Confirm: clear the halt")
    expect(confirm).to_be_visible()
    visible(region.get_by_text("Sending halted at"))
    visible(region.get_by_text("37 emails are waiting to be sent."))
    # The check runs, and Confirm waits for it, its reason beside it.
    check = region.locator("[data-clear-check]")
    expect(check).to_have_text("Checking the mailbox…")
    expect(confirm).to_be_disabled()
    reason = region.locator("#clear-halt-confirm-reason")
    expect(reason).to_have_text("Confirm waits for the mailbox check.")
    assert confirm.get_attribute("aria-describedby") == "clear-halt-confirm-reason"
    assert places() == []
    where = _top(confirm)
    # The answer's own recheck brings the passed check in place, once the
    # pointer leaves the tile grid.
    _away(page)
    expect(check).to_contain_text("The mailbox check passed at", timeout=30000)
    expect(confirm).to_be_enabled()
    expect(reason).to_contain_text("Ready")
    assert abs(_top(confirm) - where) < 0.5
    assert places() == []
    # Times in the browser's own zone, never an ISO instant or "UTC".
    for stamp in region.locator("time").all():
        expect(stamp).not_to_have_text(stamp.get_attribute("datetime"))
    assert "UTC" not in region.inner_text()
    assert _axe(page, axe_source) == []
    confirm.click()
    expect(region.get_by_text("Halt cleared.")).to_be_visible()
    assert kept()
    assert places() == []


@pytest.mark.parametrize("width", [320, 1280])
def test_the_clear_region_fits_and_is_accessible(
    page, component_origin, axe_source, width
):
    """The open preview needs no sideways scroll at phone width and passes
    axe at both widths."""
    page.set_viewport_size({"width": width, "height": 900})
    region = _open(page, component_origin)
    region.get_by_role("button", name="Clear the halt", exact=True).click()
    expect(region.get_by_role("button", name="Confirm: clear the halt")).to_be_visible()
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= window.innerWidth"
    )
    assert _axe(page, axe_source) == []

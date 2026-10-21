"""Report a bounced address on the Mail message page (#806 slice 4).

A delivered Production Family email's page offers "Report a bounced
address": Report bounce waits for an address, a note and the tick; the
report acts in place and replaces only its own region, so nothing above the
button moves and the reader keeps their place; the report then shows below
the form with its browser-local time. The page is served at its real
address and the fixture server answers the report with a real
Post/Redirect/Get redirect (delivery_components.POSTS). Refused addresses
marks a reported bounce, and its page shows the note.
"""

import pytest

from .delivery_components import BOUNCE, DELIVERED
from .test_in_place import MARK, MARKED, VIEWPORT, count_requests, scroll_below
from .waits import has_text

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

REPORT_BUTTON = "#bounce-report button[type=submit]"
NOTE = "Bounce arrived 10/12: mailbox not found."


def open_bounce(page):
    """Open the closed "Did this email bounce?" disclosure (#1563)."""
    disclosure = page.locator("#bounce-disclosure")
    assert disclosure.get_attribute("open") is None
    disclosure.locator("summary").click()


def test_report_waits_for_the_address_the_note_and_the_tick(page, component_origin):
    """Report bounce is unavailable, with its hint after it, until all three
    are given; a note of spaces does not count."""
    page.goto(component_origin + DELIVERED)
    open_bounce(page)
    button = page.locator(REPORT_BUTTON)
    hint = page.locator("#bounce-complete-hint")
    assert button.is_disabled()
    assert hint.inner_text() == "Choose the address that bounced."
    page.get_by_label("Address that bounced").select_option("head@example.org")
    page.get_by_label("What did the bounce say?").fill("   ")
    page.get_by_role("checkbox", name="I saw a bounce message").check()
    assert button.is_disabled()
    page.get_by_label("What did the bounce say?").fill(NOTE)
    assert button.is_enabled()
    page.get_by_role("checkbox", name="I saw a bounce message").uncheck()
    assert button.is_disabled()
    # The only-address notice is drawn with the page and sits after the
    # button, so its going away after a report never moves the button.
    last = page.locator("#bounce-last")
    assert "head@example.org is this Family's only address left" in last.inner_text()
    assert last.bounding_box()["y"] > button.bounding_box()["y"]
    # The hint sits after the button, never on a line above it.
    assert hint.evaluate(
        "node => node.compareDocumentPosition(document.querySelector("
        "'#bounce-report button[type=submit]')) & Node.DOCUMENT_POSITION_PRECEDING"
    )


def test_field_help_opens_from_toggletips(page, component_origin):
    """The address and note help open from "i" buttons beside their labels."""
    from playwright.sync_api import expect

    page.goto(component_origin + DELIVERED)
    open_bounce(page)
    for tip in ("#bounce-address-tip", "#bounce-note-tip"):
        bubble = page.locator(tip)
        expect(bubble).to_be_hidden()
        page.locator(f"button[aria-controls='{tip[1:]}']").click()
        expect(bubble).to_be_visible()
        page.keyboard.press("Escape")


def test_report_happens_in_place_without_moving_the_button(page, component_origin):
    """One POST, no reload, the reader's place kept: only the panel is
    replaced, the button does not move, and the report is listed below the
    form with a browser-local time."""
    from playwright.sync_api import expect

    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + DELIVERED)
    open_bounce(page)
    page.evaluate(MARK)
    posts = count_requests(page, "POST", BOUNCE)
    page.get_by_label("Address that bounced").select_option("head@example.org")
    page.get_by_label("What did the bounce say?").fill(NOTE)
    page.get_by_role("checkbox", name="I saw a bounce message").check()
    offset = scroll_below(page, REPORT_BUTTON)
    before = page.locator(REPORT_BUTTON).bounding_box()["y"] + offset
    page.locator(REPORT_BUTTON).click()
    has_text(
        page.get_by_role("status").filter(has_text="Bounce reported."),
        "Bounce reported.",
    )
    panel = page.locator("#bounce-report")
    # Focus goes to the new report list, below the button, so the page does
    # not scroll to the panel's heading.
    reports = panel.get_by_role("heading", name="Bounces reported for this email")
    expect(reports).to_be_focused()
    # The disclosure stays open across the in-place answer (data-keep-open).
    assert page.locator("#bounce-disclosure").evaluate("node => node.open")
    assert page.evaluate(MARKED) == "kept"
    # Nothing above the button changed, so it stays where it was on the
    # page; the browser scrolls down at most as far as needed to show the
    # focused report list below it, never back to the top.
    scrolled = page.evaluate("window.scrollY")
    assert offset <= scrolled
    after = page.locator(REPORT_BUTTON).bounding_box()["y"] + scrolled
    assert abs(after - before) < 1
    assert reports.evaluate(
        "node => { const box = node.getBoundingClientRect();"
        " return box.top >= 0 && box.bottom <= innerHeight; }"
    )
    assert len(posts) == 1
    # The reported address is no longer offered; the other still is.
    options = page.get_by_label("Address that bounced").locator("option")
    assert options.all_inner_texts() == ["Choose an address", "spouse@example.org"]
    # The fresh form is gated again.
    assert page.locator(REPORT_BUTTON).is_disabled()
    report = panel.get_by_role("listitem").first
    assert report.get_by_role("link", name="head@example.org").count() == 1
    # Browser-local time: noon UTC in the fixture browser's Los Angeles.
    expect(report.locator("time")).to_contain_text("5:00 AM")


def test_unavailable_report_names_its_reason(page, component_origin):
    """While the email is not delivered, Report bounce is shown unavailable
    and is described by the visible reason."""
    page.goto(component_origin + "/delivery-bounce-unavailable")
    open_bounce(page)
    button = page.locator("#bounce-report button")
    assert button.is_disabled()
    assert button.get_attribute("aria-describedby") == "bounce-unavailable"
    assert "only a delivered email can bounce" in page.inner_text("#bounce-unavailable")
    assert page.locator("#bounce-report form").count() == 0


def test_refused_addresses_mark_a_reported_bounce(page, component_origin):
    """The list marks a reported bounce under its address, and its own page
    says who reported it, when (browser-local) and what the bounce said."""
    from playwright.sync_api import expect

    page.goto(component_origin + "/delivery-refusals-bounce")
    cell = page.locator("table.data-table tbody tr").first.locator("td").nth(2)
    assert cell.inner_text().split("\n")[-1].strip().endswith("Bounce reported")
    page.goto(component_origin + "/delivery-refusal-bounce")
    main = page.locator("main")
    expect(main).to_contain_text("Reported as bounced by an Administrator")
    expect(main).to_contain_text(NOTE)
    expect(main.locator("time").first).to_contain_text("5:00 AM")

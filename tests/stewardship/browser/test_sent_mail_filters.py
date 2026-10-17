"""Sent mail's filter bar and Production / Testing choices in a browser (#1562).

The filters, the mode choices and the rows' Recipient act in place: the page
is not reloaded, the reader keeps their scroll position, the address follows
the view's linkable filters (never the Recipient, which is private POST
state), and nothing above the table moves. The bar fits one row on a desktop screen and
wraps on a phone without sideways scrolling. The fixture server answers each
in-place request with a pre-rendered page (``delivery_components``), routed
here, so each test also sees the exact request the control sent.
"""

from urllib.parse import parse_qs

import pytest

from .waits import eventually, has_text, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

PAGE = "/deliveries-names"
MARK = "document.querySelector('h1').dataset.mark = 'kept'"
MARKED = "document.querySelector('h1').dataset.mark"
# Where each control of the filter bar and the mode links sits on the page.
PLACES = """() => [...document.querySelectorAll(
    '#delivery-mode button, #table-filters input:not([type=hidden]), '
    + '#table-filters select, #table-filters button')]
    .map(node => { const box = node.getBoundingClientRect();
        return [node.id || node.textContent.trim(), Math.round(box.x),
                Math.round(box.y + scrollY)]; })"""


def answer_with(page, component_origin, fixture):
    """Answer every POST to the list page with ``fixture``'s page.

    Returns the bodies posted, each parsed. The page itself (a GET) keeps
    its ordinary fixture.
    """
    body = page.request.get(component_origin + fixture).text()
    sent = []

    def handle(route):
        """Fulfil a filtered read with the fixture; let the page through."""
        if route.request.method != "POST":
            route.continue_()
            return
        sent.append(parse_qs(route.request.post_data or "", keep_blank_values=True))
        route.fulfill(status=200, body=body, content_type="text/html")

    page.route(component_origin + PAGE + "*", handle)
    return sent


def open_list(page, component_origin, width=1280):
    """Load the list at ``width``, scrolled so the filters are mid-screen."""
    page.set_viewport_size({"width": width, "height": 700})
    page.goto(component_origin + PAGE)
    page.evaluate(MARK)
    # The browser zone is in the form before anything is applied (#558).
    eventually(
        page,
        "document.querySelector('#table-filters [name=zone]').value",
        "America/Los_Angeles",
    )


def test_the_bar_fits_one_row_and_wraps_on_a_phone(page, component_origin):
    """At 1280 px every field and the button share one row; at 390 px the bar
    wraps inside the screen and the page never scrolls sideways."""
    open_list(page, component_origin)
    # The fields line up at their tops (a date control can be a little
    # taller than a text box), and Apply sits in the same row.
    boxes = page.evaluate(
        """() => [...document.querySelectorAll(
            '#table-filters > div, #table-filters > button')]
            .map(node => { const box = node.getBoundingClientRect();
                return [Math.round(box.top), Math.round(box.bottom)]; })"""
    )
    assert len(boxes) == 6
    tops = [top for top, _ in boxes[:5]]
    assert max(tops) - min(tops) <= 2, boxes
    assert boxes[5][0] < min(bottom for _, bottom in boxes[:5]), boxes
    # Every label, the Recipient's with its toggletip too, shares one line.
    labels = page.evaluate(
        """() => [...document.querySelectorAll(
            '#table-filters > div > :is(label, .label-row)')]
            .map(node => Math.round(node.getBoundingClientRect().bottom))"""
    )
    assert len(labels) == 5 and max(labels) - min(labels) <= 1, labels
    # Each field is wide enough for what it shows: no date is clipped, and
    # an empty one has room for the "mm/dd/yyyy" placeholder beside the
    # picker button (which scrollWidth cannot see).
    assert page.evaluate(
        """() => [...document.querySelectorAll('#table-filters input[type=date]')]
            .every(node => node.scrollWidth <= node.clientWidth + 1)"""
    )
    room = page.evaluate(
        """() => [...document.querySelectorAll('#table-filters input[type=date]')]
            .map(node => {
                const style = getComputedStyle(node);
                const context = document.createElement('canvas').getContext('2d');
                context.font = style.font;
                return [node.clientWidth - parseFloat(style.paddingLeft)
                    - parseFloat(style.paddingRight),
                    context.measureText('mm/dd/yyyy').width];
            })"""
    )
    assert all(inner >= text + 28 for inner, text in room), room
    page.set_viewport_size({"width": 390, "height": 800})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.evaluate(
        """() => [...document.querySelectorAll('#table-filters > *')]
            .every(node => node.getBoundingClientRect().right <= innerWidth)"""
    )
    # The zone the days are in is named under the fields.
    has_text(
        page.locator("#delivery-zone-help"),
        "From and Through are days in this computer's time zone: America/Los_Angeles.",
    )


def test_filters_apply_in_place(page, component_origin):
    """Apply filters posts the form, swaps the table in without a reload or a
    scroll jump, records the view's linkable filters in the address bar
    (never the Recipient) and moves no control above the table."""
    sent = answer_with(page, component_origin, "/deliveries-applied")
    open_list(page, component_origin)
    page.locator("#delivery-recipient").fill("12345")
    page.locator("#delivery-state").select_option("needs_attention")
    page.evaluate("window.scrollTo(0, 120)")
    offset = page.evaluate("window.scrollY")
    before = page.evaluate(PLACES)
    page.get_by_role("button", name="Apply filters").click()
    eventually(
        page,
        "document.querySelector('tbody tr td:last-child').textContent.trim()",
        "Failed",
    )
    assert page.evaluate(MARKED) == "kept"
    (query,) = sent
    assert query["recipient"] == ["12345"]
    assert query["state"] == ["needs_attention"]
    assert query["mode"] == ["production"]
    assert query["purpose"] == ["all"]
    assert query["zone"] == ["America/Los_Angeles"]
    assert query["csrfmiddlewaretoken"]
    assert "state=needs_attention" in page.url
    assert "recipient" not in page.url and "12345" not in page.url
    assert abs(page.evaluate("window.scrollY") - offset) < 40
    assert page.evaluate(PLACES) == before
    # The mode choices now post the applied filters, the Recipient too.
    testing = page.locator("#delivery-mode form").filter(
        has=page.get_by_role("button", name="Testing")
    )
    assert testing.locator("input[name=recipient]").get_attribute("value") == "12345"
    assert testing.get_attribute("method") == "post"


def test_the_mode_choices_switch_in_place(page, component_origin):
    """Testing redraws the list in place: its choice becomes current, the
    filter form's hidden mode follows, and nothing above the table moves."""
    sent = answer_with(page, component_origin, "/deliveries-testing")
    open_list(page, component_origin)
    choices = page.locator("#delivery-mode")
    assert (
        choices.get_by_role("button", name="Production").get_attribute("aria-current")
        == "true"
    )
    before = page.evaluate(PLACES)
    choices.get_by_role("button", name="Testing").click()
    eventually(
        page,
        "document.querySelector('#delivery-mode [aria-current=true]').textContent",
        "Testing",
    )
    assert page.evaluate(MARKED) == "kept"
    assert sent and sent[0]["mode"] == ["testing"]
    assert (
        page.locator("#table-filters input[name=mode]").get_attribute("value")
        == "testing"
    )
    assert "mode=testing" in page.url
    assert page.evaluate(PLACES) == before
    # The row's Recipient now lists that Family's Testing email.
    visible(page.locator("tbody form:has(input[name=mode][value=testing]) button"))


def test_a_rows_recipient_lists_it_in_place(page, component_origin):
    """A row's Recipient posts that recipient and mode, redraws the list in
    place, sets the filter bar to it and never puts it in the address."""
    sent = answer_with(page, component_origin, "/deliveries-applied")
    open_list(page, component_origin)
    page.locator("tbody tr").first.get_by_role(
        "button", name="Castellanos, Maximiliana and Bartholomew"
    ).click()
    eventually(
        page,
        "document.querySelector('tbody tr td:last-child').textContent.trim()",
        "Failed",
    )
    assert page.evaluate(MARKED) == "kept"
    (query,) = sent
    assert query["recipient"] == ["12345"] and query["mode"] == ["production"]
    assert page.locator("#delivery-recipient").input_value() == "12345"
    assert "recipient" not in page.url and "12345" not in page.url


def test_a_through_day_before_from_holds_apply(page, component_origin):
    """The gate keeps Apply filters unavailable, saying why in its reserved
    line, while Through is before From; the line keeps its height."""
    open_list(page, component_origin)
    hint = page.locator("#delivery-filter-hint")
    height = hint.evaluate("node => node.getBoundingClientRect().height")
    assert height > 0
    page.locator("#delivery-start").fill("2026-10-10")
    page.locator("#delivery-end").fill("2026-10-01")
    page.locator("#delivery-end").blur()
    button = page.get_by_role("button", name="Apply filters")
    eventually(
        page, "document.querySelector('#table-filters button[type=submit]').disabled"
    )
    assert button.is_disabled()
    has_text(hint, "Enter a whole Through date, no earlier than From, or clear it.")
    assert hint.evaluate("node => node.getBoundingClientRect().height") == height

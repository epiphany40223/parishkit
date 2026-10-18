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

from .keyboard import tab_to_next
from .waits import eventually, has_text, recorded, visible

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


# Both mode choices share one line, and it ends above the filter bar.
CHOICES_ABOVE_THE_BAR = """() => {
    const pills = [...document.querySelectorAll('#delivery-mode button')]
        .map(node => node.getBoundingClientRect());
    const bar = document.querySelector('#table-filters').getBoundingClientRect();
    return pills.length === 2 && Math.abs(pills[0].top - pills[1].top) <= 1
        && pills.every(box => box.bottom <= bar.top);
}"""

# The mode choices share the heading's line, after its About summary.
CHOICES_BESIDE_THE_HEADING = """() => {
    const heading = document.querySelector('h1').getBoundingClientRect();
    const about = document.querySelector('details[data-about-page] > summary')
        .getBoundingClientRect();
    return [...document.querySelectorAll('#delivery-mode button')].every(node => {
        const box = node.getBoundingClientRect();
        const middle = (box.top + box.bottom) / 2;
        return middle > heading.top && middle < heading.bottom
            && box.left >= about.right;
    });
}"""


def answer_with(page, component_origin, fixture):
    """Answer every POST to the list page with ``fixture``'s page.

    Returns the bodies posted, each parsed. The page itself (a GET) keeps
    its ordinary fixture, whatever query an answer put in its address (a
    reload of that address).
    """
    body = page.request.get(component_origin + fixture).text()
    listed = page.request.get(component_origin + PAGE).text()
    sent = []

    def handle(route):
        """Fulfil a filtered read with the fixture; the page with its own."""
        if route.request.method != "POST":
            route.fulfill(status=200, body=listed, content_type="text/html")
            return
        sent.append(parse_qs(route.request.post_data or "", keep_blank_values=True))
        route.fulfill(status=200, body=body, content_type="text/html")

    page.route(component_origin + PAGE + "*", handle)
    return sent


def open_list(page, component_origin, width=1280, height=700):
    """Load the list at ``width`` by ``height``."""
    page.set_viewport_size({"width": width, "height": height})
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
    # Production / Testing sits at the end of the heading's line, above the
    # bar: beside the five filters and Apply it would not fit this row, and
    # a line of its own would push the first rows off a laptop screen.
    assert page.evaluate(CHOICES_ABOVE_THE_BAR)
    assert page.evaluate(CHOICES_BESIDE_THE_HEADING)
    page.set_viewport_size({"width": 390, "height": 800})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.evaluate(
        """() => [...document.querySelectorAll(
            '#table-filters > *, #delivery-mode button')]
            .every(node => node.getBoundingClientRect().right <= innerWidth)"""
    )
    # On a phone it takes its own line under the heading.
    assert page.evaluate(CHOICES_ABOVE_THE_BAR)
    assert not page.evaluate(CHOICES_BESIDE_THE_HEADING)
    assert page.evaluate(
        """() => document.querySelector('#delivery-mode button')
            .getBoundingClientRect().top
            >= document.querySelector('h1').getBoundingClientRect().bottom"""
    )


def test_the_zone_is_named_in_a_toggletip_on_from(page, component_origin):
    """From's label carries the zone note as a toggletip, not a line of its
    own (#227): the keyboard reaches its "i" after Purpose, it opens the
    note naming the browser's zone, and both date fields are described by
    it."""
    from playwright.sync_api import expect

    open_list(page, component_origin)
    bubble = page.locator("#delivery-zone-help")
    button = page.locator("#delivery-start-label + .toggletip .toggletip-button")
    visible(button)
    expect(bubble).to_be_hidden()
    assert (
        page.locator("#table-filters [data-browser-zone-note]:not(.toggletip)").count()
        == 0
    )
    page.locator("#delivery-purpose").focus()
    tab_to_next(page)
    expect(button).to_be_focused()
    page.keyboard.press("Enter")
    visible(bubble)
    has_text(
        bubble,
        "From and Through are days in this computer's time zone: America/Los_Angeles.",
    )
    page.keyboard.press("Escape")
    expect(bubble).to_be_hidden()
    for field in ("#delivery-start", "#delivery-end"):
        described = page.locator(field).get_attribute("aria-describedby") or ""
        assert described.split()[0] == "delivery-zone-help", (field, described)


def test_filters_apply_in_place(page, component_origin):
    """Apply filters posts the form, swaps the table in without a reload or a
    scroll jump, records the view's linkable filters in the address bar
    (never the Recipient) and moves no control above the table."""
    sent = answer_with(page, component_origin, "/deliveries-applied")
    # Short enough that the one-row answer still leaves room to stay
    # scrolled to 120 px, so a jump would show.
    open_list(page, component_origin, height=560)
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
    """The choice is the shared segmented pair (#1575), a group named by its
    visible label. Testing redraws the list in place: its segment becomes
    current and keeps focus, the filter form's hidden mode follows, and
    nothing above the table moves."""
    from playwright.sync_api import expect

    sent = answer_with(page, component_origin, "/deliveries-testing")
    open_list(page, component_origin)
    choices = page.locator("#delivery-mode").get_by_role("group", name="Emails shown")
    assert choices.evaluate("node => node.classList.contains('segmented-choice')")
    assert (
        choices.get_by_role("button", name="Production").get_attribute("aria-current")
        == "page"
    )
    assert (
        choices.get_by_role("button", name="Testing").get_attribute("aria-current")
        is None
    )
    before = page.evaluate(PLACES)
    choices.get_by_role("button", name="Testing").click()
    eventually(
        page,
        "document.querySelector('#delivery-mode [aria-current=page]').textContent",
        "Testing",
    )
    assert page.evaluate(MARKED) == "kept"
    expect(choices.get_by_role("button", name="Testing")).to_be_focused()
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


# Where the first row's Recipient and its table sit, the page's size, and
# how far the page is scrolled.
RECIPIENT_PLACE = """() => {
    const link = document.querySelector('tbody .recipient-link');
    const place = (node) => { const box = node.getBoundingClientRect();
        return [box.left, box.top, box.width, box.height]; };
    return [...place(link), ...place(link.closest('table')),
        document.documentElement.scrollWidth, document.documentElement.scrollHeight,
        scrollX, scrollY];
}"""


@pytest.mark.parametrize("width", [1280, 390])
def test_hovering_a_recipient_moves_nothing(page, component_origin, width):
    """Hovering a row's Recipient changes only its look (#736): the name,
    its table and the page keep their places and size. The resting link
    once kept the table's compact button padding that its hover look drops,
    so the name moved left under the pointer."""
    open_list(page, component_origin, width=width)
    link = page.locator("tbody .recipient-link").first
    link.scroll_into_view_if_needed()
    page.mouse.move(0, 0)
    before = page.evaluate(RECIPIENT_PLACE)
    link.hover()
    # Past the buttons' colour transition.
    page.wait_for_timeout(300)
    after = page.evaluate(RECIPIENT_PLACE)
    assert all(abs(a - b) <= 1 for a, b in zip(before, after, strict=True)), (
        before,
        after,
    )
    assert link.evaluate("node => getComputedStyle(node).paddingLeft") == "0px"


def recipient_shown(page):
    """The Recipient filter field's value."""
    return page.locator("#delivery-recipient").input_value()


def test_back_after_a_recipient_returns_to_the_list_before(page, component_origin):
    """A row's Recipient adds a history entry (#1562): the field shows the
    recipient, the address never does, and Back redraws the list before it
    in place, its field with it; Forward shows the recipient's list again,
    posting it afresh. The Recipient is kept in history state only."""
    sent = answer_with(page, component_origin, "/deliveries-applied")
    open_list(page, component_origin)
    start = page.url
    assert page.locator("tbody tr").count() == 4
    page.locator("tbody tr").first.get_by_role(
        "button", name="Castellanos, Maximiliana and Bartholomew"
    ).click()
    eventually(page, "document.querySelectorAll('tbody tr').length", 1)
    assert recipient_shown(page) == "12345"
    assert "12345" not in page.url and "recipient" not in page.url
    assert page.evaluate("history.state.inPlaceView") == [
        ["recipient", "12345"],
        ["mode", "production"],
    ]
    page.go_back()
    eventually(page, "document.querySelectorAll('tbody tr').length", 4)
    assert recipient_shown(page) == ""
    assert page.url == start
    assert page.evaluate(MARKED) == "kept"
    page.go_forward()
    eventually(page, "document.querySelectorAll('tbody tr').length", 1)
    assert recipient_shown(page) == "12345"
    assert page.evaluate(MARKED) == "kept"
    recorded(page, sent, 2)
    assert sent[1]["recipient"] == ["12345"] and sent[1]["mode"] == ["production"]
    assert sent[1]["csrfmiddlewaretoken"]


def test_back_redraws_the_filters_applied_before(page, component_origin):
    """A view the filter form drew (a POST) is drawn again on Back with the
    same body, so the Recipient typed there comes back too."""
    sent = answer_with(page, component_origin, "/deliveries-applied")
    open_list(page, component_origin)
    page.locator("#delivery-recipient").fill("777")
    page.get_by_role("button", name="Apply filters").click()
    eventually(page, "document.querySelectorAll('tbody tr').length", 1)
    page.locator("tbody .recipient-link").first.click()
    eventually(page, "history.state && history.state.inPlaceView[0][1]", "12345")
    recorded(page, sent, 2)
    page.go_back()
    recorded(page, sent, 3)
    # The fixture answers every POST with one page, so the body posted
    # again is what shows the view before was asked for.
    assert sent[2]["recipient"] == ["777"] and sent[2]["state"] == ["all"]
    assert page.evaluate(MARKED) == "kept"


def test_a_reload_shows_its_address_view(page, component_origin):
    """A reload draws the page from its address, without the Recipient:
    the list and the empty Recipient field agree, and the entry forgets the
    view a POST drew there."""
    answer_with(page, component_origin, "/deliveries-applied")
    open_list(page, component_origin)
    page.locator("tbody .recipient-link").first.click()
    eventually(page, "document.querySelectorAll('tbody tr').length", 1)
    page.reload()
    assert page.locator("tbody tr").count() == 4
    assert recipient_shown(page) == ""
    assert page.evaluate("history.state.inPlaceView") is None

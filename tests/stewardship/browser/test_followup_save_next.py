"""Save and next moves through a follow-up queue in place (#534).

The fixture server answers Save and next on one request with a real redirect
to the next request's page (carrying the remembered queue view's token), and
on the last request with a redirect to the remembered queue view
(followup_components and information_components). The next request's panel
is swapped in where the reader is: no reload, focus on its heading, the save
and the request now shown announced, and the address that request's own.
"""

import re

import pytest

from . import followup_components as followup
from . import information_components as information
from .test_in_place import MARK, MARKED, VIEWPORT, count_requests, scroll_below
from .waits import has_text, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

CASES = {
    "followup": (
        followup,
        "#followup-item",
        "#followup-save-next",
        "Second <Member> — Example <Ministry>",
        "Return to Ministry follow-up",
    ),
    "information": (
        information,
        "#information-item",
        "#information-save-next",
        "Second Family — Family DUID",
        "Return to Additional information",
    ),
}


def focused_heading(page, region):
    """Whether focus is on the first heading of ``region``."""
    return page.evaluate(
        "selector => document.activeElement === document.querySelector(selector)",
        f"{region} h2",
    )


@pytest.mark.parametrize("case", CASES)
def test_save_and_next_opens_the_next_item_in_place(page, component_origin, case):
    """One POST, carrying the queue token and the next item's id; the next
    item's panel swapped in without a reload; focus on its heading; the
    save and the item announced; the address the next item's own."""
    components, region, button, heading, back = CASES[case]
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + components.ADVANCE_ITEM)
    page.evaluate(MARK)
    scroll_below(page, button)
    posts = count_requests(page, "POST", "/record/")
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.locator(button).dblclick()
    body = sent.value.post_data
    assert "then=next" in body and f"queue={components.TOKEN}" in body
    assert f"next={components.NEXT_ITEM.split('/')[-2]}" in body
    visible(page.locator(region).get_by_role("heading", name=heading, exact=False))
    assert page.evaluate(MARKED) == "kept"
    assert focused_heading(page, region)
    has_text(
        page.get_by_role("status").filter(has_text="Follow-up saved."),
        re.compile("^Follow-up saved. " + re.escape(heading)),
    )
    assert page.url == component_origin + components.NEXT_ITEM + region
    assert posts == [component_origin + components.ADVANCE_UPDATE]
    # The return link keeps the remembered view.
    assert (
        page.get_by_role("link", name=back)
        .get_attribute("href")
        .endswith(f"?queue={components.TOKEN}")
    )


def test_save_and_next_waits_for_a_valid_save(page, component_origin):
    """Save and next is unavailable, with Save's hint, until the save is
    valid, and the page says where it leads."""
    page.goto(component_origin + followup.ADVANCE_ITEM)
    save, advance = page.locator("#followup-save"), page.locator("#followup-save-next")
    has_text(page.locator("#followup-next-help"), re.compile("opens the next open"))
    page.get_by_label("How").select_option("phone")
    assert save.is_disabled() and advance.is_disabled()
    has_text(
        page.locator("#followup-save-hint"),
        "Enter the date and time of the contact attempt to save.",
    )
    page.get_by_label("How").select_option("")
    hidden(page.locator("#contact-date"))
    assert save.is_enabled() and advance.is_enabled()


@pytest.mark.parametrize("case", CASES)
def test_save_and_next_after_the_last_item_returns_to_the_queue(
    page, component_origin, case
):
    """After the last item, Save and next returns to the remembered queue
    view, as the page says beside the button. It submits natively, so the
    queue is loaded (and its read audited) once, not fetched and loaded."""
    components, _, button, _, _ = CASES[case]
    page.goto(component_origin + components.LAST_ITEM)
    has_text(
        page.locator(button.replace("save-next", "next-help")),
        re.compile("returns to the list"),
    )
    loads = count_requests(page, "GET", components.QUEUE_VIEW)
    page.locator(button).click()
    address = component_origin + components.QUEUE_VIEW
    page.wait_for_url(lambda url: url.startswith(address))
    visible(page.locator("#table-filters"))
    assert loads == [address]


def test_an_in_place_filter_puts_the_view_token_in_the_address(page, component_origin):
    """An in-place filter change sets the address to the view's token, never
    its filters, so a reload shows the view now applied."""
    body = page.request.get(component_origin + followup.QUEUE_VIEW).text()
    queue = followup.QUEUE_VIEW.split("?")[0]
    page.route(
        component_origin + queue,
        lambda route: route.fulfill(
            status=200, content_type="text/html; charset=utf-8", body=body
        ),
    )
    page.goto(component_origin + "/followup-queue")
    page.get_by_role("button", name="Apply filters").click()
    page.wait_for_url(component_origin + followup.QUEUE_VIEW)
    assert "search" not in page.url

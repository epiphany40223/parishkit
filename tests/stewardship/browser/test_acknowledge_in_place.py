"""Acknowledgements act in place (#519 PR 4).

Each security event's Acknowledge on Home, and the critical-problems
banner's Acknowledge on any Admin page, are answered by the fixture server
with real Post/Redirect/Get redirects (security_components). Each test sets
a mark outside every region, which only a full page load could lose.
"""

import pytest

from .security_components import BANNER, HOME
from .test_in_place import MARK, MARKED, VIEWPORT, count_requests
from .waits import has_text, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

PANEL = "Security events awaiting acknowledgement"
FOCUSED = "document.activeElement === document.querySelector(selector)"


def announced(page, message):
    """Wait until the live region says ``message``."""
    has_text(page.get_by_role("status").filter(has_text=message), message)


def test_security_events_acknowledge_in_place(page, component_origin):
    """Each Acknowledge posts once, follows the redirect back to Home and
    swaps the security events in place: no reload, the event gone, focus on
    the panel's heading, the acknowledgement announced, and the address set
    to the redirect's page. After the last one the panel is gone and focus
    moves to the page's heading."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + HOME)
    page.evaluate(MARK)
    panel = page.get_by_role("region", name=PANEL)
    posts = count_requests(page, "POST", "/acknowledge")
    panel.get_by_role("button", name="Acknowledge").first.dblclick()
    visible(
        page.locator("#security-events li").first.get_by_text(
            "Staff added to a hosted-domain rule"
        )
    )
    assert panel.get_by_role("listitem").count() == 2
    assert page.evaluate(MARKED) == "kept"
    assert page.evaluate(f"selector => {FOCUSED}", "#security-events-heading")
    announced(page, "Security event acknowledged.")
    assert page.url == component_origin + HOME + "?left=2#security-events"
    assert len(posts) == 1
    for left in (1, 0):
        panel.get_by_role("button", name="Acknowledge").first.click()
        page.wait_for_url(f"**?left={left}#security-events")
    hidden(page.get_by_role("region", name=PANEL))
    assert page.locator("#security-events").count() == 1
    assert page.evaluate(MARKED) == "kept"
    assert page.evaluate(f"selector => {FOCUSED}", "main h1")
    assert len(posts) == 3


def test_critical_banner_acknowledges_in_place(page, component_origin):
    """The banner's Acknowledge posts once and, although the server answers
    with Home (another page), removes the banner in place on the page the
    Administrator is on: no reload, the address unchanged, nothing else on
    the page taken from Home's answer, focus on the page's heading, and the
    acknowledgement announced."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + BANNER)
    page.evaluate(MARK)
    posts = count_requests(page, "POST", "/critical-events/acknowledge")
    banner = page.locator("#critical-events")
    banner.get_by_role("button", name="Acknowledge").dblclick()
    announced(page, "Critical problems acknowledged.")
    hidden(page.get_by_text("Critical problems in the past 24 hours"))
    assert banner.count() == 1 and banner.inner_text() == ""
    assert page.evaluate(MARKED) == "kept"
    assert page.url == component_origin + BANNER
    # Home's answer has no security events; this page's stay.
    assert page.get_by_role("region", name=PANEL).get_by_role("listitem").count() == 3
    assert page.evaluate(f"selector => {FOCUSED}", "main h1")
    assert len(posts) == 1


def test_critical_banner_that_stays_keeps_focus(page, component_origin):
    """When more problems remain than one acknowledgement covers, the banner
    comes back from the answer and focus returns to its Acknowledge."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + BANNER)
    page.evaluate(MARK)
    body = page.request.get(component_origin + "/critical-remaining").text()
    page.route(
        "**/admin/critical-events/acknowledge",
        lambda route: route.fulfill(
            status=200, content_type="text/html; charset=utf-8", body=body
        ),
    )
    page.locator("#critical-events").get_by_role("button", name="Acknowledge").click()
    announced(page, "Critical problems acknowledged.")
    visible(page.locator("#critical-events").get_by_text("Only the oldest"))
    assert page.evaluate(MARKED) == "kept"
    assert page.url == component_origin + BANNER
    assert (
        page.evaluate("document.activeElement.closest('form')?.dataset.inPlace")
        == "critical-acknowledge"
    )


def test_refused_critical_acknowledge_shows_the_error_page(page, component_origin):
    """A refused Acknowledge is sent once and its error page is shown whole,
    with its own explanation. The error page draws the banner too (it is
    chrome), but that banner says nothing about the refusal, so it is not
    swapped in as if the acknowledgement had worked."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + BANNER)
    body = page.request.get(component_origin + "/critical-refused").text()
    page.route(
        "**/admin/critical-events/acknowledge",
        lambda route: route.fulfill(
            status=400, content_type="text/html; charset=utf-8", body=body
        ),
    )
    posts = count_requests(page, "POST", "/critical-events/acknowledge")
    page.locator("#critical-events").get_by_role("button", name="Acknowledge").click()
    visible(page.get_by_role("heading", name="This request could not be completed"))
    visible(page.get_by_text("The submitted list was changed."))
    assert page.get_by_role("region", name=PANEL).count() == 0
    assert len(posts) == 1

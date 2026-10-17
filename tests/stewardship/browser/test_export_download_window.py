"""A ready export's Download goes unavailable in place when its window ends.

#1398 (UX conventions, "Point-in-time downloads"): the export status page
counts to its "Ready to download until" time by the server's clock. When it
passes while the page is open, Download is disabled with no reload, and the
same reserved line says the download expired and links back to the report;
nothing on the page moves, also when the ready Download arrived by the
pending page's own poll. A copy shown after the end shows Download
unavailable at once. A fake clock passes the end; the page's server time is
the fixture's NOW. The fake clock keeps running in real time from when it is
installed, so each "before the end" check leaves a margin far wider than the
steps take (or pauses the clock), and the step past the end goes past it by
more than that margin.
"""

from datetime import timedelta
from uuid import UUID

import pytest
from django.urls import reverse

from .conftest import NOW
from .live_status_components import (
    EXPORT_ENDED,
    EXPORT_EXPIRING,
    EXPORT_PENDING,
    EXPORT_WINDOW_LEFT,
)
from .test_in_place import MARK, MARKED
from .test_setup_mail import place
from .waits import (
    available,
    focused,
    greyed,
    has_attribute,
    hidden,
    painted,
    same_place,
    visible,
)

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Download, its reserved "until" line and the Refresh status link below it.
DOWNLOAD = "form[data-download-until] button, [data-download-ended-gated]"
LINE = "[data-download-window]"
REFRESH = "a[data-in-place='refresh']"
# How long before the window's end a test checks that nothing has changed
# yet: far longer than the real time its steps take while the fake clock
# runs, so how fast the machine is does not matter.
MARGIN = timedelta(seconds=30)


def ms(delta):
    """``delta`` in whole milliseconds, as the fake clock counts."""
    return int(delta.total_seconds() * 1000)


def places(page):
    """Where Download, the "until" line and Refresh status are drawn."""
    return [place(page, selector) for selector in (DOWNLOAD, LINE, REFRESH)]


@pytest.mark.parametrize("width", [1280, 360])
def test_download_goes_unavailable_in_place_when_its_window_ends(
    page, component_origin, width
):
    """Download stays available until the end, then is disabled in place
    with the expired sentence on the same line; nothing moves, the page is
    not reloaded and nothing is posted. At phone width the longer sentence
    may wrap, and the line's height already allows for it."""
    page.set_viewport_size({"width": width, "height": 900})
    page.clock.install(time=NOW)
    posts = []
    page.on(
        "request",
        lambda request: posts.append(request.url) if request.method == "POST" else None,
    )
    page.goto(component_origin + EXPORT_EXPIRING)
    page.evaluate(MARK)
    download = page.get_by_role("button", name="Download export")
    line = page.locator(LINE)
    expired = line.locator("[data-window-ended]")
    available(download)
    visible(line.get_by_text("Ready to download until"))
    hidden(expired)

    # Shortly before the end nothing has changed. The places are taken
    # here, since the session chrome above the page may change as minutes
    # pass. The clock is not paused, because painted() waits for animation
    # frames, which a paused fake clock never draws; instead the margin
    # (MARGIN) is far longer than the real time these steps take.
    page.clock.fast_forward(ms(EXPORT_WINDOW_LEFT - MARGIN))
    page.wait_for_timeout(200)
    available(download)
    hidden(expired)
    painted(page)
    before = places(page)

    page.clock.fast_forward(ms(MARGIN + timedelta(seconds=2)))
    greyed(download)
    visible(expired)
    hidden(line.get_by_text("Ready to download until"))
    assert expired.inner_text().startswith("This download expired at ")
    # The expired time is the same browser-local time the line showed (now
    # invisible, so its text is read without layout).
    assert (
        expired.locator("time").inner_text()
        == line.locator("[data-window-open] time").text_content()
    )
    report = expired.get_by_role("link", name="Participation")
    has_attribute(report, "href", "/participation")
    has_attribute(download, "aria-describedby", "export-expired")
    painted(page)
    assert same_place(places(page), before)
    assert page.evaluate(MARKED) == "kept"
    assert posts == []


def test_a_page_shown_after_its_end_offers_no_download(page, component_origin):
    """A copy drawn at or after its window's end by the server's clock (the
    server answers a fresh load with its 410 page instead, so only a late
    or restored copy) shows Download unavailable at once, with the reason."""
    page.goto(component_origin + EXPORT_ENDED)
    greyed(page.get_by_role("button", name="Download export"))
    visible(page.locator("[data-window-ended]"))
    hidden(page.get_by_text("Ready to download until"))


@pytest.mark.parametrize(
    "off", [timedelta(days=1), -timedelta(days=1)], ids=["ahead", "behind"]
)
def test_the_window_is_counted_by_the_servers_clock(page, component_origin, off):
    """A computer whose clock is a day ahead of or behind the server still
    offers Download until the window's end by the server's clock and
    disables it just after, so the page corrects its clock by the server's
    time (data-server-now): counted by its own clock, a day ahead would
    show Download unavailable at once, and a day behind would keep it."""
    browser_now = NOW + off
    page.clock.install(time=browser_now)
    page.goto(component_origin + EXPORT_EXPIRING)
    download = page.get_by_role("button", name="Download export")
    expired = page.locator("[data-window-ended]")
    # Paused, so the check just before the end does not depend on how long
    # the steps take (nothing here waits for an animation frame).
    page.clock.pause_at(browser_now + EXPORT_WINDOW_LEFT - timedelta(seconds=2))
    page.wait_for_timeout(200)
    available(download)
    hidden(expired)

    page.clock.fast_forward(4000)
    greyed(download)
    visible(expired)


def test_a_download_brought_by_the_poll_goes_unavailable_at_its_end(
    page, component_origin
):
    """A pending export page whose poll brings the ready Download in (the
    usual way: the Admin waits on the page) still counts to its end. The
    poll's own swap starts the window check, so after the end Download is
    disabled in place with the reason, focus moves from Download (where the
    one automatic download left it) to the reason's link, nothing moves and
    the page is not reloaded. The automatic download is answered with 204,
    so the page stays put, as it does when a browser saves a file."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.clock.install(time=NOW)
    page.goto(component_origin + EXPORT_PENDING)
    page.evaluate(MARK)
    visible(page.get_by_text("Preparing your file"))
    # Each poll of the page reads the ready one, and the download is saved.
    page.route(
        lambda url: url == component_origin + EXPORT_PENDING,
        lambda route: route.fulfill(
            response=route.fetch(url=component_origin + EXPORT_EXPIRING)
        ),
    )
    saved = component_origin + reverse(
        "admin:report_export_download", args=[UUID(int=720)]
    )
    page.route(saved, lambda route: route.fulfill(status=204, body=""))
    posts = []
    page.on(
        "request",
        lambda request: posts.append(request.url) if request.method == "POST" else None,
    )
    download = page.get_by_role("button", name="Download export")
    line = page.locator(LINE)
    expired = line.locator("[data-window-ended]")
    # The poll's first check comes within seconds of real time. The
    # automatic download holds Download busy for ten seconds (ui-v1.js's
    # repeat guard; a saved file leaves the page in place), so it is
    # available again by the check below.
    visible(download)
    visible(line.get_by_text("Ready to download until"))
    hidden(expired)
    focused(download)

    # Shortly before the end nothing has changed (MARGIN; the clock is not
    # paused, as in the layout test above, because painted() needs frames).
    page.clock.fast_forward(ms(EXPORT_WINDOW_LEFT - MARGIN))
    page.wait_for_timeout(200)
    available(download)
    hidden(expired)
    painted(page)
    before = places(page)

    page.clock.fast_forward(ms(MARGIN + timedelta(seconds=2)))
    greyed(download)
    visible(expired)
    hidden(line.get_by_text("Ready to download until"))
    has_attribute(download, "aria-describedby", "export-expired")
    focused(expired.get_by_role("link", name="Participation"))
    painted(page)
    assert same_place(places(page), before)
    assert page.evaluate(MARKED) == "kept"
    # Only the one automatic download was posted.
    assert posts == [saved]

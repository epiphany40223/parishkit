"""Setup's way back from a failed or discarded parish data load (#1353).

A load that fails for good returns setup to collecting, so its progress page
links back to ParishSoft connection and to Load parish data (Load again)
instead of saying to cancel setup. A load discarded from another tab (a
changed key or organization, or Load again) must not keep showing Continue:
an open progress page reads its settled status once more whenever it is
shown again, and a restored Load parish data page re-reads its panel, both
in place, without a reload.
"""

import pytest

from .conftest import SETUP_LOAD_STATUS
from .setup_back_path_components import (
    CREDENTIAL_LOADED,
    FAILED_ORGANIZATION,
    PROGRESS_DONE,
    PROGRESS_FAILED,
    PROGRESS_VOIDED,
    SOURCE_LOADED,
    SOURCE_RELOAD,
    progress_status,
)
from .test_in_place import MARK, MARKED
from .test_live_status_in_place import answer_reads
from .test_setup_live_status import answer
from .test_setup_mail import place
from .waits import focused, has_attribute, has_text, hidden, same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

SHOWN = "document.dispatchEvent(new Event('visibilitychange'))"
RESTORED = (
    "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"
)
NOT_RESTORED = "window.dispatchEvent(new PageTransitionEvent('pageshow'))"


@pytest.mark.parametrize("width", [320, 1280])
def test_a_failed_load_links_back_to_the_connection(
    page, component_origin, axe_source, width
):
    """The failed panel names the organization that could not be loaded and
    the likely causes, and links to the two steps that fix it (choosing
    another organization or key, and loading again); Back goes to
    ParishSoft connection; nothing says to cancel."""
    from .test_components import axe_violations

    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + PROGRESS_FAILED)
    panel = page.locator("[data-progress-failed]")
    visible(panel)
    text = panel.inner_text()
    assert (
        f"The parish data of {FAILED_ORGANIZATION} could not be loaded from "
        "ParishSoft." in text
    )
    assert "a wrong ParishSoft API key, or an organization that is not" in text
    connection = panel.get_by_role(
        "link", name="Choose another organization or enter a different key"
    )
    load = panel.get_by_role("link", name="Go to “Load parish data”")
    assert connection.get_attribute("href") == "/admin/setup/credentials/parishsoft"
    assert load.get_attribute("href") == "/admin/setup/source"
    back = page.locator(".setup-actions a.setup-back")
    assert back.get_attribute("href") == "/admin/setup/credentials/parishsoft"
    assert "cancel setup" not in page.locator("main").inner_text().lower()
    assert page.get_by_role("link", name="Continue to the next step").count() == 0
    # A settled page is not polled.
    assert page.locator("[data-live-status][data-live-pending]").count() == 0
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []


def test_a_failed_load_goes_back_to_choose_another_organization(page, component_origin):
    """Following the failed panel's way back opens ParishSoft connection,
    where the key pasted again lists its organizations (#1554)."""
    opened = []

    def connection(route):
        """Answer the ParishSoft connection page the link opens."""
        opened.append(route.request.url)
        route.fulfill(content_type="text/html", body="<h1>ParishSoft connection</h1>")

    page.route(
        lambda url: url.endswith("/admin/setup/credentials/parishsoft"), connection
    )
    page.goto(component_origin + PROGRESS_FAILED)
    page.locator("[data-progress-failed]").get_by_role(
        "link", name="Choose another organization or enter a different key"
    ).click()
    visible(page.get_by_role("heading", name="ParishSoft connection"))
    assert opened == [component_origin + "/admin/setup/credentials/parishsoft"]


# Finish setup, the step after the load (#1578), opens only while the load
# is valid.
FINISH = '.setup-track a[data-tip*="Finish setup"]'


def test_an_open_progress_page_shows_a_discarded_load_in_place(page, component_origin):
    """Shown again, a settled progress page reads its status once; a load
    discarded meanwhile replaces Continue with the no-longer-used status and
    the step bar follows, in place. A focused control in the region holds
    the swap until focus leaves it, and nothing is polled after."""
    from parishkit.stewardship.accounts.setup_progress_views import SUMMARIES

    from .conftest import NOW

    polls = answer(
        page,
        SETUP_LOAD_STATUS,
        [progress_status(NOW, "done"), progress_status(NOW, "voided")],
    )
    # The step bar's re-read of this page, after each settled answer.
    reads = answer_reads(
        page, component_origin, PROGRESS_DONE, PROGRESS_DONE, PROGRESS_VOIDED
    )
    page.goto(component_origin + PROGRESS_DONE)
    page.evaluate(MARK)
    continuing = page.get_by_role("link", name="Continue to the next step")
    visible(continuing)
    finish = page.locator(FINISH)
    assert finish.get_attribute("href")
    summary = page.locator("details", has_text="Technical details").locator("summary")
    summary.focus()
    above = place(page, "h1")
    # A first load's pageshow is not a return to the page: nothing is read.
    page.evaluate(NOT_RESTORED)
    page.wait_for_timeout(300)
    assert polls == [] and reads == []
    # Shown again with nothing changed: one read, and the page stays as it was.
    page.evaluate(SHOWN)
    page.wait_for_timeout(500)
    assert len(polls) == 1 and len(reads) == 1
    visible(continuing)
    focused(summary)
    # Restored from the back/forward cache after a discard elsewhere, while
    # a button inside the region has focus: the swap waits for the Admin.
    tip = page.locator("[data-live-status] .toggletip-button")
    tip.focus()
    page.evaluate(RESTORED)
    page.wait_for_timeout(2500)
    assert len(polls) >= 2
    visible(continuing)
    focused(tip)
    # Focus leaves the region: the next read swaps it in place.
    summary.focus()
    voided = page.locator("[data-progress-voided]")
    visible(voided)
    hidden(continuing)
    assert voided.get_by_role("link", name="Go to “Load parish data”").count() == 1
    has_text(page.locator("[data-live-announcer]"), str(SUMMARIES["voided"]))
    # The step bar makes Finish setup unavailable.
    has_attribute(page.locator(FINISH), "aria-disabled", "true")
    assert len(reads) == 2
    assert page.evaluate(MARKED) == "kept"
    focused(summary)
    assert same_place(place(page, "h1"), above)
    # Settled again: watching stopped, and the quiet re-checks left no
    # status lines behind.
    seen = len(polls)
    page.wait_for_timeout(2500)
    assert len(polls) == seen
    assert page.locator(".live-status-problem").count() == 0
    assert all(poll.method == "GET" for poll in polls)


def checked_place(page):
    """Where "Last checked" is drawn, measured from the page heading.

    The re-check can only change the page below its heading (the region and
    its status line), so that is what must not move. The site header above
    could grow on its own once the clock was fast-forwarded: its 30-second
    status polls (ui-v1.js) fail on the component server and showed
    "Background status unavailable" on a line of its own, which moved the
    whole page down by a line, first seen on Firefox in CI (the sentence now
    replaces the pill's text in place, #1643).
    """
    left, top, width, height = place(page, "[data-progress-checked]")
    return [left, top - place(page, "h1")[1], width, height]


@pytest.mark.parametrize("failure", [503, 409, "network"])
def test_a_failed_re_check_stops_quietly(page, component_origin, failure):
    """A re-check that cannot read the status (setup finished meanwhile, a
    changed base, no network) leaves the settled page as it was: no line
    under the region, nothing moved, and never a retry."""
    from .conftest import NOW

    page.clock.install(time=NOW)
    polls = []

    def respond(route):
        """Record the read and fail it."""
        polls.append(route.request)
        if failure == "network":
            route.abort()
        else:
            route.fulfill(status=failure, body="")

    page.route(lambda url: url.endswith(SETUP_LOAD_STATUS), respond)
    page.goto(component_origin + PROGRESS_DONE)
    page.evaluate(MARK)
    continuing = page.get_by_role("link", name="Continue to the next step")
    visible(continuing)
    checked = checked_place(page)
    page.evaluate(SHOWN)
    page.wait_for_timeout(500)
    assert len(polls) == 1
    page.clock.fast_forward(60_000)
    page.wait_for_timeout(300)
    assert len(polls) == 1
    visible(continuing)
    assert page.locator(".live-status-problem:not([hidden])").count() == 0
    assert same_place(checked_place(page), checked)
    assert page.evaluate(MARKED) == "kept"


def test_a_settled_region_elsewhere_is_not_read_again(page, component_origin):
    """Only a region marked data-live-recheck-shown is re-checked: a settled
    Test email page reads nothing when its tab is shown again."""
    from .conftest import SETUP_MAIL_STATUS
    from .setup_live_components import SETTLED

    address = SETTLED[("mail", "accepted")]
    polls = answer(page, SETUP_MAIL_STATUS, [""])
    reads = answer_reads(page, component_origin, address, address)
    page.goto(component_origin + address)
    page.evaluate(MARK)
    page.evaluate(SHOWN)
    page.evaluate(NOT_RESTORED)
    page.wait_for_timeout(2500)
    assert polls == [] and reads == []
    assert page.evaluate(MARKED) == "kept"


@pytest.mark.parametrize("event", [SHOWN, RESTORED])
def test_a_load_page_shown_again_is_read_again(page, component_origin, event):
    """Load parish data shown again (its tab, or the back/forward cache)
    re-reads its panel and step bar, so "Parish data is loaded" gives way to
    Load again in place. A first load's pageshow reads nothing."""
    reads = answer_reads(page, component_origin, SOURCE_LOADED, SOURCE_RELOAD)
    page.goto(component_origin + SOURCE_LOADED)
    page.evaluate(MARK)
    loaded = page.get_by_text("Parish data is loaded.")
    visible(loaded)
    assert page.locator(FINISH).get_attribute("href")
    page.evaluate(NOT_RESTORED)
    page.wait_for_timeout(300)
    assert reads == []
    page.evaluate(event)
    visible(page.get_by_role("button", name="Load again"))
    hidden(loaded)
    has_attribute(page.locator(FINISH), "aria-disabled", "true")
    assert len(reads) == 1
    assert page.evaluate(MARKED) == "kept"


def test_the_connection_page_says_a_changed_key_discards_the_load(
    page, component_origin, axe_source
):
    """While a still-valid load exists, the saved-key notice says a different
    key or organization discards it; typing a key moves nothing."""
    from .test_components import axe_violations

    page.goto(component_origin + CREDENTIAL_LOADED)
    note = page.locator("[data-source-loaded-note]")
    visible(note)
    assert "discards the loaded data" in note.inner_text()
    save = page.get_by_role("button", name="Save and continue")
    before = place(page, ".setup-actions button")
    page.get_by_label("ParishSoft API key").fill("another-synthetic-key")
    visible(save)
    assert same_place(place(page, ".setup-actions button"), before)
    visible(note)
    assert axe_violations(page, axe_source) == []

"""Check parish data in the browser (#2201).

The quick check replaced the setup load. While the check waits for the
installer's answer, the page re-reads itself quietly every three seconds
(ui-conventions "Pages that update themselves") and swaps the answer into
its one #setup-source-panel region, with nothing moving: the action row's
Continue keeps its place from the unavailable button to the link, and the
line under the row keeps its height. Every state is accessible at a phone's
and a laptop's width.
"""

import pytest

from .conftest import NOW
from .setup_check_components import (
    CHECK_FAILED,
    CHECK_INITIAL,
    CHECK_READY,
    CHECK_STALE,
    CHECK_UNAVAILABLE,
    CHECK_WAITING,
    PAGES,
    answer_start,
    keep_start_manual,
)
from .test_in_place import MARK, MARKED
from .test_live_status_in_place import answer_reads
from .test_setup_mail import place
from .waits import same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# A little more than the wait's 3-second re-read pace.
TICK = 4 * 1000
REGION = "#setup-source-panel"
NEXT = ".setup-actions [data-nav-slot=next]"
BACK = ".setup-actions [data-nav-slot=back]"


def contains(locator, text):
    """Assert that ``locator`` comes to contain ``text``, waiting."""
    from playwright.sync_api import expect

    expect(locator).to_contain_text(text)


@pytest.mark.parametrize("width", [320, 1280])
@pytest.mark.parametrize("path", PAGES)
def test_every_check_state_is_accessible_at_both_widths(
    page, component_origin, axe_source, width, path
):
    """No axe violation and no sideways scroll, before, during and after."""
    from .test_components import axe_violations

    page.set_viewport_size({"width": width, "height": 900})
    keep_start_manual(page)
    page.goto(component_origin + path)
    visible(page.get_by_role("heading", name="Check parish data", exact=True))
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []


def test_the_wait_swaps_in_the_answer_in_place(page, component_origin):
    """The spinner gives way to the organization and its totals in place: no
    reload, the address unchanged, Continue where the unavailable one was,
    and no more re-reads."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.set_viewport_size({"width": 1280, "height": 900})
    page.clock.install(time=NOW)
    page.goto(component_origin + CHECK_WAITING)
    contains(page.locator(REGION), "Checking the parish data in ParishSoft")
    waiting = page.locator(NEXT)
    assert waiting.get_attribute("disabled") is not None
    reason = page.locator("#setup-check-reason")
    contains(reason, "Available once the check finishes.")
    assert waiting.get_attribute("aria-describedby") == "setup-check-reason"
    before = (place(page, NEXT), place(page, BACK))
    page.evaluate(MARK)
    reads = answer_reads(page, component_origin, CHECK_WAITING, CHECK_READY)
    page.clock.fast_forward(TICK)
    contains(
        page.locator(REGION),
        "ParishSoft answered for St. Example Parish (4321): 812 Families and "
        "2,034 Members.",
    )
    continuing = page.locator(f"{NEXT}.setup-continue")
    visible(continuing)
    assert continuing.get_attribute("href") == "/admin/setup/confirm"
    visible(page.get_by_role("button", name="Check again"))
    assert page.evaluate(MARKED) == "kept"
    assert page.url == component_origin + CHECK_WAITING
    # Continue keeps its row and its right edge (the row's end), and Back
    # stays where it was drawn while the check waited.
    left, top, width, _ = place(page, NEXT)
    assert same_place([top, left + width], [before[0][1], before[0][0] + before[0][2]])
    assert same_place(place(page, BACK), before[1])
    page.clock.fast_forward(3 * TICK)
    assert len(reads) == 1
    assert failures == []


@pytest.mark.parametrize(
    "path,text,linked",
    [
        (
            CHECK_FAILED,
            "The saved key no longer reaches St. Example Parish (4321).",
            True,
        ),
        (CHECK_UNAVAILABLE, "ParishSoft could not be reached.", False),
    ],
)
def test_a_failed_check_says_what_to_do(page, component_origin, path, text, linked):
    """The failure carries its code, links ParishSoft connection when a
    different key or organization is the fix, and offers Check again."""
    page.goto(component_origin + path)
    notice = page.locator("[data-source-check-error]")
    contains(notice, text)
    code = notice.get_attribute("data-source-check-error")
    assert code.startswith("parishsoft_")
    # The code is machine-readable only: a person reads the plain words.
    assert code not in notice.inner_text()
    link = notice.get_by_role(
        "link", name="Choose another organization or enter a different key"
    )
    assert link.count() == (1 if linked else 0)
    if linked:
        assert link.get_attribute("href") == "/admin/setup/credentials/parishsoft"
    visible(page.get_by_role("button", name="Check again"))
    assert page.locator(f"{NEXT}.setup-continue").count() == 0


def test_before_a_check_the_page_offers_it_with_plain_words(page, component_origin):
    """The About text says the check is quick and the load comes at Finish
    setup; nothing says it loads everything now or fixes the time zone."""
    keep_start_manual(page)
    page.goto(component_origin + CHECK_INITIAL)
    main = page.locator("main")
    contains(main, "Check that ParishSoft answers for St. Example Parish (4321)")
    visible(page.get_by_role("button", name="Check parish data"))
    # The in-place form names its region, so an ordinary load lands on it.
    assert page.locator("form[data-in-place]").get_attribute("action").endswith(REGION)
    page.locator("details summary", has_text="About this page").click()
    contains(main, "It usually takes a few seconds.")
    contains(main, "The parish data itself is loaded when you finish setup.")
    text = main.inner_text()
    assert "Campaigns offer" not in text
    assert "time zone" not in text
    assert "Load parish data" not in text


@pytest.mark.parametrize("path", [CHECK_INITIAL, CHECK_STALE])
@pytest.mark.parametrize("width", [320, 1280])
def test_the_check_starts_on_arrival_without_moving_anything(
    page, component_origin, path, width
):
    """Opening the step with no check of the current connection (none yet,
    or an earlier connection's) starts the check by itself (#2271): one
    start, the spinner in place of the status, no reload, and the action
    row where the waiting page draws it."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.set_viewport_size({"width": width, "height": 900})
    # Where the waiting page draws Back and Continue.
    page.goto(component_origin + CHECK_WAITING)
    visible(page.locator("[data-source-check-waiting]"))
    expected = (place(page, NEXT), place(page, BACK))
    posts, _, _ = answer_start(page, component_origin, CHECK_WAITING)
    # The start is so quick that a mark set by script could land after the
    # swap: count the page loads instead, which must stay one.
    loads = []
    page.on(
        "request", lambda r: loads.append(r.url) if r.resource_type == "document" else 0
    )
    page.goto(component_origin + path)
    visible(page.locator("[data-source-check-waiting]"))
    contains(page.locator(REGION), "Checking the parish data in ParishSoft")
    assert len(posts) == 1 and "action=check" in posts[0]
    assert loads == [component_origin + path]
    assert page.url == component_origin + path
    assert same_place(place(page, NEXT), expected[0])
    assert same_place(place(page, BACK), expected[1])
    # Nothing starts a second check later.
    page.wait_for_timeout(1000)
    assert len(posts) == 1
    assert failures == []


@pytest.mark.parametrize("path", [CHECK_READY, CHECK_FAILED, CHECK_UNAVAILABLE])
def test_a_check_already_made_is_not_run_again_on_a_revisit(
    page, component_origin, path
):
    """A page with a check of the current connection, ready or failed, starts
    nothing, however often it is opened; a failure offers Check again
    (#2271)."""
    posts, _, _ = answer_start(page, component_origin, CHECK_WAITING)
    for _ in range(2):
        page.goto(component_origin + path)
        visible(page.get_by_role("button", name="Check again"))
        page.wait_for_timeout(500)
    assert posts == []
    assert page.locator("[data-source-check-waiting]").count() == 0


def test_a_failed_start_shows_check_again_and_is_not_retried(page, component_origin):
    """When the automatic check comes back failed, the page shows the failure
    with Check again, and starts nothing more by itself (#2271)."""
    posts, _, _ = answer_start(page, component_origin, CHECK_FAILED)
    page.goto(component_origin + CHECK_INITIAL)
    visible(page.locator("[data-source-check-error]"))
    visible(page.get_by_role("button", name="Check again"))
    page.wait_for_timeout(1000)
    assert len(posts) == 1


def test_a_double_start_queues_one_check(page, component_origin):
    """Pressing the button, submitting again and a page shown again while the
    first start is unanswered send one start (#2271); the server returns a
    check already waiting in any case."""
    posts, routes, body = answer_start(page, component_origin, CHECK_WAITING, hold=True)
    page.goto(component_origin + CHECK_INITIAL)
    page.wait_for_timeout(300)
    assert len(posts) == 1
    page.evaluate(
        "() => { const form = document.querySelector('form[data-auto-start]');"
        " form.requestSubmit(form.querySelector('button[name=action]'));"
        " form.requestSubmit(); }"
    )
    page.evaluate(
        "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"
    )
    page.wait_for_timeout(500)
    assert len(posts) == 1
    routes[0].fulfill(status=200, content_type="text/html; charset=utf-8", body=body)
    visible(page.locator("[data-source-check-waiting]"))
    assert len(posts) == 1

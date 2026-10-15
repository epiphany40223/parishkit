"""System health in a real browser (ADM-13, #530): accessible, live and quiet."""

import re

import pytest

from .system_health_components import (
    ACCEPT_CHANGED,
    ACCEPT_DONE,
    ACCEPT_LATE,
    ACCEPT_PAGE,
    ACCEPT_PREVIEW,
    ACCEPT_STALE,
    BACKUP_BUSY,
    BACKUP_PAGE,
    BACKUP_PREVIEW,
    BACKUP_REQUESTED,
    BACKUP_STEP_UP,
    DATED,
    DEBUG_PAGE,
    DEBUG_PREVIEW,
    DEBUG_SET,
    DEBUG_STEP_UP,
    HEALTHY,
    PAGE,
    STEADY,
)
from .waits import eventually, visible


def expect(locator):
    """Load the optional browser assertion library only after explicit opt-in."""
    from playwright.sync_api import expect as browser_expect

    return browser_expect(locator)


pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
REGION = '[data-live-status="system-health"]'
ANNOUNCER = '[data-live-announcer="system-health"]'


def test_the_resume_time_reads_as_a_local_clock_time(page, component_origin):
    """At the limit the count line ends with the browser-local resume time (#530)."""
    page.goto(component_origin + PAGE)
    resume = page.locator(f"{REGION} [data-daily-resume] time")
    stamp = resume.get_attribute("datetime")
    expected = page.evaluate(
        "(stamp) => ParishDates.dayTime(new Date(stamp), new Date())", stamp
    )
    expect(resume).to_have_text(expected)
    assert "UTC" not in page.locator(f"{REGION} [data-daily-count]").inner_text()
    page.goto(component_origin + HEALTHY)
    assert page.locator(f"{REGION} [data-daily-resume]").count() == 0


def test_a_resume_time_on_the_next_day_says_tomorrow(page, component_origin):
    """The clock time alone is never ambiguous: tomorrow is said, later days dated."""
    page.goto(component_origin + PAGE)
    worded = page.evaluate("""() => {
        const now = new Date(2054, 9, 12, 14, 0);
        const at = (day, hour, minute) => new Date(2054, 9, day, hour, minute);
        return [
            ParishDates.dayTime(at(12, 23, 59), now),
            ParishDates.dayTime(at(13, 3, 10), now),
            ParishDates.dayTime(at(14, 3, 10), now),
            ParishDates.instant(at(14, 3, 10)),
        ];
    }""")
    assert worded[:2] == ["11:59 PM", "3:10 AM tomorrow"]
    assert worded[2] == worded[3]


@pytest.mark.parametrize("width", [320, 1280])
def test_health_pages_are_accessible_at_phone_and_desktop_widths(
    page, component_origin, axe_source, width
):
    """Troubled and healthy pages pass axe and fit a phone without scrolling."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in (PAGE, HEALTHY):
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert (
            page.evaluate("""async () => (await axe.run(document, {
            runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
        })).violations.map(({id,impact}) => ({id,impact}))""")
            == []
        )
        headings = page.locator(f"{REGION} h2").all_text_contents()
        # Problems first, then the six panels in the specification's order.
        assert headings[1:7] == [
            "Why sends are waiting",
            "Mail sender",
            "ParishSoft refresh",
            "Backups",
            "Debug logging",
            "Version",
        ]
        if path == HEALTHY:
            assert headings[0] == "Everything is working"
        else:
            assert headings[0] == "3 problems need attention"
        # The help is there, closed until asked for.
        assert page.locator("details.about-page").get_attribute("open") is None


def test_the_page_follows_problems_ending_and_announces_only_the_change(
    page, component_origin
):
    """One 10-second poll swaps in the healthy state; the announcer says so once."""
    page.goto(component_origin + PAGE)
    region = page.locator(REGION)
    assert region.get_attribute("data-live-interval") == "10000"
    assert region.get_attribute("data-live-pending") is not None
    visible(page.get_by_text("Mail sender 1 has stopped all Family email"))
    visible(page.get_by_role("rowheader", name="Families with an email address"))
    # The 24-hour count, with the limit it is compared with (ADM-13 PR 2c).
    visible(page.get_by_text("Emails went to 1,612 recipients in the last 24 hours."))
    assert page.locator(ANNOUNCER).text_content() == ""
    page.locator("details.technical-details summary").click()
    # A mouse click focuses a link in the region (Chromium and Firefox); it
    # must not freeze the region, and the swap puts focus back on the same
    # link in the new markup. The click itself is kept from navigating.
    page.evaluate(
        "document.addEventListener('click', (event) => event.preventDefault())"
    )
    link = page.locator(REGION).get_by_role("link", name="Refresh from ParishSoft")
    link.click()
    # WebKit does not focus a link on click, so focus it as the keyboard
    # would; in every engine the focused link must not hold the swap.
    link.focus()
    expect(page.get_by_role("heading", name="Everything is working")).to_be_visible(
        timeout=30000
    )
    assert page.evaluate(
        "document.activeElement.textContent === 'Refresh from ParishSoft'"
        " && document.querySelector('[data-live-status]')"
        ".contains(document.activeElement)"
    )
    assert page.locator(ANNOUNCER).text_content() == "Everything is working."
    visible(page.get_by_text("Nothing is holding Family email back."))
    # The count stays shown when nothing waits, and follows the poll.
    visible(page.get_by_text("Emails went to 240 recipients in the last 24 hours."))
    # The page never reloaded: the open Technical details outside the region
    # stayed open, and the page still polls.
    assert page.locator("details.technical-details").get_attribute("open") is not None
    assert region.get_attribute("data-live-pending") is not None


def test_an_unchanged_poll_keeps_the_readers_focus_and_place(page, component_origin):
    """Polls that bring the same state leave the region's elements alone.

    A keyboard user resting on a link inside the region keeps focus through
    two 10-second polls, and only "Last checked" (outside the region) moves.
    """
    page.goto(component_origin + STEADY)
    link = page.locator(REGION).get_by_role("link", name="Refresh from ParishSoft")
    link.focus()
    page.evaluate(
        "document.querySelector('[data-live-status] h2').dataset.original = 'yes'"
    )
    checked = page.locator("time[data-live-checked]")
    first = checked.get_attribute("datetime")
    # Two polls, each with the same state.
    expect(checked).not_to_have_attribute("datetime", first, timeout=30000)
    second = checked.get_attribute("datetime")
    expect(checked).not_to_have_attribute("datetime", second, timeout=30000)
    assert page.evaluate(
        "document.activeElement.textContent === 'Refresh from ParishSoft'"
    )
    # The same element: the region was not rebuilt.
    assert page.evaluate(
        "document.querySelector('[data-live-status] h2').dataset.original === 'yes'"
    )


def test_a_change_only_in_a_times_datetime_updates_the_region(page, component_origin):
    """Times are compared by their datetime, not by how the browser words them."""
    page.goto(component_origin + DATED)
    since = page.locator(f"{REGION} time[data-live-since]")
    first = since.get_attribute("datetime")
    expect(since).not_to_have_attribute("datetime", first, timeout=30000)
    visible(page.get_by_text("1 hour ago"))


def test_a_swap_leaves_focus_outside_and_keeps_a_tables_sideways_scroll(
    page, component_origin
):
    """Focus outside the region stays put; a wide table keeps its offset."""
    page.set_viewport_size({"width": 320, "height": 900})
    page.goto(component_origin + PAGE)
    # Make the Services table wider than the phone, as a long one would be.
    # Through the CSS object model: the page's CSP refuses inline styles.
    page.evaluate(
        "document.styleSheets[0].insertRule("
        "'[data-live-status] .table-scroll table { min-width: 900px }')"
    )
    table = page.locator(f"{REGION} .table-scroll").last
    assert table.evaluate("(node) => node.scrollWidth > node.clientWidth")
    table.evaluate("(node) => { node.scrollLeft = 40; }")
    outside = page.get_by_role("link", name="Refresh System health")
    outside.focus()
    expect(page.get_by_role("heading", name="Everything is working")).to_be_visible(
        timeout=30000
    )
    assert page.evaluate(
        "document.activeElement.textContent === 'Refresh System health'"
    )
    swapped = page.locator(f"{REGION} .table-scroll").last
    assert swapped.evaluate("(node) => node.scrollLeft") == 40


def test_last_checked_does_not_advance_while_a_swap_is_held(page, component_origin):
    """A selection holds the swap; the page must not claim a fresher check."""
    polls = []
    page.on(
        "requestfinished",
        lambda request: polls.append(1) if request.url.endswith("/status") else None,
    )
    page.goto(component_origin + STEADY)
    checked = page.locator("time[data-live-checked]")
    first = checked.get_attribute("datetime")
    page.evaluate(
        """() => {
        const range = document.createRange();
        range.selectNodeContents(document.querySelector('[data-live-status] h2'));
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
    }"""
    )
    # Two polls while the selection holds the swap.
    for _ in range(100):
        if len(polls) >= 2:
            break
        page.wait_for_timeout(250)
    assert len(polls) >= 2
    assert checked.get_attribute("datetime") == first
    # Once the selection ends, the next poll is adopted and "Last checked" moves.
    page.evaluate("window.getSelection().removeAllRanges()")
    expect(checked).not_to_have_attribute("datetime", first, timeout=30000)


def _routed(page, component_origin, answers, shown=None):
    """Answer the page's own POSTs as the view would, by the posted action.

    ``answers`` maps an action to ``(status, component path)``. GETs of the
    page go to the component server unchanged, or, with ``shown``, are
    answered with that component, so another state of the page is served at
    the page's real address.
    """
    from urllib.request import urlopen

    def body(path):
        """The component at ``path``, as text."""
        with urlopen(component_origin + path) as response:
            return response.read().decode()

    bodies = {
        action: (status, body(path)) for action, (status, path) in answers.items()
    }
    first = None if shown is None else body(shown)

    def respond(route):
        """Fulfil a POST from ``answers``; let anything else through."""
        if route.request.method != "POST":
            if first is None:
                route.continue_()
            else:
                route.fulfill(status=200, content_type="text/html", body=first)
            return
        data = route.request.post_data or ""
        # The action's whole value, so "preview" never matches
        # "debug_off_preview".
        action = next(
            name
            for name in bodies
            if re.search(rf"(^|&)action={re.escape(name)}(&|$)", data)
        )
        status, body = bodies[action]
        route.fulfill(status=status, content_type="text/html", body=body)

    page.route(f"{component_origin}{BACKUP_PAGE}", respond)


def _not_reloaded(page):
    """Mark the document, so a reload or a rewritten document loses the mark."""
    page.evaluate("document.body.dataset.notReloaded = 'yes'")
    return lambda: page.evaluate("document.body.dataset.notReloaded === 'yes'")


def test_take_a_backup_now_previews_and_confirms_in_place(
    page, component_origin, axe_source
):
    """Preview, then confirm, each swapped into the region; focus follows.

    The page and its POST answers share the page's own address, as in the
    app, so ui-v1.js swaps #backup-now in place.
    """
    _routed(
        page,
        component_origin,
        {"preview": (200, BACKUP_PREVIEW), "confirm": (200, BACKUP_REQUESTED)},
    )
    page.goto(component_origin + BACKUP_PAGE)
    kept = _not_reloaded(page)
    page.locator("#backup-now").get_by_role(
        "button", name="Take a backup now", exact=True
    ).click()
    confirm = page.get_by_role("button", name="Confirm: take a backup now")
    expect(confirm).to_be_visible()
    expect(confirm).to_be_focused()
    visible(page.get_by_text("the backup will wait until the send finishes"))
    page.evaluate(axe_source)
    assert (
        page.evaluate("""async () => (await axe.run(document, {
        runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
    })).violations.map(({id,impact}) => ({id,impact}))""")
        == []
    )
    confirm.click()
    expect(page.get_by_text("Backup requested at")).to_be_visible()
    assert kept()
    assert page.evaluate(
        "document.getElementById('backup-now').contains(document.activeElement)"
    )


def test_refusals_answer_inside_the_region(page, component_origin):
    """A stale sign-in offers the step-up, a race says so; never raw JSON."""
    _routed(page, component_origin, {"preview": (403, BACKUP_STEP_UP)})
    page.goto(component_origin + BACKUP_PAGE)
    kept = _not_reloaded(page)
    page.locator("#backup-now").get_by_role(
        "button", name="Take a backup now", exact=True
    ).click()
    region = page.locator("#backup-now")
    expect(region.get_by_role("button", name="Confirm with Google")).to_be_visible()
    assert kept()
    assert '"errors"' not in page.content()
    page.unroute(f"{component_origin}{BACKUP_PAGE}")
    _routed(page, component_origin, {"preview": (409, BACKUP_BUSY)})
    page.goto(component_origin + BACKUP_PAGE)
    kept = _not_reloaded(page)
    page.locator("#backup-now").get_by_role(
        "button", name="Take a backup now", exact=True
    ).click()
    expect(region.get_by_text("A backup is already requested")).to_be_visible()
    assert kept()


def test_the_button_follows_a_request_made_elsewhere(page, component_origin):
    """The poll greys the button when another request starts waiting."""
    page.goto(component_origin + BACKUP_PAGE)
    region = page.locator("#backup-now")
    expect(
        region.get_by_role("button", name="Take a backup now", exact=True)
    ).to_be_enabled()
    expect(
        region.get_by_role("button", name="Take a backup now", exact=True)
    ).to_be_disabled(timeout=30000)
    visible(region.get_by_text("and is waiting."))


def test_an_unchanged_poll_leaves_the_button_in_place(page, component_origin):
    """The first poll bringing the same button keeps the page's own element.

    Replacing an unchanged button could lose a click landing just then.
    """
    page.goto(component_origin + STEADY)
    page.evaluate(
        "document.querySelector('[data-live-mirror-target] form')"
        ".dataset.original = 'yes'"
    )
    checked = page.locator("time[data-live-checked]")
    expect(checked).not_to_have_attribute(
        "datetime", checked.get_attribute("datetime"), timeout=30000
    )
    assert page.evaluate(
        "document.querySelector('[data-live-mirror-target] form')"
        ".dataset.original === 'yes'"
    )


def test_the_button_catches_up_once_the_reader_leaves_it(page, component_origin):
    """A poll skipped while the button had focus is applied once focus moves.

    The polled region then stays the same, so the copy must still be put in
    on a later poll, not only when the region changes.
    """
    page.goto(component_origin + BACKUP_PAGE)
    region = page.locator("#backup-now")
    button = region.get_by_role("button", name="Take a backup now", exact=True)
    button.focus()
    # The first poll brings the waiting request; the focused button is left.
    eventually(
        page,
        """() => document.querySelector(
            '[data-live-status] template[data-live-mirror]'
        ).innerHTML.includes('disabled')""",
        timeout=30000,
    )
    expect(button).to_be_enabled()
    expect(button).to_be_focused()
    page.evaluate("document.activeElement.blur()")
    expect(button).to_be_disabled(timeout=30000)
    visible(region.get_by_text("and is waiting."))


def test_turn_off_debug_logging_previews_and_confirms_in_place(
    page, component_origin, axe_source
):
    """Preview, then confirm, each swapped into #debug-off; focus follows;
    nothing above the region moves (ADM-13 PR 6b)."""
    _routed(
        page,
        component_origin,
        {
            "debug_off_preview": (200, DEBUG_PREVIEW),
            "debug_off_confirm": (200, DEBUG_SET),
        },
        shown=DEBUG_PAGE,
    )
    page.goto(component_origin + BACKUP_PAGE)
    kept = _not_reloaded(page)
    region = page.locator("#debug-off")
    heading = region.get_by_role("heading", name="Turn off debug logging", exact=True)

    def place():
        """The heading's place in the document, wherever the page is scrolled."""
        return heading.evaluate("e => e.getBoundingClientRect().top + window.scrollY")

    top = place()
    region.get_by_role("button", name="Turn off debug logging", exact=True).click()
    confirm = page.get_by_role("button", name="Confirm: turn off debug logging")
    expect(confirm).to_be_visible()
    expect(confirm).to_be_focused()
    visible(region.get_by_text("Debug logging is on in: Web portal."))
    # Firefox may report the same place a few millionths of a pixel apart.
    assert abs(place() - top) < 0.5
    # The help is one click away, in a toggletip.
    region.get_by_role("button", name="More information").click()
    visible(region.get_by_text("using pk-stewardship debug-off-clear"))
    page.evaluate(axe_source)
    assert (
        page.evaluate("""async () => (await axe.run(document, {
        runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
    })).violations.map(({id,impact}) => ({id,impact}))""")
        == []
    )
    confirm.click()
    done = region.locator("[data-debug-off-set] time")
    expect(done).to_be_visible()
    # Shown in the browser's own time zone, never as the ISO instant.
    stamp = done.get_attribute("datetime")
    expect(done).not_to_have_text(stamp)
    assert "UTC" not in region.inner_text()
    assert kept()
    # Firefox may report the same place a few millionths of a pixel apart.
    assert abs(place() - top) < 0.5
    assert page.evaluate(
        "document.getElementById('debug-off').contains(document.activeElement)"
    )


def test_a_stale_sign_in_offers_the_step_up_in_the_debug_region(page, component_origin):
    """The "For your security" notice and Confirm with Google, in place."""
    _routed(
        page,
        component_origin,
        {"debug_off_preview": (403, DEBUG_STEP_UP)},
        shown=DEBUG_PAGE,
    )
    page.goto(component_origin + BACKUP_PAGE)
    kept = _not_reloaded(page)
    region = page.locator("#debug-off")
    region.get_by_role("button", name="Turn off debug logging", exact=True).click()
    expect(region.get_by_role("button", name="Confirm with Google")).to_be_visible()
    visible(region.get_by_text("For your security"))
    assert kept()
    assert '"errors"' not in page.content()


def test_the_debug_button_follows_a_switch_set_elsewhere(page, component_origin):
    """The poll greys the button, naming who set the switch and when."""
    page.goto(component_origin + DEBUG_PAGE)
    region = page.locator("#debug-off")
    button = region.get_by_role("button", name="Turn off debug logging", exact=True)
    expect(button).to_be_enabled()
    expect(button).to_be_disabled(timeout=30000)
    reason = region.locator("#debug-off-reason")
    expect(reason).to_contain_text("Debug logging was turned off by other@example.org")
    stamp = reason.locator("time").get_attribute("datetime")
    expect(reason.locator("time")).not_to_have_text(stamp)
    # The panel above says the same, and that the server's setting is on.
    panel = page.locator('[data-debug-switch="set"]')
    expect(panel).to_contain_text("still says “on”")


@pytest.mark.parametrize("width", [320, 1280])
def test_the_debug_region_is_accessible_at_phone_and_desktop_widths(
    page, component_origin, axe_source, width
):
    """Its button and its preview pass axe and fit a phone without scrolling."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in (DEBUG_PAGE, DEBUG_PREVIEW):
        page.goto(component_origin + path)
        assert page.locator("#debug-off").count() == 1
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert (
            page.evaluate("""async () => (await axe.run(document, {
            runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
        })).violations.map(({id,impact}) => ({id,impact}))""")
            == []
        )


def test_accept_this_change_once_previews_and_confirms_in_place(
    page, component_origin, axe_source
):
    """Preview, the tick box, then confirm, each in #accept-change right
    under the refusal; no heading above the region or of it moves, and the
    page is never reloaded (ADM-13 PR 5b). The preview lists the example
    Families under the failing count (PR 5c)."""
    _routed(
        page,
        component_origin,
        {"accept_preview": (200, ACCEPT_PREVIEW), "accept_confirm": (200, ACCEPT_DONE)},
        shown=ACCEPT_PAGE,
    )
    page.goto(component_origin + BACKUP_PAGE)
    kept = _not_reloaded(page)
    region = page.locator("#accept-change")
    heading = region.get_by_role("heading", name="Accept this change once", exact=True)
    _beside_the_refusal(page)
    places = _places(page, heading)
    region.get_by_role("button", name="Accept this change once", exact=True).click()
    confirm = region.get_by_role("button", name="Confirm: accept this change once")
    expect(confirm).to_be_visible()
    visible(region.get_by_text("Families with an email address"))
    visible(region.get_by_text("44%"))
    # The example Families come with the preview, under the failing count
    # (PR 5c), so nothing appears or moves after it is drawn.
    examples = region.locator('[data-accept-examples="email_eligible_families"]')
    expect(
        examples.get_by_role("row", name="Example, Ann and Bob 1201")
    ).to_be_visible()
    expect(examples.get_by_role("row", name="Sample, Carol 1202")).to_be_visible()
    assert places() == []
    # Confirm waits for the tick box, with its reason after it.
    expect(confirm).to_be_disabled()
    visible(region.get_by_text("Tick the box to confirm that you checked this"))
    region.get_by_label("I checked this in ParishSoft, and the change is real.").check()
    expect(confirm).to_be_enabled()
    # The rules are one click away, in a toggletip, and so is what the
    # examples are and when they are deleted.
    region.locator('[aria-controls="accept-change-rules-tip"]').click()
    visible(region.get_by_text("the limit itself is not raised"))
    page.keyboard.press("Escape")
    expect(region.locator("#accept-change-rules-tip")).to_be_hidden()
    region.locator('[aria-controls="accept-change-examples-tip"]').click()
    visible(region.get_by_text("Only Administrators see them"))
    page.keyboard.press("Escape")
    expect(region.locator("#accept-change-examples-tip")).to_be_hidden()
    page.evaluate(axe_source)
    assert (
        page.evaluate("""async () => (await axe.run(document, {
        runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
    })).violations.map(({id,impact}) => ({id,impact}))""")
        == []
    )
    confirm.click()
    expect(region.get_by_text("A full refresh was requested")).to_be_visible()
    reason = region.locator("#accept-change-reason")
    expect(reason).to_contain_text("Accepted by other@example.org at")
    # Both times in the browser's own time zone, never as ISO instants.
    for stamp in reason.locator("time").all():
        expect(stamp).not_to_have_text(stamp.get_attribute("datetime"))
    assert "UTC" not in region.inner_text()
    assert kept()
    assert places() == []
    _beside_the_refusal(page)


def _beside_the_refusal(page):
    """#accept-change is in the ParishSoft refresh panel, directly after the
    refused change's notice (in the element the poll keeps), and starts
    within a line of it."""
    assert page.evaluate("""() => {
        const notice = document.querySelector('[data-refused-change]');
        const kept = notice.nextElementSibling;
        return kept.matches('[data-live-keep="accept-change"]')
            && kept.firstElementChild === document.getElementById('accept-change')
            && notice.closest('section') === kept.closest('section')
            && kept.closest('section').getAttribute('aria-labelledby')
                === 'health-source-heading';
    }""")
    gap = page.evaluate("""() =>
        document.getElementById('accept-change').getBoundingClientRect().top
        - document.querySelector('[data-refused-change]').getBoundingClientRect().bottom
    """)
    assert 0 <= gap < 40, gap


def _places(page, heading):
    """Record where ``heading`` and every shown heading above it are in the
    document; the returned check lists those that moved since (Firefox may
    report the same place a few millionths of a pixel apart)."""
    script = """(own) => [...document.querySelectorAll('h1, h2, h3')]
        .filter((h) => h.getClientRects().length && (h === own
            || h.compareDocumentPosition(own) & Node.DOCUMENT_POSITION_FOLLOWING))
        .map((h) => [h.textContent.trim(),
            h.getBoundingClientRect().top + window.scrollY])"""
    before = heading.evaluate(f"own => ({script})(own)")

    def moved():
        """The headings whose place changed by half a pixel or more."""
        now = dict(heading.evaluate(f"own => ({script})(own)"))
        return [name for name, top in before if abs(now.get(name, -1e9) - top) >= 0.5]

    return moved


def test_an_open_preview_survives_a_poll(page, component_origin):
    """The poll swaps the status around the open preview, even while its
    tick box has focus: the preview, its tick, the box's focus and the
    headings stay."""
    _routed(
        page,
        component_origin,
        {"accept_preview": (200, ACCEPT_PREVIEW)},
        shown=ACCEPT_PAGE,
    )
    page.goto(component_origin + BACKUP_PAGE)
    region = page.locator("#accept-change")
    heading = region.get_by_role("heading", name="Accept this change once", exact=True)
    region.get_by_role("button", name="Accept this change once", exact=True).click()
    confirm = region.get_by_role("button", name="Confirm: accept this change once")
    expect(confirm).to_be_visible()
    box = region.get_by_label("I checked this in ParishSoft, and the change is real.")
    box.check()
    # Focused, as Chromium and Firefox leave it after a click (WebKit does
    # not, so the test puts it there): it must not hold the poll.
    box.focus()
    places = _places(page, heading)
    page.evaluate("""() => {
        document.getElementById('accept-change').dataset.marked = 'yes';
    }""")
    # The next poll's status says another Administrator accepted it.
    eventually(
        page,
        """() => {
            const mirror = document.querySelector(
                'template[data-live-mirror="accept-button"]');
            return !!mirror && mirror.innerHTML.includes('Accepted by');
        }""",
    )
    # The same region element, its preview still open, ticked and focused.
    assert region.get_attribute("data-marked") == "yes"
    expect(confirm).to_be_visible()
    expect(box).to_be_checked()
    expect(box).to_be_focused()
    expect(confirm).to_be_enabled()
    # The same refusal is shown, so the preview is not marked changed.
    assert region.locator("[data-live-stale]").count() == 0
    assert places() == []
    _beside_the_refusal(page)


def test_an_open_preview_of_an_older_refusal_is_marked_changed(page, component_origin):
    """A poll showing another refusal marks the open preview "This changed
    since you looked" on its reserved line and holds Confirm, ticked or
    not; nothing moves."""
    _routed(
        page,
        component_origin,
        {"accept_preview": (200, ACCEPT_PREVIEW)},
        shown=ACCEPT_STALE,
    )
    page.goto(component_origin + BACKUP_PAGE)
    region = page.locator("#accept-change")
    region.get_by_role("button", name="Accept this change once", exact=True).click()
    confirm = region.get_by_role("button", name="Confirm: accept this change once")
    expect(confirm).to_be_visible()
    region.get_by_label("I checked this in ParishSoft, and the change is real.").check()
    expect(confirm).to_be_enabled()
    note = region.locator("[data-live-stale-note]")
    expect(note).to_have_text("")
    where = confirm.evaluate("e => e.getBoundingClientRect().top + window.scrollY")
    heading = region.get_by_role("heading", name="Accept this change once", exact=True)
    places = _places(page, heading)
    expect(note).to_have_text(
        "This changed since you looked; review it again.", timeout=30000
    )
    expect(confirm).to_be_disabled()
    # Unticking and ticking again (the tick box's own gate) does not free it.
    box = region.get_by_label("I checked this in ParishSoft, and the change is real.")
    box.uncheck()
    box.check()
    expect(confirm).to_be_disabled()
    after = confirm.evaluate("e => e.getBoundingClientRect().top + window.scrollY")
    assert abs(after - where) < 0.5
    assert places() == []


def test_a_refusal_shown_by_a_poll_brings_its_accept_region(page, component_origin):
    """A page drawn before the refusal gets the region from the poll that
    shows the refusal, right under it; no reload needed."""
    page.goto(component_origin + ACCEPT_LATE)
    assert page.locator("#accept-change").count() == 0
    button = page.locator("#accept-change").get_by_role(
        "button", name="Accept this change once", exact=True
    )
    expect(button).to_be_enabled(timeout=30000)
    _beside_the_refusal(page)


def test_a_changed_refusal_is_said_in_the_region(page, component_origin):
    """ "This changed since you looked", never the database's words."""
    _routed(
        page,
        component_origin,
        {"accept_preview": (409, ACCEPT_CHANGED)},
        shown=ACCEPT_PAGE,
    )
    page.goto(component_origin + BACKUP_PAGE)
    kept = _not_reloaded(page)
    region = page.locator("#accept-change")
    region.get_by_role("button", name="Accept this change once", exact=True).click()
    visible(region.get_by_text("This changed since you looked; review it again."))
    assert kept()
    assert '"errors"' not in page.content()


def test_the_accept_button_follows_an_acceptance_given_elsewhere(
    page, component_origin
):
    """The poll greys the button, naming who accepted, when and until when."""
    page.goto(component_origin + ACCEPT_PAGE)
    region = page.locator("#accept-change")
    button = region.get_by_role("button", name="Accept this change once", exact=True)
    expect(button).to_be_enabled()
    expect(button).to_be_disabled(timeout=30000)
    reason = region.locator("#accept-change-reason")
    expect(reason).to_contain_text("Accepted by other@example.org at")
    expect(reason).to_contain_text("The next full refresh will use it")


@pytest.mark.parametrize("width", [320, 1280])
def test_the_accept_region_is_accessible_at_phone_and_desktop_widths(
    page, component_origin, axe_source, width
):
    """Its button and its preview pass axe and fit a phone without scrolling."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in (ACCEPT_PAGE, ACCEPT_PREVIEW):
        page.goto(component_origin + path)
        assert page.locator("#accept-change").count() == 1
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert (
            page.evaluate("""async () => (await axe.run(document, {
            runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
        })).violations.map(({id,impact}) => ({id,impact}))""")
            == []
        )

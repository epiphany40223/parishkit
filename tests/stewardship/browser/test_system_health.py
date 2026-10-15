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
    ALLOW_CLEARED,
    ALLOW_PAGE,
    ALLOW_PREVIEW,
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
    STALE,
    STALE_STATUS,
    STATUS,
    STEADY,
)
from .waits import eventually, same_place, visible


def expect(locator):
    """Load the optional browser assertion library only after explicit opt-in."""
    from playwright.sync_api import expect as browser_expect

    return browser_expect(locator)


pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
REGION = '[data-live-status="system-health"]'
ANNOUNCER = '[data-live-announcer="system-health"]'


# Every tile, in the fixed order (admin-portal spec, "Health tiles").
TILES = ("mail", "source", "backups", "debug", "source-form", "resave", "version")
NAMES = [
    "Mail",
    "ParishSoft refresh",
    "Backups",
    "Debug logging",
    "Families the form cannot open",
    "Pages and emails to re-save",
    "Version and services",
]
# Where each tile's summary row (its toggle) is drawn in the document.
TOPS = """() => [...document.querySelectorAll('[data-health-toggle]')]
    .map((b) => b.getBoundingClientRect().top + window.scrollY)"""
# The overall line's shown form (the other is display: none), and the
# fixed-height line that holds it and Refresh System health.
LINE = "#health-overall > span:visible"
HOLDER = """() => {
    const box = document.querySelector('.health-line').getBoundingClientRect();
    return [box.top + window.scrollY, box.height];
}"""


def _toggle(page, tile):
    """The tile's summary row: the disclosure button."""
    return page.locator(f"#health-{tile} [data-health-toggle]")


def _band(page, tile):
    """The tile's details band."""
    return page.locator(f"#health-{tile}-details")


def _word(page, tile):
    """The tile's shown state word."""
    return page.locator(f"#health-{tile} .status-word").filter(visible=True)


def _away(page):
    """Move the pointer off the tile grid, so held details catch up."""
    page.mouse.move(1, 1)


def _axe(page, axe_source):
    """The page's axe violations for the WCAG 2.2 AA tags."""
    page.evaluate(axe_source)
    return page.evaluate("""async () => (await axe.run(document, {
        runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
    })).violations.map(({id,impact}) => ({id,impact}))""")


def test_the_resume_time_reads_as_a_local_clock_time(page, component_origin):
    """At the limit the count line ends with the browser-local resume time (#530)."""
    page.goto(component_origin + PAGE)
    resume = page.locator("#health-mail-details [data-daily-resume] time")
    stamp = resume.get_attribute("datetime")
    expected = page.evaluate(
        "(stamp) => ParishDates.dayTime(new Date(stamp), new Date())", stamp
    )
    expect(resume).to_have_text(expected)
    count = page.locator("#health-mail-details [data-daily-count]")
    assert "UTC" not in count.inner_text()
    page.goto(component_origin + HEALTHY)
    assert page.locator("#health-mail-details [data-daily-resume]").count() == 0


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
    """Troubled and healthy pages pass axe and fit a phone without scrolling;
    the line is the first h2, then one hidden h2 per tile, in order."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in (PAGE, HEALTHY):
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert _axe(page, axe_source) == []
        headings = page.locator("[data-health-dashboard] h2")
        assert headings.nth(0).get_attribute("id") == "health-overall"
        assert headings.all_text_contents()[1:] == NAMES
        line = page.locator(LINE).inner_text().strip()
        if path == HEALTHY:
            assert line == "All working"
        else:
            assert line == "4 errors and 1 warning"
        # The help is there, closed until asked for.
        assert page.locator("details.about-page").get_attribute("open") is None


def test_each_tile_shows_its_state_as_a_word_an_icon_and_a_colour(
    page, component_origin
):
    """Working, Warning and Error read as words with their own icon shapes,
    and an Error's start bar is wider than a Warning's (status indicators);
    troubled tiles start open, Working ones closed, each band named for its
    tile."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + PAGE)
    states = {
        "mail": "error",
        "source": "error",
        "backups": "warning",
        "debug": "working",
        "source-form": "working",
        "resave": "error",
        "version": "error",
    }
    words = {"working": "Working", "warning": "Warning", "error": "Error"}
    icons = {"working": "ok", "warning": "warning", "error": "error"}
    for tile, state in states.items():
        expect(_word(page, tile)).to_have_text(words[state])
        box = page.locator(f"#health-{tile} .health-counted")
        expect(box.locator(f"svg.level-icon-{icons[state]}")).to_have_count(1)
        assert box.locator("svg").get_attribute("aria-hidden") == "true"
        toggle = _toggle(page, tile)
        opened = state != "working"
        assert toggle.get_attribute("aria-expanded") == str(opened).lower()
        assert toggle.get_attribute("aria-controls") == f"health-{tile}-details"
        band = _band(page, tile)
        if opened:
            expect(band).to_be_visible()
        else:
            expect(band).to_be_hidden()
        assert band.get_attribute("role") == "region"
    expect(page.get_by_role("region", name="Mail details")).to_be_visible()
    bar = """(tile) => parseFloat(getComputedStyle(document.querySelector(
        `#health-${tile} .health-counted`)).borderInlineStartWidth)"""
    error, warning, working = (
        page.evaluate(bar, tile) for tile in ("mail", "backups", "debug")
    )
    assert error > warning > working
    # The summary names the tile's most severe problem.
    expect(page.locator("#health-mail .health-counted .health-said")).to_contain_text(
        "Mail sender 1 halted"
    )
    expect(page.locator("#health-mail-details")).to_contain_text(
        "Mail sender 1 has stopped all Family email"
    )


def test_a_tile_opens_and_closes_in_place_without_moving_its_row(
    page, component_origin
):
    """Opening a tile shows its band after the tile's row; the row's toggles
    and everything above stay put, only the rows below move. Pointer and
    keyboard both work, the cue says what a press does, and the page never
    reloads."""
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(component_origin + HEALTHY)
    kept = _not_reloaded(page)
    for tile in TILES:
        expect(_band(page, tile)).to_be_hidden()
    before = page.evaluate(TOPS)
    # Two or three columns on a laptop, filled in the fixed order.
    columns = sum(same_place(top, before[0]) for top in before)
    assert columns in (2, 3)
    row = before[:columns]
    source = _toggle(page, "source")
    expect(source).to_contain_text("Show details")
    source.click()
    expect(_band(page, "source")).to_be_visible()
    expect(source).to_have_attribute("aria-expanded", "true")
    expect(source).to_contain_text("Hide details")
    after = page.evaluate(TOPS)
    assert same_place(after[:columns], row)
    band = _band(page, "source").bounding_box()
    box = source.bounding_box()
    # The band follows the row, full width, and the next row follows it.
    assert band["y"] >= box["y"] + box["height"]
    assert after[columns] >= band["y"] + band["height"]
    assert band["width"] > 1.5 * box["width"]
    # The keyboard opens Mail, beside it: its band comes first, in tile
    # order, and the row still does not move.
    mail = _toggle(page, "mail")
    mail.focus()
    page.keyboard.press("Enter")
    expect(_band(page, "mail")).to_be_visible()
    assert (
        _band(page, "mail").bounding_box()["y"]
        < _band(page, "source").bounding_box()["y"]
    )
    assert same_place(page.evaluate(TOPS)[:columns], row)
    source.focus()
    page.keyboard.press("Space")
    expect(_band(page, "source")).to_be_hidden()
    expect(source).to_have_attribute("aria-expanded", "false")
    assert same_place(page.evaluate(TOPS)[:columns], row)
    assert kept()


def test_one_column_at_phone_width(page, component_origin):
    """At 320 pixels the tiles are one column; each row is at least 44px."""
    page.set_viewport_size({"width": 320, "height": 900})
    page.goto(component_origin + HEALTHY)
    tops = page.evaluate(TOPS)
    assert tops == sorted(tops) and len(set(round(top) for top in tops)) == 7
    for tile in TILES:
        assert _toggle(page, tile).bounding_box()["height"] >= 44


def test_an_anchor_opens_its_tile(page, component_origin):
    """Home's links name a tile's anchor; the page opens that tile."""
    page.goto(component_origin + HEALTHY + "#health-backups")
    expect(_band(page, "backups")).to_be_visible()
    expect(_toggle(page, "backups")).to_have_attribute("aria-expanded", "true")
    expect(_band(page, "mail")).to_be_hidden()


def test_a_poll_changes_words_but_never_opens_closes_or_moves_a_tile(
    page, component_origin
):
    """The troubled page's next poll is healthy: summaries and the line
    follow it, a tile that turns Working while open stays open, a tile the
    Administrator closed stays closed, the line and the first row stay put,
    and the announcer says the new line once."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + PAGE)
    region = page.locator(REGION)
    assert region.get_attribute("data-live-interval") == "10000"
    assert page.locator(ANNOUNCER).text_content() == ""
    _toggle(page, "version").click()
    expect(_band(page, "version")).to_be_hidden()
    _toggle(page, "debug").click()
    expect(_band(page, "debug")).to_be_visible()
    _away(page)
    line = page.evaluate(HOLDER)
    tops = page.evaluate(TOPS)
    expect(_word(page, "mail")).to_have_text("Working", timeout=30000)
    expect(page.locator(LINE)).to_have_text("1 error")
    expect(page.locator(ANNOUNCER)).to_have_text(
        "System health: 1 error (Pages and emails to re-save)."
    )
    # The open Mail tile stays open and follows the poll.
    expect(_band(page, "mail")).to_be_visible()
    visible(_band(page, "mail").get_by_text("Nothing is holding Family email back."))
    expect(_band(page, "mail")).not_to_contain_text("has stopped all Family email")
    expect(_band(page, "version")).to_be_hidden()
    expect(_band(page, "debug")).to_be_visible()
    # The page-load tile keeps the state it was drawn with.
    expect(_word(page, "resave")).to_have_text("Error")
    assert same_place(page.evaluate(HOLDER), line)
    assert same_place(page.evaluate(TOPS)[:2], tops[:2])
    assert region.get_attribute("data-live-pending") is not None


def test_open_details_wait_while_the_pointer_is_over_the_grid(page, component_origin):
    """The summary rows follow at once; the details, which can change
    height, wait until the pointer leaves the grid."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + PAGE)
    mail = _band(page, "mail")
    visible(mail.get_by_text("Mail sender 1 has stopped all Family email"))
    _toggle(page, "source").hover()
    expect(_word(page, "mail")).to_have_text("Working", timeout=30000)
    # Held while the pointer is over the grid.
    visible(mail.get_by_text("Mail sender 1 has stopped all Family email"))
    _away(page)
    expect(mail).not_to_contain_text("has stopped all Family email", timeout=30000)
    visible(mail.get_by_text("Nothing is holding Family email back."))


def test_an_unchanged_poll_keeps_the_readers_focus_and_place(page, component_origin):
    """Polls that bring the same state leave the elements alone.

    A keyboard user resting on a link in an open tile keeps focus through
    two 10-second polls; only "Last checked" (outside the region) moves.
    """
    page.goto(component_origin + STEADY)
    _toggle(page, "source").click()
    _away(page)
    link = _band(page, "source").get_by_role("link", name="Refresh from ParishSoft")
    link.focus()
    page.evaluate("""() => {
        document.getElementById('health-overall').dataset.original = 'yes';
        document.querySelector('[data-live-mirror-target="health-source-panel"] p')
            .dataset.original = 'yes';
    }""")
    checked = page.locator("time[data-live-checked]")
    first = checked.get_attribute("datetime")
    # Two polls, each with the same state.
    expect(checked).not_to_have_attribute("datetime", first, timeout=30000)
    second = checked.get_attribute("datetime")
    expect(checked).not_to_have_attribute("datetime", second, timeout=30000)
    expect(link).to_be_focused()
    # The same elements: nothing was rebuilt.
    assert page.evaluate("""() =>
        document.getElementById('health-overall').dataset.original === 'yes'
        && document.querySelector('[data-live-mirror-target="health-source-panel"] p')
            .dataset.original === 'yes'""")


def test_a_change_only_in_a_times_datetime_updates_the_tiles(page, component_origin):
    """Times are compared by their datetime, not by how the browser words them."""
    page.goto(component_origin + DATED)
    since = page.locator(
        '[data-live-mirror-target="health-backups-summary"]'
        " .health-counted time[data-live-since]"
    ).first
    first = since.get_attribute("datetime")
    expect(since).not_to_have_attribute("datetime", first, timeout=30000)
    expect(since).to_have_text("1 hour ago")


def test_an_update_leaves_focus_outside_and_keeps_a_tables_sideways_scroll(
    page, component_origin
):
    """Focus outside the tiles stays put; a wide table in an open tile keeps
    its sideways offset when the poll replaces the tile's details."""
    page.set_viewport_size({"width": 320, "height": 900})
    page.goto(component_origin + PAGE)
    # Make the Services table wider than the phone, as a long one would be.
    # Through the CSS object model: the page's CSP refuses inline styles.
    page.evaluate(
        "document.styleSheets[0].insertRule("
        "'#health-version-details .table-scroll table { min-width: 900px }')"
    )
    table = page.locator("#health-version-details .table-scroll")
    assert table.evaluate("(node) => node.scrollWidth > node.clientWidth")
    table.evaluate("(node) => { node.scrollLeft = 40; }")
    outside = page.get_by_role("link", name="Refresh System health")
    outside.focus()
    _away(page)
    expect(
        page.locator("#health-version-details td", has_text="Not running")
    ).to_have_count(0, timeout=30000)
    expect(outside).to_be_focused()
    swapped = page.locator("#health-version-details .table-scroll")
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
        range.selectNodeContents(document.getElementById('health-overall'));
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


def test_the_page_says_when_it_is_not_current_and_recovers_in_place(
    page, component_origin
):
    """After three failed checks the line reads Not current with when it
    last checked, every tile turns Not current with its last known state,
    nothing opens, closes or moves, and the announcer says so once.
    Refresh System health checks again at once and brings the counted
    states back."""
    page.set_viewport_size({"width": 1280, "height": 900})
    failures = []

    def fail(route):
        """Answer a status check as a server that cannot answer."""
        failures.append(1)
        route.fulfill(status=500, content_type="text/plain", body="")

    page.route(f"{component_origin}{STALE_STATUS}", fail)
    page.goto(component_origin + STALE)
    kept = _not_reloaded(page)
    restart = page.get_by_role("button", name="Refresh System health")
    expect(restart).to_be_hidden()
    _toggle(page, "backups").click()
    _away(page)
    line = page.evaluate(HOLDER)
    tops = page.evaluate(TOPS)
    dashboard = page.locator("[data-health-dashboard]")
    expect(dashboard).to_have_attribute("data-not-current", "", timeout=60000)
    assert len(failures) >= 3
    expect(page.locator(LINE)).to_contain_text("Not current")
    expect(page.locator(LINE)).to_contain_text("Not checked since")
    last = page.locator("time[data-health-last]")
    stamp = page.locator("time[data-live-checked]").get_attribute("datetime")
    expect(last).to_have_attribute("datetime", stamp)
    expect(last).to_have_text(
        page.evaluate("(stamp) => ParishDates.time(new Date(stamp))", stamp)
    )
    expect(restart).to_be_visible()
    for tile in TILES:
        expect(_word(page, tile)).to_have_text("Not current")
        expect(
            page.locator(f"#health-{tile} .health-unknown .health-said")
        ).to_contain_text("Last known: Working.")
    expect(
        page.get_by_role("region", name="Backups details, not current")
    ).to_be_visible()
    expect(_band(page, "mail")).to_be_hidden()
    expect(page.locator(ANNOUNCER)).to_have_text(
        "System health is not current; last checked at "
        f"{last.text_content()}. The tiles show what was last known."
    )
    assert same_place(page.evaluate(HOLDER), line)
    assert same_place(page.evaluate(TOPS), tops)
    # Current again, from the button, with the troubled state it reads.
    # Count what the announcer is given: the sentence once, not twice.
    page.evaluate("""() => {
        window.announced = 0;
        new MutationObserver((records) => { window.announced += records.length; })
            .observe(document.querySelector('[data-live-announcer="system-health"]'),
                {childList: true, characterData: true, subtree: true});
    }""")
    page.unroute(f"{component_origin}{STALE_STATUS}")
    restart.click()
    expect(dashboard).not_to_have_attribute("data-not-current", "")
    expect(page.locator(LINE)).to_have_text("3 errors and 1 warning")
    expect(_word(page, "mail")).to_have_text("Error")
    expect(page.locator(ANNOUNCER)).to_have_text(
        "System health: 3 errors (Mail, ParishSoft refresh, Version and services)"
        " and 1 warning (Backups)."
    )
    assert page.evaluate("window.announced") == 1
    expect(page.get_by_role("region", name="Backups details")).to_be_visible()
    # A poll never opens a tile, even one that turned Error.
    expect(_band(page, "mail")).to_be_hidden()
    expect(restart).to_be_hidden()
    assert kept()


# The fragment of the troubled page, whatever its ?loaded= says.
TROUBLED_ROUTE = f"**{STATUS}*"
# The line box and the restart button, in the viewport.
RESTART = """() => {
    const line = document.querySelector('.health-line').getBoundingClientRect();
    const button = document.querySelector('.health-restart').getBoundingClientRect();
    return {line: [line.left, line.top, line.right, line.bottom],
            button: [button.left, button.top, button.right, button.bottom]};
}"""


@pytest.mark.parametrize("width", [320, 660, 1000, 1280])
def test_not_current_takes_over_troubled_tiles_and_the_line_fits(
    page, component_origin, width
):
    """Open Error and Warning bands, and their rows' notches, take the Not
    current border and lose their start bars; the Not current line and its
    button fit the line's fixed box, beside the menu too, so neither is cut
    off nor overlaps the tiles."""
    page.set_viewport_size({"width": width, "height": 900})
    page.route(
        TROUBLED_ROUTE,
        lambda route: route.fulfill(status=500, content_type="text/plain", body=""),
    )
    page.goto(component_origin + PAGE)
    look = """(selector) => {
        const style = getComputedStyle(document.querySelector(selector));
        return [style.borderTopColor, style.borderInlineStartWidth];
    }"""
    neutral = page.evaluate(
        """() => { const probe = document.createElement('span');
        probe.style.color = 'var(--control-border)';
        document.body.append(probe);
        const color = getComputedStyle(probe).color;
        probe.remove();
        return color; }"""
    )
    assert page.evaluate(look, "#health-mail-details")[0] != neutral
    dashboard = page.locator("[data-health-dashboard]")
    expect(dashboard).to_have_attribute("data-not-current", "", timeout=60000)
    for tile in ("mail", "backups"):
        assert page.evaluate(look, f"#health-{tile}-details") == [neutral, "1px"]
        notch = page.evaluate(
            """(tile) => getComputedStyle(document.getElementById(tile),
            '::after').borderTopColor""",
            f"health-{tile}",
        )
        assert notch == neutral
    box = page.evaluate(RESTART)
    line, button = box["line"], box["button"]
    assert line[0] <= button[0] and button[2] <= line[2]
    assert line[1] <= button[1] and button[3] <= line[3]
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_a_poll_that_gives_up_turns_not_current_and_refresh_starts_it_again(
    page, component_origin
):
    """After an hour of watching the poll stops: the line turns Not current
    with no other notice. Refresh System health starts a fresh watch that
    checks at once, brings the counted state back, says it once, and keeps
    polling."""
    page.clock.install()
    page.goto(component_origin + STEADY)
    kept = _not_reloaded(page)
    dashboard = page.locator("[data-health-dashboard]")
    checked = page.locator("time[data-live-checked]")
    # One ordinary check, then a jump past the hour.
    first = checked.get_attribute("datetime")
    page.clock.fast_forward(11_000)
    expect(checked).not_to_have_attribute("datetime", first)
    page.clock.fast_forward(61 * 60 * 1000)
    expect(dashboard).to_have_attribute("data-not-current", "")
    expect(page.locator(".live-status-problem")).to_be_hidden()
    expect(page.locator(ANNOUNCER)).to_contain_text("System health is not current")
    expect(page.get_by_role("button", name="Refresh System health")).to_be_attached()
    # The hour's jump also ends the component's Admin session, whose modal
    # "Sign in again" dialog makes the rest of the page inert, so the button
    # is clicked from script.
    page.evaluate("document.querySelector('[data-live-restart]').click()")
    expect(dashboard).not_to_have_attribute("data-not-current", "")
    expect(page.locator(LINE)).to_have_text("All working")
    expect(page.locator(ANNOUNCER)).to_have_text("System health: all working.")
    # The fresh watch polls on.
    stamp = checked.get_attribute("datetime")
    page.clock.fast_forward(11_000)
    expect(checked).not_to_have_attribute("datetime", stamp)
    expect(dashboard).not_to_have_attribute("data-not-current", "")
    assert kept()


def test_a_refused_check_turns_not_current_at_once(page, component_origin):
    """A refused check (signed out) turns the page Not current at once, with
    nothing squeezed into the line's fixed box."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.route(
        TROUBLED_ROUTE,
        lambda route: route.fulfill(status=401, content_type="text/plain", body=""),
    )
    page.goto(component_origin + PAGE)
    dashboard = page.locator("[data-health-dashboard]")
    expect(dashboard).to_have_attribute("data-not-current", "", timeout=60000)
    expect(page.locator(".live-status-problem")).to_be_hidden()
    box = page.evaluate(RESTART)
    assert box["button"][3] <= box["line"][3]


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
    page.goto(component_origin + BACKUP_PAGE + "#health-backups")
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
    page.goto(component_origin + BACKUP_PAGE + "#health-backups")
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
    page.goto(component_origin + BACKUP_PAGE + "#health-backups")
    kept = _not_reloaded(page)
    page.locator("#backup-now").get_by_role(
        "button", name="Take a backup now", exact=True
    ).click()
    expect(region.get_by_text("A backup is already requested")).to_be_visible()
    assert kept()


def test_the_button_follows_a_request_made_elsewhere(page, component_origin):
    """The poll greys the button when another request starts waiting."""
    page.goto(component_origin + BACKUP_PAGE + "#health-backups")
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
    page.goto(component_origin + BACKUP_PAGE + "#health-backups")
    region = page.locator("#backup-now")
    button = region.get_by_role("button", name="Take a backup now", exact=True)
    button.focus()
    # The first poll brings the waiting request; the focused button is left.
    eventually(
        page,
        """() => document.querySelector(
            '[data-live-status] template[data-live-mirror="backup-now-button"]'
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
    """The shared "For security" notice and Confirm with Google, in place."""
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
    visible(region.get_by_text("For security, turning off debug logging"))
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
    # The tile's details say the same, and that the server's setting is on.
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
    """#accept-change is in the ParishSoft refresh tile's details, directly
    after its problems, which end with the refused change's counts (#1214),
    and starts within a line of them."""
    assert page.evaluate("""() => {
        const notice = document.querySelector('[data-refused-change]');
        const problems = notice.parentElement;
        const keyed = problems.nextElementSibling;
        return problems.matches(
                '[data-live-mirror-target="health-source-problems"]')
            && notice === problems.lastElementChild
            && keyed.matches('[data-live-keyed="accept-change"]')
            && keyed.firstElementChild === document.getElementById('accept-change')
            && keyed.parentElement.id === 'health-source-details';
    }""")
    gap = page.evaluate("""() =>
        document.getElementById('accept-change').getBoundingClientRect().top
        - document.querySelector('[data-refused-change]').getBoundingClientRect().bottom
    """)
    assert 0 <= gap < 40, gap


def _places(page, heading):
    """Record where ``heading`` and every shown heading above it are in the
    document, each known by its id (the overall line's words change) or its
    text; the returned check lists those that moved since (Firefox may
    report the same place a few millionths of a pixel apart)."""
    script = """(own) => [...document.querySelectorAll('h1, h2, h3')]
        .filter((h) => h.getClientRects().length && (h === own
            || h.compareDocumentPosition(own) & Node.DOCUMENT_POSITION_FOLLOWING))
        .map((h) => [h.id || h.textContent.trim(),
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


def test_a_refusal_shown_by_a_poll_brings_its_accept_button(page, component_origin):
    """A page drawn before the refusal shows the region greyed with why; the
    poll that shows the refusal brings its counts and the button, right
    under them; no reload needed, and the tile is not opened for the reader."""
    page.goto(component_origin + ACCEPT_LATE + "#health-source")
    region = page.locator("#accept-change")
    button = region.get_by_role("button", name="Accept this change once", exact=True)
    expect(button).to_be_disabled()
    expect(region.locator("#accept-change-reason")).to_have_text(
        "No refused ParishSoft refresh is waiting to be accepted."
    )
    _away(page)
    expect(button).to_be_enabled(timeout=30000)
    expect(page.locator("[data-refused-change]")).to_be_visible(timeout=30000)
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


def test_allow_debug_logging_again_previews_and_confirms_in_place(
    page, component_origin, axe_source
):
    """In Testing with the switch set: preview, then confirm, each swapped
    into #debug-off; focus follows; nothing above the region moves; the
    region passes axe (ADM-13 PR 6c)."""
    _routed(
        page,
        component_origin,
        {
            "debug_allow_preview": (200, ALLOW_PREVIEW),
            "debug_allow_confirm": (200, ALLOW_CLEARED),
        },
        shown=ALLOW_PAGE,
    )
    # Debug logging is off and the switch set, so the tile is Working and
    # starts closed; its anchor opens it.
    page.goto(component_origin + BACKUP_PAGE + "#health-debug")
    kept = _not_reloaded(page)
    region = page.locator("#debug-off")
    heading = region.get_by_role("heading", name="Turn off debug logging", exact=True)

    def place():
        """The heading's place in the document, wherever the page is scrolled."""
        return heading.evaluate("e => e.getBoundingClientRect().top + window.scrollY")

    top = place()
    expect(
        region.get_by_role("button", name="Turn off debug logging", exact=True)
    ).to_be_disabled()
    region.get_by_role("button", name="Allow debug logging again", exact=True).click()
    confirm = page.get_by_role(
        "button", name="Confirm: allow debug logging again", exact=True
    )
    expect(confirm).to_be_visible()
    expect(confirm).to_be_focused()
    visible(region.get_by_text("never turns debug logging on"))
    assert abs(place() - top) < 0.5
    page.evaluate(axe_source)
    assert (
        page.evaluate("""async () => (await axe.run(document, {
        runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
    })).violations.map(({id,impact}) => ({id,impact}))""")
        == []
    )
    confirm.click()
    done = region.locator("[data-debug-allowed] time")
    expect(done).to_be_visible()
    stamp = done.get_attribute("datetime")
    expect(done).not_to_have_text(stamp)
    assert "UTC" not in region.inner_text()
    assert kept()
    assert abs(place() - top) < 0.5
    assert page.evaluate(
        "document.getElementById('debug-off').contains(document.activeElement)"
    )


@pytest.mark.parametrize("width", [320, 1280])
def test_the_allow_region_is_accessible_at_phone_and_desktop_widths(
    page, component_origin, axe_source, width
):
    """The Testing region with both buttons, and its preview, pass axe and
    fit a phone without scrolling."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in (ALLOW_PAGE, ALLOW_PREVIEW, DEBUG_PAGE):
        # The anchor opens the Debug logging tile, Working in Testing, so axe
        # checks the region as the reader sees it.
        page.goto(component_origin + path + "#health-debug")
        assert page.locator("#debug-off").count() == 1
        expect(page.locator("#debug-off")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert (
            page.evaluate("""async () => (await axe.run(document, {
            runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
        })).violations.map(({id,impact}) => ({id,impact}))""")
            == []
        )

"""System health in a real browser (ADM-13, #530): accessible, live and quiet."""

import pytest

from .system_health_components import (
    BACKUP_BUSY,
    BACKUP_PAGE,
    BACKUP_PREVIEW,
    BACKUP_REQUESTED,
    BACKUP_STEP_UP,
    DATED,
    HEALTHY,
    PAGE,
    STEADY,
)
from .waits import visible


def expect(locator):
    """Load the optional browser assertion library only after explicit opt-in."""
    from playwright.sync_api import expect as browser_expect

    return browser_expect(locator)


pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
REGION = '[data-live-status="system-health"]'
ANNOUNCER = '[data-live-announcer="system-health"]'


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


def _routed(page, component_origin, answers):
    """Answer the page's own POSTs as the view would, by the posted action.

    ``answers`` maps an action to ``(status, component path)``; GETs of the
    page go to the component server unchanged.
    """
    from urllib.request import urlopen

    bodies = {}
    for action, (status, path) in answers.items():
        with urlopen(component_origin + path) as response:
            bodies[action] = (status, response.read().decode())

    def respond(route):
        """Fulfil a POST from ``answers``; let anything else through."""
        if route.request.method != "POST":
            route.continue_()
            return
        data = route.request.post_data or ""
        action = next(name for name in bodies if f"action={name}" in data)
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
    page.locator("#backup-now").get_by_role("button", name="Take a backup now…").click()
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
    page.locator("#backup-now").get_by_role("button", name="Take a backup now…").click()
    region = page.locator("#backup-now")
    expect(region.get_by_role("button", name="Confirm with Google")).to_be_visible()
    assert kept()
    assert '"errors"' not in page.content()
    page.unroute(f"{component_origin}{BACKUP_PAGE}")
    _routed(page, component_origin, {"preview": (409, BACKUP_BUSY)})
    page.goto(component_origin + BACKUP_PAGE)
    kept = _not_reloaded(page)
    page.locator("#backup-now").get_by_role("button", name="Take a backup now…").click()
    expect(region.get_by_text("A backup is already requested")).to_be_visible()
    assert kept()


def test_the_button_follows_a_request_made_elsewhere(page, component_origin):
    """The poll greys the button when another request starts waiting."""
    page.goto(component_origin + BACKUP_PAGE)
    region = page.locator("#backup-now")
    expect(region.get_by_role("button", name="Take a backup now…")).to_be_enabled()
    expect(region.get_by_role("button", name="Take a backup now…")).to_be_disabled(
        timeout=30000
    )
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
    button = region.get_by_role("button", name="Take a backup now…")
    button.focus()
    # The first poll brings the waiting request; the focused button is left.
    page.wait_for_function(
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

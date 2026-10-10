"""Readable Admin mail tables in a real browser (#931).

Outgoing mail names each Family beside its DUID, and Family email history
has fewer, wider columns. At phone width a table scrolls inside its own
region and the page never scrolls sideways; at desktop width no date or
time cell wraps. Layout is the same in every engine, so Chromium suffices.
"""

import pytest

from .send_history_components import PAGE as HISTORY

pytestmark = pytest.mark.parametrize("browser_engine", ["chromium"], indirect=True)

OUTGOING = "/deliveries-names"

# Every time cell of the table, and whether any spans more than one line.
# A single-line inline element has exactly one client rect; a wrapped one has
# one per line, so this needs no assumption about the line height.
WRAPPED_TIMES = """table => [...table.querySelectorAll('tbody time')]
    .filter(node => node.getClientRects().length !== 1)
    .map(node => node.textContent)"""


def open_at(page, origin, path, width):
    """Load ``path`` at ``width`` and return its one data table."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(origin + path)
    # The local-time script has rewritten every time before measuring.
    page.wait_for_function(
        "[...document.querySelectorAll('time[data-local-instant]')]"
        ".every(node => node.textContent !== node.getAttribute('datetime'))"
    )
    return page.locator("table.data-table")


def assert_accessible(page, axe_source):
    """The page passes the same axe rules as the other Admin pages."""
    page.evaluate(axe_source)
    assert (
        page.evaluate("""async () => (await axe.run(document, {
        runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
    })).violations.map(({id,impact}) => ({id,impact}))""")
        == []
    )


@pytest.mark.parametrize("path", [OUTGOING, HISTORY])
def test_a_phone_scrolls_the_table_not_the_page(
    page, component_origin, axe_source, path
):
    """At 320 px the page fits the screen; any extra width is the table's."""
    table = open_at(page, component_origin, path, 320)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    region = page.locator(".table-scroll")
    assert region.evaluate("node => node.scrollWidth > node.clientWidth")
    # Even squeezed, no date or time breaks across lines.
    assert table.evaluate(WRAPPED_TIMES) == []
    assert_accessible(page, axe_source)


@pytest.mark.parametrize("path", [OUTGOING, HISTORY])
def test_a_desktop_never_wraps_dates(page, component_origin, axe_source, path):
    """At 1280 px every date and time cell reads on one line."""
    table = open_at(page, component_origin, path, 1280)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert table.locator("tbody time").count() >= 3
    assert table.evaluate(WRAPPED_TIMES) == []
    # Cells wrap only between words: no heading breaks inside a word, so
    # each heading word is no wider than its cell.
    assert (
        table.evaluate("""table => [...table.querySelectorAll('thead th')]
            .filter(th => th.scrollWidth > th.clientWidth)
            .map(th => th.textContent.trim())""")
        == []
    )
    assert_accessible(page, axe_source)


def test_outgoing_mail_shows_the_family_name_and_duid_apart(page, component_origin):
    """Dates lead (#932's order); the name opens the email; the DUID is its
    own sortable column, and the name column does not sort."""
    table = open_at(page, component_origin, OUTGOING, 1280)
    headings = table.locator("thead th")
    assert [text.split("\n")[0].strip() for text in headings.all_inner_texts()] == [
        "Created",
        "Last changed",
        "Family",
        "Family DUID",
        "Purpose",
        "Mode",
        "State",
        "Provider attempts",
    ]
    assert headings.nth(2).locator(".sort-link").count() == 0
    assert headings.nth(3).locator(".sort-link").count() == 1
    rows = table.locator("tbody tr")
    assert [rows.nth(i).locator("th").inner_text() for i in range(3)] == [
        "Castellanos, Maximiliana and Bartholomew",
        "Not in current ParishSoft data",
        "Administrator report",
    ]
    # The DUID cell follows the two times.
    assert [rows.nth(i).locator("td").nth(2).inner_text() for i in range(3)] == [
        "12345",
        "4021",
        "—",
    ]
    assert rows.nth(0).locator("td").first.locator("time").count() == 1
    assert (
        rows.nth(0)
        .get_by_role("link", name="Castellanos, Maximiliana and Bartholomew")
        .get_attribute("href")
        .startswith("/admin/mail/outgoing/")
    )


def test_family_email_history_puts_the_dates_first(page, component_origin):
    """When leads; mode and progress are muted lines under the email."""
    table = open_at(page, component_origin, HISTORY, 1280)
    assert table.locator("thead th").count() == 7
    # Seven columns fit beside the Admin menu at laptop width.
    assert page.locator(".table-scroll").evaluate(
        "node => node.scrollWidth <= node.clientWidth"
    )
    first = table.locator("tbody tr").first
    when = first.locator("td").first
    assert when.locator("dt").all_inner_texts()[:2] == ["Scheduled", "First email"]
    assert when.locator("time").count() == 2  # the reminder has no result yet
    detail = first.locator("th .cell-detail").first
    assert detail.inner_text() == "Production"
    # The muted line is lighter than the name above it.
    assert detail.evaluate("node => getComputedStyle(node).color") != first.locator(
        "th"
    ).evaluate("node => getComputedStyle(node).color")

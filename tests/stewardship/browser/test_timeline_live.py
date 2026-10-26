"""The Family hub's Timeline tab updates itself; What happened fits (#2197).

The Timeline table re-reads itself quietly every 60 seconds (UX
conventions, "Pages that update themselves", lists that others add to):
each test installs a fake clock, answers the page's own passive re-reads
with a later version of it (``timeline_components.LIVE``, two more lines)
or with the empty answer an unchanged read gets, and sets a mark outside
every region, which only a full page load could lose. The read waits while
the pointer is over the Timeline panel or focus is in it, and while its
tab is not shown. The What happened column keeps its words whole at
1280 px on both Timelines, and neither page scrolls sideways at 320 px.
"""

from urllib.parse import urlsplit

import pytest

from .automation_components import canonical
from .conftest import NOW
from .timeline_components import ADMIN, EMAILS, LIVE, TAB
from .user_timeline_components import PAGE as USER_PAGE
from .waits import eventually, has_count, has_text, hidden

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

VIEWPORT = {"width": 1280, "height": 800}
# A little more than the 60-second pace, and than the 5-second retry tick.
TICK = 61 * 1000
SHORT = 6 * 1000
MARK = "document.querySelector('h1').dataset.mark = 'kept'"
MARKED = "document.querySelector('h1').dataset.mark"
FIRST_LINE = "#table tbody tr:first-child th"
PROBLEM = "[data-reread-problem]"
# Every word in a What happened cell (heading or line) that the browser
# broke across lines: the tops of its range's boxes differ.
BROKEN_WORDS = """() => [...document.querySelectorAll(
  ".timeline-table .timeline-what, .timeline-table tbody th")].flatMap((cell) => {
  const broken = [];
  const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    for (const match of node.data.matchAll(/\\S+/g)) {
      const range = document.createRange();
      range.setStart(node, match.index);
      range.setEnd(node, match.index + match[0].length);
      const boxes = [...range.getClientRects()];
      const tops = new Set(boxes.map((box) => Math.round(box.top)));
      if (tops.size > 1) broken.push(match[0]);
    }
  }
  return broken;
})"""


# Long Details on every line, and the first three lines' words the long
# labels of a real Timeline, so the column is as squeezed as it gets.
SQUEEZE = """() => {
  const rows = [...document.querySelectorAll("table:has(.timeline-what) tbody tr")]
    .filter((row) => row.cells.length === 3);
  const long = "Email address for Adams, Ann; by staff@example.org; ".repeat(8);
  rows.forEach((row) => { row.cells[2].textContent = long; });
  ["Submissions", "Reminder", "Invitation"].forEach((word, index) => {
    if (rows[index]) rows[index].cells[1].textContent = `${word} sent`;
  });
  return rows.length;
}"""


def passive_reads(page, origin, answer=None, *, through=False):
    """Answer the page's passive re-reads of the Timeline with ``LIVE``, or
    with ``answer`` (a body) when given, or let them through to the page
    their address names (``through``); every other request goes through.
    Returns the list of the re-reads' headers, each with its ``url``."""
    reads = []
    path = urlsplit(origin + ADMIN).path

    def handle(route):
        """Serve a passive re-read; let loads and tab fetches through."""
        headers = route.request.headers
        if headers.get("x-parishkit-read") != "passive":
            route.continue_()
            return
        reads.append(headers | {"url": route.request.url})
        if through:
            route.continue_()
            return
        if answer is not None:
            route.fulfill(status=200, content_type="text/html", body=answer)
            return
        route.fulfill(response=route.fetch(url=origin + LIVE, method="GET"))

    page.route(lambda url: urlsplit(url).path == path, handle)
    return reads


def opened(page, origin):
    """Open the hub on its Timeline tab under a fake clock, mark it, and rest
    the pointer outside the Timeline panel."""
    page.set_viewport_size(VIEWPORT)
    page.clock.install(time=NOW)
    page.goto(origin + ADMIN)
    page.evaluate(MARK)
    page.mouse.move(VIEWPORT["width"] - 2, 2)
    return page.locator("#table").get_attribute("data-reread-seen")


def test_new_lines_join_the_table_in_place(page, component_origin):
    """A minute on, the table re-reads itself passively, sending the digest
    of the lines it drew, and the new lines join newest first: no reload,
    nothing said about it."""
    seen = opened(page, component_origin)
    reads = passive_reads(page, component_origin)
    assert seen
    assert page.locator("#table").get_by_text("Handled").count() == 0
    page.clock.fast_forward(TICK)
    has_text(page.locator(FIRST_LINE), "Handled")
    assert page.evaluate(MARKED) == "kept"
    assert page.url == component_origin + ADMIN
    assert len(reads) == 1
    assert reads[0]["x-parishkit-seen"] == seen
    # The fresh region carries the digest of the lines it now draws.
    assert page.locator("#table").get_attribute("data-reread-seen") != seen
    assert page.locator(PROBLEM).count() == 0


def test_an_unchanged_answer_changes_nothing_and_reads_go_on(page, component_origin):
    """The empty answer to a read whose lines are the same is a read that
    worked: nothing is swapped, no problem is said, and the next read
    follows a minute later."""
    seen = opened(page, component_origin)
    reads = passive_reads(page, component_origin, answer="")
    rows = page.locator("#table tbody tr").count()
    page.clock.fast_forward(TICK)
    eventually(page, "() => !document.querySelector('[aria-busy=true]')")
    assert len(reads) == 1
    page.clock.fast_forward(TICK)
    eventually(page, "() => !document.querySelector('[aria-busy=true]')")
    assert len(reads) == 2
    assert page.locator("#table tbody tr").count() == rows
    assert page.locator("#table").get_attribute("data-reread-seen") == seen
    assert page.locator(f"{PROBLEM}:not([hidden])").count() == 0


def test_the_pointer_over_the_table_holds_the_read(page, component_origin):
    """While the pointer is over the table no line moves under it; moved
    away, the table catches up within a short tick."""
    opened(page, component_origin)
    reads = passive_reads(page, component_origin)
    page.locator(FIRST_LINE).hover()
    page.clock.fast_forward(TICK)
    page.evaluate("() => true")  # One round trip after the clock jump.
    assert reads == []
    page.mouse.move(VIEWPORT["width"] - 2, 2)
    page.clock.fast_forward(SHORT)
    has_text(page.locator(FIRST_LINE), "Handled")
    assert len(reads) == 1


def test_a_filter_being_chosen_holds_the_read(page, component_origin):
    """Focus in the filter bar (outside the table's region, but in the
    Timeline panel) holds the read too, until it leaves."""
    opened(page, component_origin)
    reads = passive_reads(page, component_origin)
    page.locator("#timeline-kind").focus()
    page.clock.fast_forward(TICK)
    page.evaluate("() => true")
    assert reads == []
    page.evaluate("() => document.activeElement.blur()")
    page.clock.fast_forward(SHORT)
    has_text(page.locator(FIRST_LINE), "Handled")


def test_a_hidden_tab_is_not_read_and_catches_up_when_chosen(page, component_origin):
    """On another tab the Timeline is not read; choosing it again reads at
    once."""
    opened(page, component_origin)
    reads = passive_reads(page, component_origin)
    page.get_by_role("tab", name="Family", exact=True).click()
    eventually(page, f"() => location.search === '{TAB['family'].split(ADMIN)[1]}'")
    hidden(page.locator("#table"))
    page.mouse.move(VIEWPORT["width"] - 2, 2)
    page.clock.fast_forward(2 * TICK)
    page.evaluate("() => true")
    assert reads == []
    page.get_by_role("tab", name="Timeline").click()
    page.mouse.move(VIEWPORT["width"] - 2, 2)
    has_text(page.locator(FIRST_LINE), "Handled")
    assert len(reads) == 1


def test_a_tab_comes_back_with_its_filters(page, component_origin):
    """Filtered, then left for another tab and chosen again, the Timeline
    comes back at its filtered address, so its catch-up read (and a reload)
    keeps the filters rather than drawing every line under them."""
    opened(page, component_origin)
    reads = passive_reads(page, component_origin, through=True)
    page.locator("#timeline-kind").select_option("emails")
    page.get_by_role("button", name="Apply filters", exact=True).click()
    has_count(page.locator("#table tbody tr", has_text="Got past"), 0)
    page.get_by_role("tab", name="Family", exact=True).click()
    hidden(page.locator("#table"))
    page.get_by_role("tab", name="Timeline").click()
    page.mouse.move(VIEWPORT["width"] - 2, 2)
    page.clock.fast_forward(SHORT)
    eventually(page, "() => !document.querySelector('[aria-busy=true]')")
    assert canonical(page.url.removeprefix(component_origin).split("#")[0]) == EMAILS
    assert reads and "kind=emails" in reads[-1]["url"]
    assert page.locator("#table tbody tr", has_text="Got past").count() == 0
    assert page.locator("#timeline-kind").input_value() == "emails"


def test_back_never_gives_another_tab_the_timelines_address(page, component_origin):
    """Filtered, then another tab, then Back to the Timeline: choosing the
    other tab again names that tab, not the Timeline's filtered address."""
    opened(page, component_origin)
    page.locator("#timeline-kind").select_option("emails")
    page.get_by_role("button", name="Apply filters", exact=True).click()
    has_count(page.locator("#table tbody tr", has_text="Got past"), 0)
    page.get_by_role("tab", name="Family", exact=True).click()
    hidden(page.locator("#table"))
    page.go_back()
    eventually(page, "() => !document.querySelector('#table').closest('[hidden]')")
    assert canonical(page.url.removeprefix(component_origin).split("#")[0]) == EMAILS
    page.get_by_role("tab", name="Family", exact=True).click()
    hidden(page.locator("#table"))
    assert page.url.removeprefix(component_origin).split("#")[0] == TAB["family"]


@pytest.mark.parametrize("path", [ADMIN, USER_PAGE])
def test_what_happened_keeps_its_words_whole(page, component_origin, path):
    """At 1280 px What happened's heading and its lines' words (Reminder,
    Invitation, Submissions) each stay on one line, at least as wide as the
    column's minimum; at 320 px the page itself never scrolls sideways."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + path)
    column = page.locator("thead .timeline-what")
    assert column.count() == 1
    # The squeeze the Administrator saw (R52): long Details beside the
    # column, and lines that start with each of the three words.
    assert page.evaluate(SQUEEZE) >= 3
    assert page.evaluate(BROKEN_WORDS) == []
    minimum = page.evaluate(
        "() => 9 * parseFloat(getComputedStyle(document.documentElement).fontSize)"
    )
    assert column.bounding_box()["width"] >= minimum - 0.5
    page.set_viewport_size({"width": 320, "height": 640})
    eventually(
        page,
        "() => document.documentElement.scrollWidth"
        " <= document.documentElement.clientWidth",
    )

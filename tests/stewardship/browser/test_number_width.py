"""Every shown number in an Admin table stays on one line (#2342).

main's ``overflow-wrap: anywhere`` broke a seven-digit Member DUID six
digits and one in the Users page's ParishSoft Ministry leaders table, whose
``numeric`` cell only right-aligned. ui-conventions "Numbers on one line"
now covers counts, percentages and numeric identifiers as well as money
(``test_money_width.py``). Each page here squeezes its table: the Users
leaders table with seven-digit Member DUIDs beside a long Name and address
and a leader of fourteen Ministries, and Pause delivery's live mail table
with four-digit counts. At laptop width and at 320 px every number is drawn
on one line, and the page never scrolls sideways (a table too wide scrolls
in its own box). The leaders table's Ministries led cell lists every name,
wrapping only between names (#2342).
"""

import pytest

from .test_charts import assert_clean, watch
from .user_components import LEADERS_WIDE, MANY_MINISTRIES

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Each page and numbers its tables must show: the leaders' Member DUIDs and
# Pause delivery's "1,234" queued and held counts.
PAGES = [
    (LEADERS_WIDE, ["1234567", "7654321"]),
    ("/delivery-pause", ["1,234"]),
]

# Every number of four or more characters ("1,234", "1234567") shown in a
# table, with the number of lines it is drawn on: a Range over the number
# has one client rect per line box it spans. Numbers not drawn (visually
# hidden screen-reader text, a hidden element) are skipped.
NUMBERS = """() => {
  const number = /\\d[\\d,]{3,}/g;
  const found = [];
  for (const table of document.querySelectorAll("main table")) {
    const walker = document.createTreeWalker(table, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      const parent = node.parentElement;
      if (!parent.getClientRects().length
          || parent.closest(".visually-hidden, [hidden]")) continue;
      for (const match of node.data.matchAll(number)) {
        const range = document.createRange();
        range.setStart(node, match.index);
        range.setEnd(node, match.index + match[0].length);
        const tops = new Set([...range.getClientRects()]
          .filter((rect) => rect.width > 0)
          .map((rect) => Math.round(rect.top)));
        found.push({number: match[0], lines: tops.size});
      }
    }
  }
  return found;
}"""

# The Ministries led cell of the many-Ministries leader: its text, how many
# lines each name is drawn on, how many lines the list takes, and the row's
# height beside the one-Ministry row's.
MINISTRIES = """() => {
  const lists = [...document.querySelectorAll("#leaders-table .name-list")];
  const list = lists.find((node) => node.children.length > 1);
  const lineTops = (node) => new Set([...node.getClientRects()]
    .filter((rect) => rect.width > 0)
    .map((rect) => Math.round(rect.top))).size;
  const names = [...list.children];
  const height = (node) => node.closest("tr").getBoundingClientRect().height;
  const other = lists.find((node) => node !== list);
  const box = list.closest(".table-scroll");
  return {
    text: list.innerText,
    nameLines: names.map(lineTops),
    listLines: new Set(
      names.map((node) => Math.round(node.getBoundingClientRect().top))).size,
    rows: [height(list), height(other)],
    boxFits: box.scrollWidth <= box.clientWidth,
    widths: [box.scrollWidth, box.clientWidth, ...[...box.querySelectorAll("thead th")]
      .map((cell) => Math.round(cell.getBoundingClientRect().width))],
  };
}"""


def fits(page):
    """Whether the page itself is no wider than the window."""
    return page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.parametrize("width", [320, 1280])
@pytest.mark.parametrize("path,shown", PAGES)
def test_numbers_stay_on_one_line(page, component_origin, path, shown, width):
    """Each DUID and count is drawn on one line; the page never scrolls."""
    errors = watch(page)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + path)
    found = page.evaluate(NUMBERS)
    assert [item for item in found if item["lines"] != 1] == []
    for number in shown:
        assert number in [item["number"] for item in found], (number, found)
    assert fits(page)
    assert_clean(page, errors)


@pytest.mark.parametrize("width", [320, 1280])
def test_ministries_led_shows_every_name_whole(page, component_origin, width):
    """Every Ministry is listed, none breaks inside its name, and the list
    wraps onto more lines, so its row is taller than a one-Ministry row; at
    laptop width the table fits its box, and the page never scrolls."""
    errors = watch(page)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + LEADERS_WIDE)
    shown = page.evaluate(MINISTRIES)
    assert shown["text"] == ", ".join(MANY_MINISTRIES)
    assert shown["nameLines"] == [1] * len(MANY_MINISTRIES)
    assert shown["listLines"] > 1
    assert shown["rows"][0] > shown["rows"][1]
    if width == 1280:
        assert shown["boxFits"], shown["widths"]
    assert fits(page)
    assert_clean(page, errors)

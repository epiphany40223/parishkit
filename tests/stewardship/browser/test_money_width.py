"""Money in Admin tables stays on one line up to $99,999.99 (#2258).

main's ``overflow-wrap: anywhere`` broke "$3,000.00" in the Family hub's
pledge table when a long Share methods cell squeezed its column. Each page
here puts the longest amount a pledge column must hold, $99,999.99 (or its
whole-dollar form, $99,999, on Financial stewardship), in every money column
of the tables ui-conventions "Numbers and money" covers. At laptop width every
amount in every table is drawn on one line; at 320 px the page itself still
never scrolls sideways (a table that cannot fit scrolls in its own box). The
comparison, past campaigns and dashboard pages already hold seven-figure
totals in their fixtures.
"""

import pytest

from .test_charts import assert_clean, watch

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Each page, the amount its money columns hold, and how many times at least
# that amount is shown in its tables (one per money column the page has).
PAGES = [
    # The Family hub's Financial tab: Baseline, Family's answer, Reviewer's value.
    ("/hub-pledge-wide", "$99,999.99", 3),
    # The review wizard's pledge page draws the same pledge table.
    ("/review-wizard-pledge-wide", "$99,999.99", 3),
    # The Timeline's Details column, on a pledge review line.
    ("/timeline-wide", "$99,999.99", 1),
    # Pledges: Family's answer, Reviewer's value and ParishSoft's amount.
    ("/pledges-wide", "$99,999.99", 3),
    # Financial stewardship: Annual pledge and ParishSoft's two stacked sums,
    # on each of its three rows.
    ("/financial-wide", "$99,999", 9),
    # Smoke checks: tables that never squeeze money (whole-words, nowrap) or
    # whose fixtures already hold seven-figure totals; any amount counts.
    ("/campaign-comparison", None, 1),
    ("/past-campaigns", None, 1),
    ("/response-dashboard", None, 1),
    # The saved daily report's daily table.
    ("/daily-digest", None, 1),
]

# Every amount shown in a table, with the number of lines it is drawn on: a
# Range over the amount has one client rect per line box it spans. Amounts
# not drawn (a closed tab, visually hidden screen-reader text) are skipped.
AMOUNTS = """() => {
  const money = /-?\\$\\d{1,3}(?:,\\d{3})*(?:\\.\\d{2})?/g;
  const found = [];
  for (const table of document.querySelectorAll("main table")) {
    const walker = document.createTreeWalker(table, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      const parent = node.parentElement;
      if (!parent.getClientRects().length
          || parent.closest(".visually-hidden, [hidden]")) continue;
      for (const match of node.data.matchAll(money)) {
        const range = document.createRange();
        range.setStart(node, match.index);
        range.setEnd(node, match.index + match[0].length);
        const tops = new Set([...range.getClientRects()]
          .filter((rect) => rect.width > 0)
          .map((rect) => Math.round(rect.top)));
        found.push({amount: match[0], lines: tops.size});
      }
    }
  }
  return found;
}"""


@pytest.mark.parametrize("path,amount,least", PAGES)
def test_money_stays_on_one_line_at_laptop_width(
    page, component_origin, path, amount, least
):
    """At 1280 px each amount, $99,999.99 included, is drawn on one line."""
    errors = watch(page)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + path)
    found = page.evaluate(AMOUNTS)
    assert [item for item in found if item["lines"] != 1] == []
    shown = [item for item in found if amount is None or item["amount"] == amount]
    assert len(shown) >= least, found
    assert_clean(page, errors)


@pytest.mark.parametrize("path", [path for path, _amount, _least in PAGES[:5]])
def test_wide_money_pages_never_scroll_sideways_on_a_phone(
    page, component_origin, path
):
    """At 320 px the page fits; a table too wide scrolls in its own box."""
    errors = watch(page)
    page.set_viewport_size({"width": 320, "height": 800})
    page.goto(component_origin + path)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert [item for item in page.evaluate(AMOUNTS) if item["lines"] != 1] == []
    assert_clean(page, errors)

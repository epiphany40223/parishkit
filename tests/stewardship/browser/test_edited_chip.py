"""An edited decision shows "Accepted" and the shared Edited chip (#2257).

Every page that shows the decision Accepted (edited) draws it as the word
Accepted followed by the one Edited chip (``components/edited-chip.html``),
never as "(edited)" text: the review tables' Stage cell (Pledges, Census
changes, Ministry requests), the Family hub's Timeline tab and Ministry
follow-up's edited Ministry line. A screen reader hears "Accepted, edited".
The chip is drawn by the server, so nothing moves after the page loads, and
at 320 px the page never scrolls sideways.
"""

import pytest

from .census_components import EDITED_PHONE
from .followup_components import EDITED_ITEM, LEADER_STAGES, STAGES
from .pledges_components import SHARES_EVERY
from .test_charts import assert_clean, watch
from .timeline_components import EDITED as TIMELINE_EDITED

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Each page and the element that holds its edited decision: a review table's
# Stage cell, the Timeline's What happened cell, the follow-up's line.
SURFACES = [
    # Pledges at Every stage, with an Edit of Family 03 To enter by hand.
    (SHARES_EVERY, ".stage-cell"),
    (EDITED_PHONE, ".stage-cell"),
    (STAGES, ".stage-cell"),
    # A leader's accepted request reads by its decision: the stage chip.
    (LEADER_STAGES, ".stage-cell"),
    (TIMELINE_EDITED, ".timeline-table tbody th"),
    (EDITED_ITEM, "main p"),
]

# The chips' boxes, to compare before and after the page settles.
BOXES = """() => [...document.querySelectorAll(".edited-chip")].map((chip) => {
  const box = chip.getBoundingClientRect();
  return [box.x, box.y, box.width, box.height];
})"""


@pytest.mark.parametrize("width", [1280, 320])
@pytest.mark.parametrize("path,holder", SURFACES)
def test_an_edited_decision_shows_accepted_and_the_edited_chip(
    page, component_origin, path, holder, width
):
    """The holder reads "Accepted" with a visible Edited chip, heard as
    "Accepted, edited"; no "(edited)" text; nothing shifts; no page scroll."""
    from playwright.sync_api import expect

    errors = watch(page)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + path)
    cell = page.locator(holder).filter(has=page.locator(".edited-chip")).first
    chip = cell.locator(".edited-chip")
    expect(chip).to_be_visible()
    assert chip.locator('[aria-hidden="true"]').inner_text() == "Edited"
    text = cell.inner_text()
    assert "(edited)" not in text
    # The word drawn just before the chip is Accepted.
    before = chip.evaluate(
        """(chip) => {
          const range = document.createRange();
          range.setStart(chip.closest("td, th, p"), 0);
          range.setEndBefore(chip);
          return range.toString().replace(/[\\s,]+$/, "");
        }"""
    )
    assert before.endswith("Accepted"), before
    # Nowhere on the page is the decision drawn as text (case-sensitive:
    # the lowercase "accepted (edited)." notices stay text).
    assert "Accepted (edited)" not in page.locator("main").inner_text()
    # The screen reader's words: the hidden comma and "edited", not the
    # chip's shown word.
    spoken = cell.evaluate(
        """(cell) => {
          const copy = cell.cloneNode(true);
          copy.querySelectorAll('[aria-hidden="true"]').forEach((e) => e.remove());
          return copy.textContent.replace(/\\s+/g, " ");
        }"""
    )
    assert "Accepted, edited" in spoken
    boxes = page.evaluate(BOXES)
    page.wait_for_timeout(300)
    assert page.evaluate(BOXES) == boxes
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert_clean(page, errors)

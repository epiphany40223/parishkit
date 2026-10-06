"""Button labels never wrap mid-word (#614).

The page-wide ``overflow-wrap: anywhere`` let a narrow table cell squeeze a
one-word button such as Revoke into "Revok" / "e". These tests open pages
with buttons in table cells and forms, and sortable column headings (POST
buttons and GET links), at phone and desktop widths, and check every
visible button and heading control: a line break inside its label falls only
between words, a one-word label stays on one line, and the page never
scrolls sideways.
"""

import pytest

from .automation_components import ACCESS, HOME

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

# Every visible button, link-styled button and submit input: the labels that
# break inside a word, and the one-word labels that take more than one line.
# For a text label each character's line is compared with the previous
# one's: a new line must start after a space. An input's value has no text
# node, so its content height must stay under two line boxes.
BROKEN_LABELS = """() => {
  const broken = [];
  const selector = "button, .button, .link-button, .sort-link, "
    + "input[type=submit], input[type=button]";
  // Only the visible label: screen-reader text and decorative marks are
  // clipped or positioned apart, which would look like a line break.
  const shown = (node) => !node.parentElement.closest(
    ".visually-hidden, [aria-hidden=true]");
  for (const element of document.querySelectorAll(selector)) {
    if (!element.getClientRects().length) continue;
    const style = getComputedStyle(element);
    if (style.visibility === "hidden") continue;
    let raw = element.value || "";
    if (element.tagName !== "INPUT") {
      const texts = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
      raw = "";
      for (let node = texts.nextNode(); node; node = texts.nextNode()) {
        if (shown(node)) raw += node.data;
      }
    }
    const label = raw.trim().replace(/\\s+/g, " ");
    if (!label) continue;
    const oneWord = !label.includes(" ");
    if (element.tagName === "INPUT") {
      const line = parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.3;
      const content = element.clientHeight - parseFloat(style.paddingTop)
        - parseFloat(style.paddingBottom);
      if (oneWord && content >= 2 * line - 1) broken.push(label);
      continue;
    }
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    let top = null, previous = " ", lines = 1;
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (!shown(node)) continue;
      for (let index = 0; index < node.data.length; index++) {
        const character = node.data[index];
        if (/\\s/.test(character)) { previous = " "; continue; }
        const range = document.createRange();
        range.setStart(node, index);
        range.setEnd(node, index + 1);
        const box = range.getBoundingClientRect();
        if (!box.height) continue;
        const here = Math.round(box.top);
        if (top !== null && Math.abs(here - top) > 2) {
          lines += 1;
          if (previous !== " ") broken.push(`${label} (inside a word)`);
        }
        top = here;
        previous = character;
      }
    }
    if (oneWord && lines > 1) broken.push(label);
  }
  return broken;
}"""


@pytest.mark.parametrize("width", [320, 1280])
@pytest.mark.parametrize(
    "path",
    [
        ACCESS,
        HOME,
        "/automation-approval-review",
        "/hosted-files",
        "/delivery-refusals",
        "/background",
        # Sortable headings: POST buttons (talents) and GET links (ministries).
        "/talents-report",
        "/ministries",
        # The data-age lines and the late refresh's run button (#510).
        "/home-data-age",
        "/integration-settings-late",
    ],
)
def test_button_labels_never_wrap_inside_a_word(page, component_origin, path, width):
    """Revoke, Acknowledge and the other buttons keep their words whole."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + path)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.evaluate(BROKEN_LABELS) == []

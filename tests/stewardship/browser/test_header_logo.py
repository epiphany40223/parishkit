"""The header reserves the parish logo's height before the image loads (#638).

The logo is the stored menu variant (``menu_components.LOGO``). Its request
is held until the page has laid out, so the page is first drawn without the
image's size, as on a slow connection. Releasing it must not move the page:
the heading and a menu entry stay where they were.
"""

import pytest

from .menu_components import FAMILY_LOGO_PATH, LOGO, LOGO_PATH
from .waits import eventually

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# The page-relative positions of the header's bottom edge, the heading and,
# on a wide Admin window, one menu entry (the phone's menu is collapsed, and
# the Family portal has none).
POSITIONS = """() => {
  const top = (node) => node.getBoundingClientRect().top + window.scrollY;
  const entry = [...document.querySelectorAll("nav.admin-sidebar a")].find(
    (node) => node.textContent.trim() === "System logs");
  return {
    header: document.querySelector(".site-header").getBoundingClientRect().bottom
      + window.scrollY,
    heading: top(document.querySelector("main h1")),
    entry: entry && entry.checkVisibility() ? top(entry) : null,
  };
}"""
# The logo's drawn height.
LOGO_HEIGHT = (
    "() => document.querySelector('.brand img').getBoundingClientRect().height"
)


@pytest.mark.parametrize("path", [LOGO_PATH, FAMILY_LOGO_PATH], ids=["admin", "family"])
@pytest.mark.parametrize("width", [1280, 390], ids=["wide", "phone"])
def test_a_late_logo_does_not_shift_the_page(page, component_origin, path, width):
    """The page lays out the same before and after a held logo arrives."""
    held = []
    page.route(f"**{LOGO}", lambda route: held.append(route))
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(component_origin + path, wait_until="domcontentloaded")
    eventually(page, "() => document.fonts.status === 'loaded'")
    eventually(page, "() => !document.querySelector('.brand img').complete")
    assert len(held) == 1
    before = page.evaluate(POSITIONS)
    held[0].continue_()
    eventually(page, "() => document.querySelector('.brand img').naturalWidth > 0")
    after = page.evaluate(POSITIONS)
    assert after == before, (before, after)
    assert (after["entry"] is not None) == (path == LOGO_PATH and width > 1000)
    # The logo is drawn at its own size (the square menu variant fills the
    # reserved 4rem), not squeezed into a smaller strip.
    assert page.evaluate(LOGO_HEIGHT) == 64

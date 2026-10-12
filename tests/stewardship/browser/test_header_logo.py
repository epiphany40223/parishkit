"""The header reserves the parish logo's box before the image loads (#638).

The logo is a stored menu variant: the square 128-pixel one
(``menu_components.LOGO``) or a 4:1 wordmark at the widest stored menu size,
512 x 128 (``WIDE_LOGO``, #1062). Its request is held until the page has
laid out, so the page is first drawn without the image, as on a slow
connection. Releasing it must not move the page: the header's bottom edge,
the heading, a menu entry and the logo's own box (so the parish name beside
it) stay where they were.
"""

import pytest

from .menu_components import (
    FAMILY_LOGO_PATH,
    FAMILY_WIDE_PATH,
    LOGO,
    LOGO_PATH,
    WIDE_LOGO,
    WIDE_PATH,
)
from .waits import eventually

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# The page-relative positions of the header's bottom edge, the heading, the
# logo's box and, on a wide Admin window, one menu entry (the phone's menu is
# collapsed, and the Family portal has none).
POSITIONS = """() => {
  const top = (node) => node.getBoundingClientRect().top + window.scrollY;
  const entry = [...document.querySelectorAll("nav.admin-sidebar a")].find(
    (node) => node.textContent.trim() === "System logs");
  const logo = document.querySelector(".brand img").getBoundingClientRect();
  return {
    header: document.querySelector(".site-header").getBoundingClientRect().bottom
      + window.scrollY,
    heading: top(document.querySelector("main h1")),
    logo: [logo.left, logo.top + window.scrollY, logo.width, logo.height],
    entry: entry && entry.checkVisibility() ? top(entry) : null,
  };
}"""

PAGES = {
    "admin": (LOGO_PATH, LOGO),
    "family": (FAMILY_LOGO_PATH, LOGO),
    "admin-wide": (WIDE_PATH, WIDE_LOGO),
    "family-wide": (FAMILY_WIDE_PATH, WIDE_LOGO),
}


@pytest.mark.parametrize("page_name", list(PAGES))
@pytest.mark.parametrize("width", [1280, 390], ids=["wide", "phone"])
def test_a_late_logo_does_not_shift_the_page(page, component_origin, page_name, width):
    """The page lays out the same before and after a held logo arrives."""
    path, logo = PAGES[page_name]
    held = []
    page.route(f"**{logo}", lambda route: held.append(route))
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
    admin = not page_name.startswith("family")
    assert (after["entry"] is not None) == (admin and width > 1000)
    _left, _top, drawn_width, drawn_height = after["logo"]
    if logo == LOGO:
        # The square menu variant fills the reserved 4rem, at its own shape,
        # not squeezed into a smaller strip.
        assert (drawn_width, drawn_height) == (64, 64)
    elif width > 1000:
        # A wordmark is drawn 4rem tall and four times as wide (#1062).
        assert (drawn_width, drawn_height) == (256, 64)
    else:
        # On a phone it takes at most half the width, keeping its shape.
        assert drawn_width <= width / 2
        assert drawn_width == pytest.approx(4 * drawn_height, abs=1)

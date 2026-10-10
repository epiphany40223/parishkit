"""An unknown Admin or Family address shows the styled not-found page (#927).

Before #927 it showed the security middleware's plain-text "Not Found",
in the browser's fixed-width font. The page is
``not_found_components``; the server-side checks are in
``tests/stewardship/test_web_error_pages.py``.
"""

import pytest

from .not_found_components import PAGES
from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)


@pytest.mark.parametrize(
    "fixture,home",
    [
        ("/not-found-admin", "Administration home"),
        ("/not-found-family", "Family portal home"),
    ],
)
def test_unknown_portal_address_shows_the_styled_page(
    page, component_origin, fixture, home
):
    """The portal's layout, a plain-language heading and explanation, a way home."""
    body = page.request.get(component_origin + fixture).text()
    address = PAGES[fixture]
    page.route(
        f"**{address}",
        lambda route: route.fulfill(
            status=404, content_type="text/html; charset=utf-8", body=body
        ),
    )
    response = page.goto(component_origin + address)
    assert response.status == 404
    heading = page.get_by_role("heading", name="Page not found", level=1)
    visible(heading)
    visible(page.get_by_text("There is no page at this address."))
    visible(page.locator(".site-header"))
    visible(page.get_by_role("link", name=home))
    # The portal's own type, not the browser's fixed-width text rendering.
    font = page.evaluate("getComputedStyle(document.body).fontFamily")
    assert "monospace" not in font.lower()
    assert page.locator("pre").count() == 0

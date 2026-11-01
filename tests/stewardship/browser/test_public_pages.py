"""The public information pages and the site footer in a real browser (#2354).

The pages are ``public_pages_components``; the server-side checks (no
sign-in, no cookie, the parish's contact, Google's Limited Use statement) are
in ``tests/stewardship/test_public_pages.py``. Here: each page reads well on a
320px phone and a 1280px laptop, loads nothing from another site, passes the
accessibility scan, and the footer sits below the content on public and Admin
pages alike.
"""

from urllib.parse import urlsplit

import pytest

from .public_pages_components import PAGES
from .test_components import axe_violations
from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)
HEADINGS = {
    "/about/": "About this site",
    "/privacy/": "Privacy policy",
    "/terms/": "Terms of use",
}
FOOTER = ("About this site", "Privacy policy", "Terms of use")


def _footer_below_content(page):
    """The footer's links show, and the footer starts below the content.

    The page is scrolled to its end first, so a footer drawn over the
    content (fixed or sticky) would be caught.
    """
    footer = page.locator("footer.site-footer")
    for name in FOOTER:
        visible(footer.get_by_role("link", name=name, exact=True))
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    main = page.evaluate(
        "document.querySelector('main').getBoundingClientRect().bottom"
    )
    top = page.evaluate(
        "document.querySelector('footer.site-footer').getBoundingClientRect().top"
    )
    assert top >= main - 0.5, (main, top)


@pytest.mark.parametrize("width", [320, 1280])
@pytest.mark.parametrize("path", sorted(PAGES))
def test_each_page_reads_well_and_loads_only_this_site(
    page, component_origin, axe_source, path, width
):
    """Heading, footer, no sideways scroll, no other origin, no scan findings."""
    failures, origins = [], set()
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.on("request", lambda request: origins.add(urlsplit(request.url).netloc))
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(component_origin + path)
    visible(page.get_by_role("heading", name=HEADINGS[path], level=1))
    _footer_below_content(page)
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 0, overflow
    assert origins == {urlsplit(component_origin).netloc}
    assert axe_violations(page, axe_source) == []
    assert not failures


@pytest.mark.parametrize("width", [320, 1280])
def test_an_admin_page_ends_with_the_same_footer(page, component_origin, width):
    """The Admin layout (sidebar, step actions) keeps the footer below it."""
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(component_origin + "/content-settings")
    visible(page.locator("main h1").first)
    _footer_below_content(page)


def test_the_footer_is_one_short_line_on_a_phone(page, component_origin):
    """At 320px the three links fit one line, so the footer stays short."""
    page.set_viewport_size({"width": 320, "height": 800})
    page.goto(component_origin + "/about/")
    links = page.locator("footer.site-footer a")
    tops = {round(links.nth(index).bounding_box()["y"]) for index in range(3)}
    assert len(tops) == 1, tops
    height = page.locator("footer.site-footer").bounding_box()["height"]
    assert height <= 40, height
    target = page.locator("footer.site-footer a").first.bounding_box()["height"]
    assert target >= 24, target

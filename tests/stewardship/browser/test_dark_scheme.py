"""The Admin portal follows the operating system's dark scheme (#1264).

With the browser asking for dark (``prefers-color-scheme``), every Admin page,
including sign-in and a setup step, draws from the dark tokens in
``web/design_tokens.py``: no visible element keeps a light surface or the
light ink, and the page has no colour-contrast violation. The Family portal
stays light. The token values and their contrast are unit-tested in
``tests/stewardship/test_design_tokens.py``; ui-conventions "Colour scheme"
is the rule.
"""

import pytest

from parishkit.stewardship.web.design_tokens import COLORS, DARK_COLORS, hex6

from .system_health_components import PAGE as SYSTEM_HEALTH
from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

# Home, a page full of form controls, sign-in, a setup wizard step, the
# System health dashboard, System logs (a shared table) and the Response
# charts, whose drawn charts stay light cards for now.
ADMIN_PAGES = (
    "/home",
    "/campaign-settings",
    "/login",
    "/setup-shares",
    SYSTEM_HEALTH,
    "/logs",
    "/charts",
)
CONTRAST = """async () => (await axe.run(document, {
    runOnly: {type: 'rule', values: ['color-contrast']}
})).violations.map(({id, nodes}) => ({id, targets: nodes.map(n => n.target)}))"""
# Every visible element's own background and text colour, except the
# parish logo (an image whose white backing is deliberate) and the inside
# of a drawn chart, which stays light until charts get a dark scheme.
COLOURS = """() => [...document.querySelectorAll('body, body *')]
    .filter((el) => el.checkVisibility()
        && !el.closest('.brand img, [data-chart-view]'))
    .map((el) => {
        const style = getComputedStyle(el);
        return [el.tagName + (el.className && typeof el.className === 'string'
            ? '.' + el.className.split(' ').join('.') : ''),
            style.backgroundColor, style.color];
    })"""


def rgb(color):
    """A token's hex value as the rgb() string getComputedStyle reports."""
    digits = hex6(color).lstrip("#")
    red, green, blue = (int(digits[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgb({red}, {green}, {blue})"


def dark_page(page, origin, path):
    """Open ``path`` with the browser asking for the dark scheme."""
    page.emulate_media(color_scheme="dark")
    page.goto(origin + path)
    visible(page.locator("main"))


@pytest.mark.parametrize("path", ADMIN_PAGES)
def test_admin_page_draws_in_the_dark_tokens(page, component_origin, path):
    """The main surfaces take the dark values and no light colour is left."""
    dark_page(page, component_origin, path)
    body = page.locator("body")
    assert body.evaluate("el => getComputedStyle(el).backgroundColor") == rgb(
        DARK_COLORS["page"]
    )
    assert body.evaluate("el => getComputedStyle(el).color") == rgb(DARK_COLORS["ink"])
    assert page.locator(".site-header").evaluate(
        "el => getComputedStyle(el).backgroundColor"
    ) == rgb(DARK_COLORS["paper"])
    assert page.evaluate("getComputedStyle(document.documentElement).colorScheme") == (
        "dark"
    )
    light_surfaces = {rgb(COLORS[name]) for name in ("page", "paper", "paper-soft")}
    light_surfaces.add("rgb(255, 255, 255)")
    light_text = {rgb(COLORS[name]) for name in ("ink", "muted", "link", "accent")}
    leftovers = [
        (element, background, color)
        for element, background, color in page.evaluate(COLOURS)
        if background in light_surfaces or color in light_text
    ]
    assert leftovers == []


@pytest.mark.parametrize("path", ADMIN_PAGES)
def test_admin_page_in_dark_has_no_contrast_violation(
    page, component_origin, axe_source, path
):
    """axe finds no text below WCAG AA contrast in the dark scheme."""
    dark_page(page, component_origin, path)
    page.evaluate(axe_source)
    assert page.evaluate(CONTRAST) == []


def test_admin_page_stays_light_when_the_system_is_light(page, component_origin):
    """The light scheme is unchanged: the light tokens, no dark at all."""
    page.emulate_media(color_scheme="light")
    page.goto(component_origin + "/home")
    assert page.locator("body").evaluate(
        "el => getComputedStyle(el).backgroundColor"
    ) == rgb(COLORS["page"])


def test_family_page_stays_light_in_the_dark_scheme(page, component_origin):
    """The Family portal is not part of the dark scheme (Admin only)."""
    page.emulate_media(color_scheme="dark")
    page.goto(component_origin + "/family-login")
    visible(page.locator("main"))
    body = page.locator("body")
    assert body.evaluate("el => getComputedStyle(el).backgroundColor") == rgb(
        COLORS["page"]
    )
    assert body.evaluate("el => getComputedStyle(el).color") == rgb(COLORS["ink"])


def test_printed_admin_page_stays_light(page, component_origin):
    """The dark scheme is for screens; a printed Admin page is light."""
    page.emulate_media(color_scheme="dark", media="print")
    page.goto(component_origin + "/home")
    assert (
        page.evaluate(
            "getComputedStyle(document.documentElement).getPropertyValue('--page').trim()"
        )
        == COLORS["page"]
    )

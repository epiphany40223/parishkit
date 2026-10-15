"""The Admin portal's light and dark schemes (#1264).

With the browser asking for dark (``prefers-color-scheme``) and no Appearance
choice saved, every Admin page,
including sign-in and a setup step, draws from the dark tokens in
``web/design_tokens.py``: no visible element keeps a light surface or the
light ink, and the page has no colour-contrast violation. The Family portal
stays light, and so does an email sample preview (a light card). The
sidebar's Appearance choice overrides the device in place, is remembered by
the browser, applies before the body is parsed and reaches other tabs. The
token values and their contrast are unit-tested in
``tests/stewardship/test_design_tokens.py``; ui-conventions "Colour scheme"
is the rule.
"""

import pytest

from parishkit.stewardship.web.design_tokens import COLORS, DARK_COLORS, hex6

from .system_health_components import PAGE as SYSTEM_HEALTH
from .test_editor_mode import basic_from_saved, toggle
from .waits import eventually, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

# Home, a page full of form controls, sign-in, a setup wizard step, the
# System health dashboard, System logs (a shared table) and the Response
# charts, whose drawn charts stay light cards for now; then the two email
# sample previews and the new campaign's copy preview.
ADMIN_PAGES = (
    "/home",
    "/campaign-settings",
    "/login",
    "/setup-mail",
    SYSTEM_HEALTH,
    "/logs",
    "/charts",
)
PREVIEWS = ("/clone-preview",)

CONTRAST = """async () => (await axe.run(document, {
    runOnly: {type: 'rule', values: ['color-contrast']}
})).violations.map(({id, nodes}) => ({id, targets: nodes.map(n => n.target)}))"""
# Every visible element's own background and text colour, except the
# parish logo (an image whose white backing is deliberate), the inside of a
# drawn chart, which stays light until charts get a dark scheme, and an email
# sample preview's light card.
COLOURS = """() => [...document.querySelectorAll('body, body *')]
    .filter((el) => el.checkVisibility()
        && !el.closest('.brand img, [data-chart-view], .light-card'))
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


@pytest.mark.parametrize("path", ADMIN_PAGES + PREVIEWS)
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


@pytest.mark.parametrize("path", ADMIN_PAGES + PREVIEWS)
def test_admin_page_in_dark_has_no_contrast_violation(
    page, component_origin, axe_source, path
):
    """axe finds no text below WCAG AA contrast in the dark scheme."""
    dark_page(page, component_origin, path)
    page.evaluate(axe_source)
    assert page.evaluate(CONTRAST) == []


@pytest.mark.parametrize("path", PREVIEWS)
def test_email_sample_preview_stays_a_light_card(page, component_origin, path):
    """An email preview shows the email as sent: light paper and ink."""
    dark_page(page, component_origin, path)
    cards = page.locator(".light-card")
    assert cards.count() > 0
    for card in cards.all():
        visible(card)
        assert card.evaluate("el => getComputedStyle(el).backgroundColor") == rgb(
            COLORS["paper"]
        )
        assert card.evaluate("el => getComputedStyle(el).color") == rgb(COLORS["ink"])


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_focus_ring_on_the_checked_mode_segment_is_paper(
    page, component_origin, scheme
):
    """The ring inside the checked (accent) Basic segment is drawn in --paper,
    which keeps 3:1 on --accent; orange --focus would not (#1264)."""
    page.emulate_media(color_scheme=scheme)
    basic_from_saved(page, component_origin)
    # The component page is not built from admin-base.html; mark it as one,
    # in the scheme admin-gate-v1.js would choose.
    page.evaluate(
        """(scheme) => {
            document.documentElement.classList.add('admin-document');
            document.documentElement.dataset.theme = scheme;
        }""",
        scheme,
    )
    checked = toggle(page, "Basic")
    page.keyboard.press("Tab")  # A keyboard user: focus-visible.
    checked.focus()
    colors = DARK_COLORS if scheme == "dark" else COLORS
    assert checked.evaluate(
        "el => getComputedStyle(el.parentElement).outlineColor"
    ) == rgb(colors["paper"])


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


# Records, when the body element first appears, the scheme admin-gate-v1.js
# set: a page in the saved scheme from the start never flashes the other.
# Also records the Appearance select's value when the main content starts,
# which admin-menu-state-v1.js sets right after the sidebar is parsed.
AT_BODY = """new MutationObserver((records, observer) => {
    if (document.body && !window.__themeAtBody) {
        window.__themeAtBody = document.documentElement.dataset.theme || 'none';
    }
    if (!document.querySelector('main')) return;
    const select = document.querySelector('select[data-appearance]');
    window.__choiceAtMain = select ? select.value : 'none';
    observer.disconnect();
}).observe(document, {childList: true, subtree: true});"""
# A browser that refuses site storage: every Storage call throws.
NO_STORAGE = """for (const name of ['getItem', 'setItem', 'removeItem']) {
    Storage.prototype[name] = () => {
        throw new DOMException('denied', 'SecurityError');
    };
}"""
SAVED = "() => localStorage.getItem('pk-admin-appearance')"


def page_colour(page):
    """The body's background as getComputedStyle reports it."""
    return page.locator("body").evaluate("el => getComputedStyle(el).backgroundColor")


def choose(page, value):
    """Pick an Appearance option; the page must not reload or move it."""
    select = page.get_by_label("Appearance")
    visible(select)
    page.evaluate("window.__samePage = true")
    before = select.bounding_box()
    select.select_option(value)
    assert page.evaluate("window.__samePage") is True
    assert select.bounding_box() == before


@pytest.mark.parametrize(
    ("device", "chosen", "colors"),
    [("light", "dark", DARK_COLORS), ("dark", "light", COLORS)],
)
def test_appearance_choice_overrides_the_device_in_place_and_is_remembered(
    page, component_origin, axe_source, device, chosen, colors
):
    """Dark on a light device, or Light on a dark one, applies at once, stays
    across a reload from the very first paint, and reaches the sign-in page,
    which has no sidebar."""
    page.emulate_media(color_scheme=device)
    page.add_init_script(AT_BODY)
    page.goto(component_origin + "/home")
    assert page.get_by_label("Appearance").input_value() == "auto"
    choose(page, chosen)
    assert page_colour(page) == rgb(colors["page"])
    assert page.evaluate("getComputedStyle(document.documentElement).colorScheme") == (
        chosen
    )
    assert page.evaluate(SAVED) == chosen
    page.evaluate(axe_source)
    assert page.evaluate(CONTRAST) == []
    for path in ("/home", "/login"):
        page.goto(component_origin + path)
        visible(page.locator("main"))
        assert page.evaluate("window.__themeAtBody") == chosen
        if path == "/home":
            assert page.evaluate("window.__choiceAtMain") == chosen
        assert page_colour(page) == rgb(colors["page"])
    page.goto(component_origin + "/home")
    assert page.get_by_label("Appearance").input_value() == chosen


def test_match_this_device_follows_a_live_device_change(page, component_origin):
    """With nothing saved, the page follows the device as it changes."""
    page.emulate_media(color_scheme="light")
    page.goto(component_origin + "/home")
    choose(page, "dark")
    choose(page, "auto")
    assert page.evaluate(SAVED) is None
    assert page_colour(page) == rgb(COLORS["page"])
    page.emulate_media(color_scheme="dark")
    eventually(page, "document.documentElement.dataset.theme", "dark")
    assert page_colour(page) == rgb(DARK_COLORS["page"])
    page.emulate_media(color_scheme="light")
    eventually(page, "document.documentElement.dataset.theme", "light")


def test_another_open_tab_follows_a_change(page, component_origin):
    """A choice made in one Admin tab reaches the others without a reload."""
    page.emulate_media(color_scheme="light")
    page.goto(component_origin + "/home")
    other = page.context.new_page()
    other.emulate_media(color_scheme="light")
    other.goto(component_origin + "/logs")
    visible(other.locator("main"))
    choose(page, "dark")
    eventually(other, "document.documentElement.dataset.theme", "dark")
    assert page_colour(other) == rgb(DARK_COLORS["page"])


def test_saved_dark_leaves_family_pages_and_print_light(page, component_origin):
    """The Family portal shares the browser's storage but never the scheme,
    and a printed Admin page stays light whatever was chosen."""
    page.emulate_media(color_scheme="light")
    page.goto(component_origin + "/home")
    choose(page, "dark")
    page.goto(component_origin + "/family-login")
    visible(page.locator("main"))
    assert page_colour(page) == rgb(COLORS["page"])
    page.emulate_media(color_scheme="light", media="print")
    page.goto(component_origin + "/home")
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    assert (
        page.evaluate(
            "getComputedStyle(document.documentElement).getPropertyValue('--page').trim()"
        )
        == COLORS["page"]
    )


def test_refused_storage_keeps_the_choice_for_this_page_only(page, component_origin):
    """Without storage the choice still applies in place, with no script
    error, and the next page follows the device again."""
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.add_init_script(NO_STORAGE)
    page.emulate_media(color_scheme="light")
    page.goto(component_origin + "/home")
    choose(page, "dark")
    assert page_colour(page) == rgb(DARK_COLORS["page"])
    # The OS changing does not undo this page's explicit choice.
    page.emulate_media(color_scheme="dark")
    page.emulate_media(color_scheme="light")
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    page.reload()
    visible(page.locator("main"))
    assert page_colour(page) == rgb(COLORS["page"])
    assert page.get_by_label("Appearance").input_value() == "auto"
    assert errors == []


def test_back_to_a_page_shows_the_latest_choice(page, component_origin):
    """Going back (from the back/forward cache or not) shows the choice made
    on the later page, in the scheme and in the select."""
    page.emulate_media(color_scheme="light")
    page.goto(component_origin + "/home")
    choose(page, "dark")
    page.goto(component_origin + "/logs")
    choose(page, "light")
    page.go_back()
    visible(page.locator("main"))
    eventually(page, "document.documentElement.dataset.theme", "light")
    assert page_colour(page) == rgb(COLORS["page"])
    assert page.get_by_label("Appearance").input_value() == "light"

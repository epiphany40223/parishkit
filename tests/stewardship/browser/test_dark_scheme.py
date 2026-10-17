"""The Admin portal's light and dark schemes (#1264).

With the browser asking for dark (``prefers-color-scheme``) and no Appearance
choice saved, every Admin page,
including sign-in and a setup step, draws from the dark tokens in
``web/design_tokens.py``: no visible element keeps a light surface or the
light ink, and the page has no colour-contrast violation. The Family portal
stays light, and so does an email sample preview (a light card). The
header's Appearance switch overrides the device in place, is remembered by
the browser, applies before the body is parsed and reaches other tabs; it
sits at the header's top right, on a phone too, and works from the keyboard. The
token values and their contrast are unit-tested in
``tests/stewardship/test_design_tokens.py``; ui-conventions "Colour scheme"
is the rule.
"""

import pytest

from parishkit.stewardship.web.design_tokens import COLORS, DARK_COLORS, hex6

from .artwork_components import PAGE as ARTWORK
from .keyboard import tab_to_next, tab_to_previous
from .system_health_components import PAGE as SYSTEM_HEALTH
from .test_editor_mode import basic_from_saved, toggle
from .waits import eventually, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

# Home, a page full of form controls, sign-in, a setup wizard step, the
# System health dashboard, System logs (a shared table) and the Response
# charts (test_chart_scheme.py checks every chart page's drawing); then the
# two email sample previews and the new campaign's copy preview.
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
# Every visible element's own background and text colour, except an
# uploaded image (whose white backing is deliberate, see IMAGE_PREVIEWS) and
# an email sample preview's light card.
COLOURS = """() => [...document.querySelectorAll('body, body *')]
    .filter((el) => el.checkVisibility()
        && !el.closest('.brand img, .branding-preview, .hosted-file-thumbnail, '
            + '.light-card'))
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
# Also records, as soon as the main content starts (long before the radios
# are checked at DOMContentLoaded), which Appearance segment is drawn as
# selected: the only one with a background.
AT_BODY = """new MutationObserver((records, observer) => {
    if (document.body && !window.__themeAtBody) {
        window.__themeAtBody = document.documentElement.dataset.theme || 'none';
    }
    if (!document.querySelector('main')) return;
    const drawn = [...document.querySelectorAll('.appearance-switch label')]
        .filter((el) => getComputedStyle(el).backgroundColor !== 'rgba(0, 0, 0, 0)');
    window.__choiceAtMain = drawn.map((el) => el.dataset.choice).join() || 'none';
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


NAMES = {"light": "Light", "dark": "Dark", "auto": "Match this device"}


def switch(page):
    """The header's Appearance radio group."""
    return page.get_by_role("radiogroup", name="Appearance")


def checked(page):
    """The value of the checked Appearance radio, or None."""
    return page.evaluate(
        "document.querySelector('input[name=pk-appearance]:checked')?.value ?? null"
    )


def choose(page, value):
    """Click an Appearance icon; the page must not reload or move anything."""
    group = switch(page)
    visible(group)
    page.evaluate("window.__samePage = true")
    before = group.bounding_box()
    main = page.locator("main").bounding_box()
    group.get_by_role("radio", name=NAMES[value]).click()
    assert page.evaluate("window.__samePage") is True
    assert group.bounding_box() == before
    assert page.locator("main").bounding_box() == main
    assert checked(page) == value


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
    assert checked(page) == "auto"
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
        assert page.evaluate("window.__choiceAtMain") == chosen
        assert page_colour(page) == rgb(colors["page"])
        assert checked(page) == chosen


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
    assert checked(page) == "auto"
    assert errors == []


def test_back_to_a_page_shows_the_latest_choice(page, component_origin):
    """Going back (from the back/forward cache or not) shows the choice made
    on the later page, in the scheme and in the switch."""
    page.emulate_media(color_scheme="light")
    page.goto(component_origin + "/home")
    choose(page, "dark")
    page.goto(component_origin + "/logs")
    choose(page, "light")
    page.go_back()
    visible(page.locator("main"))
    eventually(page, "document.documentElement.dataset.theme", "light")
    assert page_colour(page) == rgb(COLORS["page"])
    assert checked(page) == "light"


def test_appearance_switch_works_from_the_keyboard(page, component_origin):
    """One Tab stop whose focused segment shows the focus ring; the arrow
    keys move the choice in place; each icon's tooltip is its name."""
    page.emulate_media(color_scheme="light")
    page.goto(component_origin + "/login")
    group = switch(page)
    visible(group)
    for value, name in NAMES.items():
        radio = group.get_by_role("radio", name=name)
        assert radio.evaluate("el => el.parentElement.title") == name
        assert radio.evaluate("el => el.value") == value
    # From the skip link, Tab reaches the checked radio, and the next Tab
    # leaves the group: the switch is one stop.
    page.locator(".skip-link").focus()
    tab_to_next(page)
    assert page.evaluate("document.activeElement.value") == "auto"
    tab_to_next(page)
    assert page.evaluate("document.activeElement.name") != "pk-appearance"
    tab_to_previous(page)
    assert page.evaluate("document.activeElement.value") == "auto"
    ring = page.locator("label[data-choice=auto]").evaluate(
        "el => [getComputedStyle(el).outlineStyle, getComputedStyle(el).outlineWidth]"
    )
    assert ring == ["solid", "3px"]
    page.keyboard.press("ArrowLeft")
    assert checked(page) == "dark"
    assert page.evaluate("document.activeElement.value") == "dark"
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    assert page.evaluate(SAVED) == "dark"


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_appearance_switch_is_accessible_in_both_schemes(
    page, component_origin, axe_source, scheme
):
    """axe finds nothing in the switch, and each segment is at least 32px."""
    page.emulate_media(color_scheme=scheme)
    page.goto(component_origin + "/home")
    visible(switch(page))
    for label in page.locator(".appearance-switch label").all():
        box = label.bounding_box()
        assert box["width"] >= 32 and box["height"] >= 32
    page.evaluate(axe_source)
    violations = page.evaluate(
        """async () => (await axe.run({include: [['.appearance-switch']]}))
            .violations.map(({id}) => id)"""
    )
    assert violations == []


# The switch's box and those of the header's other items, the Menu button
# and the main heading, plus the page's horizontal overflow.
GEOMETRY = """() => {
    const box = (el) => el && el.checkVisibility()
        ? el.getBoundingClientRect().toJSON() : null;
    const header = document.querySelector('.site-header');
    return {
        width: document.documentElement.clientWidth,
        scroll: document.documentElement.scrollWidth,
        header: box(header),
        switch: box(document.querySelector('.appearance-switch')),
        others: [...header.children]
            .filter((el) => !el.matches('.appearance-switch'))
            .map(box).filter(Boolean),
        menu: box(document.querySelector('.admin-menu > summary')),
        heading: box(document.querySelector('h1')),
    };
}"""


def overlaps(one, other):
    """Whether two boxes share any area."""
    return not (
        one["right"] <= other["left"]
        or other["right"] <= one["left"]
        or one["bottom"] <= other["top"]
        or other["bottom"] <= one["top"]
    )


@pytest.mark.parametrize("width", [320, 390, 1280])
@pytest.mark.parametrize("path", ["/home", "/login", "/find-family"])
def test_appearance_switch_sits_at_the_top_right(page, component_origin, path, width):
    """At phone and laptop widths the switch is on the header's first row at
    its right edge, overlaps nothing, and the page never scrolls sideways."""
    page.set_viewport_size({"width": width, "height": 700})
    page.goto(component_origin + path)
    visible(switch(page))
    shape = page.evaluate(GEOMETRY)
    header, box = shape["header"], shape["switch"]
    assert shape["scroll"] <= shape["width"]
    assert box["top"] - header["top"] < 40
    # As far in from the right edge as the parish name is from the left.
    inset = shape["others"][0]["left"] - header["left"]
    assert abs((header["right"] - box["right"]) - inset) <= 1
    assert all(not overlaps(box, other) for other in shape["others"])
    # Everything else in the header ends left of the switch or starts below it.
    assert all(
        other["right"] <= box["left"] or other["top"] >= box["bottom"]
        for other in shape["others"]
    )
    for other in (shape["menu"], shape["heading"]):
        if other:
            assert not overlaps(box, other)


# Every Admin page that previews an uploaded image, and its images (#1264
# slice 4). The parish logo in the header is covered by the same rule.
IMAGE_PREVIEWS = {
    "/branding-settings": "img.branding-preview",
    "/setup-branding-saved": ".branding-preview img",
    ARTWORK: "img.branding-preview",
    "/hosted-files": ".hosted-file-thumbnail",
}
# Each matching image's background and box.
BACKINGS = """(selector) => [...document.querySelectorAll(selector)].map((img) => {
    const box = img.getBoundingClientRect();
    return [getComputedStyle(img).backgroundColor, box.x, box.y, box.width,
        box.height];
})"""


@pytest.mark.parametrize("path", sorted(IMAGE_PREVIEWS))
def test_uploaded_images_get_a_white_backing_in_dark(page, component_origin, path):
    """An uploaded image was drawn for the light Family pages and emails, so
    in dark it sits on white, which keeps a dark logo on a transparent
    background visible. Light is unchanged (no backing), and switching in
    place moves no image and raises no page error."""
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.emulate_media(color_scheme="light")
    page.goto(component_origin + path)
    visible(page.locator("main"))
    eventually(page, "() => [...document.images].every((img) => img.complete)")
    light = page.evaluate(BACKINGS, IMAGE_PREVIEWS[path])
    assert light and {background for background, *_ in light} == {"rgba(0, 0, 0, 0)"}
    choose(page, "dark")
    dark = page.evaluate(BACKINGS, IMAGE_PREVIEWS[path])
    assert {background for background, *_ in dark} == {"rgb(255, 255, 255)"}
    assert [box for _, *box in dark] == [box for _, *box in light]
    assert errors == []

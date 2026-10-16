"""The portal stylesheet and email components share one set of tokens (#732)."""

import re
from pathlib import Path

import pytest

from parishkit.stewardship.web import design_tokens as tokens
from parishkit.stewardship.web.buttons import email_button
from parishkit.stewardship.web.report_markup import button as report_button

STATIC = (
    Path(__file__).parents[2] / "src/parishkit/stewardship/accounts/static/stewardship"
)
ROOT_BLOCK = re.compile(r":root, \.light-card\s*{(.*?)}", re.S)
DECLARATION = re.compile(r"--([a-z][a-z0-9-]*):\s*([^;]+);")
COMMENT = re.compile(r"/\*.*?\*/", re.S)


def root_properties():
    """The custom properties ui-v1.css declares on :root."""
    css = COMMENT.sub("", (STATIC / "ui-v1.css").read_text())
    return dict(DECLARATION.findall(ROOT_BLOCK.search(css)[1]))


def test_stylesheet_declares_exactly_the_module_values():
    """Every token is declared on :root with the module's value."""
    declared = root_properties()
    assert {name: declared.get(name) for name in tokens.CSS_PROPERTIES} == (
        tokens.CSS_PROPERTIES
    )
    # The body font shorthand starts from the shared sans stack.
    css = (STATIC / "ui-v1.css").read_text()
    assert f"font: 100%/1.6 {tokens.SANS};" in css


DARK_BLOCK = re.compile(
    r'@media screen {\s*html\.admin-document\[data-theme="dark"\]\s*{(.*?)}',
    re.S,
)


def dark_properties():
    """The custom properties ui-v1.css redefines for the Admin dark scheme."""
    css = COMMENT.sub("", (STATIC / "ui-v1.css").read_text())
    return dict(DECLARATION.findall(DARK_BLOCK.search(css)[1]))


def test_dark_scheme_declares_exactly_the_module_values():
    """The dark block redefines every colour token, and only colours (#1264)."""
    assert set(tokens.DARK_COLORS) == set(tokens.COLORS)
    assert dark_properties() == tokens.DARK_COLORS
    # The dark scheme applies only to screens and only to Admin pages, and
    # only through data-theme: admin-gate-v1.js reads the Appearance choice
    # and the operating system's setting, so the stylesheet never does.
    css = (STATIC / "ui-v1.css").read_text()
    assert "prefers-color-scheme" not in COMMENT.sub("", css)
    assert "color-scheme: dark;" in DARK_BLOCK.search(css)[1]


def test_only_the_portal_stylesheet_reads_the_dark_scheme():
    """Email and downloaded files stay light: no module but the tokens' own
    names DARK_COLORS (ui-conventions "Colour scheme")."""
    source = Path(__file__).parents[2] / "src/parishkit"
    readers = sorted(
        path.relative_to(source).as_posix()
        for path in source.rglob("*.py")
        if "DARK_COLORS" in path.read_text()
    )
    assert readers == ["stewardship/web/design_tokens.py"]


@pytest.mark.parametrize(
    "name,colors",
    [
        ("select-arrow-v1.svg", tokens.COLORS),
        ("select-arrow-dark-v1.svg", tokens.DARK_COLORS),
    ],
)
def test_each_select_arrow_draws_in_its_schemes_ink(name, colors):
    """The select arrow is an image, so each scheme has its own in --ink."""
    svg = (STATIC / name).read_text()
    assert re.findall(r'stroke="(#[0-9a-f]+)"', svg) == [colors["ink"]]


def test_only_layout_properties_live_outside_the_module():
    """A new colour or shape token belongs in design_tokens.py first."""
    assert set(root_properties()) - set(tokens.CSS_PROPERTIES) == {"gutter"}


@pytest.mark.parametrize("name", sorted(p.name for p in STATIC.glob("*.css")))
def test_custom_property_fallbacks_match(name):
    """A var(--token, fallback) in any stylesheet repeats the token's value."""
    css = (STATIC / name).read_text()
    for prop, fallback in re.findall(r"var\(--([a-z0-9-]+),\s*([^()]+?)\)", css):
        if prop in tokens.CSS_PROPERTIES:
            assert fallback.strip() == tokens.CSS_PROPERTIES[prop], (name, prop)


# Every other stylesheet loads after ui-v1.css and so reads its tokens
# directly. chart-v1.css is exempt: its vega-tooltip rules are vendored as
# published.
TOKEN_ONLY = sorted(
    p.name for p in STATIC.glob("*.css") if p.name not in {"ui-v1.css", "chart-v1.css"}
)


@pytest.mark.parametrize("name", TOKEN_ONLY)
def test_page_stylesheets_take_colours_only_from_the_tokens(name):
    """No hex colour and no var() fallback outside ui-v1.css (#732), so a
    token changes in one place. Every page loads ui-v1.css first."""
    css = COMMENT.sub("", (STATIC / name).read_text())
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", css)
    assert not re.findall(r"var\(--[a-z0-9-]+\s*,", css)
    # Every token the stylesheet reads exists.
    assert set(re.findall(r"var\(--([a-z0-9-]+)\)", css)) <= set(root_properties())


def test_lengths_and_colours_convert_for_email():
    """rem lengths become whole pixels; short hex becomes six digits."""
    assert tokens.px(".5rem") == 8 and tokens.px("2px") == 2
    assert tokens.px(".6rem") == 10 and tokens.px("1.25rem") == 20
    assert tokens.hex6("#fff") == "#ffffff" and tokens.hex6("#115E56") == "#115e56"
    with pytest.raises(ValueError):
        tokens.px("1em")


def test_email_button_is_bulletproof_and_uses_the_tokens():
    """One presentation cell carries colour and shape; the label is real text."""
    html = email_button("Open <report>", "https://example.org/a?b=1&c=2")
    assert html.startswith('<table role="presentation" cellpadding="0"')
    assert 'bgcolor="#115e56"' in html
    assert "background-color:#115e56;border:2px solid #115e56;" in html
    assert "border-radius:8px;mso-padding-alt:10px 20px;" in html
    assert 'href="https://example.org/a?b=1&amp;c=2"' in html
    assert "padding:10px 20px;color:#ffffff;" in html
    assert "font-family:Arial, Helvetica, sans-serif;" in html
    # 2px border + 10px padding on each side + a 20px line = 44px tall.
    assert "line-height:20px;" in html
    assert 'rel="noopener noreferrer">Open &lt;report&gt;</a>' in html
    assert "<tbody><tr><td " in html and html.endswith("</tbody></table>")
    assert "<img" not in html and "<style" not in html and "class=" not in html


def test_secondary_email_button_inverts_the_colours():
    """The secondary button is accent text on paper with an accent border."""
    html = email_button("Back", "https://example.org/", variant="secondary")
    assert 'bgcolor="#ffffff"' in html and "color:#115e56;" in html
    assert "border:2px solid #115e56;" in html


@pytest.mark.parametrize(
    "href,variant",
    [
        ("javascript:alert(1)", "primary"),
        ("/relative", "primary"),
        ("https:foo", "primary"),
        ("http:///x", "primary"),
        ("https://example.org/", "link"),
    ],
)
def test_email_button_refuses_unsafe_links_and_unknown_variants(href, variant):
    """Only absolute http(s) links and the two email variants are allowed."""
    with pytest.raises(ValueError):
        email_button("x", href, variant=variant)


def test_no_token_value_contains_a_quote():
    """Email inlines these values into style="…" attributes, so none may hold
    a quote. (The portal's serif and sans stacks quote family names; email
    never inlines them.)"""
    values = [
        *tokens.COLORS.values(),
        *tokens.RADII.values(),
        *tokens.BUTTON.values(),
        tokens.EMAIL_SANS,
    ]
    assert not [value for value in values if '"' in value or "'" in value]


def test_report_emails_draw_the_shared_email_button():
    """The daily and weekly reports' button is email_button(), not a copy."""
    url = "https://example.org/reports/1?a=1&b=2"
    assert report_button(url, "Open <it>") == email_button("Open <it>", url)


def test_only_the_shared_component_builds_email_buttons():
    """Each email builder renders buttons through email_button() (#732).

    Outlook's ``mso-padding-alt`` marks a hand-built email button; only the
    shared component may spell it. ``report_markup`` also holds the frozen
    pre-#732 button styles, which it only admits for retained reports.
    """
    source = Path(__file__).parents[2] / "src/parishkit"
    builders = sorted(
        path.relative_to(source).as_posix()
        for path in source.rglob("*.py")
        if "mso-padding-alt" in path.read_text()
    )
    assert builders == [
        "stewardship/web/buttons.py",
        "stewardship/web/report_markup.py",
    ]


# The status indicators' looks (ui-conventions "Status indicators", #1214):
# each state's text, its icon and border, and its background, as the
# .status-* classes in ui-v1.css set them.
STATUS_LOOKS = {
    "working": ("ink", "accent", "accent-soft"),
    "warning": ("warning-ink", "warning-border", "warning-bg"),
    "error": ("error-ink", "error-border", "error-bg"),
    "unknown": ("muted", "control-border", "paper-soft"),
}


SCHEMES = {"light": tokens.COLORS, "dark": tokens.DARK_COLORS}


def contrast(first, second, colors=tokens.COLORS):
    """The WCAG contrast ratio of two colour tokens' values in one scheme."""

    def luminance(color):
        """Relative luminance of a hex colour."""
        digits = tokens.hex6(color).lstrip("#")
        channels = [int(digits[i : i + 2], 16) / 255 for i in (0, 2, 4)]
        linear = [
            c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
            for c in channels
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    high, low = sorted(
        (luminance(colors[first]), luminance(colors[second])),
        reverse=True,
    )
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize("scheme", sorted(SCHEMES))
@pytest.mark.parametrize("state", sorted(STATUS_LOOKS))
def test_each_status_look_meets_the_contrast_minimums(state, scheme):
    """Text meets 4.5:1 and the icon and border 3:1 on the state's background,
    in the light and the dark scheme."""
    text, line, background = STATUS_LOOKS[state]
    assert contrast(text, background, SCHEMES[scheme]) >= 4.5
    assert contrast(line, background, SCHEMES[scheme]) >= 3


# The text tokens and the surfaces they are drawn on across the portal, and
# the non-text marks (control borders, the focus ring, level icons) with the
# surfaces they sit on. --paper is also the label colour on the accent
# buttons. --muted is not drawn on --selection or --row-hover alone.
TEXT_ON = {
    "ink": ("page", "paper", "paper-soft", "accent-soft", "row-hover", "selection"),
    "muted": ("page", "paper", "paper-soft", "accent-soft", "row-hover"),
    "link": ("page", "paper", "paper-soft", "accent-soft", "row-hover"),
    "accent": ("page", "paper", "paper-soft"),
    "accent-strong": ("page", "paper", "paper-soft", "accent-soft"),
    "unavailable": ("page", "paper", "paper-soft"),
    "paper": ("accent", "accent-strong", "ink"),
    "warning-ink": ("warning-bg", "paper"),
    "error-ink": ("error-bg", "paper"),
}
MARKS_ON = {
    "control-border": ("page", "paper", "paper-soft"),
    "focus": ("page", "paper", "paper-soft"),
    "accent": ("accent-soft",),
    "level-info": ("paper", "paper-soft"),
    "level-critical": ("paper", "paper-soft"),
    "level-audit": ("paper", "paper-soft"),
    "warning-border": ("paper", "paper-soft"),
    "error-border": ("paper", "paper-soft"),
    # The focus ring on the checked Basic/Advanced segment is --paper.
    "paper": ("accent",),
}


@pytest.mark.parametrize("scheme", sorted(SCHEMES))
def test_text_and_marks_meet_the_contrast_minimums(scheme):
    """Every text pair meets 4.5:1 and every mark 3:1 in both schemes (#1264)."""
    colors = SCHEMES[scheme]
    low = [
        (text, surface)
        for text, surfaces in TEXT_ON.items()
        for surface in surfaces
        if contrast(text, surface, colors) < 4.5
    ] + [
        (mark, surface)
        for mark, surfaces in MARKS_ON.items()
        for surface in surfaces
        if contrast(mark, surface, colors) < 3
    ]
    assert low == []


@pytest.mark.parametrize("state", sorted(STATUS_LOOKS))
def test_the_stylesheet_gives_each_state_its_tokens(state):
    """Each .status-* class reads exactly the tokens the contrast test checks."""
    css = (STATIC / "ui-v1.css").read_text()
    rule = re.search(rf"\.status-{state} {{([^}}]*)}}", css)[1]
    text, line, background = STATUS_LOOKS[state]
    assert f"--status-ink: var(--{text});" in rule
    assert f"--status-line: var(--{line});" in rule
    assert f"--status-bg: var(--{background});" in rule


def test_level_icons_take_their_colours_from_tokens():
    """No level icon has a fixed colour: not its class and not its marks."""
    from django.template.loader import render_to_string

    css = (STATIC / "ui-v1.css").read_text()
    for rule in re.findall(r"\.level-icon-[a-z]+ {[^}]*}", css):
        assert "#" not in rule, rule
    for level in ("ok", "info", "warning", "error", "critical", "unknown", "audit"):
        icon = render_to_string(
            "stewardship/components/level-icon.html", {"level": level}
        )
        colours = set(re.findall(r'(?:fill|stroke)="([^"]+)"', icon))
        assert colours <= {"currentColor", "none"}, (level, colours)


# The notice kinds (ui-conventions "Shared visual style", #732): each one's
# class, its background and its border. Every notice's text is --ink.
NOTICE_LOOKS = {
    "notice": ("warning-bg", "warning-border"),
    "notice-error": ("error-bg", "error-border"),
    "notice-info": ("accent-soft", "accent"),
}


@pytest.mark.parametrize("kind", sorted(NOTICE_LOOKS))
def test_each_notice_kind_reads_its_tokens(kind):
    """Each notice class sets exactly the background and border tokens listed."""
    css = (STATIC / "ui-v1.css").read_text()
    rule = re.search(rf"^\.{kind} {{([^}}]*)}}", css, re.M)[1]
    background, border = NOTICE_LOOKS[kind]
    assert f"background: var(--{background});" in rule
    assert re.search(rf"border(?:-color)?: [^;]*var\(--{border}\);", rule)


@pytest.mark.parametrize("scheme", sorted(SCHEMES))
@pytest.mark.parametrize("kind", sorted(NOTICE_LOOKS))
def test_each_notice_kind_meets_the_contrast_minimums(kind, scheme):
    """A notice's ink text meets 4.5:1 and its border 3:1, in both schemes."""
    background, border = NOTICE_LOOKS[kind]
    assert contrast("ink", background, SCHEMES[scheme]) >= 4.5
    assert contrast(border, background, SCHEMES[scheme]) >= 3


# The neutral notices that use the information kind (#732 slice 5), by
# template and a fragment of their text, so a later edit cannot quietly turn
# them back into warnings.
INFO_NOTICES = {
    "campaign-mail-families.html": ('id="family-test-sent"',),
    "emailed-reports.html": ('id="weekly-requested"',),
    "hosted-files.html": ("Uploaded {{ name }}",),
    "campaign-comparison.html": ("the latest complete figures are shown",),
    "weekly-digest.html": ("Manually requested report.", "Saved report:"),
}
TEMPLATES = STATIC.parent.parent / "templates/stewardship"


@pytest.mark.parametrize("name", sorted(INFO_NOTICES))
def test_neutral_notices_use_the_information_kind(name):
    """The notice class nearest before each listed fragment is notice-info."""
    source = (TEMPLATES / name).read_text()
    for fragment in INFO_NOTICES[name]:
        start = source.rindex('class="notice', 0, source.index(fragment))
        assert source.startswith('class="notice notice-info"', start), fragment

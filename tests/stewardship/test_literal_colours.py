"""No literal colours outside the colour tokens (#1264 slice 4).

Every colour the Admin dark scheme changes is a token (``web/design_tokens.py``,
declared by ``ui-v1.css`` and ``chart-v1.css``), so a rule that names a colour
itself stays light in dark. This guard fails on a literal colour (hex, a colour
function or a CSS colour name) anywhere in the portal's stylesheets, its
scripts' strings, its templates' styles and its static images, except the few
places ui-conventions "Colour scheme" allows. Comments are skipped, so an issue
number such as #565 is not a colour.
"""

import re
from pathlib import Path

import pytest
from matplotlib.colors import CSS4_COLORS

PACKAGE = Path(__file__).parents[2] / "src/parishkit/stewardship"
STATIC = PACKAGE / "accounts/static/stewardship"

# A colour function's name, or a CSS colour name as a whole word (so neither
# white-space nor var(--gold) counts). Hex is matched on its own.
LITERAL = re.compile(
    r"#[0-9a-f]{3,8}\b"
    r"|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\("
    rf"|(?<![\w-])(?:{'|'.join(sorted(CSS4_COLORS, key=len, reverse=True))})(?![\w-])",
    re.I,
)
CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
# A stylesheet's strings, braces and semicolons, and the text between them.
CSS_TOKEN = re.compile(r"\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|[{};]|[^{};\"']+", re.S)
# A script's strings and comments, in source order, so a comment marker
# inside a string (a URL's //) stays part of the string.
JS_TOKEN = re.compile(
    r"(?P<string>\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`)"
    r"|//[^\n]*|/\*.*?\*/",
    re.S,
)
TEMPLATE_COMMENT = re.compile(
    r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}|{#.*?#}|<!--.*?-->", re.S
)
# Where a template can name a colour: a style element or a presentation
# attribute.
TEMPLATE_STYLE = re.compile(
    r"<style\b.*?</style>"
    r"|\b(?:style|fill|stroke|color|bgcolor|stop-color)\s*=\s*(?:\"[^\"]*\"|'[^']*')",
    re.S | re.I,
)

# The blocks that declare the colour tokens: light (the page and the light
# card) and the Admin dark scheme, in ui-v1.css and chart-v1.css.
TOKEN_BLOCKS = {":root, .light-card", 'html.admin-document[data-theme="dark"]'}
# The white backing behind an uploaded image in dark.
IMAGE_BACKING = "background-color: #fff"
# The select arrow and its dark twin draw in their scheme's ink
# (test_design_tokens.py checks which).
INKED_IMAGES = {"select-arrow-v1.svg", "select-arrow-dark-v1.svg"}


def css_declarations(css):
    """Yield ``(blocks, declaration)`` for each declaration in ``css``.

    ``blocks`` is the tuple of enclosing preludes (at-rules and selectors),
    outermost first, with whitespace collapsed; ``declaration`` is one
    ``property: value`` without its semicolon. Comments are dropped first.
    """
    stack, text = [], ""
    for token in CSS_TOKEN.findall(CSS_COMMENT.sub("", css)):
        if token == "{":
            stack.append(" ".join(text.split()))
            text = ""
        elif token in ";}":
            if text.strip():
                yield tuple(stack), " ".join(text.split())
            text = ""
            if token == "}":
                stack.pop()
        else:
            text += token


def allowance(blocks, declaration):
    """Which allowed place a literal colour in this declaration stands in,
    or None (ui-conventions "Colour scheme", "No literal colours").

    Shadows and dialog backdrops are a translucent dark shade that works in
    both schemes, and print is always light.
    """
    prop = declaration.split(":", 1)[0].strip()
    selector = blocks[-1] if blocks else ""
    if any(block.startswith("@media print") for block in blocks):
        return "print"
    if prop in {"box-shadow", "text-shadow"}:
        return "shadow"
    if selector.endswith("::backdrop") and prop == "background":
        return "backdrop"
    if selector in TOKEN_BLOCKS and prop.startswith("--"):
        return "token"
    if (
        selector.startswith('html.admin-document[data-theme="dark"]')
        and "branding-preview" in selector
        and declaration == IMAGE_BACKING
    ):
        return "image backing"
    return None


def stylesheet_literals(css):
    """The declarations with a literal colour, split into those an allowance
    covers (as ``{allowance: count}``) and the rest."""
    allowed, refused = {}, []
    for blocks, declaration in css_declarations(css):
        if not LITERAL.search(declaration.split(":", 1)[-1]):
            continue
        place = allowance(blocks, declaration)
        if place:
            allowed[place] = allowed.get(place, 0) + 1
        else:
            refused.append((blocks[-1] if blocks else "", declaration))
    return allowed, refused


def script_literals(source):
    """The literal colours in a script's strings (its comments are skipped)."""
    return [
        match.group("string")
        for match in JS_TOKEN.finditer(source)
        if match.group("string") and LITERAL.search(match.group("string"))
    ]


def template_literals(source):
    """The literal colours in a template's style elements and attributes."""
    return [
        style
        for style in TEMPLATE_STYLE.findall(TEMPLATE_COMMENT.sub("", source))
        if LITERAL.search(style)
    ]


def portal_files(pattern):
    """The package's files matching ``pattern``, vendored files excluded."""
    return sorted(path for path in PACKAGE.rglob(pattern) if "vendor" not in path.parts)


STYLESHEETS = portal_files("*.css")
SCRIPTS = portal_files("*.js")
TEMPLATES = portal_files("*.html")
IMAGES = portal_files("*.svg")


def label(path):
    """A file's name relative to the package, for test ids."""
    return path.relative_to(PACKAGE).as_posix()


@pytest.mark.parametrize("path", STYLESHEETS, ids=label)
def test_stylesheet_names_no_colour_outside_the_allowed_places(path):
    """A rule reads a token; only the allowed places name a colour."""
    _, refused = stylesheet_literals(path.read_text())
    assert refused == []


def test_every_allowed_place_is_still_used():
    """Each allowance still covers a real rule, so the list cannot grow
    stale: an allowance nothing needs any more is removed here and from the
    spec."""
    used = set()
    for path in STYLESHEETS:
        used |= set(stylesheet_literals(path.read_text())[0])
    assert used == {"print", "shadow", "backdrop", "token", "image backing"}
    assert {path.name for path in IMAGES} >= INKED_IMAGES


@pytest.mark.parametrize("path", SCRIPTS, ids=label)
def test_script_strings_name_no_colour(path):
    """A script draws in the tokens (chart-v1.js reads them from the
    stylesheet), never in a colour of its own."""
    assert script_literals(path.read_text()) == []


@pytest.mark.parametrize("path", TEMPLATES, ids=label)
def test_template_styles_name_no_colour(path):
    """A template's styles and presentation attributes name no colour."""
    assert template_literals(path.read_text()) == []


@pytest.mark.parametrize("path", IMAGES, ids=label)
def test_static_images_draw_in_the_current_colour(path):
    """An inline image draws in currentColor; only the select arrows, which
    a stylesheet loads as a background, name their scheme's ink."""
    if path.name in INKED_IMAGES:
        return
    assert not LITERAL.search(path.read_text())


@pytest.mark.parametrize(
    "css,refused",
    [
        ("a { color: #fff; }", [("a", "color: #fff")]),
        ("a { color: rgb(0 0 0); }", [("a", "color: rgb(0 0 0)")]),
        ("a { color: hsl(0 0% 0%) }", [("a", "color: hsl(0 0% 0%)")]),
        ("a { border: 1px solid Gold; }", [("a", "border: 1px solid Gold")]),
        ("a { color: var(--ink, #1b2a31); }", [("a", "color: var(--ink, #1b2a31)")]),
        (
            "@media screen { a { --ink: #000; } }",
            [("a", "--ink: #000")],
        ),
        ("/* #565 white */ a { white-space: nowrap; color: var(--gold); }", []),
        ("a { color: currentColor; }", []),
        ("a { color: transparent; background: Canvas; }", []),
        ("a { box-shadow: 0 1px 2px rgb(0 0 0 / 5%); }", []),
        ("@media print { a { border-color: #999; } }", []),
        (".dialog::backdrop { background: rgb(0 0 0 / 45%); }", []),
        (":root, .light-card { --ink: #1b2a31; }", []),
    ],
)
def test_the_stylesheet_scan_finds_literal_colours(css, refused):
    """The scanner finds each kind of literal colour outside comments, a
    var() fallback included, and lets the allowed places through."""
    assert stylesheet_literals(css)[1] == refused


def test_the_script_and_template_scans_skip_comments():
    """An issue number in a comment is not a colour; one in a string is."""
    assert script_literals("// #565\nconst a = 'https://x/#y'; /* red */") == []
    assert script_literals("el.style.color = '#fff';") == ["'#fff'"]
    assert script_literals('const c = "rgb(0 0 0)";') == ['"rgb(0 0 0)"']
    assert template_literals("{# #fff #}<p>(#565) grey</p>") == []
    assert template_literals('<p style="color: red">') == ['style="color: red"']
    assert template_literals('<path fill="currentColor">') == []

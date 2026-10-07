"""The portal stylesheet and email components share one set of tokens (#732)."""

import re
from pathlib import Path

import pytest

from parishkit.stewardship.web import design_tokens as tokens
from parishkit.stewardship.web.buttons import email_button

STATIC = (
    Path(__file__).parents[2] / "src/parishkit/stewardship/accounts/static/stewardship"
)
ROOT_BLOCK = re.compile(r":root\s*{(.*?)}", re.S)
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


def test_only_layout_properties_live_outside_the_module():
    """A new colour or shape token belongs in design_tokens.py first."""
    assert set(root_properties()) - set(tokens.CSS_PROPERTIES) == {"shadow", "gutter"}


@pytest.mark.parametrize("name", sorted(p.name for p in STATIC.glob("*.css")))
def test_custom_property_fallbacks_match(name):
    """A var(--token, fallback) in any stylesheet repeats the token's value."""
    css = (STATIC / name).read_text()
    for prop, fallback in re.findall(r"var\(--([a-z0-9-]+),\s*([^()]+?)\)", css):
        if prop in tokens.CSS_PROPERTIES:
            assert fallback.strip() == tokens.CSS_PROPERTIES[prop], (name, prop)


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
    assert ">Open &lt;report&gt;</a>" in html
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
        ("https://example.org/", "link"),
    ],
)
def test_email_button_refuses_unsafe_links_and_unknown_variants(href, variant):
    """Only absolute http(s) links and the two email variants are allowed."""
    with pytest.raises(ValueError):
        email_button("x", href, variant=variant)

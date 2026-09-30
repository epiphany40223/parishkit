"""The live visual editor reports, in plain words, what sanitizing removes."""

import pytest

from parishkit.stewardship.web.content import (
    MAX_TEXT_BYTES,
    removed_markup,
    sanitize_html,
)


def test_removed_elements_attributes_links_and_comments_are_reported():
    """Each kind of removal is named once, sorted, without the raw values."""
    raw = (
        "<script>alert(1)</script>"
        '<p class="x" onclick="steal()">Hi '
        '<a href="javascript:steal()" target="_blank">bad</a> '
        '<a href="https://example.org/" title="ok" rel="me">ok</a></p>'
        '<!-- note --><div style="color: red">line</div><img src="x.png">'
    )
    assert removed_markup(raw) == [
        "<script> element and its content",
        "HTML comments",
        "class attribute",
        "image not from the hosted file library",
        "javascript: link target",
        "onclick attribute",
        "style attribute",
        "target attribute",
    ]
    # The report describes exactly what the sanitizer drops.
    clean = sanitize_html(raw)
    for gone in ("script", "onclick", "javascript", "img", "style=", "class="):
        assert gone not in clean


@pytest.mark.parametrize(
    "raw",
    [
        "<p>Plain <b>bold</b> and <i>italic</i></p>",
        "<div>one line</div><div>two</div>",
        '<p><a href="mailto:office@example.org">Email</a> '
        '<a href="tel:+15025551234">Call</a></p>',
        "Just text\n\nwith paragraphs",
        "",
    ],
)
def test_structure_preserving_rewrites_are_not_removals(raw):
    """div-to-paragraph and b/i renames keep content, so nothing is reported."""
    assert removed_markup(raw) == []


def test_input_is_bounded_like_the_sanitizer():
    """Oversized source is refused before parsing, as sanitize_html does."""
    with pytest.raises(ValueError):
        removed_markup("x" * (MAX_TEXT_BYTES + 1))


def test_descendants_of_elements_dropped_with_content_are_not_listed():
    """An svg's own paths vanish with it; only the svg itself is reported."""
    raw = '<svg><path d="M0 0" class="x"/><g><rect/></g></svg><p>kept</p>'
    assert removed_markup(raw) == ["<svg> element and its content"]
    assert sanitize_html(raw) == "<p>kept</p>"

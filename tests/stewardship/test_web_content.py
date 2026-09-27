"""Hostile content, bounded image decode and spreadsheet/header injection cases."""

from io import BytesIO

import pytest
from PIL import Image

from parishkit.stewardship.web.content import (
    MAX_IMAGE_BYTES,
    MAX_TEXT_BYTES,
    prepare_content,
    prepare_graphics,
    render_template,
    sanitize_html,
    validate_template,
)
from parishkit.stewardship.web.exports import csv_cell, download_headers


@pytest.mark.parametrize(
    "html",
    [
        "<script>secret()</script>",
        "<img src='https://tracking.example/a'>",
        "<svg onload='secret()'><script>secret()</script></svg>",
        "<form><input name='secret'></form>",
        "<!--secret--><iframe src='https://tracking.example'>secret</iframe>",
    ],
)
def test_active_or_tracking_content_is_removed(html):
    """Unsupported markup cannot retain execution, forms or remote resource loads."""
    result = sanitize_html(html)
    assert "secret" not in result and "tracking.example" not in result


def test_safe_markup_and_plain_text_roundtrip():
    """Sanitization is idempotent and plain text is generated independently."""
    result = prepare_content(
        '<h2>Hello</h2><p style="color:red" onclick="alert(1)">A &amp; B '
        '<a href="https://example.org" title="Visit">link</a></p>'
    )
    assert "style" not in result.html and "onclick" not in result.html
    assert "noopener noreferrer" in result.html
    assert result.text == "Hello\n\nA & B link: https://example.org"
    assert sanitize_html(result.html) == result.html
    assert (
        prepare_content("<p>A</p>", text="Edited alternative").text
        == "Edited alternative"
    )


# Chrome/WebKit editable regions: a bare first line, one <div> per line,
# <div><br></div> for a blank line, <b> for bold and styled spans.
CHROME_EDITOR = (
    "Dear {{ family_member_names }}:<div><br></div>"
    '<div>Stewardship is <b>a way</b> of <span style="color:red">life</span>.'
    "</div><div><br></div><div>In gratitude,<br>The Committee<br></div>"
)


@pytest.mark.parametrize(
    "value,expected",
    [
        (
            CHROME_EDITOR,
            "<p>Dear {{ family_member_names }}:</p>"
            "<p>Stewardship is <strong>a way</strong> of life.</p>"
            "<p>In gratitude,<br>The Committee</p>",
        ),
        # Wrappers of blocks unwrap; a line wrapper inside a list item breaks.
        (
            "<section><div>One</div><div><ul><li><div>a</div><div>b</div></li>"
            "</ul></div></section>",
            "<p>One</p><ul><li>a<br>b</li></ul>",
        ),
        (
            "<blockquote><div>q1</div><div>q2</div></blockquote><i>x</i>",
            "<blockquote><p>q1</p><p>q2</p></blockquote><em>x</em>",
        ),
        ("<h1>Title</h1><h5>Sub</h5>", "<h2>Title</h2><h3>Sub</h3>"),
        ("<div>loose<p>block</p></div>", "<p>loose</p><p>block</p>"),
        # Text typed or pasted into the HTML source box keeps its paragraphs.
        (
            "Dear Alex,\r\n\r\nLine one\nLine two\n\n\n  Last",
            "<p>Dear Alex,</p><p>Line one<br>Line two</p><p>Last</p>",
        ),
        (
            '<div onclick="x()"><script>secret()</script>safe</div>',
            "<p>safe</p>",
        ),
    ],
)
def test_editor_and_plain_structure_survives_sanitizing(value, expected):
    """Browser line wrappers become paragraphs instead of one run-on paragraph."""
    assert sanitize_html(value) == expected
    assert sanitize_html(expected) == expected


@pytest.mark.parametrize(
    "value",
    [
        "Hello {{ family_code }}",
        "One line of text",
        "<p>a</p>\n<p></p><p>b<br></p>",
        "<h2>TEST</h2><p>banner</p>Inline template {{ family_url }}",
    ],
)
def test_canonical_content_is_left_unchanged(value):
    """Canonical parts, and their concatenation, stay fixed points."""
    assert sanitize_html(value) == value


def test_plain_text_keeps_paragraphs_lists_and_link_targets():
    """Blank lines between blocks, bullets and numbers, and "label: URL" links."""
    result = prepare_content(
        "<p>Dear Alex,</p><p>First<br>second</p>"
        '<ul><li>a<ul><li>nested</li></ul></li><li><a href="{{ family_url }}">'
        "Begin</a></li></ul><ol><li>one</li><li>two</li></ol>"
        '<p><a href="mailto:office@example.org">office@example.org</a> or '
        '<a href="https://example.org/">https://example.org/</a></p>'
    )
    assert result.text == (
        "Dear Alex,\n\nFirst\nsecond\n\n- a\n  - nested\n"
        "- Begin: {{ family_url }}\n\n1. one\n2. two\n\n"
        "office@example.org or https://example.org/"
    )


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,secret",
        "file:///private",
        "java&#x09;script:alert(1)",
    ],
)
def test_unsafe_link_schemes_are_not_preserved(url):
    """HTML entity/control tricks cannot create active link destinations."""
    assert "href" not in sanitize_html(f'<a href="{url}">link</a>')


@pytest.mark.parametrize(
    "value",
    [
        "{{unknown}}",
        "{{family.name}}",
        "{% include 'x' %}",
        "{# comment #}",
        "{{family_name",
        "secret\x00",
        "x" * (MAX_TEXT_BYTES + 1),
        "\ud800",
    ],
)
def test_invalid_templates_reject_without_echo(value):
    """The template language contains only approved simple placeholders."""
    with pytest.raises(ValueError) as error:
        validate_template(value)
    assert "secret" not in str(error.value)


def test_substitution_is_inert_and_output_is_sanitized_again():
    """Escaping handles text/attribute contexts; post-render URL filtering remains."""
    values = {
        "family_name": '<img src="https://tracking.example">',
        "family_url": "javascript:alert(1)",
    }
    result = render_template(
        '<p>{{ family_name }}</p><a href="{{family_url}}">Go</a>', values, html=True
    )
    assert "<img" not in result and "href" not in result
    with pytest.raises(ValueError):
        render_template("{{family_name}}", {})
    with pytest.raises(ValueError):
        render_template(
            "{{family_name}}", {"family_name": "a\r\nBcc:bad"}, subject=True
        )


def png(size=(300, 200)):
    """An entirely synthetic raster, never an image from parish data."""
    buffer = BytesIO()
    Image.new("RGB", size, "blue").save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def test_graphic_variants_decode_reencode_and_use_random_names():
    """All output is static bounded PNG regardless of the caller's filename."""
    first, second = prepare_graphics(png()), prepare_graphics(png())
    assert set(first) == {"large", "small", "favicon"}
    assert first["large"].name != second["large"].name
    for label, limit in (("large", 1024), ("small", 128), ("favicon", 32)):
        result = first[label]
        with Image.open(BytesIO(result.data)) as image:
            assert image.format == "PNG" and not image.info
            assert max(image.size) <= limit
        assert result.content_type == "image/png" and result.name.endswith(".png")


@pytest.mark.parametrize(
    "data",
    [
        b"<svg onload='secret()'/>",
        b"not an image",
        b"x" * (MAX_IMAGE_BYTES + 1),
        b"GIF89a",
    ],
)
def test_invalid_graphics_fail_without_reflecting_bytes(data):
    """Signature, type, decode and compressed-size boundaries fail closed."""
    with pytest.raises(ValueError, match="supported, bounded static image"):
        prepare_graphics(BytesIO(data))


def test_pixel_limit_precedes_full_decode(monkeypatch):
    """An image's metadata cannot silently relax the app's decompression budget."""
    monkeypatch.setattr("parishkit.stewardship.web.content.MAX_IMAGE_PIXELS", 10)
    with pytest.raises(ValueError):
        prepare_graphics(png((4, 4)))


@pytest.mark.parametrize(
    "value",
    [
        "=1+1",
        "+cmd",
        "-cmd",
        "@sum(1)",
        " \t=1",
        "\ttext",
        "\rtext",
        "\u00a0=1",
        "\ufeff=1",
    ],
)
def test_csv_formula_neutralization(value):
    """Spreadsheet import cannot execute formulas hidden behind friendly spacing."""
    assert csv_cell(value) == "'" + value
    assert csv_cell("plain, text") == "plain, text"
    assert csv_cell(None) == ""


@pytest.mark.parametrize(
    "filename",
    [
        "../private.csv",
        "a\\b.csv",
        "a\r\nHeader: value",
        "\ud800.csv",
        "\u202esecret.csv",
        "",
        ".",
        "..",
    ],
)
def test_download_filename_rejects_paths_controls_and_surrogates(filename):
    """No header/path injection or bidirectional filename spoofing is retained."""
    with pytest.raises(ValueError):
        download_headers(filename, content_type="text/csv")


def test_download_headers_use_safe_ascii_and_rfc5987_names():
    """Unicode display names remain encoded, not inserted as unsafe raw headers."""
    headers = download_headers('parish "é".csv', content_type="text/csv")
    assert (
        "filename*=UTF-8''parish%20%22%C3%A9%22.csv" in headers["Content-Disposition"]
    )
    assert headers["Cache-Control"] == "no-store"
    with pytest.raises(ValueError):
        download_headers("file.html", content_type="text/html")

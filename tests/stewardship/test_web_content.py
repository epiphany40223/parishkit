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
    assert result.text == "Hello\n\nA & B link"
    assert sanitize_html(result.html) == result.html
    assert (
        prepare_content("<p>A</p>", text="Edited alternative").text
        == "Edited alternative"
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

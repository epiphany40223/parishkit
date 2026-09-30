"""Hosted files (#346): detection, names, placeholders, sanitizer and mail checks.

These run without PostgreSQL; the database guards, Admin page and public
route are covered by ``database/test_hosted_files_postgresql.py``.
"""

import io
import os
import stat
from uuid import uuid4

import pytest
from PIL import Image

from parishkit.config import ConfigError
from parishkit.stewardship.accounts import hosted_file_storage as storage
from parishkit.stewardship.accounts.hosted_file_serving import _disposition
from parishkit.stewardship.accounts.hosted_files import (
    clean_name,
    derive_slug,
    download_name,
    placeholder,
    valid_slug,
)
from parishkit.stewardship.family_delivery import FamilyDeliveryMail
from parishkit.stewardship.mail_layout import email_document
from parishkit.stewardship.web.content import (
    HostedLinks,
    file_references,
    image_references,
    prepare_content,
    removed_markup,
    render_template,
    sanitize_html,
    validate_admin_digest_content,
    validate_share_label,
    validate_template,
)
from parishkit.stewardship.web.hosted_file_types import (
    MAX_FILE_BYTES,
    FileRefused,
    detect,
)

from . import hosted_file_samples as samples

ORIGIN = "https://parish.example.org"
TOKEN = "T" * 43
LINKS = HostedLinks(
    ORIGIN,
    {"guide": f"{ORIGIN}/files/{'G' * 43}", "picnic": f"{ORIGIN}/files/{TOKEN}"},
)


# --- Detection -------------------------------------------------------------


@pytest.mark.parametrize("kind", ["docx", "xlsx", "pptx"])
def test_office_documents_are_detected_from_their_main_part(kind):
    """The kind comes from [Content_Types].xml, never a name or declared type."""
    data = samples.office(kind)
    detected = detect(data)
    assert (detected.kind, detected.data, detected.width) == (kind, data, None)


def test_pdf_is_stored_byte_for_byte():
    """No scan or rewrite: the stored bytes are the uploaded bytes."""
    data = samples.pdf()
    assert detect(data).kind == "pdf" and detect(data).data == data


def test_the_size_limit_is_exactly_ten_megabytes():
    """10 MB is accepted; one more byte is refused before any parsing."""
    assert detect(samples.pdf(MAX_FILE_BYTES)).kind == "pdf"
    with pytest.raises(FileRefused) as refused:
        detect(samples.pdf(MAX_FILE_BYTES + 1))
    assert refused.value.reason == "too_large"


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"", "empty"),
        (samples.HTML_AS_PDF, "type"),
        (samples.SVG, "type"),
        (b"%PDF-1.7\n<script>alert(1)</script>", "type"),
        (samples.OLE2, "legacy_office"),
        (samples.office("docm"), "macro"),
        (samples.office("xlsm"), "macro"),
        (samples.office("pptm"), "macro"),
        (samples.office("dotx"), "type"),
        (samples.office("ppsx"), "type"),
        (
            samples.office("docx", extra_parts=[("word/vbaProject.bin", b"\0")]),
            "macro",
        ),
        (
            samples.office("xlsx", extra_parts=[("xl/activeX/activeX1.xml", "<x/>")]),
            "macro",
        ),
        (
            samples.office(
                "docx",
                content_types='<!DOCTYPE t [<!ENTITY a "a">]><Types>&a;</Types>',
            ),
            "type",
        ),
        (
            samples.office("docx", extra_parts=[("../evil.txt", "x")]),
            "type",
        ),
        (
            samples.office(
                "docx",
                extra_parts=[
                    (
                        "word/_rels/settings.xml.rels",
                        samples.relationship(
                            "attachedTemplate", "https://evil.example/t.dotm"
                        ),
                    )
                ],
            ),
            "macro",
        ),
        (
            samples.office(
                "docx",
                extra_parts=[
                    (
                        "word/_rels/document.xml.rels",
                        samples.relationship("oleObject", "https://evil.example/x"),
                    )
                ],
            ),
            "macro",
        ),
        (
            samples.office(
                "docx",
                extra_parts=[
                    (
                        "word/_rels/document.xml.rels",
                        samples.relationship(
                            "oleObject", "embeddings/oleObject1.bin", external=False
                        ),
                    )
                ],
            ),
            "macro",
        ),
        (
            samples.office(
                "docx",
                extra_parts=[
                    (
                        "word/_rels/document.xml.rels",
                        samples.relationship("subDocument", "https://evil.example/s"),
                    )
                ],
            ),
            "macro",
        ),
        (
            samples.office(
                "docx",
                extra_parts=[
                    (
                        "word/_rels/webSettings.xml.rels",
                        samples.relationship("frame", "https://evil.example/f"),
                    )
                ],
            ),
            "macro",
        ),
        (
            samples.office(
                "pptx", extra_parts=[("ppt/embeddings/oleObject1.bin", b"\0")]
            ),
            "macro",
        ),
        (
            samples.office(
                "xlsx",
                extra_types='<Override PartName="/xl/macrosheets/sheet1.xml" '
                'ContentType="application/vnd.ms-excel.macrosheet+xml"/>',
            ),
            "macro",
        ),
        (
            samples.office(
                "xlsx",
                extra_types='<Override PartName="/xl/macrosheets/sheet2.xml" '
                'ContentType="application/vnd.ms-excel.intlmacrosheet+xml"/>',
            ),
            "macro",
        ),
        (samples.image("GIF", frames=3), "animated"),
        (samples.image("PNG", frames=2), "animated"),
        (samples.bomb_png(), "image"),
        (b"\x89PNG\r\n\x1a\ntruncated", "image"),
        (samples.image("WEBP"), "type"),
    ],
    ids=[
        "empty",
        "html-named-pdf",
        "svg",
        "pdf-without-eof",
        "legacy-office",
        "docm",
        "xlsm",
        "pptm",
        "template",
        "slideshow",
        "vba-part",
        "activex",
        "remote-template",
        "external-ole",
        "embedded-ole-relationship",
        "subdocument",
        "frame",
        "embedded-ole-part",
        "excel4-macrosheet",
        "excel4-intl-macrosheet",
        "dtd",
        "zip-slip",
        "animated-gif",
        "animated-png",
        "decompression-bomb",
        "corrupt-png",
        "webp",
    ],
)
def test_hostile_and_unsupported_uploads_are_refused(data, reason):
    """Every refusal has one closed reason the Admin page explains."""
    with pytest.raises(FileRefused) as refused:
        detect(data)
    assert refused.value.reason == reason


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (samples.relocated_activex(), "macro"),
        (samples.activex_binary(), "macro"),
        (samples.alt_chunk(), "macro"),
        (
            samples.office(
                "docx",
                extra_parts=[
                    (
                        "word/_rels/document.xml.rels",
                        samples.relationship(
                            "vbaProject", "code/project.bin", external=False
                        ),
                    )
                ],
            ),
            "macro",
        ),
        (samples.custom_ui(), "macro"),
        (
            samples.office(
                "xlsx",
                extra_types='<Override PartName="/xl/d.xml" ContentType="application/'
                'vnd.openxmlformats-officedocument.spreadsheetml.dialogsheet+xml"/>',
            ),
            "macro",
        ),
        (samples.padded_type("attachedTemplate"), "macro"),
        (
            samples.duplicate_entries("[Content_Types].xml", "[Content_Types].xml"),
            "type",
        ),
        (
            samples.duplicate_entries("[Content_Types].xml", "[content_types].xml"),
            "type",
        ),
    ],
    ids=[
        "relocated-activex",
        "activex-binary",
        "altchunk",
        "relocated-vba-project",
        "custom-ui",
        "dialog-sheet",
        "padded-relationship-type",
        "duplicate-entry",
        "case-variant-duplicate",
    ],
)
def test_relocated_and_disguised_active_content_is_refused(data, reason):
    """Office finds parts by relationship and content type, not folder."""
    with pytest.raises(FileRefused) as refused:
        detect(data)
    assert refused.value.reason == reason


def test_a_zip_that_is_not_office_is_refused():
    """A ZIP without an Office main part is not an accepted type."""
    import zipfile

    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as package:
        package.writestr("readme.txt", "hello")
    with pytest.raises(FileRefused):
        detect(stream.getvalue())


def test_a_compressed_content_types_bomb_is_never_expanded():
    """[Content_Types].xml is read only up to its 1 MB bound."""
    huge = "<Types>" + " " * (8 * 1024 * 1024) + "</Types>"
    with pytest.raises(FileRefused) as refused:
        detect(samples.office("docx", content_types=huge))
    assert refused.value.reason == "type"


def test_a_polyglot_image_is_reencoded_without_its_payload():
    """A GIF with a ZIP appended becomes a clean PNG; the ZIP does not survive."""
    detected = detect(samples.gifar())
    assert detected.kind == "png"
    assert b"PK\x03\x04" not in detected.data
    assert detected.data.startswith(b"\x89PNG")


def test_images_are_reencoded_without_metadata_and_fitted():
    """EXIF (with GPS) and ICC are gone; orientation is applied; sides fit 2,048."""
    data = samples.image(
        "JPEG", (3000, 1000), exif=samples.gps_exif(), icc=b"synthetic-icc-profile"
    )
    detected = detect(data)
    assert detected.kind == "jpeg"
    # Orientation 6 rotates the 3000x1000 photo to portrait, then it is fitted.
    assert (detected.width, detected.height) == (683, 2048)
    with Image.open(io.BytesIO(detected.data)) as stored:
        assert not stored.getexif()
        assert "icc_profile" not in stored.info
        assert "exif" not in stored.info


def test_a_multi_picture_jpeg_is_a_still_photo():
    """MPO from phones keeps its first picture; the others are dropped."""
    data = samples.mpo()
    with Image.open(io.BytesIO(data)) as source:
        assert source.format == "MPO" and source.n_frames == 2
    detected = detect(data)
    assert (detected.kind, detected.width, detected.height) == ("jpeg", 40, 20)
    with Image.open(io.BytesIO(detected.data)) as stored:
        assert stored.format == "JPEG" and getattr(stored, "n_frames", 1) == 1


def test_office_charts_and_ordinary_links_are_accepted():
    """Only active relationships are refused; hyperlinks and packages pass."""
    data = samples.office(
        "docx",
        extra_parts=[
            (
                "word/_rels/document.xml.rels",
                samples.relationship("hyperlink", "https://parish.example.org"),
            ),
            ("word/embeddings/Microsoft_Excel_Worksheet.xlsx", b"PK"),
        ],
    )
    assert detect(data).kind == "docx"


def test_a_still_gif_is_stored_as_png():
    """GIF input becomes PNG, keeping its pixel size."""
    detected = detect(samples.image("GIF", (30, 10)))
    assert (detected.kind, detected.width, detected.height) == ("png", 30, 10)


# --- Names and slugs ------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "taken", "slug"),
    [
        ("Ministry Guide.pdf", set(), "ministry-guide"),
        ("C:\\Users\\me\\Café Menu (2027).PDF", set(), "cafe-menu-2027"),
        ("???.png", set(), "file"),
        ("guide.pdf", {"guide", "guide-2"}, "guide-3"),
        ("a" * 80 + "-b.pdf", set(), "a" * 64),
    ],
)
def test_slugs_are_derived_from_the_base_name(name, taken, slug):
    """ASCII, lowercase, hyphenated, bounded and free."""
    derived = derive_slug(clean_name(name), taken)
    assert derived == slug and valid_slug(derived)


@pytest.mark.parametrize(
    "slug", ["", "-a", "a-", "a--b", "A", "a_b", "a.b", "a" * 65, "é"]
)
def test_invalid_slugs_are_refused(slug):
    """Only lowercase letters, digits and single inner hyphens, up to 64."""
    assert not valid_slug(slug)


def test_names_are_cleaned_and_downloads_get_the_detected_extension():
    """Paths and unsafe characters go; the saved name matches the stored type."""
    assert clean_name('..\\..\\evil"<x>.pdf') == "evilx.pdf"
    assert clean_name("") == "file"
    assert download_name("photo.png", "jpeg") == "photo.jpg"
    assert download_name("Guía", "pdf") == "Guía.pdf"


def test_disposition_is_attachment_for_documents_and_inline_for_images():
    """Both a safe ASCII fallback and the exact UTF-8 name are sent."""

    class Row:
        original_name = 'Guía "final".pdf'
        kind = "pdf"

    header = _disposition(Row)
    assert header.startswith('attachment; filename="Gu_a _final_.pdf";')
    assert "filename*=UTF-8''Gu%C3%ADa%20%22final%22.pdf" in header
    Row.kind = "png"
    assert _disposition(Row).startswith("inline; ")


# --- Placeholders and sanitizer --------------------------------------------


def test_file_placeholders_are_a_separate_family():
    """They are found in bodies, never in subjects or share labels."""
    body = '<p><a href="{{ file.guide }}">Guide</a> {{ parish_name }}</p>'
    assert validate_template(body) == {"parish_name"}
    assert file_references(body) == {"guide"}
    with pytest.raises(ValueError):
        validate_template("See {{ file.guide }}", subject=True)
    with pytest.raises(ValueError):
        validate_share_label("See {{ file.guide }}")
    with pytest.raises(ValueError):
        validate_template("{{ file.Guide }}")
    with pytest.raises(ValueError):
        validate_template("{{ file.guide\n}}")
    with pytest.raises(ValueError):
        validate_admin_digest_content("Digest", "<p>{{ file.guide }}</p>", "x")


def test_placeholders_expand_to_absolute_links_in_html_and_text():
    """The link uses the token, is escaped, and survives the output sanitizer."""
    html = render_template(
        '<p><a href="{{ file.guide }}">Guide</a></p>', {}, html=True, files=LINKS
    )
    assert f'href="{ORIGIN}/files/{"G" * 43}"' in html
    text = render_template("Guide: {{file.guide}}", {}, files=LINKS)
    assert text == f"Guide: {ORIGIN}/files/{'G' * 43}"
    with pytest.raises(ValueError):
        render_template("{{ file.guide }}", {})


def test_a_missing_file_links_to_the_unavailable_page_and_drops_its_image():
    """Only reachable after a restore; the page still renders."""
    html = render_template(
        '<p><a href="{{ file.gone }}">Gone</a><img src="{{ file.gone }}" alt=""></p>',
        {},
        html=True,
        files=LINKS,
    )
    assert f'href="{ORIGIN}/files/unavailable"' in html and "<img" not in html


def test_the_sanitizer_keeps_only_hosted_images_with_known_attributes():
    """Stored content keeps placeholder images; anything else is removed."""
    raw = (
        '<p><img alt="Cover > art" src="{{ file.picnic }}" width="600" '
        'style="x" onerror="y" height="9">'
        '<img src="https://tracker.example/p.gif" alt="">'
        '<img src="data:image/png;base64,AAAA" alt="">'
        f'<img src="{ORIGIN}/files/{TOKEN}" alt="">'
        '<img src="{{ file.picnic }}" alt="x" width="99999"></p>'
    )
    clean = sanitize_html(raw)
    assert clean == (
        '<p><img alt="Cover &gt; art" src="{{ file.picnic }}" width="600">'
        '<img src="{{ file.picnic }}" alt="x"></p>'
    )
    assert sanitize_html(clean) == clean
    assert "image not from the hosted file library" in removed_markup(raw)
    assert image_references(clean) == ({"picnic"}, False)
    assert image_references('<p><img src="{{ file.a }}"></p>') == ({"a"}, True)


def test_rendered_images_survive_only_from_this_origin():
    """Output sanitizing and the delivery check pin the deployment's origin."""
    html = render_template(
        '<p><img src="{{ file.picnic }}" alt="Picnic"></p>', {}, html=True, files=LINKS
    )
    assert html == f'<p><img src="{ORIGIN}/files/{TOKEN}" alt="Picnic"></p>'
    assert prepare_content(html, origin=ORIGIN).html == html
    assert "<img" not in prepare_content(html).html
    assert "<img" not in prepare_content(html, origin="https://other.example").html


def _mail(html, origin):
    """A Family delivery carrying ``html``, checked like the real dispatcher."""
    return FamilyDeliveryMail(
        uuid4(),
        "parish@example.org",
        "office@example.org",
        ("family@example.org",),
        "Campaign",
        html,
        prepare_content(html, origin=origin or None).text,
        banner_origin=origin,
    )


def test_the_family_delivery_check_accepts_hosted_images_from_its_origin_only():
    """Rendered mail with a hosted image passes; a foreign image fails."""
    good = f'<p><img src="{ORIGIN}/files/{TOKEN}" alt="Picnic"></p>'
    _mail(good, ORIGIN)
    with pytest.raises(ValueError):
        _mail(good, "https://other.example")
    with pytest.raises(ValueError):
        _mail(good, "")
    with pytest.raises(ValueError):
        _mail('<p><img src="https://tracker.example/p.gif" alt=""></p>', ORIGIN)


def test_the_email_layout_styles_hosted_images_only():
    """Hosted images fit phones; compiler markup keeps its own attributes."""
    document = email_document(
        f'<p><img src="{ORIGIN}/files/{TOKEN}" alt="Picnic"></p>'
        '<img src="cid:chart" alt="Chart">'
    )
    assert f'<img style="max-width:100%;height:auto;border:0;" src="{ORIGIN}' in (
        document
    )
    assert '<img src="cid:chart" alt="Chart">' in document
    assert ".pk-body img{max-width:100%;height:auto;border:0;}" in document


def test_placeholder_text_is_canonical():
    """The copy button's text matches what the in-use search looks for."""
    assert placeholder("guide") == "{{ file.guide }}"


# --- Storage ------------------------------------------


@pytest.fixture
def media(tmp_path):
    """A private media root like the deployment's."""
    root = tmp_path / "media"
    root.mkdir(mode=0o700)
    return root


def test_storage_writes_private_files_and_sweeps_orphans(media):
    """Files are 0600, named by UUID only; the sweep keeps rows' files."""
    kept, orphan = uuid4(), uuid4()
    with storage.storage_lock(media):
        storage.write(media, kept, b"kept")
        storage.write(media, orphan, b"orphan")
        with pytest.raises(ConfigError):
            storage.write(media, kept, b"again")
        storage.sweep(media, {kept})
    directory = media / storage.DIRECTORY
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert sorted(os.listdir(directory)) == sorted([".lock", kept.hex])
    assert stat.S_IMODE((directory / kept.hex).stat().st_mode) == 0o600
    assert storage.exists(media, kept, 4) and not storage.exists(media, kept, 5)
    stream = storage.open_file(media, kept, 4)
    assert stream.read() == b"kept"
    stream.close()
    (directory / "link").symlink_to(directory / kept.hex)
    storage.remove(media, kept)
    storage.remove(media, kept)
    assert not storage.exists(media, kept, 4)


def test_a_symlinked_file_is_never_served(media):
    """A link in place of a stored file reads as missing."""
    target = media / "secret"
    target.write_bytes(b"secret")
    identifier = uuid4()
    with storage.storage_lock(media):
        pass
    (media / storage.DIRECTORY / identifier.hex).symlink_to(target)
    assert storage.open_file(media, identifier, 6) is None


def test_family_email_renders_hosted_links_and_images():
    """An invitation expands file links in both parts and passes the check."""
    from parishkit.stewardship.jobs.family_mail_content import (
        FamilyMailTemplate,
        render_family_mail,
    )

    from .test_family_mail_content import identity

    html = (
        '<p>{{ family_code }} <a href="{{ family_url }}" rel="noopener noreferrer">'
        'Respond</a> <a href="{{ file.guide }}" rel="noopener noreferrer">Guide</a>'
        '<img src="{{ file.picnic }}" alt="Picnic"></p>'
    )
    template = FamilyMailTemplate("Invitation", html, prepare_content(html).text)
    rendered = render_family_mail(
        identity=identity(),
        configuration_id=uuid4(),
        template_id=uuid4(),
        template=template,
        values={},
        sender="parish@example.org",
        intended_recipients=("a@example.org",),
        files=LINKS,
    )
    assert f'<img src="{ORIGIN}/files/{TOKEN}" alt="Picnic">' in rendered.html
    assert f"Guide: {ORIGIN}/files/{'G' * 43}" in rendered.text
    assert prepare_content(rendered.html, origin=ORIGIN).html == rendered.html


def test_hosted_images_are_styled_whatever_their_attribute_order():
    """An author may write alt before src; the image still fits phones."""
    document = email_document(
        f'<p><img alt="Picnic" src="{ORIGIN}/files/{TOKEN}" width="600"></p>'
    )
    assert (
        f'<img style="max-width:100%;height:auto;border:0;" alt="Picnic" src="{ORIGIN}'
        in (document)
    )

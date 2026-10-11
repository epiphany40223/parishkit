"""Each Parish logo size uploaded on its own, with its own rules (#1062)."""

import io
from uuid import uuid4

import pytest
from django import forms
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from parishkit.stewardship.accounts.branding_files import create_bundle
from parishkit.stewardship.accounts.branding_slots import (
    SLOTS,
    check_slot,
    file_size,
)
from parishkit.stewardship.accounts.branding_views import LogoUploadForm
from parishkit.stewardship.web.content import image_facts, prepare_graphics

KB = 1024
MB = 1024 * KB


def image(width, height, kind="PNG", *, mode="RGB", frames=1):
    """Encoded bytes of a real image of the given size and format."""
    output = io.BytesIO()
    first = Image.new(mode, (width, height), "blue" if mode == "RGB" else 1)
    if frames == 1:
        first.save(output, format=kind)
    else:
        rest = [Image.new(mode, (width, height), "red") for _ in range(frames - 1)]
        first.save(output, format=kind, save_all=True, append_images=rest)
    return output.getvalue()


def padded(data, size):
    """``data`` grown to exactly ``size`` bytes. PNG decoders stop at IEND,
    so trailing bytes change only the file's size, the rule under test."""
    return data + b"\0" * (size - len(data))


def refusal(name, data):
    """The message ``check_slot`` refuses ``data`` with for slot ``name``."""
    with pytest.raises(forms.ValidationError) as error:
        check_slot(name, data)
    return error.value.messages[0]


@pytest.mark.parametrize(
    ("name", "width", "height", "output"),
    [
        ("large", 256, 100, (256, 100)),
        ("large", 2000, 1000, (1024, 512)),
        ("menu", 512, 128, (128, 32)),
        ("menu", 64, 64, (64, 64)),
        ("menu", 2048, 600, (128, 38)),
        ("icon", 128, 128, (128, 128)),
        ("icon", 140, 128, (128, 117)),
        ("icon", 512, 512, (128, 128)),
        ("favicon", 16, 16, (16, 16)),
        ("favicon", 64, 64, (32, 32)),
        ("favicon", 512, 512, (32, 32)),
    ],
)
def test_accepted_sizes_are_fitted_never_enlarged(name, width, height, output):
    """An accepted size is a fresh PNG fitted within its output bound;
    one already smaller is kept at its own size, never blurred upward."""
    graphic = check_slot(name, image(width, height))
    assert (graphic.width, graphic.height) == output
    assert graphic.content_type == "image/png"
    assert graphic.data.startswith(b"\x89PNG")


@pytest.mark.parametrize(
    ("name", "width", "height", "message"),
    [
        (
            "large",
            255,
            255,
            "Large logo: the image is 255 × 255 pixels; the longer side must be "
            "at least 256 pixels.",
        ),
        (
            "menu",
            513,
            128,
            "Menu logo: the image is 513 × 128 pixels; a menu logo can be at "
            "most 4 times as wide as it is tall.",
        ),
        (
            "menu",
            100,
            101,
            "Menu logo: the image is 100 × 101 pixels; a menu logo can't be "
            "taller than it is wide.",
        ),
        (
            "menu",
            128,
            63,
            "Menu logo: the image is 128 × 63 pixels; a menu logo must be at "
            "least 64 pixels tall.",
        ),
        (
            "menu",
            2049,
            600,
            "Menu logo: the image is 2,049 × 600 pixels; neither side of a menu "
            "logo may be over 2,048 pixels.",
        ),
        (
            "icon",
            141,
            128,
            "Icon: the image is 141 × 128 pixels; an icon must be square (within 10%).",
        ),
        (
            "icon",
            127,
            127,
            "Icon: the image is 127 × 127 pixels; an icon must be at least 128 "
            "pixels on each side.",
        ),
        (
            "favicon",
            400,
            120,
            "Favicon: the image is 400 × 120 pixels; a favicon must be square "
            "(within 10%).",
        ),
        (
            "favicon",
            15,
            15,
            "Favicon: the image is 15 × 15 pixels; a favicon must be at least 16 "
            "pixels on each side.",
        ),
        (
            "favicon",
            513,
            513,
            "Favicon: the image is 513 × 513 pixels; a favicon can be at most "
            "512 pixels on a side.",
        ),
    ],
)
def test_shape_rules_name_the_slot_the_rule_and_the_file(name, width, height, message):
    """Each shape rule's boundary refuses with the slot, the rule and the
    image's actual size."""
    assert refusal(name, image(width, height)) == message


@pytest.mark.parametrize(
    ("name", "limit", "message"),
    [
        ("large", 5 * MB, "Large logo: the file is 5.1 MB; the limit is 5 MB."),
        ("menu", 1 * MB, "Menu logo: the file is 1.1 MB; the limit is 1 MB."),
        ("icon", 1 * MB, "Icon: the file is 1.1 MB; the limit is 1 MB."),
        ("favicon", 256 * KB, "Favicon: the file is 257 KB; the limit is 256 KB."),
    ],
)
def test_byte_limits_refuse_one_byte_over(name, limit, message):
    """A file at its slot's byte limit is read; one byte over is refused
    before decoding, saying the file's size rounded up."""
    data = image(300, 300)
    assert check_slot(name, padded(data, limit)).data
    assert refusal(name, padded(data, limit + 1)) == message


def test_format_animation_pixels_and_unreadable_files():
    """Formats are by content, per slot; animation and huge images are
    refused with what the file was."""
    assert (
        refusal("icon", image(200, 200, "JPEG"))
        == "Icon: the file is a JPEG image; an icon must be a PNG."
    )
    assert (
        refusal("favicon", image(32, 32, "WEBP"))
        == "Favicon: the file is a WebP image; a favicon must be a PNG."
    )
    assert check_slot("menu", image(200, 100, "JPEG")).data
    assert check_slot("large", image(300, 300, "WEBP")).data
    assert (
        refusal("large", image(300, 300, frames=2))
        == "Large logo: the image is animated; choose a single still image."
    )
    assert (
        refusal("menu", b"<svg xmlns='http://www.w3.org/2000/svg'/>")
        == "Menu logo: the file is not a PNG, JPEG or WebP image that can be read."
    )
    assert (
        refusal("favicon", b"GIF89a")
        == "Favicon: the file is not a PNG image that can be read."
    )
    # 4,001 × 4,000 is just over 16 megapixels (a 1-bit image keeps it small).
    assert (
        refusal("large", image(4001, 4000, mode="1"))
        == "Large logo: the image is 4,001 × 4,000 pixels; the limit is 16 "
        "megapixels."
    )


def test_displayed_size_follows_exif_rotation():
    """A JPEG stored on its side is judged as it is displayed."""
    output = io.BytesIO()
    exif = Image.Exif()
    exif[0x0112] = 6
    Image.new("RGB", (100, 200)).save(output, format="JPEG", exif=exif)
    facts = image_facts(output.getvalue())
    assert (facts.width, facts.height) == (200, 100)
    assert check_slot("menu", output.getvalue()).width == 128


def test_file_size_rounds_up():
    """A size just over a limit never reads as the limit itself."""
    assert file_size(1 * MB) == "1 MB"
    assert file_size(1 * MB + 1) == "1.1 MB"
    assert file_size(3 * MB + MB // 5) == "3.2 MB"
    assert file_size(300 * KB) == "300 KB"
    assert file_size(1) == "1 KB"


def upload(name, data):
    """A posted file field."""
    return SimpleUploadedFile(name + ".png", data, content_type="image/png")


def bound(mode, **files):
    """The Parish logos form posted in ``mode`` with ``files``."""
    data = {"base_digest": "a" * 64} | ({"mode": mode} if mode else {})
    return LogoUploadForm(
        data, {name: upload(name, value) for name, value in files.items()}
    )


def test_one_image_mode_is_the_default_and_unchanged():
    """No mode is one image, as before #1062: the single file is required,
    and per-size files sent alongside are ignored."""
    form = bound(None)
    assert not form.is_valid()
    assert list(form.errors) == ["logo"]
    form = bound(None, logo=image(150, 80), favicon=image(400, 120))
    assert form.is_valid(), form.errors
    assert form.cleaned_data["mode"] == "one"
    assert "sizes" not in form.cleaned_data


def test_each_size_mode_checks_every_chosen_size():
    """Every chosen size is checked; any refusal marks its own field, so
    nothing is staged; an empty upload asks for at least one size."""
    form = bound("each")
    assert not form.is_valid()
    assert form.errors["large"] == ["Choose an image for at least one size to upload."]
    form = bound("each", favicon=image(400, 120), icon=image(200, 200))
    assert not form.is_valid()
    assert list(form.errors) == ["favicon"]
    form = bound("each", favicon=image(32, 32), menu=image(256, 64))
    assert form.is_valid(), form.errors
    assert set(form.cleaned_data["sizes"]) == {"favicon", "menu"}
    assert "logo" not in form.errors


def test_each_size_mode_bounds_the_files_together():
    """The files chosen together stay under what one request can carry."""
    big = padded(image(600, 600), 4 * MB + 1)
    form = bound("each", large=big, menu=padded(image(256, 64), MB))
    assert not form.is_valid()
    assert form.errors["large"] == [
        "Together the chosen files are 5.1 MB; one upload can carry at most "
        "5 MB. Upload the larger sizes on their own."
    ]
    form = bound("each", large=padded(image(600, 600), 4 * MB), menu=image(256, 64))
    assert form.is_valid(), form.errors


def test_each_size_field_shows_its_rule_and_is_shown_only_in_its_mode():
    """Each size's one-line rule stays visible, the rest is behind its tip,
    and the browser checks its byte limit in the server's words."""
    form = LogoUploadForm(initial={"base_digest": "a" * 64})
    assert list(form.fields) == [
        "mode",
        "logo",
        "large",
        "menu",
        "icon",
        "favicon",
        "base_digest",
    ]
    assert form.fields["logo"].widget.attrs["data-show-when"] == "mode=one"
    for name, slot in SLOTS.items():
        field = form.fields[name]
        assert field.help_text == slot.hint and field.tip == slot.tip
        attrs = field.widget.attrs
        assert attrs["data-show-when"] == "mode=each"
        assert attrs["data-max-bytes"] == slot.max_bytes
        assert attrs["data-max-bytes-message"].startswith(f"{slot.title}: the file")
    assert form.fields["favicon"].widget.attrs["accept"] == "image/png"
    html = form.as_div()
    assert 'value="each"' in html and "Each size separately" in html


def test_a_bundle_takes_four_separate_sizes(tmp_path):
    """A bundle of four separately given sizes keeps each size's own file;
    the menu logo and the icon need not be the same image."""
    tmp_path.chmod(0o700)
    sizes = {
        "large": check_slot("large", image(600, 300)),
        "menu": check_slot("menu", image(256, 64)),
        "icon": check_slot("icon", image(200, 200)),
        "favicon": check_slot("favicon", image(32, 32)),
    }
    receipts = {item.label: item for item in create_bundle(tmp_path, uuid4(), sizes)}
    assert (receipts["menu"].width, receipts["menu"].height) == (128, 32)
    assert (receipts["icon"].width, receipts["icon"].height) == (128, 128)
    assert receipts["menu"].sha256 != receipts["icon"].sha256


def test_a_huge_image_is_refused_without_being_decoded(monkeypatch):
    """A small, highly compressed PNG of huge dimensions is refused from its
    header alone: reading a PNG's EXIF would decode every pixel first."""
    from PIL import ImageFile

    data = image(9000, 9000, mode="1")
    assert len(data) < 1 * MB

    def loaded(self):
        raise AssertionError("the image was decoded")

    monkeypatch.setattr(ImageFile.ImageFile, "load", loaded)
    assert (
        refusal("large", data)
        == "Large logo: the image is 9,000 × 9,000 pixels; the limit is 16 "
        "megapixels."
    )


def test_a_multi_picture_jpeg_is_a_field_error():
    """An MPO file (which the JPEG decoder also opens) is refused as an
    unreadable image at its field, never an unexpected error."""
    output = io.BytesIO()
    frames = [Image.new("RGB", (300, 300), color) for color in ("red", "blue")]
    frames[0].save(output, format="MPO", save_all=True, append_images=frames[1:])
    data = output.getvalue()
    with Image.open(io.BytesIO(data)) as probe:
        assert probe.format == "MPO"
    for name, formats in (("large", "PNG, JPEG or WebP"), ("icon", "PNG")):
        assert refusal(name, data) == (
            f"{SLOTS[name].title}: the file is not a {formats} image that can be read."
        )
    # One-image mode refuses it too (the view shows this at its field).
    with pytest.raises(ValueError):
        prepare_graphics(io.BytesIO(data))


def test_one_image_over_its_limit_is_refused_in_the_pages_words():
    """The server refuses an oversized single image in the words the page
    uses for it as soon as the file is chosen."""
    form = bound(None, logo=padded(image(300, 300), 5 * MB + 1))
    assert not form.is_valid()
    message = "Parish logo: the file is 5.1 MB; the limit is 5 MB."
    assert form.errors["logo"] == [message]
    page = LogoUploadForm(initial={"base_digest": "a" * 64})
    template = page.fields["logo"].widget.attrs["data-max-bytes-message"]
    assert str(template).replace("{size}", "5.1 MB") == message

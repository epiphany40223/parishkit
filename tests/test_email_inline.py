"""Inline charts are bounded in-memory related parts, not file-path attachments."""

from dataclasses import replace

import pytest

from parishkit.config import ConfigError
from parishkit.email.base import (
    MAX_INLINE_BYTES,
    Attachment,
    Email,
    InlineImage,
    build_message,
)


def email(**changes):
    """Synthetic public envelope with an accessible text alternative."""
    return Email(
        subject="Statistics",
        sender="from@example.org",
        to=("admin@example.org",),
        text="One out of two (50%)",
        html='<p>One out of two (50%)</p><img src="cid:chart@example.org" alt="Chart">',
        **changes,
    )


def test_inline_image_stays_with_html_and_preserves_other_parts(tmp_path):
    """No remote image URL is needed, and neither Bcc nor attachments become inline."""
    attachment = tmp_path / "report.txt"
    attachment.write_text("Report", encoding="utf-8")
    image = InlineImage(b"synthetic image bytes", "chart@example.org")
    message = build_message(
        email(
            inline_images=(image,),
            attachments=(Attachment(attachment, "text/plain"),),
            cc=("copy@example.org",),
            bcc=("private@example.org",),
        )
    )
    assert message.get_content_type() == "multipart/mixed"
    alternative, ordinary_attachment = message.get_payload()
    assert alternative.get_content_type() == "multipart/alternative"
    plain, related = alternative.get_payload()
    assert plain.get_content_type() == "text/plain"
    assert "One out of two" in plain.get_content()
    assert related.get_content_type() == "multipart/related"
    html, chart = related.get_payload()
    assert "cid:chart@example.org" in html.get_content()
    assert chart.get_content_type() == "image/png"
    assert chart["Content-ID"] == "<chart@example.org>"
    assert chart.get_content_disposition() == "inline"
    assert chart.get_payload(decode=True) == image.data
    assert ordinary_attachment.get_content_disposition() == "attachment"
    assert message["Cc"] == "copy@example.org" and message["Bcc"] is None
    assert "synthetic image bytes" not in repr(image)


@pytest.mark.parametrize(
    "changes",
    [
        {"data": b""},
        {"data": "not bytes"},
        {"data": b"x" * (MAX_INLINE_BYTES + 1)},
        {"content_id": ""},
        {"content_id": "x" * 129},
        {"content_id": "<chart>"},
        {"content_id": "chart\r\nBcc: private@example.org"},
        {"content_id": None},
        {"mime_type": "text/plain"},
        {"mime_type": "image/svg+xml"},
        {"mime_type": []},
        {"mime_type": None},
    ],
)
def test_inline_image_rejects_unsafe_or_unbounded_input(changes):
    """Diagnostics never echo the supplied private bytes or header values."""
    with pytest.raises(ConfigError):
        InlineImage(**({"data": b"image", "content_id": "chart"} | changes))


@pytest.mark.parametrize("case", ["no_html", "duplicate", "unknown", "count", "bytes"])
def test_builder_rejects_invalid_inline_collection(case):
    """Bound the complete message too, not just each image independently."""
    image = InlineImage(b"image", "chart")
    message = email(inline_images=(image,))
    if case == "no_html":
        message = replace(message, html=None)
    elif case == "duplicate":
        message = replace(message, inline_images=(image, image))
    elif case == "unknown":
        message = replace(message, inline_images=(object(),))
    elif case == "count":
        message = replace(
            message,
            inline_images=tuple(InlineImage(b"x", f"chart-{i}") for i in range(17)),
        )
    else:
        message = replace(
            message,
            inline_images=(InlineImage(b"x" * MAX_INLINE_BYTES, "large"), image),
        )
    with pytest.raises(ConfigError):
        build_message(message)


def test_html_only_inline_email_still_has_plain_fallback():
    """Related content does not remove the existing provider-neutral fallback."""
    message = build_message(
        replace(email(inline_images=(InlineImage(b"x", "chart"),)), text=None)
    )
    assert "HTML-capable" in message.get_body(preferencelist=("plain",)).get_content()

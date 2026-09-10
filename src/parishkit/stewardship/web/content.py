"""Bounded allowlist HTML, inert substitutions and re-encoded parish graphics."""

import re
import warnings
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
from io import BytesIO
from uuid import uuid4

import nh3
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_TEXT_BYTES = 128 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
TAGS = {"p", "br", "strong", "em", "ul", "ol", "li", "h2", "h3", "blockquote", "a"}
PLACEHOLDERS = frozenset(
    {
        "parish_name",
        "family_name",
        "family_member_names",
        "family_code",
        "family_url",
        "generic_family_url",
        "campaign_name",
        "financial_period",
        "pronoun",
    }
)
PLACEHOLDER = re.compile(r"{{\s*([a-z_]+)\s*}}")


def bounded_text(value):
    """Bound UTF-8 input before parsing; invalid surrogates are rejected safely."""
    try:
        if (
            type(value) is not str
            or len(value.encode("utf-8")) > MAX_TEXT_BYTES
            or "\x00" in value
        ):
            raise ValueError
    except (ValueError, UnicodeError):
        raise ValueError("Content must be bounded valid text.") from None
    return value


def sanitize_html(value):
    """No images, styles, forms, event handlers or executable URL schemes."""
    clean = nh3.clean(
        bounded_text(value),
        tags=TAGS,
        attributes={"a": {"href", "title"}},
        url_schemes={"https", "http", "mailto", "tel"},
        clean_content_tags={"script", "style", "iframe", "object", "svg", "math"},
        link_rel="noopener noreferrer",
        strip_comments=True,
    )
    return bounded_text(clean)


class _PlainText(HTMLParser):
    """Extract readable text only after sanitization, preserving block boundaries."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in {"p", "br", "li", "h2", "h3", "blockquote"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"p", "li", "h2", "h3", "blockquote"}:
            self.parts.append("\n")


@dataclass(frozen=True)
class SafeContent:
    """The owning immutable content record stores these already-sanitized values."""

    html: str
    text: str


def prepare_content(html, *, text=None):
    """Sanitize before storage; allow independently edited, bounded plain text."""
    clean = sanitize_html(html)
    parser = _PlainText()
    parser.feed(clean)
    plain = (
        bounded_text(text)
        if text is not None
        else re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip()
    )
    return SafeContent(clean, plain)


def validate_template(value, *, subject=False):
    """Only simple named placeholders; never evaluate Django/Jinja expressions."""
    bounded_text(value)
    if subject and (len(value) > 254 or any(char in value for char in "\r\n")):
        raise ValueError("Invalid email subject.")
    names = set(PLACEHOLDER.findall(value))
    remainder = PLACEHOLDER.sub("", value)
    if names - PLACEHOLDERS or any(
        marker in remainder for marker in ("{{", "}}", "{%", "%}", "{#", "#}")
    ):
        raise ValueError("Unsupported template placeholder.")
    return frozenset(names)


def render_template(value, substitutions, *, html=False, subject=False):
    """Escape every substitution, then sanitize again at the output boundary."""
    names = validate_template(value, subject=subject)
    if not names <= substitutions.keys():
        raise ValueError("A required template substitution is missing.")
    replacements = {
        name: escape(bounded_text(substitutions[name]), quote=True)
        if html
        else bounded_text(substitutions[name])
        for name in names
    }
    # Count the exact UTF-8 expansion before allocating the combined result.
    # Each escaped replacement is individually bounded by six times the input;
    # repeated placeholders must never multiply that into a gigabyte buffer.
    lengths = {name: len(text.encode("utf-8")) for name, text in replacements.items()}
    total = len(value.encode("utf-8"))
    for match in PLACEHOLDER.finditer(value):
        total += lengths[match[1]] - len(match[0].encode("utf-8"))
    if total > MAX_TEXT_BYTES:
        raise ValueError("Template output exceeds the content limit.")
    rendered = PLACEHOLDER.sub(lambda match: replacements[match[1]], value)
    if subject and (len(rendered) > 254 or any(char in rendered for char in "\r\n")):
        raise ValueError("Invalid email subject substitution.")
    bounded_text(rendered)
    return sanitize_html(rendered) if html else rendered


@dataclass(frozen=True)
class Graphic:
    """Validated bytes with a random server filename, no retained upload metadata."""

    name: str
    content_type: str
    data: bytes
    width: int
    height: int


def prepare_graphics(upload):
    """Bounded PNG/JPEG/WebP input; static PNG variants strip EXIF and active data."""
    try:
        data = upload.read(MAX_IMAGE_BYTES + 1)
        if not isinstance(data, bytes) or not 0 < len(data) <= MAX_IMAGE_BYTES:
            raise ValueError
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as probe:
                if (
                    probe.format not in {"PNG", "JPEG", "WEBP"}
                    or probe.width * probe.height > MAX_IMAGE_PIXELS
                    or getattr(probe, "n_frames", 1) != 1
                ):
                    raise ValueError
                probe.verify()
            with Image.open(BytesIO(data)) as source:
                source.load()
                normalized = ImageOps.exif_transpose(source).convert("RGBA")
                # Every output is at most 1,024 pixels per side. Bound the pixel
                # copy before discarding metadata rather than making several
                # full-resolution RGBA buffers from an accepted 16 MP upload.
                normalized.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                # A fresh pixel buffer deliberately discards EXIF/ICC/text data.
                clean = Image.frombytes("RGBA", normalized.size, normalized.tobytes())
        result = {}
        for label, size in (("large", 1024), ("small", 128), ("favicon", 32)):
            variant = clean.copy()
            variant.thumbnail((size, size), Image.Resampling.LANCZOS)
            output = BytesIO()
            variant.save(output, format="PNG")
            result[label] = Graphic(
                f"{uuid4().hex}.png", "image/png", output.getvalue(), *variant.size
            )
        return result
    except (
        OSError,
        ValueError,
        SyntaxError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise ValueError("Upload must be a supported, bounded static image.") from None

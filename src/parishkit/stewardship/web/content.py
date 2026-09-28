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
        "parish_website",
        "parish_phone",
        "parish_email",
        "online_giving_url",
        "family_name",
        "family_member_names",
        "family_code",
        "family_url",
        "generic_family_url",
        "campaign_name",
        "campaign_start",
        "campaign_end",
        "campaign_timezone",
        "campaign_year",
        "financial_period",
        "financial_start",
        "financial_end",
        "pronoun",
    }
)
PLACEHOLDER = re.compile(r"{{\s*([a-z_]+)\s*}}")
FAMILY_CREDENTIAL_PLACEHOLDERS = frozenset({"family_code", "family_url"})
ADMIN_DIGEST_PLACEHOLDERS = PLACEHOLDERS - {
    "family_name",
    "family_member_names",
    "family_code",
    "family_url",
    "generic_family_url",
    "pronoun",
}
FAMILY_CODE_MARKER = "PARISHKIT_REDACTED_FAMILY_CODE"
FAMILY_LINK_MARKER = "https://parishkit.invalid/redacted-family-link"
# Keep identical to the non-sendable seed in stewardship_receipt_seed_v1.
RECEIPT_ALLOCATION_MARKER = "PARISHKIT_PENDING_RECEIPT"
DIGEST_ALLOCATION_MARKER = "PARISHKIT_PENDING_DAILY_DIGEST"
SHARE_PLACEHOLDERS = frozenset(
    {
        "parish_name",
        "pronoun",
        "campaign_year",
        "financial_period",
        "financial_start",
        "financial_end",
    }
)


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


# Line and section wrappers that browsers' editable regions and pasted
# documents use instead of <p>. They are never stored: _normalize turns them
# into paragraphs so their line and paragraph structure survives sanitizing.
_WRAPPERS = frozenset({"div", "section", "article", "header", "footer", "main"})
# Presentational spellings of allowed tags, renamed rather than stripped.
_RENAMES = {"b": "strong", "i": "em", "h1": "h2", "h4": "h3", "h5": "h3", "h6": "h3"}
_BLOCKS = frozenset({"p", "h2", "h3", "ul", "ol", "blockquote"})
# Any tag that _normalize must rewrite, in nh3's canonical serialization.
_REWRITTEN = re.compile(
    "<(?:" + "|".join(sorted(_WRAPPERS | _RENAMES.keys())) + r")[\s>]"
)
_MARKUP = re.compile(r"<[A-Za-z!/?]")
_BR = ("br", [], [])


# Elements whose content is dropped along with the tag, and allowed link
# schemes; removed_markup reports against the same policy.
_DROPPED_WITH_CONTENT = frozenset(
    {"script", "style", "iframe", "object", "svg", "math"}
)
_LINK_SCHEMES = frozenset({"https", "http", "mailto", "tel"})


def _clean(value, tags):
    """The one nh3 policy: no images, styles, forms, handlers or unsafe schemes."""
    return nh3.clean(
        value,
        tags=tags,
        attributes={"a": {"href", "title"}},
        url_schemes=set(_LINK_SCHEMES),
        clean_content_tags=set(_DROPPED_WITH_CONTENT),
        link_rel="noopener noreferrer",
        strip_comments=True,
    )


def _plain_paragraphs(value):
    """Markup-free text with line breaks: blank lines separate paragraphs.

    Someone may type or paste ordinary text into the HTML source box. HTML
    would collapse its line breaks into one run-on paragraph, so blank lines
    become paragraphs and single line breaks become <br>. Text is not escaped
    here; the following nh3 pass parses it exactly as it would have anyway.
    """
    paragraphs = re.split(r"\n[ \t]*\n\s*", value.replace("\r\n", "\n").strip())
    return "".join(
        "<p>" + "<br>".join(paragraph.split("\n")) + "</p>"
        for paragraph in paragraphs
        if paragraph.strip()
    )


class _Tree(HTMLParser):
    """Parse nh3's well-formed output into (tag, attrs, children) and strings."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = ("", [], [])
        self.stack = [self.root]

    def handle_data(self, data):
        self.stack[-1][2].append(data)

    def handle_starttag(self, tag, attrs):
        node = (_RENAMES.get(tag, tag), attrs, [])
        self.stack[-1][2].append(node)
        if tag != "br":
            self.stack.append(node)

    def handle_endtag(self, tag):
        tag = _RENAMES.get(tag, tag)
        if tag != "br" and any(node[0] == tag for node in self.stack[1:]):
            while self.stack.pop()[0] != tag:
                pass


def _serialize(nodes):
    """Write a normalized tree back as HTML for the final nh3 pass."""
    parts = []
    for node in nodes:
        if isinstance(node, str):
            parts.append(escape(node, quote=False))
            continue
        tag, attrs, children = node
        parts.append(
            "<"
            + tag
            + "".join(
                f' {name}="{escape(value or "", quote=True)}"' for name, value in attrs
            )
            + ">"
        )
        if tag != "br":
            parts.append(_serialize(children) + f"</{tag}>")
    return "".join(parts)


def _is_block(node):
    """True for a paragraph-level element or a wrapper standing in for one."""
    return not isinstance(node, str) and node[0] in _BLOCKS | _WRAPPERS


def _meaningful(nodes):
    """True when inline content has visible text, not only spaces and <br>."""
    return any(
        (
            node.replace("\xa0", " ").strip()
            if isinstance(node, str)
            else node[0] != "br"
        )
        for node in nodes
    )


def _trim_breaks(nodes):
    """Drop <br> and blank text at the edges of a line wrapper's content."""
    nodes = list(nodes)
    for index in (0, -1):
        while nodes and (
            nodes[index] == _BR
            or (isinstance(nodes[index], str) and not nodes[index].strip())
        ):
            nodes.pop(index)
    return nodes


def _inline(nodes):
    """Normalize inline content; nested wrappers or blocks become line breaks."""
    result = []
    for node in nodes:
        if isinstance(node, str):
            result.append(node)
        elif node[0] in {"ul", "ol"}:
            result.append(_block(node))
        elif _is_block(node):
            inner = _trim_breaks(_inline(node[2]))
            if result and _meaningful(inner) and result[-1] != _BR:
                result.append(_BR)
            result.extend(inner)
        else:
            result.append((node[0], node[1], _inline(node[2])))
    return result


def _block(node):
    """Normalize one block element's children according to its content model."""
    tag, attrs, children = node
    if tag == "blockquote":
        return (tag, attrs, _flow(children))
    if tag in {"ul", "ol"}:
        return (
            tag,
            attrs,
            [
                _block(child)
                if not isinstance(child, str) and child[0] == "li"
                else child
                for child in children
            ],
        )
    return (tag, attrs, _inline(children))


def _flow(nodes, *, wrapped=False):
    """Normalize a container (the document, a blockquote or a wrapper) of blocks.

    A container without wrappers (other than a wrapper's own content, which
    is ``wrapped``) is only normalized below it, so canonical
    content (and a concatenation of canonical parts, such as a TEST banner
    and a template) is left exactly as it is. Where a wrapper is present,
    wrappers of inline content become paragraphs, wrappers of blocks are
    unwrapped, loose inline runs beside them become paragraphs, and runs of
    only spaces or <br> (an editor's blank line) are dropped.
    """
    if not wrapped and not any(
        not isinstance(node, str) and node[0] in _WRAPPERS for node in nodes
    ):
        return [
            node
            if isinstance(node, str)
            else _block(node)
            if _is_block(node)
            else (node[0], node[1], _inline(node[2]))
            for node in nodes
        ]
    result, run = [], []

    def flush():
        """Close the pending inline run as a paragraph, or keep only its spaces."""
        if _meaningful(run):
            result.append(("p", [], _trim_breaks(_inline(run))))
        else:
            result.extend(node for node in run if isinstance(node, str))
        run.clear()

    for node in nodes:
        if not _is_block(node):
            run.append(node)
            continue
        flush()
        if node[0] not in _WRAPPERS:
            result.append(_block(node))
        elif any(_is_block(child) for child in node[2]):
            result.extend(_flow(node[2], wrapped=True))
        else:
            run.extend(node[2])
            flush()
    flush()
    return result


def _normalize(clean):
    """Rewrite wrappers and presentational tags in nh3 output (see _flow)."""
    parser = _Tree()
    parser.feed(clean)
    parser.close()
    return _serialize(_flow(parser.root[2]))


def sanitize_html(value):
    """No images, styles, forms, event handlers or executable URL schemes.

    Structure survives: browser line wrappers (<div>) become paragraphs and
    <b>/<i> become <strong>/<em> before the allowlist would strip them to bare
    text, and markup-free text keeps its paragraphs. Content that is already
    canonical passes through unchanged, so stored content stays a fixed point.
    """
    value = bounded_text(value)
    if "\n" in value and not _MARKUP.search(value):
        value = _plain_paragraphs(value)
    staged = _clean(value, TAGS | _WRAPPERS | _RENAMES.keys())
    if _REWRITTEN.search(staged):
        value = _normalize(staged)
    return bounded_text(_clean(value, TAGS))


class _PlainText(HTMLParser):
    """Extract readable text only after sanitization, preserving block boundaries.

    Paragraphs are separated by a blank line, list items start with "- " (or
    "1. " in numbered lists) and each link is written "label: URL" so its
    target survives in the plain-text alternative.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.lists = []
        self.link = None

    def handle_data(self, data):
        self.parts.append(data)
        if self.link is not None:
            self.link[1].append(data)

    def handle_starttag(self, tag, attrs):
        # A nested list continues its item's lines; only the outermost list
        # is set apart from surrounding paragraphs.
        if tag in {"p", "br", "h2", "h3", "blockquote"} or (
            tag in {"ul", "ol"} and not self.lists
        ):
            self.parts.append("\n")
        if tag in {"ul", "ol"}:
            self.lists.append(0 if tag == "ol" else None)
        elif tag == "li":
            depth = max(len(self.lists) - 1, 0)
            number = self.lists[-1] if self.lists else None
            if number is not None:
                self.lists[-1] = number = number + 1
            self.parts.append("\n" + "  " * depth + (f"{number}. " if number else "- "))
        elif tag == "a":
            self.link = (dict(attrs).get("href") or "", [])

    def handle_endtag(self, tag):
        if tag in {"ul", "ol"} and self.lists:
            self.lists.pop()
        if tag in {"p", "h2", "h3", "blockquote"} or (
            tag in {"ul", "ol"} and not self.lists
        ):
            self.parts.append("\n")
        elif tag == "a" and self.link is not None:
            href, label = self.link[0], "".join(self.link[1]).strip()
            self.link = None
            # A bare "mailto:"/"tel:" target repeats the visible address.
            target = re.sub(r"^(mailto|tel):", "", href)
            if href and target != label:
                self.parts.append(": " + href if label else href)


_SCHEME = re.compile(r"^\s*([a-z][a-z0-9+.-]*):", re.IGNORECASE)


class _Markup(HTMLParser):
    """Record the raw tags, attributes and comments of unsanitized input.

    Only parsed, never rendered: this reports what the sanitizer will drop.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.comments = False
        # Depth inside an element dropped with its content: its descendants
        # vanish with it, so they are not reported separately.
        self.dropped = 0

    def handle_starttag(self, tag, attrs):
        if not self.dropped:
            self.tags.append((tag, attrs))
        if tag in _DROPPED_WITH_CONTENT:
            self.dropped += 1

    def handle_endtag(self, tag):
        if tag in _DROPPED_WITH_CONTENT and self.dropped:
            self.dropped -= 1

    def handle_startendtag(self, tag, attrs):
        if not self.dropped:
            self.tags.append((tag, attrs))

    def handle_comment(self, data):
        self.comments = True


def removed_markup(value):
    """Describe, in plain words, the markup that sanitize_html drops from value.

    The live visual editor shows this beside the sanitized preview, so an
    Admin who pastes HTML knows why an element, attribute or link vanished.
    Structure-preserving rewrites (div to paragraph, b to strong) are not
    removals and are not reported. The result is sorted and bounded.
    """
    parser = _Markup()
    parser.feed(bounded_text(value))
    parser.close()
    kept = TAGS | _WRAPPERS | _RENAMES.keys()
    removed = set()
    for tag, attrs in parser.tags:
        if tag not in kept:
            removed.add(
                f"<{tag}> element and its content"
                if tag in _DROPPED_WITH_CONTENT
                else f"<{tag}> element"
            )
            continue
        for name, attribute in attrs:
            if tag == "a" and name in {"href", "title", "rel"}:
                scheme = _SCHEME.match(attribute or "") if name == "href" else None
                if scheme and scheme.group(1).lower() not in _LINK_SCHEMES:
                    removed.add(f"{scheme.group(1).lower()}: link target")
                continue
            removed.add(f"{name} attribute")
    if parser.comments:
        removed.add("HTML comments")
    return sorted(removed)[:20]


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


def family_email_problems(subject, html, text):
    """Every way an invitation/reminder breaks the access contract, per part.

    Returns ``(part, problem, names)`` tuples in a fixed order: ``part`` is
    "subject", "html" or "text"; ``problem`` is "credential" (an access
    placeholder in the subject), "reserved" (a reserved system marker) or
    "missing" (a required placeholder absent from a body); ``names`` are the
    placeholders concerned. Editors turn these into specific messages.
    """
    problems = []
    names = validate_template(subject, subject=True) & FAMILY_CREDENTIAL_PLACEHOLDERS
    if names:
        problems.append(("subject", "credential", tuple(sorted(names))))
    for part, value in (("subject", subject), ("html", html), ("text", text)):
        if FAMILY_CODE_MARKER in value or FAMILY_LINK_MARKER in value:
            problems.append((part, "reserved", ()))
    for part, value in (("html", html), ("text", text)):
        missing = FAMILY_CREDENTIAL_PLACEHOLDERS - validate_template(value)
        if missing:
            problems.append((part, "missing", tuple(sorted(missing))))
    return problems


def validate_family_email(subject, html, text):
    """Share the invitation/reminder contract across editing, apply and rendering.

    Access credentials belong in both body alternatives, never a subject/header.
    Generated plaintext is checked after HTML extraction, so an href-only link
    cannot silently disappear from that alternative. Authors can edit the plain
    text explicitly when extraction cannot preserve the required placeholders.
    """
    messages = {
        "credential": "Family credentials belong in email bodies, not subjects.",
        "reserved": "Family email contains a reserved placeholder.",
        "missing": "Each Family email body requires its code and link.",
    }
    problems = family_email_problems(subject, html, text)
    if problems:
        raise ValueError(messages[problems[0][1]])


def validate_receipt_content(subject, html, text):
    """Receipts never substitute access credentials, including in optional prose.

    Use this at authoring, configuration apply and rendering. Reserved markers
    are forbidden too: a receipt is never a credential-bearing dispatch input.
    An empty subject is allowed for the separately authored body-only block;
    the email template and final envelope enforce a nonempty subject.
    """
    for value, header in ((subject, True), (html, False), (text, False)):
        if validate_template(value, subject=header) & FAMILY_CREDENTIAL_PLACEHOLDERS:
            raise ValueError("Submission receipts cannot contain access credentials.")
        if any(
            marker in value
            for marker in (
                FAMILY_CODE_MARKER,
                FAMILY_LINK_MARKER,
                RECEIPT_ALLOCATION_MARKER,
            )
        ):
            raise ValueError("Submission receipts cannot contain reserved markers.")


def validate_admin_digest_content(subject, html, text):
    """Digest templates use public parish/campaign facts, never a chosen Family."""
    for value, header in ((subject, True), (html, False), (text, False)):
        if not validate_template(value, subject=header) <= ADMIN_DIGEST_PLACEHOLDERS:
            raise ValueError("Admin digests require public campaign placeholders.")
        if any(
            marker in value
            for marker in (
                FAMILY_CODE_MARKER,
                FAMILY_LINK_MARKER,
                RECEIPT_ALLOCATION_MARKER,
                DIGEST_ALLOCATION_MARKER,
            )
        ):
            raise ValueError("Admin digests cannot contain reserved markers.")


def validate_share_label(value):
    """Share options permit only non-private parish, period and pronoun values."""
    if type(value) is not str or not value.strip() or len(value) > 1024:
        raise ValueError("A bounded share-option label is required.")
    if not validate_template(value) <= SHARE_PLACEHOLDERS:
        raise ValueError("Unsupported share-option placeholder.")
    return value


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

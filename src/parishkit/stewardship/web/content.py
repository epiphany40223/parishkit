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
TAGS = {
    "p",
    "br",
    "strong",
    "em",
    "ul",
    "ol",
    "li",
    "h2",
    "h3",
    "blockquote",
    "a",
    "img",
}
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
# A hosted file (#346): {{ file.<slug> }}, with only spaces inside the braces
# so stored text and the database's in-use search match exactly.
FILE_PLACEHOLDER = re.compile(r"{{ *file\.([a-z0-9]+(?:-[a-z0-9]+)*) *}}")
# A hosted file's public link path (see accounts.hosted_files).
_FILE_LINK = r"/files/[A-Za-z0-9_-]{43}"
# What a placeholder for a file that no longer exists expands to: a link
# that shows the "no longer available" page and is never an image source.
UNAVAILABLE_FILE_PATH = "/files/unavailable"
MAX_ALT = 250
MAX_IMAGE_WIDTH = 2048
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
# Elements with no content or end tag.
_VOID = frozenset({"br", "img"})


# Elements whose content is dropped along with the tag, and allowed link
# schemes; removed_markup reports against the same policy.
_DROPPED_WITH_CONTENT = frozenset(
    {"script", "style", "iframe", "object", "svg", "math"}
)
_LINK_SCHEMES = frozenset({"https", "http", "mailto", "tel"})


def _image_source(value, origin):
    """Whether ``value`` is an accepted inline image source (#346).

    Without an origin (content being authored or stored) only the canonical
    file placeholder is accepted; with one (rendered output and the delivery
    checks) only this deployment's hosted-file link is.
    """
    if origin is None:
        return FILE_PLACEHOLDER.fullmatch(value) is not None
    return re.fullmatch(re.escape(origin) + _FILE_LINK, value) is not None


def _attribute_filter(origin):
    """Keep only valid hosted-image attributes; links pass unchanged."""

    def keep(element, attribute, value):
        if element != "img":
            return value
        if attribute == "src":
            return value if _image_source(value, origin) else None
        if attribute == "alt":
            return value if len(value) <= MAX_ALT else None
        if attribute == "width":
            valid = value.isascii() and value.isdecimal() and len(value) <= 4
            return value if valid and 1 <= int(value) <= MAX_IMAGE_WIDTH else None
        return None

    return keep


# One image tag as nh3 writes it: each attribute double-quoted, and a value
# never holds a raw quote (it may hold ">", so match attribute by attribute).
_IMAGE_TAG = re.compile(r'<img((?:\s+[a-z-]+="[^"]*")*)\s*/?>')
_ATTRIBUTE = re.compile(r'\s+([a-z-]+)="([^"]*)"')


def _image_attributes(tag):
    """The attributes of one ``_IMAGE_TAG`` match, as a dict."""
    return dict(_ATTRIBUTE.findall(tag[1]))


def _drop_sourceless(html):
    """Remove each image whose source was not a hosted image (nh3 kept the tag)."""
    return _IMAGE_TAG.sub(
        lambda tag: tag[0] if "src" in _image_attributes(tag) else "", html
    )


def _clean(value, tags, origin=None):
    """The one nh3 policy: no styles, forms, handlers or unsafe schemes.

    The only images are hosted ones (see ``_image_source``).
    """
    clean = nh3.clean(
        value,
        tags=tags,
        attributes={"a": {"href", "title"}, "img": {"src", "alt", "width"}},
        attribute_filter=_attribute_filter(origin),
        url_schemes=set(_LINK_SCHEMES),
        clean_content_tags=set(_DROPPED_WITH_CONTENT),
        link_rel="noopener noreferrer",
        strip_comments=True,
    )
    return _drop_sourceless(clean)


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
        if tag not in _VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        tag = _RENAMES.get(tag, tag)
        if tag not in _VOID and any(node[0] == tag for node in self.stack[1:]):
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
        if tag not in _VOID:
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


def text_html(value):
    """Escape plain text for HTML exactly as the sanitizer writes text.

    Server-built fragments (receipt facts, Testing banners) are compared
    against sanitizer output by the delivery check, so they must match it:
    quotes stay literal, a non-breaking space (common in names pasted from
    word processors) is written as ``&nbsp;``, and CR or CRLF line endings
    become LF, as the HTML parser normalizes them.
    """
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return escape(value, quote=False).replace("\u00a0", "&nbsp;")


def sanitize_html(value, *, origin=None):
    """No styles, forms, event handlers, executable URL schemes or foreign images.

    Structure survives: browser line wrappers (<div>) become paragraphs and
    <b>/<i> become <strong>/<em> before the allowlist would strip them to bare
    text, and markup-free text keeps its paragraphs. Content that is already
    canonical passes through unchanged, so stored content stays a fixed point.
    An ``<img>`` survives only as a hosted image (#346): a file placeholder in
    stored content, or, when ``origin`` is given (rendered output and the
    delivery checks), this deployment's hosted-file link.
    """
    value = bounded_text(value)
    if "\n" in value and not _MARKUP.search(value):
        value = _plain_paragraphs(value)
    staged = _clean(value, TAGS | _WRAPPERS | _RENAMES.keys(), origin)
    if _REWRITTEN.search(staged):
        value = _normalize(staged)
    return bounded_text(_clean(value, TAGS, origin))


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
        if tag == "img":
            values = dict(attrs)
            if not _image_source(values.get("src") or "", None):
                removed.add("image not from the hosted file library")
                continue
            for name in values.keys() - {"src", "alt", "width"}:
                removed.add(f"{name} attribute")
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


def prepare_content(html, *, text=None, origin=None):
    """Sanitize before storage; allow independently edited, bounded plain text.

    ``origin`` admits rendered hosted-image links (see ``sanitize_html``);
    content being stored never passes one.
    """
    clean = sanitize_html(html, origin=origin)
    parser = _PlainText()
    parser.feed(clean)
    plain = (
        bounded_text(text)
        if text is not None
        else re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip()
    )
    return SafeContent(clean, plain)


def file_references(value):
    """The hosted-file slugs a template names with ``{{ file.<slug> }}``."""
    return frozenset(FILE_PLACEHOLDER.findall(bounded_text(value)))


def image_references(html):
    """The slugs sanitized HTML shows as inline images, and whether one lacks alt.

    Only images whose source is a file placeholder survive sanitizing, so
    this sees every inline image in stored content.
    """
    slugs, missing_alt = set(), False
    for tag in _IMAGE_TAG.finditer(html):
        attributes = _image_attributes(tag)
        source = attributes.get("src", "")
        if FILE_PLACEHOLDER.fullmatch(source):
            slugs.add(FILE_PLACEHOLDER.fullmatch(source)[1])
            missing_alt = missing_alt or "alt" not in attributes
    return frozenset(slugs), missing_alt


def validate_template(value, *, subject=False):
    """Only simple named placeholders; never evaluate Django/Jinja expressions.

    Returns the named placeholders. Hosted-file placeholders (#346) are
    accepted in bodies and returned separately by ``file_references``; a
    subject cannot carry one.
    """
    bounded_text(value)
    if subject and (len(value) > 254 or any(char in value for char in "\r\n")):
        raise ValueError("Invalid email subject.")
    names = set(PLACEHOLDER.findall(value))
    remainder = PLACEHOLDER.sub("", value)
    files = FILE_PLACEHOLDER.findall(remainder)
    remainder = FILE_PLACEHOLDER.sub("", remainder)
    if (
        (subject and files)
        or names - PLACEHOLDERS
        or any(marker in remainder for marker in ("{{", "}}", "{%", "%}", "{#", "#}"))
    ):
        raise ValueError("Unsupported template placeholder.")
    return frozenset(names)


@dataclass(frozen=True)
class HostedLinks:
    """The public link of every hosted file (#346), for one render.

    ``origin`` is the deployment's public origin and ``urls`` maps each slug
    to its absolute link. A slug with no file (possible only after a restore,
    or for a reopened archived campaign) links to the unavailable page.
    """

    origin: str
    urls: dict

    def url(self, slug):
        """The absolute link for ``slug``."""
        return self.urls.get(slug) or self.origin + UNAVAILABLE_FILE_PATH


def render_template(value, substitutions, *, html=False, subject=False, files=None):
    """Escape every substitution, then sanitize again at the output boundary.

    ``files`` (``HostedLinks``) expands hosted-file placeholders; content that
    names a file cannot render without it.
    """
    names = validate_template(value, subject=subject)
    if not names <= substitutions.keys():
        raise ValueError("A required template substitution is missing.")
    if file_references(value):
        if not isinstance(files, HostedLinks):
            raise ValueError("Hosted file links are required.")
        value = FILE_PLACEHOLDER.sub(
            lambda match: escape(files.url(match[1]), quote=True), value
        )
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
    if not html:
        return rendered
    return sanitize_html(rendered, origin=files.origin if files else None)


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
    """Digest templates use public parish/campaign facts, never a chosen Family.

    Hosted files (#346) are for parishioners, so Admin digests carry none.
    """
    for value, header in ((subject, True), (html, False), (text, False)):
        if not validate_template(value, subject=header) <= ADMIN_DIGEST_PLACEHOLDERS:
            raise ValueError("Admin digests require public campaign placeholders.")
        if file_references(value) or "<img" in value:
            raise ValueError("Admin digests cannot link hosted files.")
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
    if not validate_template(value) <= SHARE_PLACEHOLDERS or file_references(value):
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
    return _normalized_variants(
        upload, (("large", 1024), ("small", 128), ("favicon", 32))
    )


# Campaign artwork (#248): one normalized PNG, fitted within this many pixels
# per side. A banner is shown up to about 600 CSS px wide; a section icon at
# about 96 CSS px, so 256 stays sharp on high-density phone screens.
ARTWORK_SIZES = {"banner": 1024, "section": 256}


def prepare_artwork(upload, label):
    """Normalize one campaign banner or section icon like a logo upload.

    A banner must be wide (at least 2:1) and an icon roughly square (at most
    2:1 either way), so neither can crowd a phone screen or a Family page.
    """
    if label not in ARTWORK_SIZES:
        raise ValueError("Unknown artwork kind.")
    graphics = _normalized_variants(upload, ((label, ARTWORK_SIZES[label]),))
    graphic = graphics[label]
    ratio = graphic.width / graphic.height
    if (label == "banner" and ratio < 2) or (
        label == "section" and not 0.5 <= ratio <= 2
    ):
        raise ValueError("Upload does not have the expected shape.")
    return graphics


def _normalized_variants(upload, sizes):
    """Decode one bounded upload once and emit fresh PNGs fitted to each size.

    Every output is re-encoded from a fresh pixel buffer, so EXIF, ICC and
    text chunks from the upload are never kept.
    """
    try:
        data = upload.read(MAX_IMAGE_BYTES + 1)
        if not isinstance(data, bytes) or not 0 < len(data) <= MAX_IMAGE_BYTES:
            raise ValueError
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data), formats=("PNG", "JPEG", "WEBP")) as probe:
                if (
                    probe.format not in {"PNG", "JPEG", "WEBP"}
                    or probe.width * probe.height > MAX_IMAGE_PIXELS
                    or getattr(probe, "n_frames", 1) != 1
                ):
                    raise ValueError
                probe.verify()
            with Image.open(BytesIO(data), formats=("PNG", "JPEG", "WEBP")) as source:
                source.load()
                normalized = ImageOps.exif_transpose(source).convert("RGBA")
                # Every output is at most 1,024 pixels per side. Bound the pixel
                # copy before discarding metadata rather than making several
                # full-resolution RGBA buffers from an accepted 16 MP upload.
                normalized.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                # A fresh pixel buffer deliberately discards EXIF/ICC/text data.
                clean = Image.frombytes("RGBA", normalized.size, normalized.tobytes())
        result = {}
        for label, size in sizes:
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


# Widest banner a Family email shows; most email layouts are about 600px wide.
EMAIL_BANNER_WIDTH = 600


def email_banner(image, alt):
    """Server-built banner image for the top of a Family email (#248).

    ``image`` is ``{"url", "width", "height"}`` for a published campaign
    banner, or ``None`` for no banner. Parish content itself never carries
    images; this markup comes only from here. Email clients need absolute
    HTTPS image addresses, so any other address yields no banner at all.
    The inline style keeps the banner fluid on narrow phone mail clients.
    """
    if not image:
        return ""
    url = image["url"]
    if type(url) is not str or not url.startswith("https://") or '"' in url:
        return ""
    width = min(image["width"], EMAIL_BANNER_WIDTH)
    height = max(1, round(image["height"] * width / image["width"]))
    return (
        f'<p><img src="{escape(url, quote=True)}" alt="{escape(alt, quote=True)}" '
        f'width="{width}" height="{height}" style="display:block;width:100%;'
        f'max-width:{width}px;height:auto;border:0"></p>'
    )


# The fixed Testing notice route_family_mail() puts before any banner; its
# description text is escaped, so it contains no "<".
_TEST_NOTICE = r"(?:<h2>TEST</h2><p>[^<]*</p>)?"
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def without_email_banner(html, origin):
    """Remove the one server-built campaign banner (#248) before content checks.

    Family email HTML is otherwise exactly sanitized parish content, which
    never admits images. Only exactly what email_banner() builds is allowed,
    only once, only first (after the fixed Testing notice, if any), only from
    this deployment's public HTTPS ``origin``, and only a wide image (at least
    twice as wide as tall), so it can never serve as a hidden tracking pixel.
    Anything else is left in place and fails the sanitizer comparison.
    """
    if type(origin) is not str or not re.fullmatch(
        r"https://[A-Za-z0-9.\-]+(?::[0-9]{1,5})?", origin
    ):
        return html
    match = re.match(
        _TEST_NOTICE
        + r'(<p><img src="'
        + re.escape(origin)
        + r"/branding/"
        + _UUID
        + r'\.png" alt="[^"<>]*" width="([0-9]{1,4})" height="([0-9]{1,4})" '
        r'style="display:block;width:100%;max-width:\2px;height:auto;border:0">'
        r"</p>)",
        html,
    )
    if not match or int(match.group(2)) < 2 * int(match.group(3)):
        return html
    return html[: match.start(1)] + html[match.end(1) :]

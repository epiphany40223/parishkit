"""The zip and XML safety layer of the campaign archive reader (#1018).

An archive workbook is untrusted input: a hand-edited or hostile file could
be a compression bomb, carry macros, pull data from another workbook or a
remote source, or hide an XML entity attack. ``check_package`` refuses all of
that from the raw bytes, before openpyxl opens anything, in this order (see
docs/specs/stewardship/campaign-history/spec.md, "Validation", check 1):

1. the file size;
2. the zip directory: every part on the allowlist of plain cell-data parts,
   no two names equal ignoring case, and the declared sizes within the
   limits (an early refusal only, since the directory can lie);
3. every part read once as a stream that counts the bytes actually
   decompressed, stopping at the per-part, total and ratio limits, and every
   XML part refused if it holds a document type or entity declaration, is
   not UTF-8, or holds a NUL byte (which only UTF-16 or UTF-32 text would);
4. the hosted-files OOXML checks (no macros, ActiveX or OLE objects, an
   ``.xlsx`` main part); and
5. every relationship internal, and a worksheet's relationships only to its
   printer settings.

Only the standard library is used; openpyxl reads the bytes afterwards from
memory, so what it decompresses is exactly what was checked here (zipfile
stops each part at its declared size and verifies its CRC).
"""

import re
import xml.etree.ElementTree as ElementTree
import zipfile
import zlib
from io import BytesIO

MIB = 1024 * 1024
# One upload is at most 20 MB; the converted 2020-2026 files are about 2 MB.
MAX_FILE_BYTES = 20 * MIB
MAX_PART_BYTES = 100 * MIB
MAX_TOTAL_BYTES = 300 * MIB
# The ratio is checked only once a part has decompressed this much, so small
# and highly repetitive parts (a theme, an empty sheet) are never refused.
RATIO_FLOOR_BYTES = MIB
# Decompressed bytes per compressed byte; the converted files peak near 15.
MAX_RATIO = 100
CHUNK_BYTES = 64 * 1024
MAX_PARTS = 10_000

# The parts a spreadsheet program writes for plain cell data, and nothing
# else. Matched case-sensitively against the whole name: Excel, LibreOffice,
# Google Sheets and openpyxl all write these exact spellings.
ALLOWED_PARTS = re.compile(
    r"\[Content_Types\]\.xml"
    r"|_rels/\.rels"
    r"|docProps/(?:app|core|custom)\.xml"
    r"|customXml/(?:item\d+\.xml|itemProps\d+\.xml|_rels/item\d+\.xml\.rels)"
    r"|xl/workbook\.xml"
    r"|xl/_rels/workbook\.xml\.rels"
    r"|xl/worksheets/sheet\d+\.xml"
    r"|xl/worksheets/_rels/sheet\d+\.xml\.rels"
    r"|xl/printerSettings/printerSettings\d+\.bin"
    r"|xl/styles\.xml"
    r"|xl/theme/theme\d+\.xml"
    r"|xl/sharedStrings\.xml"
    r"|xl/calcChain\.xml"
)
SHEET_RELATIONSHIPS = re.compile(r"xl/worksheets/_rels/sheet\d+\.xml\.rels")
# What a refused part is, by its folder, so the reason can say what was
# found without repeating a name the file chose. Checked in order.
REFUSED_KINDS = (
    ("vbaproject", "macro"),
    ("activex/", "macro"),
    ("macrosheets/", "macro"),
    ("externallinks/", "an external workbook link"),
    ("connections.xml", "a data connection"),
    ("querytables/", "a query table"),
    ("embeddings/", "an embedded object"),
    ("drawings/", "a drawing"),
    ("charts/", "a chart"),
    ("media/", "an image"),
    ("pivot", "a pivot table"),
    ("slicer", "a slicer"),
    ("comments", "a comment"),
)
# An XML declaration's encoding; only UTF-8 is accepted, because the
# declaration checks below search the bytes as UTF-8 text.
XML_ENCODING = re.compile(rb"""^<\?xml[^>]*?\sencoding\s*=\s*["']([^"']*)["']""")
UTF8_BOM = b"\xef\xbb\xbf"
DECLARATIONS = (b"<!doctype", b"<!entity")


class PackageRefused(Exception):
    """The package is refused; ``code`` and ``rule`` are fixed text.

    Neither ever repeats a part name, a value or anything else the file
    chose. ``code`` is one of: ``file_size``, ``unreadable``, ``not_xlsx``,
    ``macro``, ``part_not_allowed``, ``external_relationship`` and
    ``unsafe_to_read``.
    """

    def __init__(self, code, rule):
        super().__init__(rule)
        self.code = code
        self.rule = rule


def check_package(data):
    """Refuse an unsafe or non-plain workbook package; return None when safe."""
    if not data:
        raise PackageRefused("file_size", "the file is empty")
    if len(data) > MAX_FILE_BYTES:
        raise PackageRefused("file_size", "the file is larger than 20 MB")
    try:
        with zipfile.ZipFile(BytesIO(data)) as package:
            entries = package.infolist()
            _check_directory(entries)
            relationships = [
                (entry.filename, text)
                for entry, text in _read_parts(package, entries)
                if entry.filename.endswith(".rels")
            ]
    except PackageRefused:
        raise
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        KeyError,
        RuntimeError,
        NotImplementedError,
        EOFError,
        OSError,
        ValueError,
        zlib.error,
    ):
        # A damaged zip, a bad CRC (a part longer or shorter than its
        # directory entry says) or an encrypted part all land here.
        raise PackageRefused("unreadable", "the file is not a readable .xlsx") from None
    _check_office(data)
    for name, text in relationships:
        _check_relationships(name, text)


def _check_directory(entries):
    """Check part names and declared sizes before anything is decompressed."""
    if len(entries) > MAX_PARTS:
        raise PackageRefused("unsafe_to_read", "the package has too many parts")
    seen, declared = set(), 0
    for entry in entries:
        name = entry.filename
        if name.lower() in seen:
            raise PackageRefused("not_xlsx", "the package repeats a part name")
        seen.add(name.lower())
        if not ALLOWED_PARTS.fullmatch(name):
            raise PackageRefused(*_refused_part(name))
        if entry.flag_bits & 0x1:
            raise PackageRefused("unreadable", "the package is encrypted")
        declared += entry.file_size
        if (
            entry.file_size > MAX_PART_BYTES
            or declared > MAX_TOTAL_BYTES
            or _ratio_exceeded(entry.file_size, entry.compress_size)
        ):
            raise PackageRefused(
                "unsafe_to_read", "the package decompresses past its limits"
            )


def _refused_part(name):
    """The (code, rule) for a part off the allowlist, by what it is."""
    lowered = name.lower()
    for marker, kind in REFUSED_KINDS:
        if marker in lowered:
            if kind == "macro":
                return "macro", "the package contains macros or ActiveX controls"
            return "part_not_allowed", f"the package contains {kind}"
    return "part_not_allowed", "the package contains a part that is not cell data"


def _ratio_exceeded(size, compressed):
    """Whether a part past the floor decompresses more than MAX_RATIO-fold."""
    return size > RATIO_FLOOR_BYTES and size > MAX_RATIO * max(1, compressed)


def _read_parts(package, entries):
    """Read every part once, counting; yield each relationship part's bytes.

    The counts are of bytes actually decompressed, so a directory that
    understates a part's size cannot get past them. XML parts are searched
    for declarations on the stream, with an overlap so a declaration split
    across two chunks is still found, before any XML parser sees them.
    """
    total = 0
    for entry in entries:
        xml = not entry.filename.endswith(".bin")
        keep = entry.filename.endswith(".rels")
        size, tail, kept = 0, b"", []
        with package.open(entry) as stream:
            while chunk := stream.read(CHUNK_BYTES):
                if size == 0 and xml:
                    _check_xml_start(chunk)
                size += len(chunk)
                total += len(chunk)
                if (
                    size > MAX_PART_BYTES
                    or total > MAX_TOTAL_BYTES
                    or _ratio_exceeded(size, entry.compress_size)
                ):
                    raise PackageRefused(
                        "unsafe_to_read", "the package decompresses past its limits"
                    )
                if xml:
                    window = (tail + chunk).lower()
                    if any(marker in window for marker in DECLARATIONS):
                        raise PackageRefused(
                            "unsafe_to_read",
                            "an XML part declares a document type or entity",
                        )
                    if b"\x00" in chunk:
                        raise PackageRefused(
                            "unsafe_to_read", "an XML part is not UTF-8 text"
                        )
                    tail = window[-(max(map(len, DECLARATIONS)) - 1) :]
                if keep:
                    kept.append(chunk)
        if keep:
            yield entry, b"".join(kept)
        else:
            yield entry, None


def _check_xml_start(chunk):
    """Refuse an XML part whose byte-order mark or declaration is not UTF-8."""
    if chunk.startswith((b"\xfe\xff", b"\xff\xfe")):
        raise PackageRefused("unsafe_to_read", "an XML part is not UTF-8 text")
    declared = XML_ENCODING.match(chunk.removeprefix(UTF8_BOM))
    if declared is not None and declared[1].lower() not in {b"utf-8", b"utf8"}:
        raise PackageRefused("unsafe_to_read", "an XML part is not UTF-8 text")


def _check_office(data):
    """Apply the hosted-files OOXML checks and require a workbook."""
    from parishkit.stewardship.web.hosted_file_types import FileRefused, ooxml_kind

    try:
        kind = ooxml_kind(data)
    except FileRefused as refused:
        if refused.reason == "macro":
            raise PackageRefused(
                "macro", "the package contains macros, ActiveX or OLE objects"
            ) from None
        raise PackageRefused("not_xlsx", "the file is not an .xlsx workbook") from None
    if kind != "xlsx":
        raise PackageRefused("not_xlsx", "the file is not an .xlsx workbook")


def _check_relationships(name, text):
    """Refuse an external relationship, or a worksheet one to anything but
    its printer settings (a hyperlink, comment or drawing)."""
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        raise PackageRefused("unreadable", "the file is not a readable .xlsx") from None
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "Relationship":
            continue
        mode = element.get("TargetMode", "Internal").strip()
        target = element.get("Target", "").strip()
        if mode != "Internal" or "://" in target or target.startswith(("//", "\\")):
            raise PackageRefused(
                "external_relationship",
                "the package links to something outside the file",
            )
        kind = element.get("Type", "").strip().rsplit("/", 1)[-1]
        if SHEET_RELATIONSHIPS.fullmatch(name) and kind != "printerSettings":
            raise PackageRefused(
                "part_not_allowed",
                "a worksheet links to something other than its printer settings",
            )

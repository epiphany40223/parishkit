"""Detect and normalize an uploaded hosted file (#346) from its bytes alone.

The type comes only from the content: never from the file name, its
extension or the browser's declared type. PDF and Office (OOXML) files are
kept byte for byte after structural checks; images are decoded and re-encoded
from a fresh pixel buffer, like campaign artwork, so no metadata or trailing
data survives. See docs/specs/stewardship/hosted-files/spec.md.
"""

import warnings
import xml.etree.ElementTree as ElementTree
import zipfile
import zlib
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from .content import MAX_IMAGE_PIXELS

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_SIDE = 2048
MAX_ZIP_ENTRIES = 10_000
MAX_CONTENT_TYPES_BYTES = 1024 * 1024
# Relationship parts (*.rels) are small; bound each and all of them together.
MAX_RELS_BYTES = 1024 * 1024
MAX_ALL_RELS_BYTES = 8 * 1024 * 1024
# Relationship types that load code or content from elsewhere: a remote
# template (the classic way to deliver macros to a clean .docx), OLE objects
# (embedded or linked), sub-documents, frames, ActiveX controls, imported
# RTF/HTML chunks (altChunk, which can carry OLE), VBA projects and ribbon
# customizations. Any relationship of these types is refused, internal or
# external: Office finds parts by relationship, not by folder, so this also
# catches parts moved out of their usual folders. Embedded OLE objects are
# refused too, the safer default for files the parish publishes on its own
# domain.
ACTIVE_RELATIONSHIPS = (
    "/attachedtemplate",
    "/oleobject",
    "/subdocument",
    "/frame",
    "/control",
    "/activexcontrolbinary",
    "/afchunk",
    "/vbaproject",
    "/extensibility",
)
# Content-type markers of active parts wherever they are stored: macro-enabled
# main parts, VBA, Excel 4.0 macro sheets, ActiveX and dialog sheets.
ACTIVE_CONTENT_TYPES = ("macroenabled", "vba", "macrosheet", "activex", "dialogsheet")
# Embedded parts that are OLE objects or macro-enabled Office packages.
ACTIVE_EMBEDDINGS = (".bin", ".docm", ".xlsm", ".pptm", ".dotm", ".xltm", ".potm")
JPEG_QUALITY = 85
# The main part's content type names the Office document kind. Macro-enabled,
# template and slide-show variants have other main types and are refused.
OOXML_MAIN = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ".main+xml": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml": (
        "xlsx"
    ),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ".main+xml": "pptx",
}
CONTENT_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "png": "image/png",
    "jpeg": "image/jpeg",
}
EXTENSIONS = {
    "pdf": ".pdf",
    "docx": ".docx",
    "xlsx": ".xlsx",
    "pptx": ".pptx",
    "png": ".png",
    "jpeg": ".jpg",
}
OLE2_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "PNG"),
    (b"\xff\xd8\xff", "JPEG"),
    (b"GIF87a", "GIF"),
    (b"GIF89a", "GIF"),
)


class FileRefused(ValueError):
    """An upload this library does not accept, with a closed reason code.

    Reasons: ``empty``, ``too_large``, ``type`` (not an accepted type),
    ``legacy_office`` (an OLE2 file: old Office formats and password-protected
    Office files), ``macro`` (macro or ActiveX content), ``animated`` (more
    than one image frame) and ``image`` (an image too large or undecodable).
    """

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class DetectedFile:
    """Bytes ready to store, their kind, and an image's pixel size."""

    kind: str
    data: bytes
    width: int | None = None
    height: int | None = None


def detect(data):
    """Classify and, for images, normalize one upload; raise FileRefused otherwise."""
    if type(data) is not bytes or not data:
        raise FileRefused("empty")
    if len(data) > MAX_FILE_BYTES:
        raise FileRefused("too_large")
    if data.startswith(OLE2_SIGNATURE):
        raise FileRefused("legacy_office")
    if data.startswith(b"%PDF-"):
        if b"%%EOF" not in data[-1024:]:
            raise FileRefused("type")
        return DetectedFile("pdf", data)
    if data.startswith(b"PK\x03\x04"):
        return DetectedFile(_ooxml_kind(data), data)
    for signature, image_format in IMAGE_SIGNATURES:
        if data.startswith(signature):
            return _normalized_image(data, image_format)
    raise FileRefused("type")


def _ooxml_kind(data):
    """An Office package's kind, after refusing unsafe or unexpected packages.

    Only ``[Content_Types].xml`` and the relationship parts are
    decompressed, each only up to its bound, so a compression bomb in
    another part is never expanded. Refused as active content: VBA and
    ActiveX parts, macro-enabled or Excel 4.0 macro-sheet content types,
    embedded OLE objects or macro-enabled packages, and relationships to
    templates, OLE objects, sub-documents or frames.
    """
    try:
        with zipfile.ZipFile(BytesIO(data)) as package:
            entries = package.infolist()
            if len(entries) > MAX_ZIP_ENTRIES:
                raise FileRefused("type")
            seen = set()
            for entry in entries:
                name = entry.filename.replace("\\", "/")
                parts = name.split("/")
                if name.startswith("/") or ".." in parts:
                    raise FileRefused("type")
                # OPC forbids part names equal ignoring case; a repeat lets
                # the checked copy differ from the one a reader uses.
                if name.lower() in seen:
                    raise FileRefused("type")
                seen.add(name.lower())
                lowered = "/" + name.lower()
                if (
                    lowered.endswith(("/vbaproject.bin", "/vbadata.xml"))
                    or "/activex/" in lowered
                    or (
                        "/embeddings/" in lowered
                        and lowered.endswith(ACTIVE_EMBEDDINGS)
                    )
                ):
                    raise FileRefused("macro")
            with package.open("[Content_Types].xml") as stream:
                text = stream.read(MAX_CONTENT_TYPES_BYTES + 1)
            relationships = _relationship_parts(package, entries)
    except FileRefused:
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
        raise FileRefused("type") from None
    if len(text) > MAX_CONTENT_TYPES_BYTES or b"<!doctype" in text.lower():
        raise FileRefused("type")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        raise FileRefused("type") from None
    types = [
        element.get("ContentType", "")
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] in {"Default", "Override"}
    ]
    if any(
        marker in value.lower() for value in types for marker in ACTIVE_CONTENT_TYPES
    ):
        raise FileRefused("macro")
    for part in relationships:
        _check_relationships(part)
    kinds = {OOXML_MAIN[value] for value in types if value in OOXML_MAIN}
    if len(kinds) != 1:
        raise FileRefused("type")
    return kinds.pop()


def _relationship_parts(package, entries):
    """Read every ``*.rels`` part, each and all together within their bounds."""
    parts, total = [], 0
    for entry in entries:
        if not entry.filename.lower().endswith(".rels"):
            continue
        with package.open(entry) as stream:
            text = stream.read(MAX_RELS_BYTES + 1)
        total += len(text)
        if len(text) > MAX_RELS_BYTES or total > MAX_ALL_RELS_BYTES:
            raise FileRefused("type")
        parts.append(text)
    return parts


def _check_relationships(text):
    """Refuse relationships that pull templates, OLE objects or frames."""
    if b"<!doctype" in text.lower():
        raise FileRefused("type")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        raise FileRefused("type") from None
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "Relationship":
            continue
        if element.get("Type", "").strip().lower().endswith(ACTIVE_RELATIONSHIPS):
            raise FileRefused("macro")


def _normalized_image(data, image_format):
    """Decode a bounded still image and re-encode it without any metadata.

    PNG and GIF become PNG (keeping transparency); JPEG stays JPEG, since a
    photo saved as PNG would be several times larger. The result is fitted
    within ``MAX_IMAGE_SIDE`` pixels per side.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data), formats=(image_format,)) as probe:
                if probe.width * probe.height > MAX_IMAGE_PIXELS:
                    raise FileRefused("image")
                # A multi-picture JPEG (MPO, from many phones and cameras)
                # is a still photo with extra views; only frame 0 is kept.
                if probe.format != "MPO" and getattr(probe, "n_frames", 1) != 1:
                    raise FileRefused("animated")
                probe.verify()
            with Image.open(BytesIO(data), formats=(image_format,)) as source:
                source.seek(0)
                source.load()
                image = ImageOps.exif_transpose(source)
                mode = "RGB" if image_format == "JPEG" else "RGBA"
                image = image.convert(mode)
                image.thumbnail(
                    (MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS
                )
                # A fresh pixel buffer deliberately discards EXIF/ICC/text data.
                clean = Image.frombytes(mode, image.size, image.tobytes())
        output = BytesIO()
        if image_format == "JPEG":
            clean.save(output, format="JPEG", quality=JPEG_QUALITY)
            kind = "jpeg"
        else:
            clean.save(output, format="PNG")
            kind = "png"
    except FileRefused:
        raise
    except (
        OSError,
        ValueError,
        SyntaxError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise FileRefused("image") from None
    result = output.getvalue()
    if len(result) > MAX_FILE_BYTES:
        raise FileRefused("too_large")
    return DetectedFile(kind, result, *clean.size)

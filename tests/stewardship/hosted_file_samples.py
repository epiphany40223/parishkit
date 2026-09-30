"""Real and hostile sample uploads for the hosted-file tests (#346)."""

import io
import zipfile

from PIL import Image

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="{part}" ContentType="{main}"/>{extra}</Types>'
)
MAIN = {
    "docx": (
        "/word/document.xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml."
        "document.main+xml",
    ),
    "xlsx": (
        "/xl/workbook.xml",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    ),
    "pptx": (
        "/ppt/presentation.xml",
        "application/vnd.openxmlformats-officedocument.presentationml."
        "presentation.main+xml",
    ),
    "docm": (
        "/word/document.xml",
        "application/vnd.ms-word.document.macroEnabled.main+xml",
    ),
    "xlsm": (
        "/xl/workbook.xml",
        "application/vnd.ms-excel.sheet.macroEnabled.main+xml",
    ),
    "pptm": (
        "/ppt/presentation.xml",
        "application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml",
    ),
    "dotx": (
        "/word/document.xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml."
        "template.main+xml",
    ),
    "ppsx": (
        "/ppt/presentation.xml",
        "application/vnd.openxmlformats-officedocument.presentationml."
        "slideshow.main+xml",
    ),
}


def pdf(size=None):
    """A minimal PDF, padded to exactly ``size`` bytes when given."""
    head = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n"
    tail = b"\ntrailer << /Root 1 0 R >>\n%%EOF\n"
    padding = b"" if size is None else b"%" * (size - len(head) - len(tail))
    return head + padding + tail


def office(kind, *, extra_parts=(), extra_types="", content_types=None):
    """A minimal OOXML package of ``kind`` (see ``MAIN``), optionally hostile."""
    part, main = MAIN[kind]
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr(
            "[Content_Types].xml",
            content_types
            or CONTENT_TYPES.format(part=part, main=main, extra=extra_types),
        )
        package.writestr(part.lstrip("/"), "<document/>")
        for name, data in extra_parts:
            package.writestr(name, data)
    return stream.getvalue()


def image(image_format, size=(40, 20), *, exif=None, icc=None, frames=1):
    """A real image; ``exif``/``icc`` add metadata the server must strip."""
    stream = io.BytesIO()
    colors = ("red", "blue", "green")
    pictures = [Image.new("RGB", size, colors[index % 3]) for index in range(frames)]
    options = {}
    if exif is not None:
        options["exif"] = exif
    if icc is not None:
        options["icc_profile"] = icc
    if frames > 1:
        options.update(save_all=True, append_images=pictures[1:], duration=100)
    pictures[0].save(stream, format=image_format, **options)
    return stream.getvalue()


def gps_exif():
    """EXIF carrying a GPS position and an orientation that rotates the image."""
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: rotate 90 degrees clockwise to display.
    exif[0x8825] = {1: "N", 2: (38.0, 53.0, 0.0), 3: "W", 4: (77.0, 2.0, 0.0)}
    return exif.tobytes()


def bomb_png():
    """A tiny file that decodes to 25 million pixels."""
    stream = io.BytesIO()
    Image.new("1", (5000, 5000)).save(stream, format="PNG")
    return stream.getvalue()


def gifar():
    """A GIF with a ZIP (JAR) appended: a classic polyglot."""
    return image("GIF") + office("docx")


SVG = (
    b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg">'
    b"<script>alert(1)</script></svg>"
)
HTML_AS_PDF = b"<!DOCTYPE html><html><script>alert(document.cookie)</script></html>"
OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504


def mpo():
    """A two-picture MPO JPEG, as many phones and cameras write."""
    stream = io.BytesIO()
    first = Image.new("RGB", (40, 20), "red")
    first.save(
        stream, format="MPO", save_all=True, append_images=[Image.new("RGB", (40, 20))]
    )
    return stream.getvalue()


RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
    'relationships"><Relationship Id="rId1" Type="http://schemas.'
    'openxmlformats.org/officeDocument/2006/relationships/{kind}" '
    'Target="{target}"{mode}/></Relationships>'
)


def relationship(kind, target, *, external=True):
    """One relationship part of ``kind`` (for example attachedTemplate)."""
    return RELS.format(
        kind=kind, target=target, mode=' TargetMode="External"' if external else ""
    )


OFFICE_RELATIONSHIPS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
)
ACTIVEX = "application/vnd.ms-office.activeX+xml"


def relocated_activex():
    """ActiveX moved out of activeX/, found by its control relationship."""
    return office(
        "docx",
        extra_parts=[
            (
                "word/_rels/document.xml.rels",
                relationship("control", "ctl/c1.xml", external=False),
            ),
            ("word/ctl/c1.xml", "<ocx/>"),
        ],
        extra_types=f'<Override PartName="/word/ctl/c1.xml" ContentType="{ACTIVEX}"/>',
    )


def activex_binary():
    """An activeXControlBinary relationship to a relocated binary."""
    kind = "http://schemas.microsoft.com/office/2006/relationships/activeXControlBinary"
    rels = RELS.format(kind="x", target="c1.bin", mode="").replace(
        OFFICE_RELATIONSHIPS + "x", kind
    )
    return office("docx", extra_parts=[("word/ctl/_rels/c1.xml.rels", rels)])


def alt_chunk():
    """An altChunk importing RTF that can carry OLE objects."""
    return office(
        "docx",
        extra_parts=[
            (
                "word/_rels/document.xml.rels",
                relationship("aFChunk", "afchunk.rtf", external=False),
            ),
            ("word/afchunk.rtf", r"{\rtf1{\object\objupdate\objemb}}"),
        ],
    )


def custom_ui():
    """A ribbon customization part."""
    kind = "http://schemas.microsoft.com/office/2007/relationships/ui/extensibility"
    rels = RELS.format(kind="x", target="customUI/customUI14.xml", mode="").replace(
        OFFICE_RELATIONSHIPS + "x", kind
    )
    return office(
        "docx",
        extra_parts=[("_rels/.rels", rels), ("customUI/customUI14.xml", "<customUI/>")],
    )


def padded_type(kind):
    """A relationship whose Type carries trailing white space."""
    return office(
        "docx",
        extra_parts=[
            (
                "word/_rels/settings.xml.rels",
                RELS.format(
                    kind=kind + "  ",
                    target="https://evil.example/t.dotm",
                    mode=' TargetMode="External"',
                ),
            )
        ],
    )


def duplicate_entries(first, second):
    """A package that stores two entries named ``first`` and ``second``."""
    import warnings

    part, main = MAIN["docx"]
    stream = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(stream, "w") as package:
            package.writestr(
                first, CONTENT_TYPES.format(part=part, main=MAIN["docm"][1], extra="")
            )
            package.writestr(
                second, CONTENT_TYPES.format(part=part, main=main, extra="")
            )
            package.writestr(part.lstrip("/"), "<document/>")
    return stream.getvalue()

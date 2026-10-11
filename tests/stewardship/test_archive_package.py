"""The archive reader's zip and XML safety layer (#1018).

Hostile packages are made from a synthetic workbook by adding, replacing or
rewriting parts; each must be refused with its fixed reason before openpyxl
opens anything. None of these files is ever kept.
"""

import shutil
import struct
import subprocess
import zipfile
from io import BytesIO

import pytest

from parishkit.stewardship.archive import package
from parishkit.stewardship.archive.package import PackageRefused, check_package
from parishkit.stewardship.archive.workbook import ArchiveRefused, read_archive

from .archive_factory import workbook_bytes

SHEET = "xl/worksheets/sheet2.xml"
DOCTYPE = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>'


@pytest.fixture(scope="module")
def plain():
    """One valid synthetic workbook's bytes."""
    return workbook_bytes()


def repack(data, *, add=None, replace=None, compression=zipfile.ZIP_DEFLATED):
    """The package with parts added or replaced (name -> bytes)."""
    add, replace = add or {}, replace or {}
    output = BytesIO()
    with (
        zipfile.ZipFile(BytesIO(data)) as source,
        zipfile.ZipFile(output, "w", compression) as target,
    ):
        for entry in source.infolist():
            content = replace.get(entry.filename, source.read(entry))
            target.writestr(entry.filename, content)
        for name, content in add.items():
            target.writestr(name, content)
    return output.getvalue()


def understate(data, name, size):
    """The package with a part's central-directory size rewritten to ``size``."""
    data = bytearray(data)
    position = data.find(b"PK\x01\x02")
    while position != -1:
        length = struct.unpack_from("<H", data, position + 28)[0]
        if data[position + 46 : position + 46 + length] == name.encode():
            struct.pack_into("<I", data, position + 24, size)
        position = data.find(b"PK\x01\x02", position + 4)
    return bytes(data)


def refusal(data):
    """The (code, rule) a package is refused with."""
    with pytest.raises(PackageRefused) as caught:
        check_package(data)
    return caught.value.code, caught.value.rule


def test_a_plain_workbook_passes(plain):
    """openpyxl's own output is plain cell data."""
    assert check_package(plain) is None


def test_printer_settings_are_allowed(plain):
    """A sheet's printer settings, as Excel saves them, pass."""
    rels = (
        b'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://'
        b'schemas.openxmlformats.org/package/2006/relationships"><Relationship '
        b'Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        b'relationships/printerSettings" Target="../printerSettings/'
        b'printerSettings1.bin"/></Relationships>'
    )
    data = repack(
        plain,
        add={
            "xl/worksheets/_rels/sheet2.xml.rels": rels,
            "xl/printerSettings/printerSettings1.bin": b"\x00" * 200,
        },
    )
    assert check_package(data) is None


@pytest.mark.parametrize(
    ("part", "code", "words"),
    [
        ("xl/vbaProject.bin", "macro", "macros"),
        ("xl/externalLinks/externalLink1.xml", "part_not_allowed", "external"),
        ("xl/connections.xml", "part_not_allowed", "data connection"),
        ("xl/queryTables/queryTable1.xml", "part_not_allowed", "query table"),
        ("xl/embeddings/oleObject1.bin", "part_not_allowed", "embedded object"),
        ("xl/drawings/drawing1.xml", "part_not_allowed", "drawing"),
        ("xl/charts/chart1.xml", "part_not_allowed", "chart"),
        ("xl/media/image1.png", "part_not_allowed", "image"),
        ("xl/comments1.xml", "part_not_allowed", "comment"),
        ("xl/secret-name-4411.xml", "part_not_allowed", "not cell data"),
    ],
)
def test_parts_off_the_allowlist_are_refused(plain, part, code, words):
    """Links, connections, objects, drawings and charts are refused by name."""
    found, rule = refusal(repack(plain, add={part: b"<x/>"}))
    assert found == code and words in rule
    assert "4411" not in rule


@pytest.mark.parametrize(
    "rels_name", ["xl/_rels/workbook.xml.rels", "xl/worksheets/_rels/sheet2.xml.rels"]
)
def test_an_external_relationship_is_refused(plain, rels_name):
    """A hyperlink or remote source (TargetMode External) is refused."""
    with zipfile.ZipFile(BytesIO(plain)) as source:
        names = set(source.namelist())
        text = source.read(rels_name) if rels_name in names else None
    link = (
        b'<Relationship Id="rId99" Type="http://schemas.openxmlformats.org/'
        b'officeDocument/2006/relationships/hyperlink" '
        b'Target="https://example.invalid/x" TargetMode="External"/>'
    )
    if text is None:
        text = (
            b'<?xml version="1.0"?><Relationships xmlns="http://schemas.'
            b'openxmlformats.org/package/2006/relationships"></Relationships>'
        )
        changed = repack(plain, add={rels_name: text.replace(b"</R", link + b"</R")})
    else:
        changed = repack(
            plain,
            replace={
                rels_name: text.replace(b"</Relationships>", link + b"</Relationships>")
            },
        )
    assert refusal(changed)[0] == "external_relationship"


def test_a_worksheet_link_to_anything_but_printer_settings_is_refused(plain):
    """An internal comments relationship on a sheet is still refused."""
    rels = (
        b'<?xml version="1.0"?><Relationships xmlns="http://schemas.'
        b'openxmlformats.org/package/2006/relationships"><Relationship Id="r" '
        b'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        b'relationships/comments" Target="../sheet1.xml"/></Relationships>'
    )
    data = repack(plain, add={"xl/worksheets/_rels/sheet2.xml.rels": rels})
    assert refusal(data)[0] == "part_not_allowed"


@pytest.mark.parametrize("part", [SHEET, "xl/sharedStrings.xml"])
@pytest.mark.parametrize(
    "content",
    [DOCTYPE, b'<?xml version="1.0"?><!ENTITY b SYSTEM "file:///etc/passwd"><x/>'],
)
def test_document_type_and_entity_declarations_are_refused(plain, part, content):
    """In a worksheet and in the shared strings, before any parser."""
    with zipfile.ZipFile(BytesIO(plain)) as source:
        present = part in source.namelist()
    data = (
        repack(plain, replace={part: content})
        if present
        else repack(plain, add={part: content})
    )
    assert refusal(data)[0] == "unsafe_to_read"


def test_a_declaration_split_across_chunks_is_found(plain, monkeypatch):
    """The search overlaps chunks, so no split hides a declaration."""
    monkeypatch.setattr(package, "CHUNK_BYTES", 7)
    content = b"<?xml version='1.0'?>   <!DOCTYPE x><x/>"
    assert refusal(repack(plain, replace={SHEET: content}))[0] == "unsafe_to_read"


@pytest.mark.parametrize(
    "content",
    [
        '<?xml version="1.0" encoding="UTF-16"?><x/>'.encode("utf-16"),
        b'<?xml version="1.0" encoding="ISO-8859-1"?><x/>',
    ],
)
def test_xml_that_is_not_utf8_is_refused(plain, content):
    """A UTF-16 declaration could hide one from the byte search."""
    assert refusal(repack(plain, replace={SHEET: content}))[0] == "unsafe_to_read"


@pytest.mark.parametrize("part", [SHEET, "xl/sharedStrings.xml"])
def test_a_declaration_padded_past_the_first_chunk_is_refused(plain, part):
    """Padding cannot push a Latin-1 encoding past the declaration check."""
    content = (
        b'<?xml version="1.0"'
        + b" " * 70000
        + b'encoding="ISO-8859-1"?><sst><si><t>caf\xe9</t></si></sst>'
    )
    with zipfile.ZipFile(BytesIO(plain)) as source:
        present = part in source.namelist()
    changed = (
        repack(plain, replace={part: content})
        if present
        else repack(plain, add={part: content})
    )
    assert refusal(changed)[0] == "unsafe_to_read"


def test_a_declaration_must_close_within_its_window(plain):
    """An unclosed or very long declaration is refused, whatever it names."""
    content = b'<?xml version="1.0"' + b" " * 2000 + b"?><x/>"
    assert refusal(repack(plain, replace={SHEET: content}))[0] == "unsafe_to_read"


def test_a_short_part_with_a_declaration_is_checked_whole(plain):
    """A part shorter than the window is still checked."""
    content = b'<?xml version="1.0" encoding="windows-1252"?><x/>'
    assert refusal(repack(plain, replace={SHEET: content}))[0] == "unsafe_to_read"


def test_a_high_compression_ratio_is_refused(plain):
    """Two megabytes of one byte compress far past the ratio limit."""
    bomb = b"<x>" + b" " * (2 * 1024 * 1024) + b"</x>"
    assert refusal(repack(plain, replace={SHEET: bomb}))[0] == "unsafe_to_read"


def test_the_ratio_is_enforced_while_reading(plain, monkeypatch):
    """With the directory check bypassed, the counted stream still stops."""
    monkeypatch.setattr(package, "_check_directory", lambda entries: None)
    bomb = b"<x>" + b" " * (2 * 1024 * 1024) + b"</x>"
    assert refusal(repack(plain, replace={SHEET: bomb}))[0] == "unsafe_to_read"


def test_an_oversized_part_is_refused(plain, monkeypatch):
    """A part past the per-part limit, declared or read."""
    monkeypatch.setattr(package, "MAX_PART_BYTES", 2000)
    assert refusal(plain)[0] == "unsafe_to_read"
    monkeypatch.setattr(package, "_check_directory", lambda entries: None)
    assert refusal(plain)[0] == "unsafe_to_read"


def test_the_total_limit_is_enforced(plain, monkeypatch):
    """All parts together past the total limit."""
    monkeypatch.setattr(package, "MAX_TOTAL_BYTES", 5000)
    assert refusal(plain)[0] == "unsafe_to_read"


def test_a_directory_that_understates_a_part_is_stopped_while_reading(plain):
    """The read stops at the declared size, and the CRC then refuses it."""
    data = understate(plain, SHEET, 10)
    assert refusal(data)[0] == "unreadable"


def test_size_limits_and_non_zip_bytes(plain, monkeypatch):
    """Empty, over 20 MB, or not a zip at all."""
    assert refusal(b"")[0] == "file_size"
    assert refusal(b"x" * (20 * 1024 * 1024 + 1))[0] == "file_size"
    assert refusal(b"%PDF-1.7 not a workbook")[0] == "unreadable"


def test_a_package_of_another_office_kind_is_refused(plain):
    """The hosted-files checks require the .xlsx main part."""
    with zipfile.ZipFile(BytesIO(plain)) as source:
        types = source.read("[Content_Types].xml")
    changed = types.replace(
        b"spreadsheetml.sheet.main+xml", b"spreadsheetml.template.main+xml"
    )
    assert (
        refusal(repack(plain, replace={"[Content_Types].xml": changed}))[0]
        == "not_xlsx"
    )


def test_macro_content_types_are_refused(plain):
    """A macro-enabled main part is refused by the hosted-files checks."""
    with zipfile.ZipFile(BytesIO(plain)) as source:
        types = source.read("[Content_Types].xml")
    changed = types.replace(
        b"spreadsheetml.sheet.main+xml", b"sheet.macroEnabled.main+xml"
    )
    assert (
        refusal(repack(plain, replace={"[Content_Types].xml": changed}))[0] == "macro"
    )


def test_the_reader_reports_a_package_refusal_as_one_problem(plain):
    """read_archive stops at the package and reports its reason."""
    with pytest.raises(ArchiveRefused) as caught:
        read_archive(repack(plain, add={"xl/connections.xml": b"<x/>"}))
    [problem] = caught.value.problems
    assert problem.code == "part_not_allowed" and problem.sheet is None


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
def test_a_workbook_saved_again_by_libreoffice_passes_the_allowlist(plain, tmp_path):
    """A hand-corrected file saved by LibreOffice stays within the allowlist."""
    source = tmp_path / "in" / "archive.xlsx"
    source.parent.mkdir()
    source.write_bytes(plain)
    subprocess.run(
        [
            "soffice",
            "--headless",
            f"-env:UserInstallation=file://{tmp_path}/profile",
            "--convert-to",
            "xlsx:Calc MS Excel 2007 XML",
            "--outdir",
            str(tmp_path / "out"),
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    assert check_package((tmp_path / "out" / "archive.xlsx").read_bytes()) is None

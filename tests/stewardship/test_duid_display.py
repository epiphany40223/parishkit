"""ParishSoft DUIDs are identifiers and never show thousands separators."""

import ast
import csv
import io
import re
from pathlib import Path

import pytest
from django.template import engines

from parishkit.stewardship.reports.information_rendering import render_information
from parishkit.stewardship.web.presentation import duid

from .test_ministry_exports import document as ministry_document

TEMPLATES = Path(__file__).parents[2] / "src/parishkit/stewardship"
REPORTS = TEMPLATES / "reports"
# A download heading or label naming a DUID other than a Family's or a
# Member's, which no download shows (UX conventions, "Table column order",
# #1123).
OTHER_DUID = re.compile(r"\b(?:Ministry|Fund|WorkGroup|Workgroup|Role) DUIDs?\b")
# Display strings still allowed, each with the work that removes it. None
# remain: #1126 (PR #1137) deleted the Ministry summary download.
OTHER_DUID_ALLOWED = {}
# Template variables whose name marks a ParishSoft identifier.
IDENTIFIER = re.compile(
    r"\{\{ ?(?:[a-zA-Z0-9_]+\.)*"
    r"(?:duid|[a-z_]+_duid|family__family_duid|ministry_id|fund)\|number"
)


@pytest.mark.parametrize(
    "value,expected",
    [(12345, "12345"), (1234567, "1234567"), (-42, "-42"), ("157419", "157419")],
)
def test_duid_is_plain_digits(value, expected):
    assert duid(value) == expected


@pytest.mark.parametrize("value", [True, 1.5, "12,345", "", "12a", None])
def test_duid_rejects_non_identifiers(value):
    with pytest.raises(ValueError):
        duid(value)


def test_duid_template_filter_never_groups():
    """The Admin templates' filter renders a large DUID without a comma."""
    template = engines["django"].from_string(
        "{% load stewardship %}{{ value|duid }} / {{ value|number }}"
    )
    assert template.render({"value": 1234567}) == "1234567 / 1,234,567"


def test_no_template_groups_a_duid_as_a_count():
    """A DUID rendered with |number would show commas; use |duid instead."""
    offenders = [
        f"{path.relative_to(TEMPLATES)}: {match.group(0)}"
        for path in TEMPLATES.rglob("*.html")
        for match in IDENTIFIER.finditer(path.read_text())
    ]
    assert offenders == []


@pytest.mark.parametrize("action", ["join", "leave"])
def test_ministry_export_duid_columns_have_no_commas(action):
    """Exported Member and Ministry DUIDs stay plain digits in CSV cells."""
    # The sample Member DUID (12345) would read "12,345" if grouped.
    report = ministry_document(action=action, count=3)
    output = io.BytesIO()
    render_information(report, output, format="csv")
    rows = list(csv.reader(io.StringIO(output.getvalue().decode())))
    header = rows[0]
    columns = [index for index, name in enumerate(header) if "DUID" in name]
    assert columns
    for row in rows[1:]:
        for index in columns:
            if index < len(row):
                assert "," not in row[index]


def display_strings(source):
    """Every string constant in ``source`` except docstrings."""
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        )
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def other_duid_strings(source):
    """Each display string in ``source`` naming a non-Family, non-Member DUID."""
    return [text for text in display_strings(source) if OTHER_DUID.search(text)]


def test_no_download_names_another_duid():
    """Report and download modules carry no Ministry, fund, WorkGroup or role
    DUID heading or label; only the listed one remains until it is removed."""
    found = {}
    for path in sorted(REPORTS.glob("*.py")):
        for text in other_duid_strings(path.read_text(encoding="utf-8")):
            key = (path.name, text)
            found[key] = found.get(key, 0) + 1
    assert found == OTHER_DUID_ALLOWED


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ('HEADINGS = ("Ministry", "Ministry DUID")', ["Ministry DUID"]),
        ('label = f"Fund DUIDs: {funds}"', ["Fund DUIDs: "]),
        ('HEADINGS = ("Member", "Member DUID", "Family DUID")', []),
        ('def f():\n    """Each Ministry DUID shown."""\n', []),
    ],
)
def test_download_guard_finds_other_duids(source, found):
    """The guard reads display strings and skips docstrings."""
    assert other_duid_strings(source) == found

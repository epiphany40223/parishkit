"""ParishSoft DUIDs are identifiers and never show thousands separators."""

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


@pytest.mark.parametrize("action", ["summary", "join"])
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

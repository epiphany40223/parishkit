"""Admin tables keep every DUID in its own column (#932).

The admin-portal spec's "Table column order" section gives each Family,
Member and Ministry DUID its own column beside the name. A table cell that
prints a DUID beside another value ("Name<br>DUID 1234", "Name (DUID
1234)", "{{ name }} {{ duid }}") cannot be sorted or scanned by DUID and
copies badly into a spreadsheet, so this guard refuses one in any Admin
template, except the tables a later slice of #932 (or the issue named) still
has to split.
"""

import re
from pathlib import Path

import pytest

TEMPLATES = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship"
)

# Table cells never nest, so a non-greedy match from an opening <td> or
# row-heading <th> (scope="row" anywhere among its attributes) to its
# closing tag is one cell. Column headings (<th scope="col">) are labels,
# not values, and are left out.
CELL = re.compile(
    r"<(?:td(?=[\s>])|th\s[^>]*?\bscope=\"row\")[^>]*>(.*?)</t[dh]>", re.DOTALL
)
# A DUID label, translated or in a blocktranslate, followed later in the
# same cell by a printed value: the combined "label n" form the spec forbids.
LABELLED = re.compile(r"DUID\b[^<]*?\{\{")
# Every printed value in a cell, and the variable each one prints.
VALUE = re.compile(r"\{\{\s*([\w.]*)")
# Text that is not a cell value: a menu choice (<option>, where "Name
# (1234)" helps someone pick) and any HTML tag, whose attributes (a hidden
# input's DUID, a checkbox's label) are not shown. Both are dropped first.
NOT_SHOWN = re.compile(r"<option\b.*?</option>|<[^>]*>", re.DOTALL)

# Tables that still combine a name and a DUID, each with the number of
# combined cells it holds and the work that splits it. Lower a count, or
# remove the entry, when that work lands; the guard then holds it.
PENDING = {
    "talents-report.html": (1, "#932 slice 3 (Members table)"),
    "ministry-report.html": (2, "#932 slice 3"),
    "campaign-ministries-preview.html": (1, "#932 slice 3"),
    "users.html": (7, "#922 removes these tables, else #932 slice 3"),
    "deliveries.html": (1, "#934 (Outgoing mail)"),
}


def combined(cell):
    """Whether ``cell`` prints a DUID together with another value.

    That is either a DUID label followed by a value, or a ``*duid``
    variable shown beside any other ``{{ }}`` value. Values in tag
    attributes or menu choices are not cell values, so they do not count.
    """
    if LABELLED.search(cell):
        return True
    names = VALUE.findall(NOT_SHOWN.sub("", cell))
    return len(names) > 1 and any(name.lower().endswith("duid") for name in names)


def combined_cells(text):
    """Every table cell in ``text`` that prints a DUID with another value."""
    return [cell.group(0) for cell in CELL.finditer(text) if combined(cell.group(1))]


@pytest.mark.parametrize(
    "path",
    sorted(TEMPLATES.rglob("*.html")),
    ids=lambda path: path.relative_to(TEMPLATES).as_posix(),
)
def test_no_table_cell_combines_a_name_and_a_duid(path):
    """A DUID gets its own column; only the listed pending tables differ."""
    found = combined_cells(path.read_text(encoding="utf-8"))
    name = path.relative_to(TEMPLATES).as_posix()
    # A pending count that no longer matches is stale: a lower count means
    # some cells were split, so lower it or drop the entry; a higher one
    # means a new combined cell was added.
    expected = PENDING.get(name, (0, ""))[0]
    assert len(found) == expected, found


@pytest.mark.parametrize(
    ("cell", "is_combined"),
    [
        ('<td>{{ row.name }}<br>{% translate "DUID" %} {{ row.duid }}</td>', True),
        ('<th scope="row">{{ n }} ({% translate "Member DUID" %} {{ d }})</th>', True),
        ('<th class="x" scope="row">{{ n }} {% translate "DUID" %} {{ d }}</th>', True),
        ("<td>{% blocktranslate %}DUID {{ d }}{% endblocktranslate %}</td>", True),
        ("<td>{{ row.name }} {{ row.family_duid|duid }}</td>", True),
        ('<td class="numeric">{{ row.family_duid|duid }}</td>', False),
        ("<td>{{ row.name }} {{ row.size }}</td>", False),
        ('<td><input value="{{ row.duid }}">{{ row.name }}</td>', False),
        ("<td><option>{{ name }} ({{ duid }})</option></td>", False),
        ('<th scope="col">{% translate "Family DUID" %}</th>', False),
        ('<th class="numeric" scope="col">{% translate "DUID" %} {{ x }}</th>', False),
        ('<td>{% translate "Search by DUID" %}</td>', False),
    ],
)
def test_guard_recognizes_combined_cells(cell, is_combined):
    """The pattern catches each combined form and spares a plain DUID column."""
    assert bool(combined_cells(cell)) is is_combined

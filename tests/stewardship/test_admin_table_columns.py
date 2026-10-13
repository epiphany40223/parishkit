"""Admin tables keep every DUID in its own column (#932), and show only
Family and Member DUIDs (#1123).

The UX conventions' "Table column order" section gives each Family and
Member DUID its own column beside the name. A table cell that
prints a DUID beside another value ("Name<br>DUID 1234", "Name (DUID
1234)", "{{ name }} {{ duid }}") cannot be sorted or scanned by DUID and
copies badly into a spreadsheet, so this guard refuses one in any Admin
template, except the tables a later slice of #932 (or the issue named) still
has to split. A second check reads each table's column headings: every DUID
heading is "Family DUID" or "Member DUID" and sits right after the name it
identifies. A third holds the shared ``numeric`` class on every DUID heading
and every cell that prints only a DUID. A fourth refuses any Ministry, fund
or other non-Family, non-Member DUID shown anywhere in a template, and a
fifth keeps the Member DUID to the pages where staff act on a Member in
ParishSoft.
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
PENDING: dict[str, tuple[int, str]] = {}

# A table's headings, and each heading's text: a translated label assigned
# for a sort heading, or a plain column heading (<th scope="col">). A plain
# heading that prints {{ heading }} repeats the label assigned just before
# it (the Ministry report's one-Ministry view), so it is skipped.
THEAD = re.compile(r"<thead\b.*?</thead>", re.DOTALL)
HEADING = re.compile(
    r'\{% translate "([^"]+)" as heading %\}'
    r'|<th\b[^>]*\bscope="col"[^>]*>(?!\{\{ heading \}\})(.*?)</th>',
    re.DOTALL,
)
LABEL = re.compile(r'\{% translate "([^"]+)" %\}')
# Each DUID heading and the name headings it may follow. Census changes'
# Member column is headed Who.
NAMED_BY = {
    "Family DUID": {"Family"},
    "Member DUID": {"Member", "Who"},
}
# DUID columns that do not follow a name, on purpose, with the reason.
APART = {
    ("source-form.html", "Member DUID"): "UX conventions: the name may be the "
    "refused value, so Families the form cannot open shows no Member name",
}


# A sort heading's label, then the markup up to the next label or the row's
# end. In it, the sort_heading tags given that label (``heading``) and the
# plain headings that print it are the label's column headings (the
# Ministry report picks one of the two); a loop's own headings are not.
LABELLED_HEADING = re.compile(
    r'\{% translate "([^"]+)" as heading %\}(.*?)'
    r'(?=\{% translate "[^"]+" as heading %\}|</tr>|</thead>|\Z)',
    re.DOTALL,
)
SORT_TAG = re.compile(r"\{% sort_heading \S+ \"[^\"]+\" heading\b[^%]*%\}")
REPEATED = re.compile(r"<th\b[^>]*>\{\{ heading \}\}")
# A plain DUID column heading, and its attributes.
PLAIN_DUID = re.compile(r'<th\b([^>]*)>\{% translate "(?:Family|Member) DUID" %\}')
# A cell that prints only a DUID (a ``*duid`` variable, optionally through
# the ``duid`` filter), and its attributes.
LONE_DUID = re.compile(r"<td\b([^>]*)>\{\{\s*[\w.]*duid\s*(?:\|\s*duid\s*)?\}\}</td>")


def unaligned(text):
    """Each DUID heading or lone-DUID cell in ``text`` without ``numeric``.

    A sort heading names its classes as the tag's last argument, a plain
    heading or cell in its ``class`` attribute; either way the word
    "numeric" must be there.
    """
    found = []
    for match in LABELLED_HEADING.finditer(text):
        if "DUID" in match.group(1):
            tags = SORT_TAG.findall(match.group(2)) + REPEATED.findall(match.group(2))
            found += [tag for tag in tags if "numeric" not in tag]
    for pattern in (PLAIN_DUID, LONE_DUID):
        found += [
            match.group(0)
            for match in pattern.finditer(text)
            if "numeric" not in match.group(1)
        ]
    return found


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


def column_headings(text):
    """Each table's column headings in ``text``, as lists of their labels.

    A heading with no translated label (a selection checkbox) reads "".
    """
    tables = []
    for head in THEAD.findall(text):
        labels = []
        for match in HEADING.finditer(head):
            plain = LABEL.search(match.group(2) or "")
            labels.append(match.group(1) or (plain.group(1) if plain else ""))
        tables.append(labels)
    return tables


@pytest.mark.parametrize(
    "path",
    sorted(TEMPLATES.rglob("*.html")),
    ids=lambda path: path.relative_to(TEMPLATES).as_posix(),
)
def test_each_duid_heading_follows_its_name(path):
    """A DUID column is named for what it identifies and sits beside its name."""
    name = path.relative_to(TEMPLATES).as_posix()
    for labels in column_headings(path.read_text(encoding="utf-8")):
        for index, label in enumerate(labels):
            if "DUID" not in label or (name, label) in APART:
                continue
            assert label in NAMED_BY, labels
            assert index and labels[index - 1] in NAMED_BY[label], labels


@pytest.mark.parametrize(
    "path",
    sorted(TEMPLATES.rglob("*.html")),
    ids=lambda path: path.relative_to(TEMPLATES).as_posix(),
)
def test_duid_headings_and_cells_are_numeric(path):
    """Every DUID heading and lone-DUID cell carries the shared ``numeric``
    class, so DUIDs line up as numbers in every table (UX conventions)."""
    assert unaligned(path.read_text(encoding="utf-8")) == []


# Template comments, which are never shown.
COMMENT = re.compile(r"\{% comment %\}.*?\{% endcomment %\}|\{#.*?#\}", re.DOTALL)
# A shown label for a DUID other than a Family's or a Member's: a named
# Ministry, fund, WorkGroup or role DUID, an inline "(DUID n)", or a bare
# "DUID" label, which names no record (#1123).
OTHER_DUID_LABEL = re.compile(
    r"(?:Ministry|Fund|WorkGroup|Workgroup|Role) DUIDs?\b"
    r'|\(DUID |\{% translate "DUID" %\}'
)
# Printed values that hold a Family's DUID under a name that does not say
# so: each template's rows are Families (the Family timeline's envelope
# number is printed with the same plain-digits filter).
FAMILY_ROWS = {
    ("campaign-mail-families.html", "row.duid"),
    ("campaign-mail-families.html", "item.duid"),
    ("family-timeline.html", "identity.duid"),
    ("family-timeline.html", "identity.envelope"),
    ("go-live-families.html", "row.duid"),
    ("presence.html", "row.duid"),
    ("system-health-accept.html", "duid"),
}
# Bare "DUID" labels that name a Family's DUID in a line about one Family.
FAMILY_LABELS = {"find-family-results.html": "a Family search result's DUID"}
# The pages where staff act on a specific Member in ParishSoft, the only ones
# that show a Member DUID (UX conventions, "Table column order", #1123), plus
# the Users page's ParishSoft Ministry leaders table and its import review,
# kept at the Administrator's request (2026-10-12, #932).
MEMBER_DUID_PAGES = {
    "census-changes.html",
    "limitations-report.html",
    "ministry-followup.html",
    "ministry-report.html",
    "ministry-roster.html",
    "source-form.html",
    "user-leader-import.html",
    "users.html",
}
# A printed value and its filters.
PRINTED = re.compile(r"\{\{\s*([\w.]+)\s*((?:\|[^}]*)?)\}\}")


def shown_text(text):
    """``text`` without comments, tags (attributes) or menu choices' markup."""
    return NOT_SHOWN.sub("", COMMENT.sub("", text))


def other_duids(name, text):
    """Every shown non-Family, non-Member DUID label or value in ``text``."""
    found = []
    if name not in FAMILY_LABELS:
        found += OTHER_DUID_LABEL.findall(COMMENT.sub("", text))
    for variable, filters in PRINTED.findall(shown_text(text)):
        is_duid = variable.lower().endswith("duid") or "|duid" in filters
        named = "family" in variable or "member" in variable
        if is_duid and not named and (name, variable) not in FAMILY_ROWS:
            found.append(variable)
    return found


@pytest.mark.parametrize(
    "path",
    sorted(TEMPLATES.rglob("*.html")),
    ids=lambda path: path.relative_to(TEMPLATES).as_posix(),
)
def test_no_template_shows_another_duid(path):
    """Only Family and Member DUIDs are shown; a Ministry, fund or other
    record is named, and its DUID stays in attributes and form values."""
    name = path.relative_to(TEMPLATES).as_posix()
    assert other_duids(name, path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "path",
    sorted(TEMPLATES.rglob("*.html")),
    ids=lambda path: path.relative_to(TEMPLATES).as_posix(),
)
def test_member_duid_only_where_staff_act_on_a_member(path):
    """A Member DUID shows only on the agreed worklists and reports."""
    name = path.relative_to(TEMPLATES).as_posix()
    text = COMMENT.sub("", path.read_text(encoding="utf-8"))
    shows = "Member DUID" in text or "member_duid" in shown_text(text)
    assert not shows or name in MEMBER_DUID_PAGES


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ('<td>{{ m.name }} ({% translate "DUID" %} {{ m.duid|duid }})</td>', 2),
        ('{% translate "Ministry DUID" as heading %}', 1),
        ('{% translate "Fund DUIDs:" %} {{ fund|duid }}', 2),
        ('<td class="numeric">{{ row.ministry_duid|duid }}</td>', 1),
        ('<td class="numeric">{{ row.family_duid|duid }}</td>', 0),
        ('<input type="hidden" name="ministry" value="{{ ministry.duid }}">', 0),
        ('<option value="{{ duid }}">{{ name }}</option>', 0),
        ("{% comment %}Never its Ministry DUID (#1123).{% endcomment %}", 0),
    ],
)
def test_guard_finds_other_duids(text, found):
    """The check catches shown Ministry and fund DUIDs and spares the rest."""
    assert len(other_duids("example.html", text)) == found


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ('{% translate "Family DUID" as heading %}{% sort_heading t "d" heading %}', 1),
        (
            '{% translate "Family DUID" as heading %}'
            '{% sort_heading t "d" heading "numeric" %}',
            0,
        ),
        (
            '{% translate "Member DUID" as heading %}{% if one %}<th scope="col">'
            '{{ heading }}</th>{% else %}{% sort_heading t "d" heading "numeric" %}'
            '{% endif %}<th scope="col">{% translate "Joining" %}</th></tr>',
            1,
        ),
        ('{% translate "Family" as heading %}{% sort_heading t "f" heading %}', 0),
        ('<th scope="col">{% translate "Member DUID" %}</th>', 1),
        ('<th scope="col" class="numeric">{% translate "Member DUID" %}</th>', 0),
        ("<td>{{ row.family_duid|duid }}</td>", 1),
        ("<td>{{ row.member_duid }}</td>", 1),
        ('<td class="numeric nowrap">{{ refusal.family_duid|duid }}</td>', 0),
        ("<td>{{ row.name }}</td>", 0),
    ],
)
def test_guard_finds_unaligned_duids(text, found):
    """The alignment check catches each unaligned form and spares the rest."""
    assert len(unaligned(text)) == found


@pytest.mark.parametrize(
    ("text", "tables"),
    [
        (
            '<thead><tr>{% translate "Member" as heading %}{% sort_heading t "m" '
            'heading %}<th scope="col" class="numeric">{% translate "Member DUID" '
            "%}</th></tr></thead>",
            [["Member", "Member DUID"]],
        ),
        (
            '<thead><tr><th scope="col"><input type="checkbox"></th>'
            '{% translate "Ministry" as heading %}<th scope="col">{{ heading }}'
            "</th></tr></thead>",
            [["", "Ministry"]],
        ),
    ],
)
def test_column_headings_reads_each_heading_once(text, tables):
    """Sort headings, plain headings and repeated labels are each read once."""
    assert column_headings(text) == tables


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

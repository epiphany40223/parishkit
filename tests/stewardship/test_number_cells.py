"""Every shown number stays on one line (ui-conventions "Numbers on one line", #2342).

main's ``overflow-wrap: anywhere`` broke a seven-digit Member DUID six
digits and one in the Users page's leaders table, because its ``numeric``
cell only right-aligned. These checks keep the shared rule in one place
(``ui-v1.css``) and every Admin table cell that shows a DUID inside it;
``browser/test_number_width.py`` measures the drawn result.
"""

import re
from pathlib import Path

STATIC = Path(__file__).parents[2] / "src/parishkit/stewardship/accounts"
TEMPLATES = STATIC / "templates/stewardship"
CSS = STATIC / "static/stewardship/ui-v1.css"

# A table cell that shows one number and nothing else: its opening tag,
# then (after any template tags) one value that is a DUID (through the
# shared ``duid`` filter or raw, any ``*_duid``), an envelope number, or a
# count through ``number`` or ``intcomma``, then only template tags and
# punctuation (a "—" for none) up to ``</td>``. A count followed by words is
# mixed text (keep_numbers), so the closing ``</td>`` keeps those cells
# out.
NUMBER_CELL = re.compile(
    r"<td(?P<attrs>[^>]*)>(?:\{%[^%]*%\}|[^<{\w]+)*"
    r"\{\{ [^}]*?(?:\|duid|_duid|\benvelope|\|number|\|intcomma)\b[^}]* \}\}"
    r"(?:\{%[^%]*%\}|[^<{\w]+)*</td>"
)


def test_a_numeric_cell_never_wraps_in_any_table():
    """One shared rule, for plain tables too (the directory is one)."""
    css = CSS.read_text(encoding="utf-8")
    rule = re.search(r"^table td\.numeric, [^{]*\{([^}]*)\}", css, re.M)
    assert rule is not None
    assert "white-space: nowrap" in rule.group(1)
    assert "table tbody th.numeric" in rule.group(0)


def test_every_number_cell_is_numeric_or_nowrap():
    """A DUID, envelope or count cell carries the shared class, never a
    copy of the rule."""
    cells, missing = 0, []
    for path in sorted(TEMPLATES.rglob("*.html")):
        for match in NUMBER_CELL.finditer(path.read_text(encoding="utf-8")):
            cells += 1
            classes = re.search(r'class="([^"]*)"', match["attrs"])
            # Words only, so a class set inside a template tag
            # ("numeric{% if not count %} mail-queue-zero{% endif %}") counts.
            words = re.findall(r"[\w-]+", classes.group(1)) if classes else []
            if not {"numeric", "nowrap"} & set(words):
                missing.append(f"{path.relative_to(TEMPLATES)}: {match.group(0)}")
    assert missing == []
    # The pattern still finds the cells it guards (Users' leaders table,
    # the directory's DUID and envelope, Sent mail, the System health
    # examples, Pause delivery's counts, and more).
    assert cells >= 30


def test_the_number_cell_pattern_has_no_false_positives():
    """Mixed text and other values are not number cells."""
    assert NUMBER_CELL.search('<td class="numeric">{{ row.member_duid }}</td>')
    assert NUMBER_CELL.search("<td>{{ row.envelope|default_if_none:'' }}</td>")
    assert NUMBER_CELL.search("<td>{% if x %}{{ count|intcomma }}{% endif %}</td>")
    for text in (
        "<td>{{ x|number }} emails</td>",
        "<td>{{ row.name }}</td>",
        "<td>{{ row.numbered_label }}</td>",
        "<td>{{ row.duidish }}</td>",
    ):
        assert NUMBER_CELL.search(text) is None, text


def test_a_duid_under_a_name_is_one_unit():
    """The review work tables' DUID line under a name is nowrap (#2342)."""
    source = (TEMPLATES / "components/review-worklist.html").read_text(encoding="utf-8")
    assert (
        '<span class="stack-line cell-detail nowrap"><span class="visually-hidden">'
        "{{ column.detail_heading }} </span>{{ detail }}</span>"
    ) in source


def test_ministries_led_lists_every_name_as_one_unit():
    """Each Ministry name is a nowrap unit with its comma, so the list wraps
    only between names, inside the shared name-list (#2342)."""
    source = (TEMPLATES / "users.html").read_text(encoding="utf-8")
    assert (
        '<td><span class="name-list">{% for name in row.ministry_names %}'
        '<span class="nowrap">{{ name }}{% if not forloop.last %},{% endif %}'
        "</span>{% if not forloop.last %} {% endif %}{% endfor %}</span></td>"
    ) in source
    assert "ministries_label" not in source
    css = CSS.read_text(encoding="utf-8")
    assert re.search(r"^\.name-list \{[^}]*min-width: 12rem", css, re.M)


def test_keep_numbers_wraps_each_number_and_escapes_the_rest():
    """Each count, decimal and percentage is one ``num`` unit; tags and the
    words around them are kept as they are (#2342)."""
    from django.utils.safestring import mark_safe

    from parishkit.stewardship.accounts.templatetags.stewardship import keep_numbers

    def span(text):
        """The markup keep_numbers puts around one number."""
        return f'<span class="num">{text}</span>'

    assert keep_numbers("1,234") == span("1,234")
    assert keep_numbers("3 out of 10 (30.5%) <b>") == (
        f"{span('3')} out of {span('10')} ({span('30.5%')}) &lt;b&gt;"
    )
    assert keep_numbers("None yet") == "None yet"
    # Digits inside a tag of safe markup are never wrapped.
    assert keep_numbers(mark_safe('<a href="/x?page=22">9,999</a>')) == (
        f'<a href="/x?page=22">{span("9,999")}</a>'
    )
    # A character entity is neither wrapped nor split: the digits in
    # "&#x27;" and "&#8212;" are not numbers.
    assert keep_numbers("it's 5") == f"it&#x27;s {span('5')}"
    assert keep_numbers(mark_safe("x &#8212; 5")) == f"x &#8212; {span('5')}"
    assert keep_numbers(mark_safe("&nbsp;12")) == f"&nbsp;{span('12')}"
    css = CSS.read_text(encoding="utf-8")
    assert re.search(r"^\.num \{ white-space: nowrap; \}", css, re.M)


# Each table cell whose count sits in mixed text, with how often the cell
# passes it through keep_numbers.
MIXED_TEXT_COUNTS = {
    "schedule-table.html": 2,
    "schedule-settings.html": 2,
    "background.html": 1,
    "background-task.html": 1,
    "components/report-sends.html": 1,
}


def test_counts_in_mixed_text_cells_go_through_keep_numbers():
    """Every listed cell's counts are kept on one line (#2342)."""
    for name, count in MIXED_TEXT_COUNTS.items():
        source = (TEMPLATES / name).read_text(encoding="utf-8")
        assert source.count("|keep_numbers") == count, name

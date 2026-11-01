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

# A table cell whose shown value is a DUID: its opening tag, then (after
# any template tags) the value through the shared ``duid`` filter or a
# Member DUID shown with a blank default.
DUID_CELL = re.compile(
    r"<td(?P<attrs>[^>]*)>(?:\{%[^%]*%\})*"
    r"\{\{ [^}]*(?:\|duid|member_duid\|default_if_none:\"\") \}\}"
)


def test_a_numeric_cell_never_wraps_in_any_table():
    """One shared rule, for plain tables too (the directory is one)."""
    css = CSS.read_text(encoding="utf-8")
    rule = re.search(r"^table td\.numeric, [^{]*\{([^}]*)\}", css, re.M)
    assert rule is not None
    assert "white-space: nowrap" in rule.group(1)
    assert "table tbody th.numeric" in rule.group(0)


def test_every_duid_cell_is_numeric_or_nowrap():
    """A DUID cell carries the shared class, never a copy of the rule."""
    cells, missing = 0, []
    for path in sorted(TEMPLATES.rglob("*.html")):
        for match in DUID_CELL.finditer(path.read_text(encoding="utf-8")):
            cells += 1
            classes = re.search(r'class="([^"]*)"', match["attrs"])
            if not {"numeric", "nowrap"} & set(
                classes.group(1).split() if classes else []
            ):
                missing.append(f"{path.relative_to(TEMPLATES)}: {match.group(0)}")
    assert missing == []
    # The pattern still finds the cells it guards (Users' leaders table,
    # the directory, Sent mail, the System health examples, and more).
    assert cells >= 10


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

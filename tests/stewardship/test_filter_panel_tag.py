"""The ``filterpanel`` tag collapses a report's filters unless one is applied."""

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.template import Context, Template, TemplateSyntaxError

from parishkit.stewardship.accounts.templatetags.stewardship import applied_filters
from parishkit.stewardship.reports.census_changes import CensusQuery
from parishkit.stewardship.reports.directories import DirectoryQuery
from parishkit.stewardship.reports.information import InformationQuery


@dataclass(frozen=True)
class Query:
    """A report query: two filters, plus the fields that are not filters."""

    search: str = ""
    show: str = "any"
    sort: str = "name"
    zone: str | None = ""
    page: int = 1


TEMPLATES = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship"
)


def render(source, **context):
    """Render a template that loads the stewardship tag library."""
    return Template("{% load stewardship %}" + source).render(Context(context))


def test_count_ignores_defaults_sort_page_and_zone():
    """Only fields that differ from the query's defaults count as applied."""
    assert applied_filters(Query()) == 0
    assert applied_filters(Query(sort="newest", page=3, zone="America/Detroit")) == 0
    assert applied_filters(Query(search="Smith")) == 1
    assert applied_filters(Query(search="Smith", show="cannot_serve")) == 2


def test_count_reads_each_report_query_s_own_defaults():
    """A report's default view is unfiltered even when its default narrows it."""
    assert applied_filters(DirectoryQuery()) == 0
    # Responses shown (mode) is a page mode outside the panel (#1535).
    assert applied_filters(DirectoryQuery(search="Example", mode="testing")) == 1
    # "Current actionable requests" is Additional information's normal list.
    assert applied_filters(InformationQuery()) == 0
    assert applied_filters(InformationQuery(disposition="all")) == 1


def test_applied_filters_is_also_the_template_filter():
    """Census changes' empty table and its panel count the same filters (#227).

    "Work to do" is the page's normal list, and the browser zone is not a
    filter, so neither counts.
    """
    assert applied_filters(CensusQuery(zone="America/Detroit")) == 0
    assert applied_filters(CensusQuery(status="all")) == 1
    assert applied_filters(CensusQuery(status="all", history="yes")) == 2
    assert (
        render("{{ query|applied_filters }}", query=CensusQuery(route="automatic"))
        == "1"
    )


def test_panel_starts_closed_with_nothing_applied():
    """The panel is closed, says none are applied and keeps escaping intact."""
    html = render(
        "{% filterpanel query %}<p>{{ note }}</p>{% endfilterpanel %}",
        query=Query(),
        note="<b>x</b>",
    )
    assert html == (
        '<details class="filter-panel" data-filter-panel><summary>Filters: '
        '<span id="table-filters-applied" data-table-sync>none applied</span>'
        "</summary><p>&lt;b&gt;x&lt;/b&gt;</p></details>"
    )


def test_panel_opens_and_counts_applied_filters_and_extra_choices():
    """An applied filter is never hidden: the panel opens and counts it."""
    html = render(
        "{% filterpanel query extra %}x{% endfilterpanel %}",
        query=Query(search="Smith"),
        extra=True,
    )
    assert html.startswith('<details class="filter-panel" data-filter-panel open>')
    assert ">2 applied</span>" in html
    assert ">1 applied</span>" in render(
        "{% filterpanel query extra %}x{% endfilterpanel %}", query=Query(), extra=True
    )


def test_an_extra_choice_stops_counting_the_field_it_overrides():
    """While mailing columns are on, the reach they override is not counted.

    Include mailing columns always lists "By postal mail only" (#951), so the
    reader's own reach is kept but not applied; once the box is off it counts.
    """
    source = '{% filterpanel query mailing "reach" %}x{% endfilterpanel %}'
    query = DirectoryQuery(reach="email")
    # On: only Include mailing columns counts; the overridden reach does not.
    assert ">1 applied</span>" in render(source, query=query, mailing=True)
    assert ">2 applied</span>" in render(
        source, query=DirectoryQuery(reach="email", phone="yes"), mailing=True
    )
    # Off: the reader's reach is applied again, and counts.
    assert ">1 applied</span>" in render(source, query=query, mailing=False)
    assert "none applied" in render(source, query=DirectoryQuery(), mailing=False)
    assert applied_filters(query, ("reach",)) == 0


def test_panel_takes_a_query_and_at_most_one_extra():
    """A typo in the tag fails loudly rather than rendering a wrong panel."""
    with pytest.raises(TemplateSyntaxError):
        Template("{% load stewardship %}{% filterpanel %}x{% endfilterpanel %}")
    with pytest.raises(TemplateSyntaxError):
        Template("{% load stewardship %}{% filterpanel a b c d %}x{% endfilterpanel %}")


def test_open_word_opens_the_panel_by_default_only_when_given():
    """The Family directory passes ``open``; other panels stay closed."""
    opened = render(
        '{% filterpanel query mailing "reach" open %}x{% endfilterpanel %}',
        query=DirectoryQuery(),
        mailing=False,
    )
    assert opened.startswith('<details class="filter-panel" data-filter-panel open>')
    assert "none applied" in opened
    closed = render(
        "{% filterpanel query %}x{% endfilterpanel %}", query=DirectoryQuery()
    )
    assert closed.startswith('<details class="filter-panel" data-filter-panel><')


def test_fixed_word_draws_an_always_open_box():
    """Census changes passes ``fixed`` (#1682): a titled box, no disclosure.

    Its heading names the box and carries the same synced count, so an
    in-place Apply updates it as it does a panel's summary.
    """
    html = render(
        "{% filterpanel query fixed %}x{% endfilterpanel %}",
        query=CensusQuery(status="all"),
    )
    assert html == (
        '<section class="filter-panel" data-filter-panel'
        ' aria-labelledby="table-filters-heading">'
        '<h2 id="table-filters-heading">Filters: '
        '<span id="table-filters-applied" data-table-sync>1 applied</span></h2>'
        "x</section>"
    )
    assert "<details" not in html


# Django comments, tags and variables, removed before the panel's markup is
# parsed: they may sit inside a start tag ({% if %} selected{% endif %}).
TEMPLATE_SYNTAX = re.compile(
    r"{% comment %}.*?{% endcomment %}|{#.*?#}|{%.*?%}|{{.*?}}", re.DOTALL
)
VOID = frozenset({"input", "br", "hr", "img", "meta", "link", "wbr"})


class PanelCells(HTMLParser):
    """Record each label's ancestors and the order of the form's children."""

    def __init__(self):
        super().__init__()
        self.stack = []
        self.labels = []  # (label id, its ancestors, innermost last)
        self.children = []  # the form's direct child elements, in order

    def handle_starttag(self, tag, attrs):
        if self.stack == ["form"]:
            self.children.append((tag, dict(attrs).get("id")))
        if tag == "label":
            self.labels.append((dict(attrs).get("for"), list(self.stack)))
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            del self.stack[len(self.stack) - 1 - self.stack[::-1].index(tag) :]


def panel_cells(name):
    """Parse the form inside a report's ``filterpanel`` tag."""
    source = (TEMPLATES / name).read_text(encoding="utf-8")
    body = source.split("{% filterpanel", 1)[1].split("{% endfilterpanel %}", 1)[0]
    body = body.split("%}", 1)[1]
    # Apply filters and Clear filters are the shared component (#1651), one
    # cell of the grid; keep its place as markup.
    body = re.sub(
        r'{% include "stewardship/components/filter-actions.html".*?%}',
        '<div id="filter-actions"></div>',
        body,
    )
    parser = PanelCells()
    parser.feed(TEMPLATE_SYNTAX.sub("", body))
    return parser


@pytest.mark.parametrize(
    "name", ["census-changes.html", "information.html", "financial-report.html"]
)
def test_each_panel_filter_is_one_grid_cell(name):
    """Every label and its control share one div, a cell of the panel's grid.

    The open panel's form is a grid (#1411, ui-v1.css): a label or control
    left loose in the form would take a cell of its own and split the
    field. A range fieldset is one cell, its two ends a div each; Apply
    filters and Clear filters (one cell) follow the last field, and only
    full-row notes come after them, so they can share the last row of
    fields.
    """
    parser = panel_cells(name)
    assert parser.labels
    for label, ancestors in parser.labels:
        assert ancestors in (["form", "div"], ["form", "fieldset", "div"]), label
    tags = [tag for tag, _id in parser.children]
    after = tags[parser.children.index(("div", "filter-actions")) + 1 :]
    assert after and set(after) == {"p"}, parser.children

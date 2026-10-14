"""The ``filterpanel`` tag collapses a report's filters unless one is applied."""

from dataclasses import dataclass

import pytest
from django.template import Context, Template, TemplateSyntaxError

from parishkit.stewardship.accounts.templatetags.stewardship import applied_filters
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
    assert applied_filters(DirectoryQuery(search="Example", mode="testing")) == 2
    # "Current actionable requests" is Additional information's normal list.
    assert applied_filters(InformationQuery()) == 0
    assert applied_filters(InformationQuery(disposition="all")) == 1


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


def test_panel_takes_a_query_and_at_most_one_extra():
    """A typo in the tag fails loudly rather than rendering a wrong panel."""
    with pytest.raises(TemplateSyntaxError):
        Template("{% load stewardship %}{% filterpanel %}x{% endfilterpanel %}")
    with pytest.raises(TemplateSyntaxError):
        Template("{% load stewardship %}{% filterpanel a b c %}x{% endfilterpanel %}")

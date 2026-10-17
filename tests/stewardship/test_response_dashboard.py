"""The Response dashboard's funnel shaping and page, without a database (#477, #1537).

The funnel is the synthetic response-metrics rows the chart tests use, and
the statistics and daily chart the digest test document's; the view's
admission, guard and audit are covered against PostgreSQL in
``database/test_response_dashboard_postgresql.py``.
"""

import re
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import UUID

import pytest
from django.http import QueryDict
from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship import observability
from parishkit.stewardship.reports import workspace_views
from parishkit.stewardship.reports.chart_specs import participation_chart
from parishkit.stewardship.reports.digest_presentation import (
    participation_table_context,
)
from parishkit.stewardship.reports.facts import FactUnavailable
from parishkit.stewardship.reports.response_dashboard import (
    HOURLY_SPAN,
    DashboardQuery,
    at_grain,
    chosen_grain,
    funnel_panels,
    tiles,
)
from parishkit.stewardship.reports.statistics import StatisticsUnavailable
from parishkit.stewardship.reports.workspace import ReportQuery
from parishkit.stewardship.reports.workspace_views import (
    daily_table,
    mode_links,
    scope_links,
    statistics_panel,
)
from parishkit.stewardship.web.dates import using

from .test_chart_specs import metrics
from .test_daily_digest_content import document
from .test_response_metrics import START

PATH = reverse("admin:response_dashboard")
# The one region every choice of the page refreshes in place and lands on
# (#519, #1537).
REGION = "response-dashboard"


def page_context(query=None, funnel=..., *, can_test=True, value=None):
    """The context the view gives the template, from synthetic values.

    ``funnel`` is the mode's metrics (None: a Testing view with no
    rehearsal; by default the chart tests' metrics). A Production view also
    carries the statistics, the daily chart and table, and the export.
    """
    query = query or ReportQuery()
    value = value or document()
    chart = value.participation
    campaign = SimpleNamespace(
        pk=chart.campaign_id,
        active_configuration=SimpleNamespace(
            name=chart.campaign_name, values=SimpleNamespace(modules=["financial"])
        ),
        state="active",
    )
    context = {
        "campaign": campaign,
        "query": query,
        "testing": query.mode == "testing",
        "can_test": can_test,
        "mode_links": mode_links(query),
        "metrics": None,
    }
    funnel = metrics() if funnel is ... else funnel
    if funnel is not None:
        context |= funnel_panels(funnel)
    if query.mode == "testing":
        return context
    presented = participation_table_context(chart)
    return context | {
        **presented,
        "scope_links": scope_links(query),
        "selection": SimpleNamespace(updating=False, expected=True),
        "statistics": value.statistics,
        **statistics_panel(value.statistics),
        "export_key": UUID(int=10),
        "export_allowed": True,
        "row_count": len(chart.days),
        **daily_table(chart, presented, query),
        "daily_chart": participation_chart(chart),
    }


def render(*args, **kwargs):
    """The Response dashboard page as the view renders it."""
    with using("us_long"):
        return render_to_string(
            "stewardship/response-dashboard.html", page_context(*args, **kwargs)
        )


def test_cli_query_accepts_only_its_closed_vocabulary():
    """``report responses`` takes mode and grain, each from a fixed list."""
    assert DashboardQuery.parse(QueryDict("")) == DashboardQuery("production", "auto")
    assert DashboardQuery.parse(QueryDict("mode=testing&grain=day")) == (
        DashboardQuery("testing", "day")
    )
    for invalid in ("mode=live", "grain=week", "page=2", "mode=testing&mode=testing"):
        with pytest.raises(ValueError):
            DashboardQuery.parse(QueryDict(invalid))


def test_grain_is_hourly_for_a_short_span_and_daily_after():
    """Auto picks by what the series spans; a reader's choice always wins."""
    value = metrics()
    assert chosen_grain(value, "auto") == "hour"
    assert chosen_grain(value, "day") == "day"
    # Nothing to chart: hourly is as good as any.
    assert chosen_grain(metrics(families=(), sends=False), "auto") == "hour"
    # The span runs from the earliest send or first instant to the cutoff.
    late = replace(value, as_of=START + HOURLY_SPAN + timedelta(days=1))
    assert chosen_grain(late, "auto") == "day"
    assert chosen_grain(late, "hour") == "hour"


def test_regrouping_reuses_the_rows_and_keeps_the_totals():
    """Daily buckets come from the same rows, so the series still adds up."""
    value = metrics()
    daily = at_grain(value, "day")
    assert at_grain(value, "hour") is value
    assert daily.grain == "day" and len(daily.activity) == 1
    assert daily.families is value.families
    assert sum(bucket.submissions for bucket in daily.activity) == value.stage(
        "submitted"
    )


def test_tiles_carry_every_stage_count():
    """One tile per stage with its share of Invited and its note."""
    shown = tiles(metrics())
    assert [(tile.key, tile.text, tile.details, tile.bar) for tile in shown] == [
        ("invited", "4", (), (4, 4)),
        (
            "link_followed",
            "5",
            ("125% compared with invited", "Includes mail-scanner prefetches"),
            (5, 4),
        ),
        ("form_opened", "4", ("100% compared with invited",), (4, 4)),
        ("progressed", "3", ("75% compared with invited",), (3, 4)),
        ("submitted", "3", ("75% compared with invited",), (3, 4)),
    ]


def test_page_shows_the_funnel_then_the_statistics_chart_and_export():
    """Families by stage, the funnel, the statistics, the daily chart and
    the small export panel, in that order, in one in-place region (#1537)."""
    page = render()
    assert "<h1>Response dashboard</h1>" in page
    headings = re.findall(r"<h2[^>]*>([^<—]+)", page)
    assert [heading.strip() for heading in headings] == [
        "Families by stage",
        "Response funnel",
        "Current active population statistics",
        "Daily participation",
        "Export",
    ]
    assert f'<div id="{REGION}" data-in-place-region' in page
    # The stage tiles come first, then the statistics' own tiles.
    assert re.findall(r'<span class="stat-value">([^<]+)</span>', page)[:5] == [
        "4",
        "5",
        "4",
        "3",
        "3",
    ]
    assert "125% compared with invited" in page
    # No "Also counted" figures, no Lists of Families, no activity chart.
    for gone in ("Also counted", "Submitted more than once", "Lists of Families"):
        assert gone not in page
    assert 'id="response-activity-spec"' not in page and "By hour" not in page
    # The funnel labels its bars, so it has no exact-values table; the
    # page's only table is the daily one.
    assert 'id="response-funnel-spec"' in page
    assert 'class="chart-table"' not in page
    assert page.count("<table") == 1 and "exact daily values" in page
    # No inline script or style.
    assert not re.search(r"<script(?![^>]*\bsrc=)(?![^>]*application/json)", page)
    assert " style=" not in page and "<style" not in page


def test_statistics_panel_has_one_data_as_of_line():
    """ "Data as of" the counting moment, in browser-local time (#1537); the
    other time lines, the pledge periods and Technical details are gone."""
    value = document()
    page = render(value=value)
    instant = value.statistics.observed_at.isoformat()
    time = f'<time datetime="{instant}" data-local-instant>{instant}</time>'
    assert f"<p>Data as of {time}</p>" in page
    for gone in (
        "Counted at",
        "ParishSoft data as of",
        "Upcoming pledge period",
        "Comparison period",
        "Giving observed at",
        "Technical details",
        "Families eligible right now and their latest answers.",
    ):
        assert gone not in page, gone
    # No "UTC" label: the browser shows the time in its own zone.
    panel = page[page.index('id="participation-statistics"') :]
    assert "UTC" not in panel[: panel.index("</section>")]


def test_export_panel_is_small_with_xlsx_first():
    """One sentence, the format menu (XLSX first and chosen, no PNG) and
    Generate export; no latest-data export (#1537)."""
    page = render()
    panel = page[page.index('id="participation-export"') :]
    assert "<p>Exports the daily participation data on this page.</p>" in panel
    assert re.findall(r'<option value="(\w+)"', panel) == ["xlsx", "csv", "pdf"]
    assert '<option value="xlsx" selected>' in panel
    assert reverse("admin:report_export_create") in panel
    assert reverse("admin:report_export_create").startswith("/admin/reports/responses/")
    for gone in (
        "Queue export",
        "Export with the latest data",
        "Statistics cards are not part",
        "dates ascending",
        "Sensitive",
    ):
        assert gone not in panel, gone


def test_choices_are_in_place_links_carrying_the_view():
    """The Production / Testing rehearsal pair (Administrators only) and the
    Chart population pair each link to this page and land on its region; a
    mode link keeps the population, sort and size (#1535, #1537)."""
    query = ReportQuery(scope="current", sort="date_asc", size="25")
    page = render(query)
    common = "scope=current&amp;page=1&amp;sort=date_asc&amp;size=25&amp;timezone=UTC"
    production = f'<a href="{PATH}?{common}#{REGION}" data-in-place="mode-production"'
    assert production + ' aria-current="page">Production' in page
    testing = (
        f'<a href="{PATH}?scope=current&amp;page=1&amp;mode=testing&amp;sort=date_asc'
        f'&amp;size=25&amp;timezone=UTC#{REGION}" data-in-place="mode-testing">'
        "Testing rehearsal"
    )
    assert testing in page
    current = f'<a href="{PATH}?{common}#{REGION}" data-in-place="scope-current"'
    assert current + ' aria-current="page">Current population' in page
    # The Chart population sits in the Daily participation panel.
    chart = page[page.index('id="participation-chart"') :]
    assert 'id="participation-scope"' in chart
    assert page.index('id="dashboard-mode"') < page.index('id="response-stages"')
    # Staff are never offered Testing.
    staff = render(can_test=False)
    assert "Testing rehearsal" not in staff and "mode=testing" not in staff


def test_testing_view_shows_the_rehearsal_funnel_only():
    """Families by stage and the funnel show the rehearsal; the Production
    panels are left out with one line saying why (#1537)."""
    page = render(ReportQuery(mode="testing"))
    assert "never counted in Production" in page
    assert "Families by stage" in page and 'id="response-funnel-spec"' in page
    reason = (
        '<p id="production-only">Population statistics, daily participation and '
        "the export count Production responses only. Choose Production to see "
        "them.</p>"
    )
    assert reason in page
    for gone in ("participation-statistics", "participation-chart", "export-format"):
        assert gone not in page
    testing = f'<a href="{PATH}?scope=historical&amp;page=1&amp;mode=testing'
    assert testing in page and 'aria-current="page">Testing rehearsal' in page
    # With no rehearsal the view says there is nothing to show.
    empty = render(ReportQuery(mode="testing"), None)
    assert "no Testing responses to show" in empty and reason in empty
    assert "stat-tile" not in empty and "data-chart=" not in empty
    assert "never counted in Production" not in empty
    # Production never says it.
    assert 'id="production-only"' not in render()


def test_funnel_is_named_by_its_heading_not_a_drawn_title():
    """The panel heading names the chart; the drawn title is left out, and
    so is the count axis, since each bar carries its count (#1537); the
    emailed chart keeps both."""
    from parishkit.stewardship.reports.chart_specs import funnel_chart

    shown = funnel_panels(metrics())["funnel"].spec
    assert "title" not in shown and shown["encoding"]["x"]["axis"] is None
    emailed = funnel_chart(metrics()).spec
    assert "title" in emailed and emailed["encoding"]["x"]["axis"]
    page = render()
    assert '<h2 id="funnel-heading">Response funnel</h2>' in page
    assert 'data-chart-title="Response funnel"' in page


def test_no_invitations_yet_says_so_instead_of_shares():
    """With nothing invited a share means nothing, so the page says why."""
    rows = tuple(replace(row, invited_at=None) for row in metrics().families)
    page = render(funnel=metrics(families=rows))
    assert "No invitations have been delivered yet." in page
    assert "compared with invited" not in page
    assert "No invitations" not in render()


def test_tiles_draw_their_share_of_invited_as_bars():
    """Each stage tile's share bar runs against Invited and is hidden from
    AT (#720); with nothing invited no stage tile has a bar."""
    page = render(ReportQuery(mode="testing"))
    bars = re.findall(
        r'<span class="stat-bar" aria-hidden="true">'
        r'<progress value="(\d+)" max="(\d+)"></progress></span></li>',
        page,
    )
    assert bars == [("4", "4"), ("5", "4"), ("4", "4"), ("3", "4"), ("3", "4")]
    rows = tuple(replace(row, invited_at=None) for row in metrics().families)
    uninvited = render(ReportQuery(mode="testing"), metrics(families=rows))
    assert "<progress" not in uninvited


def test_daily_table_links_keep_the_mode_and_population():
    """A daily-table navigator or heading keeps the page's view (#1537)."""
    query = ReportQuery(scope="current", size="5")
    links = [urlsplit(href).query for href in scope_links(query).values()]
    assert all("mode" not in link for link in links)
    assert ("mode", "testing") in ReportQuery(mode="testing").carried()


def test_unreadable_statistics_leave_the_funnel_and_say_so():
    """When the statistics or daily facts cannot be read, the Production
    view still shows the funnel and says the other panels are unavailable
    (#1537), with no zero assumed."""
    context = page_context(ReportQuery(mode="testing")) | {
        "query": ReportQuery(),
        "testing": False,
        "participation_unavailable": True,
    }
    with using("us_long"):
        page = render_to_string("stewardship/response-dashboard.html", context)
    assert "Families by stage" in page and 'id="response-funnel-spec"' in page
    assert '<p id="production-unavailable">' in page
    assert "no zero values have been assumed" in page
    for gone in ("participation-statistics", "participation-chart", "export-format"):
        assert gone not in page
    assert 'id="production-unavailable"' not in render()


@pytest.mark.parametrize(
    ("error", "cause"),
    [(FactUnavailable("x"), "facts"), (StatisticsUnavailable("x"), "statistics")],
)
def test_swallowed_participation_errors_are_logged(monkeypatch, caplog, error, cause):
    """The in-page degradation keeps its debug log, so a real code fault
    hidden as "unavailable" stays visible (#1537)."""
    from contextlib import ExitStack

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(workspace_views, "guarded_participation", fail)
    monkeypatch.setattr(observability, "debug_logging_enabled", lambda: True)
    with caplog.at_level("DEBUG", logger="parishkit.stewardship.debug"):
        result = workspace_views._participation(ExitStack(), None, None, ReportQuery())
    assert result == (None, cause)
    assert [r.exc_info[1] for r in caplog.records] == [error]


def test_no_parishsoft_data_wording_differs_from_a_pending_chart():
    """No ParishSoft data yet is not a wait of a few minutes (#1537)."""
    context = page_context(ReportQuery(mode="testing")) | {
        "query": ReportQuery(),
        "testing": False,
        "participation_unavailable": True,
        "participation_cause": "statistics",
    }
    with using("us_long"):
        page = render_to_string("stewardship/response-dashboard.html", context)
    assert "none is available yet" in page and "few minutes" not in page

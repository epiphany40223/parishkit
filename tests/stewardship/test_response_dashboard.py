"""The response dashboard's options, shaping and page, without a database (#477).

The metrics are the synthetic response-metrics rows the chart tests use; the
view's admission, guard and audit are covered against PostgreSQL in
``database/test_response_dashboard_postgresql.py``.
"""

import re
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from django.http import QueryDict
from django.template.loader import render_to_string

from parishkit.stewardship.reports.response_dashboard import (
    HOURLY_SPAN,
    DashboardQuery,
    at_grain,
    chosen_grain,
    figures,
    page_context,
    tiles,
)
from parishkit.stewardship.web.dates import using

from .test_chart_specs import metrics
from .test_response_metrics import START

CAMPAIGN = SimpleNamespace(
    pk=UUID(int=477), active_configuration=SimpleNamespace(name="Sample campaign")
)
PATH = f"/admin/reports/{CAMPAIGN.pk}/responses/"
# The dashboard region its switches refresh in place and land on (#519).
REGION = "response-dashboard"


def test_query_accepts_only_its_closed_vocabulary():
    """Mode and grain are the only parameters, each from a fixed list."""
    assert DashboardQuery.parse(QueryDict("")) == DashboardQuery("production", "auto")
    assert DashboardQuery.parse(QueryDict("mode=testing&grain=day")) == (
        DashboardQuery("testing", "day")
    )
    for invalid in ("mode=live", "grain=week", "page=2", "mode=testing&mode=testing"):
        with pytest.raises(ValueError):
            DashboardQuery.parse(QueryDict(invalid))


def test_query_urls_leave_defaults_out_and_keep_the_other_choice():
    """Switching mode keeps the grain and switching grain keeps the mode."""
    query = DashboardQuery("testing", "day")
    assert DashboardQuery().url(CAMPAIGN.pk) == PATH
    assert query.url(CAMPAIGN.pk, mode="production") == PATH + "?grain=day"
    assert query.url(CAMPAIGN.pk, grain="hour") == PATH + "?mode=testing&grain=hour"


def test_grain_is_hourly_for_a_short_span_and_daily_after():
    """Auto picks by what the chart spans; a reader's choice always wins."""
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


def test_tiles_and_figures_carry_every_count():
    """One tile per stage with its share of Invited; three figures beside them."""
    value = metrics()
    shown = tiles(value)
    assert [(tile["key"], tile["count"], tile["share"]) for tile in shown] == [
        ("invited", 4, "100%"),
        ("link_followed", 5, "125%"),
        ("form_opened", 4, "100%"),
        ("progressed", 3, "75%"),
        ("submitted", 3, "75%"),
    ]
    assert shown[1]["note"] == "Includes mail-scanner prefetches"
    assert [count for _label, count in figures(value)] == [0, 0, 1]


def render(query=None, value=None, *, can_test=True):
    """The dashboard page as the view renders it, from synthetic metrics."""
    with using("us_long"):
        context = page_context(
            CAMPAIGN, query or DashboardQuery(), value, can_test=can_test
        )
        return render_to_string("stewardship/response-dashboard.html", context)


def test_page_shows_tiles_figures_and_both_charts():
    """Counts first, then the two charts, each with its summary and table."""
    page = render(value=metrics())
    assert "<h1>Response dashboard</h1>" in page
    assert "Sample campaign — Production" in page
    assert re.findall(r'<span class="stat-value">(\d+)</span>', page) == [
        "4",
        "5",
        "4",
        "3",
        "3",
    ]
    assert "125% compared with invited" in page
    assert "Includes mail-scanner prefetches" in page
    assert "<dt>Submitted more than once</dt><dd>1</dd>" in page
    assert 'id="response-funnel-spec"' in page and 'id="response-activity-spec"' in page
    assert page.count('class="chart-table"') == 2
    # The page loads the chart engine and carries no inline script or style.
    assert "stewardship/chart-v1.js" in page
    assert not re.search(r"<script(?![^>]*\bsrc=)(?![^>]*application/json)", page)
    assert " style=" not in page and "<style" not in page
    # The grain choice marks the one shown; the Admin sees the mode switch.
    # Each is an in-place link that refreshes the dashboard region (ui-v1.js,
    # #519); its fragment lands an ordinary load there too.
    hour = f'<a href="{PATH}?grain=hour#{REGION}" data-in-place="grain-hour"'
    assert hour + ' aria-current="page">By hour' in page
    day = f'<a href="{PATH}?grain=day#{REGION}" data-in-place="grain-day">By day'
    assert day in page
    production = f'<a href="{PATH}#{REGION}" data-in-place="mode-production"'
    assert production + ' aria-current="page">Production' in page
    testing = f'<a href="{PATH}?mode=testing#{REGION}" data-in-place="mode-testing"'
    assert testing + ">Testing rehearsal" in page
    assert f'<div id="{REGION}" data-in-place-region>' in page
    # The lists behind the counts are links with their lengths (#477, PR 5).
    assert f'<a href="{PATH}submitted/">Families that submitted</a>: 3' in page
    assert f'<a href="{PATH}data-quality/">ParishSoft data to check</a> <' in page
    # No Family name or DUID reaches the page: counts only.
    assert "family_duid" not in page


def test_staff_page_has_no_testing_switch():
    """Testing data is for Administrators; Staff are not offered the switch."""
    page = render(value=metrics(), can_test=False)
    assert "Testing rehearsal" not in page and "mode=testing" not in page


def test_testing_page_says_so_and_handles_no_rehearsal():
    """The Testing view is labelled; with no rehearsal it says why it is empty."""
    query = DashboardQuery("testing")
    page = render(query, metrics())
    assert "Sample campaign — Testing rehearsal" in page
    assert 'class="notice"' in page and "never counted in Production" in page
    testing = f'<a href="{PATH}?mode=testing#{REGION}" data-in-place="mode-testing"'
    assert testing + ' aria-current="page">Testing' in page
    empty = render(query, None)
    assert "no Testing responses to show" in empty
    assert "stat-tile" not in empty and "data-chart=" not in empty
    # No figures, no Testing notice: there is nothing it could qualify.
    assert "never counted in Production" not in empty


def test_page_charts_are_named_by_their_headings_not_drawn_titles():
    """The panel heading names each chart; the drawn title is left out."""
    shaped = page_context(CAMPAIGN, DashboardQuery(), metrics())
    funnel, activity = shaped["charts"]
    assert "title" not in funnel.spec and "title" not in activity.spec
    page = render(value=metrics())
    assert '<h2 id="funnel-heading">Response funnel</h2>' in page
    assert '<h2 id="activity-heading">Response activity by hour</h2>' in page
    assert 'data-chart-title="Response funnel"' in page


def test_no_invitations_yet_says_so_instead_of_shares():
    """With nothing invited a share means nothing, so the page says why."""
    rows = tuple(replace(row, invited_at=None) for row in metrics().families)
    page = render(value=metrics(families=rows))
    assert "No invitations have been delivered yet." in page
    assert "compared with invited" not in page
    assert "No invitations" not in render(value=metrics())

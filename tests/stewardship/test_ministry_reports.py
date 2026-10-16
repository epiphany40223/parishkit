"""Private report filters and capability scope without database startup."""

from uuid import uuid4

import pytest
from django.http import QueryDict

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.audit.schemas import ContextKind, sanitize
from parishkit.stewardship.reports.ministries import (
    MinistryQuery,
    can_report,
    matching_summaries,
    packet_choices,
    summary_totals,
)
from parishkit.stewardship.reports.ministry_export_views import (
    TOO_MANY,
    TOO_MANY_FIX,
    TooManyMinistries,
    packet_selection,
)

from .test_ministry_table_columns import ministry_page as render_report


@pytest.mark.parametrize(
    "values",
    [
        {"page": "0"},
        {"page": "01"},
        {"page": "10001"},
        {"page": "١"},
        {"search": "x" * 201},
        {"search": "private\x00value"},
        {"start": "2026-02-30"},
        {"start": "20261001"},
        {"start": "2026-10-02", "end": "2026-10-01"},
        # The retired Ministry activity filter (#1267), any value.
        {"activity": "any"},
        {"activity": "inactive"},
        {"state": "complete"},
        {"history": "yes"},
        {"sort": "name;SELECT"},
        {"scope": "9"},
        {"operational": "true"},
    ],
)
def test_filters_fail_closed_without_reflecting_private_values(values):
    """Browser input cannot select projection authority or escape bounded filters."""
    with pytest.raises(ValueError):
        MinistryQuery.parse(values, detail=True)


def test_native_filters_keep_private_state_and_reject_duplicates():
    """Summary-only and detailed forms have explicit filter semantics."""
    value = MinistryQuery.parse(
        {
            "search": "Private Member",
            "history": "all",
            "start": "2026-01-01",
            "zone": "America/New_York",
            "page": "2",
        },
        detail=True,
    )
    assert "Private" not in repr(value) and value.page == 2
    assert (
        "page" not in value.form_values()
        and value.form_values()["search"] == "Private Member"
    )
    with pytest.raises(ValueError):
        MinistryQuery.parse(QueryDict("search=one&search=two"), detail=True)
    for values in (
        {"history": "all"},
        {"state": "resolved"},
        {"sort": "newest"},
        {"start": "2026-01-01"},
    ):
        with pytest.raises(ValueError):
            MinistryQuery.parse(values)


def test_dates_need_the_browsers_zone_and_an_unused_zone_is_dropped():
    """Days are the viewer's browser zone (#558): never guessed, never another."""
    from parishkit.stewardship.web.dates import UnknownZone

    for zone in ("", "Etc/Unknown", "../../etc/passwd", "EST5EDT "):
        with pytest.raises(UnknownZone):
            MinistryQuery.parse({"end": "2026-01-01", "zone": zone}, detail=True)
    query = MinistryQuery.parse({"zone": "Etc/Unknown"}, detail=True)
    assert query.zone == "" and query.form_values()["zone"] == ""
    query = MinistryQuery.parse(
        {"start": "2026-01-01", "zone": "Asia/Kathmandu"}, detail=True
    )
    assert query.form_values()["zone"] == "Asia/Kathmandu"


@pytest.mark.parametrize(
    "roles,scope,expected",
    [
        (set(), set(), False),
        ({"ministry_leader"}, set(), False),
        ({"ministry_leader"}, {9}, True),
        ({"staff"}, set(), True),
        ({"administrator"}, set(), True),
    ],
)
def test_report_entry_requires_operational_role_or_current_assignment(
    roles, scope, expected
):
    """Leader entry does not imply parish-wide campaign/report access."""
    assert (
        can_report(Principal(uuid4(), frozenset(roles), frozenset(scope))) is expected
    )
    assert not can_report(None)


def test_audit_scope_is_only_a_canonical_ministry_duid():
    """Neither contact text nor browser-shaped booleans fit scope metadata."""
    assert sanitize(ContextKind.ACTION, {"ministry_duid": 9}) == {"ministry_duid": 9}
    for value in (True, 0, -1, 2**31, "9", "private@example.org", [9]):
        with pytest.raises(ValueError):
            sanitize(ContextKind.ACTION, {"ministry_duid": value})


def summary_result(*names, total=None):
    """A summary page as the selection returns it: Ministries named ``names``."""
    rows = [{"duid": duid, "name": name} for duid, name in enumerate(names, 1)]
    return {"summaries": rows, "total": len(rows) if total is None else total}


def test_packet_choices_reuse_a_page_that_holds_the_whole_summary():
    """A page with every unfiltered Ministry needs no second read (#533).

    The choices are by name, case-insensitively, whatever the table's sort.
    """

    def read_every():
        """Fails the test: this page already holds every Ministry."""
        raise AssertionError("The whole summary was read again.")

    result = summary_result("choir", "Altar servers", "Bread")
    assert [
        row["name"]
        for row in packet_choices(result, MinistryQuery(sort="duid_desc"), read_every)
    ] == ["Altar servers", "Bread", "choir"]


@pytest.mark.parametrize(
    "query, total",
    [
        (MinistryQuery(), 3),  # More Ministries than this page shows.
        (MinistryQuery(page=2), 3),  # A later page.
        (MinistryQuery(search="choir"), 1),  # A search narrows the table.
    ],
)
def test_packet_choices_list_every_ministry_beyond_the_table(query, total):
    """A page or search narrower than the scope reads it whole (#533)."""
    reads = []

    def read_every():
        """The whole unfiltered summary in scope."""
        reads.append(True)
        return summary_result("Choir", "Bread", "Altar servers")

    choices = packet_choices(summary_result("Choir", total=total), query, read_every)
    assert reads == [True]
    assert [row["name"] for row in choices] == ["Altar servers", "Bread", "Choir"]
    # Only names and DUIDs: the choices carry no request counts.
    assert all(set(row) == {"duid", "name"} for row in choices)


def test_packet_boxes_list_the_choices_not_the_table_rows():
    """The packet's boxes are its choices, and they do not follow the table.

    The table shows one Ministry; the packet offers both. Its choices are
    not marked data-table-sync, so paging or re-sorting the table in place
    keeps the reader's ticks (#533).
    """
    shown = {
        "name": "Food pantry",
        "duid": 9,
        "active": True,
        "in_campaign": True,
        "joining": 1,
        "leaving": 0,
        "unresolved": 1,
        "progress": "0 of 1",
    }
    choices = [{"duid": 4, "name": "Choir"}, {"duid": 9, "name": "Food pantry"}]
    html = render_report([], summaries=[shown], packet_choices=choices)
    start = html.index('<div id="ministry-packet-choices">')
    packet = html[start : html.index("</fieldset>", start)]
    assert packet.count('name="ministries"') == 2
    assert 'value="4"> Choir' in packet and 'value="9"> Food pantry' in packet
    assert "data-table-sync" not in packet
    # The form states the packet limit the server enforces.
    assert "at most 200 ticked Ministries" in packet


def test_packet_limit_is_refused_with_its_own_message():
    """Ticking more than 200 Ministries gets a refusal saying what to do."""
    form = {"format": "pdf", "browser_timezone": "UTC", "request_key": "k"}

    def chosen(count):
        """The packet form with ``count`` Ministries ticked."""
        values = QueryDict(mutable=True)
        values.update(form)
        values.setlist("ministries", [str(duid) for duid in range(1, count + 1)])
        return values

    assert len(packet_selection(chosen(200))) == 200
    with pytest.raises(TooManyMinistries):
        packet_selection(chosen(201))
    assert "at most 200 ticked Ministries" in str(TOO_MANY)
    assert "leave every box unticked" in str(TOO_MANY_FIX)


def counted(name, joining, leaving, completed):
    """A summary row as the selection returns it, with consistent counts."""
    requests = joining + leaving
    return {
        "duid": len(name),
        "name": name,
        "joining": joining,
        "leaving": leaving,
        "unresolved": requests - completed,
        "completed": completed,
        "requests": requests,
        "progress": f"{completed} out of {requests}",
    }


def test_summary_totals_add_up_every_count():
    """The page's totals add each count across the Ministries given (#720)."""
    rows = [counted("Choir", 3, 1, 2), counted("Bread", 0, 2, 2)]
    assert summary_totals(rows) == {
        "joining": 3,
        "leaving": 3,
        "unresolved": 2,
        "completed": 4,
        "requests": 6,
    }
    assert summary_totals([])["requests"] == 0


def test_matching_summaries_reuse_a_page_that_holds_every_match():
    """A page with every matching Ministry needs no second read (#720)."""

    def read_every(query):
        """Fails the test: this page already holds every match."""
        raise AssertionError("The matching summary was read again.")

    result = summary_result("Choir", "Bread")
    assert (
        matching_summaries(result, MinistryQuery(), read_every) == (result["summaries"])
    )


def test_matching_summaries_read_beyond_the_page_with_the_same_search():
    """Totals cover every page: the whole match is read with the query's
    search, so they follow the search and not the paging (#720)."""
    reads = []
    query = MinistryQuery(search="o", page=2)

    def read_every(given):
        """The whole matching summary for ``given``."""
        reads.append(given)
        return summary_result("Choir", "Food", "Hospitality")

    rows = matching_summaries(summary_result("Choir", total=3), query, read_every)
    assert reads == [query]
    assert [row["name"] for row in rows] == ["Choir", "Food", "Hospitality"]


def test_totals_tiles_chart_and_cell_bars_render_above_the_table():
    """The totals region follows the search in place, its chart draws its
    values so it has no exact-values table, and each row's progress cell
    has a bar hidden from assistive technology (#720)."""
    html = render_report(
        [], summaries=[counted("Choir", 3, 1, 2), counted("Bread", 0, 0, 0)]
    )
    start = html.index('<div id="ministry-totals" data-table-sync>')
    region = html[start : html.index('<div id="', start + 1)]
    assert "Totals for all matching Ministries" in region
    assert 'class="stat-value">3</span>' in region  # Join requests.
    assert 'class="stat-value">1</span>' in region  # Leave requests.
    assert 'data-chart="ministry-followup-spec"' in region
    assert "Exact values" not in region
    assert start < html.index("<table>")
    assert (
        '<progress value="2" max="4" aria-hidden="true"></progress> 2 out of 4' in html
    )
    # A Ministry with no request has no bar, only its text.
    assert '<td class="progress-cell">0 out of 0</td>' in html


def test_totals_without_requests_show_tiles_and_no_chart():
    """With no request nothing is drawn: the tiles still read zero."""
    html = render_report([], summaries=[counted("Choir", 0, 0, 0)])
    assert 'id="ministry-totals"' in html
    assert "data-chart=" not in html

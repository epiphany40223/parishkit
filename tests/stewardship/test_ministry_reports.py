"""Private report filters and capability scope without database startup."""

from uuid import uuid4

import pytest
from django.http import QueryDict

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.audit.schemas import ContextKind, sanitize
from parishkit.stewardship.reports import ministry_followup
from parishkit.stewardship.reports.ministries import MinistryQuery, can_report
from parishkit.stewardship.reports.ministry_export_views import (
    TOO_MANY,
    TOO_MANY_FIX,
    TooManyMinistries,
    packet_selection,
)
from parishkit.stewardship.reports.ministry_followup import (
    NOT_IN_SOURCE,
    FollowupQuery,
    request_totals,
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


def test_packet_boxes_list_the_choices_not_the_table_rows():
    """The packet's boxes are its choices, and they do not follow the table.

    By Ministry shows one Ministry; the packet offers both. Its choices are
    not marked data-table-sync, so filtering, paging or re-sorting the
    table in place keeps the reader's ticks (#533).
    """
    shown = {
        "name": "Food pantry",
        "duid": 9,
        "active": True,
        "in_campaign": True,
        "joining": 1,
        "leaving": 0,
        "unresolved": 1,
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


def selection(totals):
    """A stand-in for the queue's selection: each read's total by filters.

    ``totals`` maps (action, state) to the count the selection would give;
    every read is recorded, so a test can see which counts were read.
    """
    reads = []

    def select(campaign_id, query, principal, *, limit, **_):
        """The selection's answer with no rows (``page_limit`` 0)."""
        assert limit == 0
        reads.append((query.action, query.state, query.outcome))
        return {
            "authorized": True,
            "ministries": [],
            "total": totals[(query.action, query.state)],
        }

    return select, reads


STAFF = Principal(uuid4(), frozenset({"staff"}))


def test_totals_count_the_view_and_its_progress_whatever_the_status(monkeypatch):
    """The tiles count what the table selects; the progress chart counts
    the same requests whatever their status (#1133)."""
    select, reads = selection(
        {("join", "unresolved"): 3, ("any", "any"): 10, ("any", "unresolved"): 4}
    )
    monkeypatch.setattr(ministry_followup, "_select", select)
    # The default view: 4 unresolved requests, 3 of them joins.
    totals = request_totals(None, FollowupQuery(), STAFF, 4)
    assert totals == {
        "joining": 3,
        "leaving": 1,
        "requests": 10,
        "unresolved": 4,
        "completed": 6,
    }
    # The view's own total answered the unresolved count: not read again.
    assert reads == [("join", "unresolved", "any"), ("any", "any", "any")]


def test_totals_read_nothing_the_view_already_answers(monkeypatch):
    """A Join view of every status needs only the unresolved count."""
    select, reads = selection({("join", "unresolved"): 2})
    monkeypatch.setattr(ministry_followup, "_select", select)
    query = FollowupQuery(action="join", state="any")
    assert request_totals(None, query, STAFF, 5) == {
        "joining": 5,
        "leaving": 0,
        "requests": 5,
        "unresolved": 2,
        "completed": 3,
    }
    assert reads == [("join", "unresolved", "any")]


def test_totals_for_members_no_longer_in_parishsoft_have_no_progress(monkeypatch):
    """Nothing can be recorded for those requests, so no chart (#1217)."""
    select, reads = selection({("join", NOT_IN_SOURCE): 1})
    monkeypatch.setattr(ministry_followup, "_select", select)
    totals = request_totals(None, FollowupQuery(state=NOT_IN_SOURCE), STAFF, 2)
    assert totals == {"joining": 1, "leaving": 1, "requests": None}
    # An empty view reads nothing at all.
    reads.clear()
    assert request_totals(None, FollowupQuery(state=NOT_IN_SOURCE), STAFF, 0) == {
        "joining": 0,
        "leaving": 0,
        "requests": None,
    }
    assert reads == []


def test_totals_refuse_a_count_outside_the_readers_scope(monkeypatch):
    """Each count passes the queue's cross-check (#389 L3)."""

    def select(campaign_id, query, principal, *, limit, **_):
        """A count naming a Ministry this leader does not lead."""
        return {"authorized": True, "ministries": [{"duid": 77}], "total": 1}

    monkeypatch.setattr(ministry_followup, "_select", select)
    leader = Principal(uuid4(), frozenset({"ministry_leader"}), frozenset({9}))
    with pytest.raises(PermissionError):
        request_totals(None, FollowupQuery(), leader, 1)


TOTALS = {"joining": 3, "leaving": 1, "requests": 6, "unresolved": 4, "completed": 2}


def test_totals_tiles_and_chart_render_between_the_filters_and_the_table():
    """The totals follow the filters in place (data-table-sync), under the
    filter bar so a change in their height never moves Apply filters; the
    chart draws its values, so it has no exact-values table (#720, #1133)."""
    html = render_report([], totals=TOTALS)
    start = html.index('<div id="ministry-totals" data-table-sync>')
    region = html[start : html.index("</div>", html.index("</ul>", start))]
    assert ">Totals</h2>" in region
    assert 'class="stat-value">3</span>' in region  # Join requests.
    assert 'class="stat-value">1</span>' in region  # Leave requests.
    assert 'data-chart="ministry-followup-spec"' in html
    assert "Exact values" not in html
    assert html.index('id="table-filters"') < start < html.index('id="by-ministry"')
    assert html.index('id="by-ministry"') < html.index("data-table-region")


def test_totals_without_requests_show_tiles_and_no_chart():
    """With no request nothing is drawn: the tiles still read zero."""
    html = render_report([])
    assert 'id="ministry-totals"' in html
    assert "data-chart=" not in html


def counts(duid, name, joining=0, leaving=0, unresolved=0, links=None):
    """One By Ministry row as the view builds it, with its count links."""
    return {
        "duid": duid,
        "name": name,
        "active": True,
        "in_campaign": True,
        "joining": joining,
        "leaving": leaving,
        "unresolved": unresolved,
        "count_links": links or {},
    }


def test_by_ministry_counts_apply_their_filters_in_place():
    """Each linked count applies its Ministry and request to the table in
    place and sets the filter bar (#1133); a count without a link (zero, or
    a Ministry the reader may not follow up) is plain text."""
    link = "/admin/reports/ministries/?ministry=9&amp;action=join&amp;state=any"
    rows = [
        counts(9, "Choir", 3, 0, 2, {"joining": link.replace("&amp;", "&")}),
        counts(4, "Bread", 1, 1, 1),
    ]
    html = render_report([], summaries=rows)
    start = html.index('<section id="by-ministry" data-table-sync')
    section = html[start : html.index("</section>", start)]
    anchor = section[section.index(f'<a href="{link}#table"') :]
    anchor = anchor[: anchor.index("</a>")]
    for attribute in (
        'data-in-place="count-9-joining"',
        "data-in-place-filters",
        'aria-label="3 join requests for Choir"',
        'data-in-place-message="Join requests for Choir shown."',
    ):
        assert attribute in anchor, attribute
    assert section.count("<a ") == 1
    assert '<td class="numeric">2</td>' in section  # Choir's unresolved.


def test_one_ministry_reads_as_one_line():
    """A reader with one Ministry gets one line, not a table (#1133)."""
    html = render_report([], summaries=[counts(9, "Choir", 1, 2, 3)], show_marks=False)
    start = html.index('<section id="by-ministry"')
    section = html[start : html.index("</section>", start)]
    assert "<table" not in section
    assert (
        '<p class="by-ministry-line">Choir: 1 join request · 2 leave requests'
        " · 3 unresolved"
    ) in " ".join(section.split())

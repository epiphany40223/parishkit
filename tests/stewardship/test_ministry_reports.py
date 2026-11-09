"""Private report filters and capability scope without database startup."""

import re
from uuid import uuid4

import pytest
from django.http import QueryDict

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.audit.schemas import ContextKind, sanitize
from parishkit.stewardship.reports.ministries import MinistryQuery, can_report

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


def test_the_worklist_has_no_totals_or_chart_above_its_table():
    """The Join and Leave requests tiles and the Follow-up progress chart are
    gone (#1676, folded into #1684): a review worklist has nothing between
    its filters and its table. By Ministry stays above the filters, so a
    count applied in place never moves under the pointer."""
    html = render_report([])
    assert 'id="ministry-totals"' not in html
    assert "ministry-followup-spec" not in html and "Follow-up progress" not in html
    assert "stat-tile" not in html
    filters = html.index('id="table-filters"')
    table = html.index("data-table-region")
    assert html.index('id="by-ministry"') < filters < table
    assert "<h2" not in html[filters:table]


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
    link = "/admin/reports/ministries/?ministry=9&amp;kind=join&amp;stage=all"
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
        " · 3 to review"
    ) in " ".join(section.split())


# One sentence of each help, to tell them apart (#2395).
LEADER_HELP = "the Ministries you lead. Stage chooses which ones show"
STAFF_HELP = "Live join and leave requests for the Ministries you may follow up"


def about(html):
    """The About this page panel's text, tags and spacing dropped."""
    start = html.index('<details class="about-page"')
    panel = html[start : html.index("</details>", start)]
    return " ".join(re.sub(r"<[^>]+>", " ", panel).split())


def test_a_leader_gets_a_short_help_and_no_by_ministry():
    """A Ministry leader's page (#2395): a short About this page, at most a
    quarter of Staff's, and no By Ministry disclosure or its script. Staff
    keep the full help and By Ministry."""
    rows = [counts(9, "Choir", 1, 2, 3)]
    leader = render_report(
        [],
        summaries=rows,
        roles=frozenset({"ministry_leader"}),
        show_marks=False,
        campaign_marks=False,
    )
    staff = render_report([], summaries=rows)
    short, full = about(leader), about(staff)
    assert LEADER_HELP in short and STAFF_HELP not in short
    for words in ("Accept:", "Edit:", "Ignore:", "Review one at a time"):
        assert words in short, words
    assert len(short.split()) * 4 <= len(full.split())
    assert 'id="by-ministry"' not in leader and "by-ministry" not in leader
    assert "remember-open-v1.js" not in leader
    # The worklist itself is unchanged.
    assert 'id="table-filters"' in leader and "data-table-region" in leader
    assert STAFF_HELP in full and LEADER_HELP not in full
    assert 'id="by-ministry"' in staff and "remember-open-v1.js" in staff


def test_a_leaders_page_skips_the_by_ministry_read(monkeypatch):
    """The view reads no By Ministry counts for a Ministry leader (#2395).
    Staff still read them, with their count links."""
    from parishkit.stewardship.reports import ministry_followup_views as views

    reads = []

    def summaries(campaign_id, query, principal, **options):
        """The Ministry report's summary, recorded."""
        reads.append(principal)
        return {"summaries": [counts(9, "Choir", 1)]}

    monkeypatch.setattr(views, "ministry_page", summaries)
    leader = Principal(uuid4(), frozenset({"ministry_leader"}), frozenset({4, 9}))
    overview = views._overview(uuid4(), leader, None, None)
    assert reads == [] and overview["by_ministry"] == []
    assert "packet_choices" not in overview
    staff = Principal(uuid4(), frozenset({"staff"}))
    overview = views._overview(uuid4(), staff, None, None)
    assert reads == [staff]
    assert [row["name"] for row in overview["by_ministry"]] == ["Choir"]
    assert overview["by_ministry"][0]["count_links"]

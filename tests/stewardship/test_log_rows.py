"""Database-free log filter grammar, display whitelist and time-ordered merge."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from django.utils.datastructures import MultiValueDict

from parishkit.stewardship.audit.log_rows import (
    DETAIL_FIELDS,
    EVENTS,
    LEVELS,
    LogQuery,
    NothingShown,
    audit_row,
    log_table,
    merge,
    operational_row,
)
from parishkit.stewardship.web.dates import UnknownZone

NOW = datetime(2026, 9, 20, 12, 0, 0, 123456, tzinfo=UTC)
IDENTIFIER = "1abcdef0-0000-4000-8000-000000000000"


def test_debug_is_excluded_until_chosen_and_a_form_shows_what_it_ticks():
    """The first visit shows every level but Debug, plus audit records; a
    submitted form shows exactly its ticks and must tick at least one (#601)."""
    first = LogQuery.parse({})
    assert first.levels == LEVELS[1:] and first.audits
    assert "DEBUG" not in LogQuery().levels
    chosen = LogQuery.parse({"applied": "yes", "debug": "yes", "error": "yes"})
    assert chosen.levels == ("DEBUG", "ERROR") and not chosen.audits
    audit_only = LogQuery.parse({"applied": "yes", "audit": "yes"})
    assert audit_only.levels == () and audit_only.audits
    # Refused with its own exception, so the view can say what to do.
    with pytest.raises(NothingShown):
        LogQuery.parse({"applied": "yes"})
    # Paging keeps the applied filters; the filter fields never carry the
    # snapshot, page, size or sort, so applying filters starts afresh.
    paged = LogQuery.parse(
        {
            "applied": "yes",
            "error": "yes",
            "through": "2026-09-20T12:00:00.123456+00:00",
            "page": "3",
            "size": "25",
            "sort": "oldest",
        }
    )
    assert paged.snapshot == NOW and (paged.page_number, paged.page_size) == (3, 25)
    assert paged.oldest and paged.order == "oldest"
    assert paged.form_values() == {"applied": "yes", "error": "yes"}
    fresh = LogQuery()
    assert fresh.snapshot is None and (fresh.page_number, fresh.page_size) == (1, 50)
    assert fresh.order == "newest" and not fresh.oldest


def test_query_accepts_only_the_closed_bounded_grammar():
    """Identifiers and types are exact reviewed values, never free text."""
    query = LogQuery.parse(
        MultiValueDict(
            {
                "applied": ["yes"],
                "audit": ["yes"],
                "event": ["dashboard_viewed"],
                "actor": [IDENTIFIER],
                "correlation": [IDENTIFIER],
                "campaign": [IDENTIFIER],
                "start": ["2026-09-01"],
                "end": ["2026-09-01"],
                "zone": ["America/New_York"],
            }
        )
    )
    assert (query.audits, query.levels, query.event, query.actor) == (
        True,
        (),
        "dashboard_viewed",
        IDENTIFIER,
    )
    assert "dashboard_viewed" in EVENTS and "task_failed" in EVENTS
    assert list(EVENTS) == sorted(set(EVENTS))
    # The suggestions are not the whole vocabulary: owners and SQL triggers
    # write types directly, and those must stay searchable by exact identifier.
    assert "admin_login" not in EVENTS
    assert LogQuery.parse({"event": "admin_login"}).event == "admin_login"
    assert LogQuery.parse({"event": "a" * 64}).event == "a" * 64
    for values in (
        {"source": "everything"},
        {"audit": "on", "applied": "yes"},
        {"audit": "yes"},
        # The retired Source and its replacement are never sent together.
        {"source": "audit", "audit": "yes", "applied": "yes"},
        {"applied": "yes", "source": "operational"},
        {"event": "drop table"},
        {"event": "Dashboard_Viewed"},
        {"event": "admin%"},
        {"event": "_login"},
        {"event": "a" * 65},
        {"actor": "not-a-uuid"},
        {"actor": IDENTIFIER.upper()},
        {"correlation": IDENTIFIER[:-1]},
        {"campaign": "{" + IDENTIFIER + "}"},
        {"start": "2026-02-30"},
        {"start": "20260201"},
        {"start": "2026-09-02", "end": "2026-09-01"},
        # The exclusive upper bound of the last representable day would overflow.
        {"end": "9999-12-31"},
        {"end": "3000-01-01"},
        {"start": "2019-12-31"},
        {"applied": "no"},
        {"debug": "on", "applied": "yes"},
        # A tick without the submitted-form marker is not a real form.
        {"debug": "yes"},
        # The retired keyset cursor is refused like any unknown field.
        {"before": "2026-09-20T12:00:00.123456+00:00"},
        {"through": "2026-09-20T12:00:00+00:00"},
        {"through": "2026-09-20T12:00:00.123456-04:00"},
        {"through": "2026-13-20T12:00:00.123456+00:00"},
        {"page": "-1"},
        {"page": "1" * 10},
        {"page": "٣"},
        {"size": "all"},
        {"size": "7"},
        {"sort": "created_at"},
        {"sort": "time"},
        # Search text is short and printable, and never an address (#536).
        {"text": "admin@example.org"},
        {"text": "a" * 65},
        {"text": "tab\there"},
        {"ministry": "0"},
        {"ministry": "042"},
        {"ministry": "2147483648"},
        {"ministry": "-1"},
        {"ministry": "4 2"},
        {"ministry": "٣"},
        {"subject": "not-a-uuid"},
        {"subject": IDENTIFIER.upper()},
        {"q": "anything"},
        {"source": 5},
    ):
        with pytest.raises(ValueError):
            LogQuery.parse(values)
    with pytest.raises(ValueError):
        LogQuery.parse(MultiValueDict({"source": ["audit", "both"]}))


@pytest.mark.parametrize(
    ("values", "levels", "audits"),
    [
        # The old "Same campaign" action and an old form's "Audit only".
        ({"source": "audit", "campaign": IDENTIFIER}, (), True),
        ({"applied": "yes", "info": "yes", "source": "audit"}, (), True),
        # The old critical-events banner: Critical, operational only.
        (
            {"applied": "yes", "critical": "yes", "source": "operational"},
            ("CRITICAL",),
            False,
        ),
        ({"source": "operational"}, LEVELS[1:], False),
        ({"source": "both"}, LEVELS[1:], True),
        ({"applied": "yes", "debug": "yes", "source": "both"}, ("DEBUG",), True),
    ],
)
def test_a_retired_source_choice_maps_onto_the_checkboxes(values, levels, audits):
    """An older tab's Source (#601) becomes the matching ticks, kept for one
    release; paging and the export then carry the ticks, never ``source``."""
    query = LogQuery.parse(values)
    assert (query.levels, query.audits, query.source) == (levels, audits, "")
    carried = query.form_values()
    assert "source" not in carried and carried["applied"] == "yes"
    assert ("audit" in carried) is audits


def test_dates_are_days_in_the_browser_zone():
    """From and Through are local days; the zone travels with the filters.

    From starts at local midnight and Through ends where the next local day
    starts (#558), so an evening in New York that is already the next UTC day
    still belongs to its local day, and a fall-back day lasts 25 hours.
    """
    query = LogQuery.parse(
        {"start": "2026-11-01", "end": "2026-11-01", "zone": "America/New_York"}
    )
    assert query.bounds == (
        datetime(2026, 11, 1, 4, tzinfo=UTC),
        datetime(2026, 11, 2, 5, tzinfo=UTC),
    )
    # Either day alone bounds one side only.
    east = LogQuery.parse({"end": "2026-10-04", "zone": "Asia/Tokyo"})
    assert east.bounds == (None, datetime(2026, 10, 4, 15, tzinfo=UTC))
    assert LogQuery().bounds == (None, None)
    # Paging, sorting and the export carry the zone with the other filters.
    assert query.form_values()["zone"] == "America/New_York"
    table = log_table(query, [], through=NOW, action="/admin/system/logs/")
    assert ("zone", "America/New_York") in table.carried


@pytest.mark.parametrize("zone", [None, "", "Etc/Unknown", "Mars/Olympus", "UTC "])
def test_dates_without_a_known_zone_are_refused(zone):
    """A date without the browser's zone is never read as UTC: it is a zone
    refusal the view words on its own (a tab opened before #558 sends none)."""
    values = {"start": "2026-10-04"} | ({} if zone is None else {"zone": zone})
    with pytest.raises(UnknownZone):
        LogQuery.parse(values)
    with pytest.raises(UnknownZone):
        LogQuery.parse({"end": "2026-10-04", "zone": zone or ""})


def test_a_zone_without_dates_is_harmless():
    """Filters without dates need no zone: a known one is kept for the next
    Apply, and one the catalog does not know is dropped, not refused."""
    assert LogQuery.parse({"zone": "Asia/Kathmandu"}).zone == "Asia/Kathmandu"
    unknown = LogQuery.parse({"applied": "yes", "error": "yes", "zone": "Mars/Base"})
    assert unknown.zone == "" and "zone" not in unknown.form_values()
    assert LogQuery.parse({"zone": ""}).zone == ""


def operational(moment, *, level="INFO", context=None, identifier=None):
    """One stored diagnostic record as the view reads it."""
    return operational_row(
        dict(
            id=identifier or uuid4(),
            created_at=moment,
            level=level,
            event="task_failed",
            actor_id=None,
            correlation_id=uuid4(),
            context={} if context is None else context,
        )
    )


def audit(moment, *, context=None, identifier=None):
    """One stored audit record; a login event has no context row at all."""
    return audit_row(
        {
            "id": identifier or uuid4(),
            "created_at": moment,
            "event_type": "dashboard_viewed",
            "actor_id": uuid4(),
            "correlation_id": uuid4(),
            "campaign_reference": uuid4(),
            "subject_id": None,
            "auditcontext__context": context,
        }
    )


def test_rows_show_only_reviewed_fields_with_short_scalar_values():
    """A key that merely looks safe is not enough, whatever a future schema stores."""
    row = operational(
        NOW,
        level="CRITICAL",
        context={
            "outcome": "failed",
            "count": 3,
            "retryable": True,
            "reason": None,
            "ministry_duids": [3, 9],
            # Identifier-shaped, flat and textual, but in no reviewed schema.
            "note": "person@example.org",
            "family_name": "person@example.org",
            # A reviewed field holding something it never should.
            "status": "x" * 129,
            "field": ["person@example.org"],
            "kind": {"email": "person@example.org"},
            "Bad Key": "person@example.org",
            7: "person@example.org",
        },
    )
    assert row["details"] == [
        ("count", "3"),
        ("ministry_duids", "3, 9"),
        ("outcome", "failed"),
        ("reason", ""),
        ("retryable", "True"),
    ]
    assert "example.org" not in str(row["details"]) and "xxx" not in str(row)
    assert "note" not in DETAIL_FIELDS and "outcome" in DETAIL_FIELDS
    # Severity is a word and a symbol, never color alone.
    assert (row["level_symbol"], str(row["level_label"])) == ("‼", "Critical")
    assert row["icon"] == "critical"
    assert operational(NOW, context="text")["details"] == []
    record = audit(NOW)
    # Audit records have no severity, and a bare login event has no context.
    assert record["level"] is None and str(record["level_label"]) == "Audit record"
    # They have an icon of their own instead (#601).
    assert record["icon"] == "audit"
    assert record["details"] == [] and record["campaign_id"] is not None


def test_merge_orders_the_union_by_time_then_identifier_in_both_directions():
    """Each source's first rows merge into exactly the union's first rows."""
    low, high = UUID(int=1), UUID(int=2)
    tied = [
        operational(NOW, identifier=low),
        audit(NOW, identifier=high),
    ]
    older = [operational(NOW - timedelta(seconds=n)) for n in (1, 3)]
    oldest = [audit(NOW - timedelta(seconds=n)) for n in (2, 4)]
    page = merge([tied[0], *older], [tied[1], *oldest])[:4]
    # The same instant is ordered by identifier, exactly as each query orders it.
    assert [row["id"] for row in page[:2]] == [high, low]
    assert [str(row["source"]) for row in page] == [
        "Audit",
        "Operational",
        "Operational",
        "Audit",
    ]
    ascending = merge([*reversed(older), tied[0]], [*reversed(oldest)], oldest=True)
    assert [row["created_at"] for row in ascending] == sorted(
        row["created_at"] for row in ascending
    )
    assert merge([], []) == []


def test_log_table_carries_filters_and_snapshot_but_no_url():
    """Every navigator and heading control is a POST form carrying the
    filters and the snapshot; the Time column is the only sort."""
    query = LogQuery.parse(
        {"applied": "yes", "error": "yes", "actor": IDENTIFIER, "size": "25"}
    )
    rows = [operational(NOW)] * 25
    table = log_table(
        query, rows, through=NOW, action="/admin/system/logs/", number=2, total=60
    )
    assert (table.pages, table.method, table.action) == (
        3,
        "post",
        "/admin/system/logs/",
    )
    fields = dict(table.next_fields)
    assert fields["actor"] == IDENTIFIER and fields["page"] == "3"
    assert fields["through"] == "2026-09-20T12:00:00.123456+00:00"
    assert fields["sort"] == "newest" and fields["size"] == "25"
    assert table.aria_sort("time") == "descending"
    assert table.sort_target("time") == "oldest"
    capped = log_table(
        query,
        rows,
        through=NOW,
        action="/admin/system/logs/",
        total=10_000,
        capped=True,
    )
    assert capped.page_label == (1, None) and capped.next_fields


def test_every_row_explains_its_type_in_plain_words():
    """Known types have a sentence; an unknown type still reads as words."""
    from parishkit.stewardship.audit.log_descriptions import describe

    assert str(describe("task_claim")) == "A background worker started a task."
    assert describe("some_new_event") == "Some new event."
    row = operational_row(
        {
            "id": uuid4(),
            "created_at": NOW,
            "level": "CRITICAL",
            "event": "source_refresh_invalid",
            "actor_id": None,
            "correlation_id": uuid4(),
            "context": {},
        }
    )
    assert "previous data was kept" in str(row["description"])
    row = audit_row(
        {
            "id": uuid4(),
            "created_at": NOW,
            "event_type": "admin_login",
            "actor_id": None,
            "correlation_id": uuid4(),
            "campaign_reference": None,
            "subject_id": None,
            "auditcontext__context": None,
        }
    )
    description = str(row["description"])
    # Profile-neutral: LOCAL signs in without Google (#649).
    assert description.endswith("Ministry leader) signed in.")
    assert "Google" not in description

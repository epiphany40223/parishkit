"""Database-free log filter grammar, display whitelist and keyset merge."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from django.utils.datastructures import MultiValueDict

from parishkit.stewardship.audit.log_rows import (
    DETAIL_FIELDS,
    EVENTS,
    LEVELS,
    LogQuery,
    audit_row,
    merge,
    operational_row,
)

NOW = datetime(2026, 9, 20, 12, 0, 0, 123456, tzinfo=UTC)
IDENTIFIER = "1abcdef0-0000-4000-8000-000000000000"


def test_debug_is_excluded_until_chosen_and_an_empty_choice_means_none():
    """The first visit's default differs from a submitted form with no tick."""
    assert LogQuery.parse({}).levels == LEVELS[1:]
    assert "DEBUG" not in LogQuery().levels
    chosen = LogQuery.parse({"applied": "yes", "debug": "yes", "error": "yes"})
    assert chosen.levels == ("DEBUG", "ERROR")
    assert LogQuery.parse({"applied": "yes"}).levels == ()
    # Paging keeps the applied filters and never carries a stale cursor forward.
    paged = LogQuery.parse(
        {
            "applied": "yes",
            "error": "yes",
            "before": "2026-09-20T12:00:00.123456+00:00",
            "before_id": IDENTIFIER,
        }
    )
    assert paged.cursor == (NOW, UUID(IDENTIFIER))
    assert paged.form_values() == {"applied": "yes", "error": "yes", "source": "both"}
    assert LogQuery().cursor is None


def test_query_accepts_only_the_closed_bounded_grammar():
    """Identifiers and types are exact reviewed values, never free text."""
    query = LogQuery.parse(
        MultiValueDict(
            {
                "source": ["audit"],
                "event": ["dashboard_viewed"],
                "actor": [IDENTIFIER],
                "correlation": [IDENTIFIER],
                "campaign": [IDENTIFIER],
                "start": ["2026-09-01"],
                "end": ["2026-09-01"],
            }
        )
    )
    assert (query.source, query.event, query.actor) == (
        "audit",
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
        {"before": "2026-09-20T12:00:00.123456+00:00"},
        {"before_id": IDENTIFIER},
        {"before": "2026-09-20T12:00:00+00:00", "before_id": IDENTIFIER},
        {"before": "2026-09-20T12:00:00.123456-04:00", "before_id": IDENTIFIER},
        {"before": "2026-13-20T12:00:00.123456+00:00", "before_id": IDENTIFIER},
        {"text": "anything"},
        {"source": 5},
    ):
        with pytest.raises(ValueError):
            LogQuery.parse(values)
    with pytest.raises(ValueError):
        LogQuery.parse(MultiValueDict({"source": ["audit", "both"]}))


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
    assert operational(NOW, context="text")["details"] == []
    record = audit(NOW)
    # Audit records have no severity, and a bare login event has no context.
    assert record["level"] is None and str(record["level_label"]) == "Audit record"
    assert record["details"] == [] and record["campaign_id"] is not None


def test_merge_is_newest_first_across_sources_with_a_stable_cursor():
    """A keyset cursor cannot skip or repeat entries while the log grows."""
    low, high = UUID(int=1), UUID(int=2)
    tied = [
        operational(NOW, identifier=low),
        audit(NOW, identifier=high),
    ]
    older = [operational(NOW - timedelta(seconds=n)) for n in (1, 3)]
    oldest = [audit(NOW - timedelta(seconds=n)) for n in (2, 4)]
    page, following = merge([tied[0], *older], [tied[1], *oldest], size=4)
    # The same instant is ordered by identifier, exactly as each query orders it.
    assert [row["id"] for row in page[:2]] == [high, low]
    assert [row["created_at"] for row in page] == sorted(
        (row["created_at"] for row in page), reverse=True
    )
    assert [str(row["source"]) for row in page] == [
        "Audit",
        "Operational",
        "Operational",
        "Audit",
    ]
    last = page[-1]
    assert following == {
        "before": last["created_at"].isoformat(timespec="microseconds"),
        "before_id": str(last["id"]),
    }
    # The cursor round-trips through the closed grammar to the same instant.
    assert LogQuery.parse(following).cursor == (last["created_at"], last["id"])
    assert merge(older, [], size=4) == (older, None)
    assert merge([], [], size=4) == ([], None)
    # Microseconds are always written, even when they are zero.
    whole = operational(NOW.replace(microsecond=0))
    _, cursor = merge([whole, *older], [], size=1)
    assert cursor["before"] == "2026-09-20T12:00:00.000000+00:00"

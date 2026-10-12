"""The count-only Ministry leader change audit (#1034), without a database.

The PostgreSQL behavior (promotion, activation, the logins' grants) is in
database/test_leader_changes_postgresql.py.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.stewardship.audit.log_descriptions import DESCRIPTIONS, describe
from parishkit.stewardship.audit.log_rows import ACTIVITY_TYPES
from parishkit.stewardship.audit.schemas import Action, ContextKind, sanitize
from parishkit.stewardship.campaigns import leader_changes


def test_nothing_is_recorded_without_the_pointers():
    """A login without the recorder grant (``None``) never reaches SQL."""
    assert leader_changes.record_leader_changes(None) is None


def test_the_context_sql_writes_is_counts_and_closed_identifiers_only():
    """The recorder's context shape passes the audit context schema, which
    SQL's stewardship_safe_context_v1 mirrors, and no address does."""
    context = {"count": 0, "matching_count": 3, "snapshot_id": uuid4()}
    safe = sanitize(ContextKind.ACTION, context)
    assert safe["count"] == 0 and safe["matching_count"] == 3
    with pytest.raises(ValueError):
        sanitize(ContextKind.ACTION, {"email": "a@example.org"})


def test_the_event_is_described_and_grouped_with_refreshes():
    """System logs explain it and list it under ParishSoft refreshes only."""
    event = Action.MINISTRY_LEADERS_CHANGED.value
    assert event == "ministry_leaders_changed"
    assert describe(event) == DESCRIPTIONS[event]
    assert "Count" in DESCRIPTIONS[event] and "Matching count" in DESCRIPTIONS[event]
    groups = [key for key, types in ACTIVITY_TYPES.items() if event in types]
    assert groups == ["parishsoft"]


class Timeout(Exception):
    """A statement stopped by its statement_timeout, as psycopg reports it."""

    sqlstate = "57014"
    diag = SimpleNamespace(
        message_primary="canceling statement due to statement timeout"
    )


def test_a_failure_is_logged_but_a_time_limit_propagates(monkeypatch):
    """Best-effort: the owner goes on, unless its own time limit stopped it."""
    from parishkit.stewardship import observability

    logged = []
    monkeypatch.setattr(
        observability, "emit_failure", lambda error, **kw: logged.append(kw)
    )
    leader_changes._absorb(RuntimeError("permission denied"))
    assert logged == [{"level": 30}]
    with pytest.raises(Timeout):
        leader_changes._absorb(Timeout())
    assert len(logged) == 1

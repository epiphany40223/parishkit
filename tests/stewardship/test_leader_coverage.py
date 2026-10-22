"""Ministry requests' Leaders column statuses (#1594), without a database.

``leader_status`` turns the coverage definer's counts for one Ministry into
the column's state, word and command-line form; ``read_coverage`` maps the
definer's refusal to ``CoverageRefused`` and keys its answer by Ministry.
"""

import json
from types import SimpleNamespace

import pytest
from django.db import DatabaseError

from parishkit.stewardship.reports import leader_coverage
from parishkit.stewardship.reports.leader_coverage import (
    REFUSAL,
    CoverageRefused,
    leader_status,
)


@pytest.mark.parametrize(
    "counts, state, word, working",
    [
        (None, "no_leader", "No leader", False),
        ({}, "no_leader", "No leader", False),
        ({"leaders": 2, "users": 0, "signed_in": 0}, "no_user", "No user yet", False),
        (
            {"leaders": 2, "users": 1, "signed_in": 0},
            "not_signed_in",
            "Not signed in yet",
            False,
        ),
        ({"leaders": 3, "users": 2, "signed_in": 1}, "signed_in", "1 signed in", True),
        (
            {"leaders": 1500, "users": 1200, "signed_in": 1100},
            "signed_in",
            "1,100 signed in",
            True,
        ),
    ],
)
def test_each_count_gives_its_state(counts, state, word, working):
    """The states in the order a Ministry gains them, and their words."""
    status = leader_status(counts)
    assert (status.state, str(status.word), status.working) == (state, word, working)
    assert status.as_json() == {
        "state": state,
        "signed_in": (counts or {}).get("signed_in", 0),
    }


class _Cursor:
    """A cursor that answers one value, or raises one database error."""

    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.executed = answer, error, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        """Record the call, then raise the configured error if any."""
        self.executed.append((sql, params))
        if self.error is not None:
            raise self.error

    def fetchone(self):
        return (self.answer,)


def _patched(monkeypatch, cursor):
    """Route read_coverage's cursor and savepoint to the fake."""
    from contextlib import nullcontext

    monkeypatch.setattr(leader_coverage.connection, "cursor", lambda: cursor)
    monkeypatch.setattr(leader_coverage.transaction, "atomic", nullcontext)


class _Cause(Exception):
    """A driver error's SQLSTATE and primary message, as psycopg exposes them."""

    def __init__(self, message):
        super().__init__(message)
        self.sqlstate = "42501"
        self.diag = SimpleNamespace(message_primary=message)


def _refusal(message):
    """A DatabaseError whose cause carries SQLSTATE 42501 and ``message``."""
    error = DatabaseError("refused")
    error.__cause__ = _Cause(message)
    return error


def test_the_answer_is_keyed_by_ministry(monkeypatch):
    """Rows come back keyed by DUID; a malformed row is left out."""
    rows = [
        {"ministry": 4, "leaders": 1, "users": 0, "signed_in": 0},
        {"ministry": 9, "leaders": 2, "users": 2, "signed_in": 1},
        {"leaders": 5},
        "junk",
    ]
    cursor = _Cursor(answer=json.dumps({"ministries": rows}))
    _patched(monkeypatch, cursor)
    assert leader_coverage.read_coverage("key") == {4: rows[0], 9: rows[1]}
    assert cursor.executed[0][1][0] == "key"


def test_only_the_definers_own_refusal_is_a_refusal(monkeypatch):
    """The definer's words are CoverageRefused; another 42501 (a login
    without EXECUTE) propagates as a database error."""
    _patched(monkeypatch, _Cursor(error=_refusal(REFUSAL)))
    with pytest.raises(CoverageRefused):
        leader_coverage.read_coverage("key")
    _patched(monkeypatch, _Cursor(error=_refusal("permission denied for function")))
    with pytest.raises(DatabaseError):
        leader_coverage.read_coverage("key")

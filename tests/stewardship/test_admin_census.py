"""``census resolve`` and ``census review``: documents, options, vocabulary.

Both are Census changes row writes (#463, #528). The commands' behavior
against a database is in
``database/test_admin_census_resolve_cli_postgresql.py`` and
``database/test_admin_census_review_cli_postgresql.py``.
"""

import io
import json
from argparse import Namespace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from django.db import DatabaseError, IntegrityError

from parishkit.stewardship import admin_census, admin_changes, admin_cli
from parishkit.stewardship.accounts.policy import Capability
from parishkit.stewardship.responses.models import RESOLUTION_ACTIONS, REVIEW_DECISIONS

from .test_admin_reads import FORBIDDEN, members

PROPOSAL = UUID("00000000-0000-4000-8000-000000000001")
RESOLUTION = UUID("00000000-0000-4000-8000-000000000002")
KEY = UUID("00000000-0000-4000-8000-000000000003")
CREATED = datetime(2026, 10, 12, 18, 30, tzinfo=UTC)


def test_the_actions_are_the_services():
    """The parser's choices, spelled out before Django, are the service's."""
    assert admin_cli.CENSUS_ACTIONS == RESOLUTION_ACTIONS
    assert admin_cli.CENSUS_DECISIONS == REVIEW_DECISIONS


def test_the_review_document_names_ids_versions_and_states_only():
    """Golden document: no note, Family, Member or value, nor a guarded word."""
    model = admin_census.CensusReview(
        created=True,
        request_key=KEY,
        review={
            "id": RESOLUTION,
            "proposal_id": PROPOSAL,
            "decision": "approved",
            "expected_version": 3,
            "created_at": CREATED,
        },
        proposal={
            "id": PROPOSAL,
            "version": 4,
            "decision": "approved",
            "execution": "pending",
        },
    )
    document = model.to_document()
    assert document == {
        "created": True,
        "request_key": str(KEY),
        "review": {
            "id": str(RESOLUTION),
            "proposal_id": str(PROPOSAL),
            "decision": "approved",
            "expected_version": 3,
            "created_at": "2026-10-12T18:30:00+00:00",
        },
        "proposal": {
            "id": str(PROPOSAL),
            "version": 4,
            "decision": "approved",
            "execution": "pending",
        },
    }
    for name in members(document):
        assert FORBIDDEN.search(name) is None, name
    assert "note" not in set(members(document))


def test_review_checks_the_administrator_s_role_first(monkeypatch):
    """``census review`` is an Administrator's (``PUBLISH_CENSUS``): its
    first step is that read-only check (#1341), before it reads the note,
    makes a key or looks anything up."""
    calls = []

    def denied(caller, service, *, read_only=False, capability=None):
        """Refuse as the admission does for a user who lacks the capability."""
        calls.append((read_only, capability))
        raise PermissionError("denied")

    def unexpected(*arguments, **options):
        """Nothing after the role check may run."""
        raise AssertionError("read or looked up before the role check")

    monkeypatch.setattr(admin_changes, "_admit_change", denied)
    monkeypatch.setattr(admin_cli, "_input", unexpected)
    monkeypatch.setattr(admin_cli, "_request_key", unexpected)
    with pytest.raises(PermissionError):
        admin_cli.census_review(Namespace(), None, None, {"caller": None})
    assert calls == [(True, Capability.PUBLISH_CENSUS)]


def test_the_document_names_ids_versions_and_states_only():
    """Golden document: no note, Family, Member or value, nor a guarded word."""
    model = admin_census.CensusResolve(
        created=True,
        request_key=KEY,
        resolution={
            "id": RESOLUTION,
            "proposal_id": PROPOSAL,
            "action": "entered",
            "expected_version": 3,
            "created_at": CREATED,
        },
        proposal={
            "id": PROPOSAL,
            "version": 4,
            "decision": "unreviewed",
            "execution": "resolved_external",
        },
    )
    document = model.to_document()
    assert document == {
        "created": True,
        "request_key": str(KEY),
        "resolution": {
            "id": str(RESOLUTION),
            "proposal_id": str(PROPOSAL),
            "action": "entered",
            "expected_version": 3,
            "created_at": "2026-10-12T18:30:00+00:00",
        },
        "proposal": {
            "id": str(PROPOSAL),
            "version": 4,
            "decision": "unreviewed",
            "execution": "resolved_external",
        },
    }
    for name in members(document):
        assert FORBIDDEN.search(name) is None, name
    assert "note" not in set(members(document))


@pytest.mark.parametrize(
    "argv",
    [
        # Each required piece missing.
        ["census", "resolve", "--action", "entered", "--expected-version", "1"],
        ["census", "resolve", str(PROPOSAL), "--expected-version", "1"],
        ["census", "resolve", str(PROPOSAL), "--action", "entered"],
        # Outside the page's vocabulary.
        [
            *("census", "resolve", str(PROPOSAL)),
            *("--action", "published", "--expected-version", "1"),
        ],
        [
            *("census", "resolve", "not-a-uuid"),
            *("--action", "entered", "--expected-version", "1"),
        ],
        [
            *("census", "resolve", str(PROPOSAL)),
            *("--action", "entered", "--expected-version", "0"),
        ],
        [
            *("census", "resolve", str(PROPOSAL)),
            *("--action", "ignored", "--expected-version", "1"),
            *("--request-key", "nine"),
        ],
        # Standard input cannot carry both the note and the answer.
        [
            *("census", "resolve", str(PROPOSAL)),
            *("--action", "entered", "--expected-version", "1", "--note", "-"),
        ],
        # census review: each required piece missing, and its vocabulary.
        ["census", "review", "--decision", "approved", "--expected-version", "1"],
        ["census", "review", str(PROPOSAL), "--expected-version", "1"],
        ["census", "review", str(PROPOSAL), "--decision", "approved"],
        [
            *("census", "review", str(PROPOSAL)),
            *("--decision", "approve", "--expected-version", "1"),
        ],
        [
            *("census", "review", str(PROPOSAL)),
            *("--decision", "entered", "--expected-version", "1"),
        ],
        [
            *("census", "review", str(PROPOSAL)),
            *("--decision", "approved", "--expected-version", "0"),
        ],
    ],
)
def test_options_outside_the_pages_vocabulary_are_usage_errors(argv):
    """A missing or malformed option is a usage error before admission."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2
    assert json.loads(out.getvalue())["error"]["code"] == "usage"


class Driver(Exception):
    """The driver error a database error carries, with its SQLSTATE."""

    def __init__(self, sqlstate):
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


def raised(error, sqlstate):
    """``error`` as Django raises it: chained from the driver's error."""
    try:
        try:
            raise Driver(sqlstate)
        except Driver as cause:
            raise error from cause
    except DatabaseError as chained:
        return chained


def test_only_a_unique_violation_is_the_racing_write():
    """A unique violation (23505) is the page's racing second write, so
    stale; any other refusal, or a unique violation not raised as an
    integrity error, is the database error it is."""
    assert admin_census.racing_write(raised(IntegrityError("duplicate"), "23505"))
    for error in (
        raised(IntegrityError("guard"), "23514"),
        raised(IntegrityError("guard"), "P0001"),
        raised(DatabaseError("other"), "23505"),
        IntegrityError("no cause"),
    ):
        assert not admin_census.racing_write(error), error

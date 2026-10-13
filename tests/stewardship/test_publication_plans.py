"""The pre-PUT check and input validation of publication plans (#528).

``matches_confirmed`` decides whether a fresh plan, made right before a PUT,
is exactly the one the Administrator confirmed; it is pure, so these tests
need no database. The PostgreSQL tests cover the stored rows and guards.
"""

from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.stewardship.responses.publication import Outcome, plan_entity
from parishkit.stewardship.responses.publication_models import (
    PublicationConfirmation,
    PublicationEntity,
    PublicationField,
    PublicationOutcome,
    PublicationPlan,
)
from parishkit.stewardship.responses.publication_plans import (
    confirm_plan,
    matches_confirmed,
    record_outcome,
    record_plan,
)

from .test_admin_reads import FORBIDDEN
from .test_response_publication import payload, proposal


def confirmed():
    """A planned first-name write and the rows a confirmed plan stored for it."""
    rows = [
        proposal("first_name", "Patricia", "Pat", version=3),
        proposal("prefix", "Dr.", "", handling="manual", version=1),
    ]
    fresh = plan_entity("member", 7, rows, payload(firstName="Pat"))
    entity = SimpleNamespace(
        entity_kind="member",
        entity_key="7",
        read_sha256=fresh.current_digest,
        planned_sha256=fresh.planned_digest,
    )
    fields = [
        SimpleNamespace(proposal_id="first_name", proposal_version=3, outcome="write"),
        SimpleNamespace(proposal_id="prefix", proposal_version=1, outcome="by_hand"),
    ]
    return entity, fields, fresh, rows


def test_an_unchanged_fresh_plan_matches():
    """Same proposals, versions, outcomes and payloads: the PUT may go ahead."""
    assert matches_confirmed(*confirmed())


def test_any_change_since_confirmation_invalidates_the_entity():
    """A decision or edit (a new version), a changed ParishSoft read, a
    different outcome, a blocked entity or another entity never match."""
    entity, fields, fresh, rows = confirmed()
    edited = [replace_row(rows[0], version=4), rows[1]]
    assert not matches_confirmed(entity, fields, fresh, edited)
    moved = plan_entity("member", 7, rows, payload(firstName="Pat", fax="555"))
    assert not matches_confirmed(entity, fields, moved, rows)
    caught_up = plan_entity("member", 7, rows, payload(firstName="Patricia"))
    assert caught_up.fields[0].outcome is Outcome.ALREADY_IN_PARISHSOFT
    assert not matches_confirmed(entity, fields, caught_up, rows)
    conflict = plan_entity("member", 7, rows, payload(firstName="Pam"))
    assert conflict.blocked and not matches_confirmed(entity, fields, conflict, rows)
    other = SimpleNamespace(**{**vars(entity), "entity_key": "8"})
    assert not matches_confirmed(other, fields, fresh, rows)
    assert not matches_confirmed(entity, fields[:1], fresh, rows)
    assert not matches_confirmed(entity, fields, replace(fresh, payload=None), rows)


def replace_row(row, **values):
    """A copy of a proposal stand-in with ``values`` changed."""
    return SimpleNamespace(**{**vars(row), **values})


def test_services_refuse_malformed_input_before_any_query():
    """Identifiers must be UUIDs, counts plain integers, results and failure
    words from their closed sets, and outcomes need the publication phase."""
    key = uuid4()
    with pytest.raises(ValueError):
        record_plan(
            str(key),
            campaign_id=key,
            source_snapshot_id=key,
            organization_id=1,
            request_key=key,
            plans=[],
            rows=[],
        )
    with pytest.raises(ValueError):
        record_plan(
            key,
            campaign_id=key,
            source_snapshot_id=key,
            organization_id=True,
            request_key=key,
            plans=[],
            rows=[],
        )
    fresh = confirmed()[2]
    with pytest.raises(ValueError, match="no proposal row"):
        record_plan(
            key,
            campaign_id=key,
            source_snapshot_id=key,
            organization_id=1,
            request_key=key,
            plans=[fresh],
            rows=[],
        )
    for counts in ((0, 0, 0), (1, -1, 0), (1, True, 0)):
        with pytest.raises(ValueError):
            confirm_plan(
                None,
                key,
                key,
                session_id=key,
                authenticated_at=None,
                request_key=key,
                write_entities=counts[0],
                write_fields=counts[1],
                blocked_entities=counts[2],
            )
    claim = SimpleNamespace(phase="publication", fence=1)
    for result, failure in (
        ("written", ""),
        ("failed", ""),
        ("failed", "Provider said: secret"),
        ("published", "provider_unavailable"),
    ):
        with pytest.raises(ValueError):
            record_outcome(key, claim=claim, result=result, failure_kind=failure)
    with pytest.raises(ValueError):
        record_outcome(
            key, claim=SimpleNamespace(phase="full", fence=1), result="published"
        )


def test_no_column_name_suggests_personal_or_secret_data():
    """The admin read allowlists refuse these names (test_admin_reads); the
    tables hold identifiers, versions, counts and hashes only."""
    for model in (
        PublicationPlan,
        PublicationEntity,
        PublicationField,
        PublicationConfirmation,
        PublicationOutcome,
    ):
        for field in model._meta.concrete_fields:
            assert FORBIDDEN.search(field.column) is None, (model, field.column)

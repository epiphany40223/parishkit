"""Publication plans, confirmations and outcomes under the real roles (#528).

Migration 0087 (``schema/migrations/0087_publication_plans.sql``) adds the
append-only record of a ParishSoft publication (DAT-09.01). These tests run
the services on the restricted worker and web logins, the SQL guards as
installed, and the file's own DO block against a database without one of
its objects. Nothing here calls ParishSoft.

No path approves a census change yet (the ADM-09 review page comes later),
so ``approve`` sets a decision as the schema owner with triggers off.
"""

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses.models import (
    ProposedChange,
    PublicationEntity,
    PublicationField,
    PublicationOutcome,
    PublicationPlan,
)
from parishkit.stewardship.responses.publication import (
    EntityPlan,
    FieldPlan,
    Outcome,
)
from parishkit.stewardship.responses.publication_plans import (
    confirm_plan,
    confirmation_counts,
    record_outcome,
    record_plan,
)
from parishkit.stewardship.source.leases import acquire_source, release_source
from parishkit.stewardship.source.models import (
    SourceCurrent,
    SourceMutationLease,
    SourceSnapshot,
)

from .source_builders import running_source_task
from .test_background_grants_postgresql import task_login
from .test_census_resolution_postgresql import prepared
from .test_portal_identity_guards_postgresql import forge

pytestmark = pytest.mark.django_db(transaction=True)

FROZEN = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0087_publication_plans.sql"
)
READ = "a" * 64
PLANNED = "b" * 64


def owner(statement, params=()):
    """Run one statement as the schema owner with every trigger off."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica")
        cursor.execute(statement, params)


def approve(row):
    """Approve one census change (no service does yet) and return it fresh."""
    owner(
        "UPDATE stewardship_proposed_change SET decision='approved', "
        "version=version+1 WHERE id=%s",
        [row.pk],
    )
    row.refresh_from_db()
    return row


def writing(row, *, outcome=Outcome.WRITE, blocked=False):
    """One entity plan for ``row``'s field alone (hashes made up)."""
    hashes = (
        (READ, PLANNED) if outcome is Outcome.WRITE and not blocked else (None,) * 2
    )
    return EntityPlan(
        row.entity_kind,
        row.entity_key,
        (FieldPlan(row.pk, row.field, outcome),),
        blocked,
        {} if hashes[0] else None,
        *hashes,
    )


def plan(admin, rows, plans, *, key=None):
    """Record a plan through the service on the restricted worker login."""
    snapshot = SourceSnapshot.objects.get(pk=rows[0].current_source_id)
    campaign = rows[0].submission.campaign_id
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        return record_plan(
            admin,
            campaign_id=campaign,
            source_snapshot_id=snapshot.pk,
            organization_id=snapshot.organization_id,
            request_key=key or uuid4(),
            plans=plans,
            rows=rows,
        )


def confirm(harness, actor, record, session, *, key=None, **counts):
    """Confirm through the service on the restricted web login."""
    values = confirmation_counts(record.pk) | counts
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return confirm_plan(
            harness.service.store,
            actor,
            record.pk,
            session_id=session.pk,
            authenticated_at=session.authenticated_at,
            request_key=key or uuid4(),
            **values,
        )


def outcome(entity, claim, result, failure_kind=""):
    """Record one attempt through the service on the restricted worker login."""
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        return record_outcome(
            entity.pk, claim=claim, result=result, failure_kind=failure_kind
        )


def context(event_type):
    """Each audit context of one event type, oldest first."""
    return [
        AuditContext.objects.get(event=event).context
        for event in AuditEvent.objects.filter(event_type=event_type).order_by(
            "created_at"
        )
    ]


@pytest.fixture
def ready(response_service):
    """A live response with its automatic first-name change approved, the
    By-hand address change, an Administrator with a fresh session and Staff."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)
    harness, admin, staff, home, first = prepared(response_service)
    return harness, admin, staff, home, approve(first), forge(admin)


def test_plan_confirm_and_record_outcomes(ready):
    """A plan keeps each proposal's version and the hashes; the confirmation
    matches its counts; attempts run in order and stop at published; every
    audit holds a word and a count only."""
    harness, admin, _, home, first, session = ready
    key = uuid4()
    rows = [first, home]
    plans = [writing(first), writing(home, outcome=Outcome.BY_HAND)]
    record = plan(admin, rows, plans, key=key)
    assert plan(admin, rows, plans, key=key).pk == record.pk
    assert not record.testing and record.actor_id == admin
    entity = PublicationEntity.objects.get(plan=record, entity_kind="member")
    assert (entity.read_sha256, entity.planned_sha256) == (READ, PLANNED)
    assert list(
        PublicationField.objects.filter(entity__plan=record)
        .order_by("field")
        .values_list("proposal_id", "proposal_version", "outcome")
    ) == [(first.pk, first.version, "write"), (home.pk, home.version, "by_hand")]
    assert context("census_publication_planned") == [
        {"outcome": "succeeded", "count": 2}
    ]
    counts = confirmation_counts(record.pk)
    assert counts == {"write_entities": 1, "write_fields": 1, "blocked_entities": 0}
    confirmation = confirm(harness, admin, record, session, key=key)
    assert confirm(harness, admin, record, session, key=key).pk == confirmation.pk
    assert context("census_publication_confirmed") == [
        {"outcome": "changed", "count": 1}
    ]
    claim = acquire_source(**running_source_task(), phase="publication")
    failed = outcome(entity, claim, "failed", "provider_unavailable")
    published = outcome(entity, claim, "published")
    assert (failed.attempt, published.attempt) == (1, 2)
    assert published.fence == claim.fence
    with pytest.raises(DatabaseError, match="attempt or fence"):
        outcome(entity, claim, "failed", "provider_unavailable")
    assert context("census_publication_recorded") == [
        {"outcome": "failed", "count": 1},
        {"outcome": "succeeded", "count": 1},
    ]
    release_source(claim)
    # Nothing here changes a proposal.
    first.refresh_from_db()
    assert (first.execution, first.version) == ("pending", entity_version(first))


def entity_version(row):
    """The version a plan recorded for ``row``."""
    return PublicationField.objects.get(proposal=row).proposal_version


def test_plan_refuses_stale_versions_and_disagreeing_outcomes(ready):
    """Each field keeps its proposal's current version and an outcome that
    agrees with the proposal's handling and decision."""
    harness, admin, _, home, first, session = ready
    stale = ProposedChange.objects.get(pk=first.pk)
    approve(first)
    for rows, plans in (
        ([stale], [writing(stale)]),
        ([home], [writing(home)]),
        ([home], [writing(home, outcome=Outcome.NOT_APPROVED)]),
        ([first], [writing(first, outcome=Outcome.NOT_APPROVED)]),
        ([first], [writing(first, outcome=Outcome.BY_HAND)]),
    ):
        with pytest.raises(DatabaseError, match="differs from its census change"):
            plan(admin, rows, plans)
    # An entity whose hashes disagree with its fields cannot commit.
    hashed = EntityPlan(
        "member",
        first.entity_key,
        (FieldPlan(first.pk, first.field, Outcome.ALREADY_IN_PARISHSOFT),),
        False,
        {},
        READ,
        PLANNED,
    )
    with pytest.raises(DatabaseError, match="disagrees with its fields"):
        plan(admin, [first], [hashed])
    assert not PublicationPlan.objects.exists()


def test_plan_refuses_staff_and_rows_added_later(ready):
    """Only a current Administrator's preflight is planned, and a plan's
    entities and fields are written in its own transaction only."""
    _, admin, staff, home, first, session = ready
    with pytest.raises(DatabaseError, match="current Administrator"):
        plan(staff, [first], [writing(first)])
    record = plan(admin, [first], [writing(first)])
    with (
        task_login(ServiceRole.WORKER, exact=True, reconnect=True),
        pytest.raises(DatabaseError, match="written with the plan"),
        transaction.atomic(),
    ):
        PublicationEntity.objects.create(
            plan=record, entity_kind="member", entity_key="999", blocked=False
        )


def stale_session(admin):
    """An Administrator session that signed in ten minutes ago."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT statement_timestamp()")
        now = cursor.fetchone()[0]
    return forge(admin, authenticated_at=now - timedelta(minutes=10))


def test_confirmation_refusals(ready):
    """Fresh sign-in, the Administrator, exact counts, unchanged proposals,
    Production mode and no unfinished overlap are each required."""
    harness, admin, staff, _, first, session = ready
    record = plan(admin, [first], [writing(first)])
    with pytest.raises(PermissionError):
        confirm(harness, staff, record, forge(staff))
    with pytest.raises(DatabaseError, match="fresh Administrator"):
        confirm(harness, admin, record, stale_session(admin))
    with pytest.raises(DatabaseError, match="counts differ"):
        confirm(harness, admin, record, session, write_fields=2)
    first_plan = record
    confirm(harness, admin, first_plan, session)
    # A second plan of the same change waits for the first to finish.
    second = plan(admin, [first], [writing(first)])
    with pytest.raises(DatabaseError, match="still writing"):
        confirm(harness, admin, second, session)
    claim = acquire_source(**running_source_task(), phase="publication")
    outcome(PublicationEntity.objects.get(plan=first_plan), claim, "invalidated")
    release_source(claim)
    confirm(harness, admin, second, session)
    # A decision or edit since preflight makes the plan stale.
    third = plan(admin, [first], [writing(first)])
    approve(first)
    with pytest.raises(DatabaseError, match="changed since preflight"):
        confirm(harness, admin, third, session)


def test_testing_mode_plans_are_never_confirmed(ready):
    """In Testing mode publication stops after preflight."""
    harness, admin, _, _, first, session = ready
    owner("UPDATE stewardship_system_configuration SET mode='testing'")
    record = plan(admin, [first], [writing(first)])
    assert record.testing
    with pytest.raises(DatabaseError, match="Testing mode"):
        confirm(harness, admin, record, session)


def test_outcomes_need_a_confirmed_plan_and_the_live_fence(ready):
    """No attempt is recorded on an unconfirmed plan or off the fence."""
    harness, admin, _, _, first, session = ready
    record = plan(admin, [first], [writing(first)])
    entity = PublicationEntity.objects.get(plan=record)
    claim = acquire_source(**running_source_task(), phase="publication")
    with pytest.raises(DatabaseError, match="attempt or fence"):
        outcome(entity, claim, "published")
    confirm(harness, admin, record, session)
    release_source(claim)
    full = acquire_source(**running_source_task(), phase="full")
    with pytest.raises(ValueError):
        outcome(entity, full, "published")
    release_source(full)
    with (
        task_login(ServiceRole.WORKER, exact=True, reconnect=True),
        pytest.raises(DatabaseError, match="attempt or fence"),
        transaction.atomic(),
    ):
        # Forged directly, at a fence no live lease holds.
        PublicationOutcome.objects.create(
            entity=entity, attempt=1, result="published", fence=claim.fence
        )


def test_history_is_immutable_and_unaudited_rows_cannot_commit(ready):
    """Every table refuses UPDATE and DELETE, and a plan or confirmation
    without its audit event cannot commit."""
    harness, admin, _, _, first, session = ready
    record = plan(admin, [first], [writing(first)])
    confirm(harness, admin, record, session)
    for table in (
        "plan",
        "entity",
        "field",
        "confirmation",
    ):
        for statement in (
            f"UPDATE stewardship_publication_{table} SET actor_id=actor_id",
            f"DELETE FROM stewardship_publication_{table}",
        ):
            with (
                pytest.raises(DatabaseError, match="immutable"),
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute(statement)
    snapshot = SourceSnapshot.objects.get(pk=first.current_source_id)
    with (
        task_login(ServiceRole.WORKER, exact=True, reconnect=True),
        pytest.raises(DatabaseError, match="requires its audit"),
        work_transaction(),
    ):
        PublicationPlan.objects.create(
            actor_id=admin,
            campaign_id=first.submission.campaign_id,
            source_snapshot=snapshot,
            organization_id=snapshot.organization_id,
            request_key=uuid4(),
            testing=False,
        )


def test_do_block_refuses_a_missing_guard(ready):
    """The file's closing check raises when an object it installs is gone."""
    text = FROZEN.read_text(encoding="utf-8")
    check = text[text.index("DO $check$") :]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(check)  # installed as declared
    for statement in (
        "DROP TRIGGER stewardship_publication_outcome_guard "
        "ON stewardship_publication_outcome",
        "ALTER FUNCTION public.stewardship_publication_audited_v1"
        "(text, uuid, uuid, jsonb) SECURITY INVOKER",
        "DROP TRIGGER stewardship_publication_entity_effect "
        "ON stewardship_publication_entity",
    ):
        with (
            pytest.raises(DatabaseError, match="0087|publication"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement)
            cursor.execute(check)

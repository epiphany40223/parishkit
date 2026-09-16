"""Adversarial database ownership and recipient projection parity for Family mail."""

from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction

from parishkit.stewardship.campaigns.credential_models import RehearsalCodeReservation
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.family_mail_tasks import enqueue_preparation
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.scheduler import scheduler_session

from .campaign_builders import campaign_clock, complete_empty_catchup
from .response_builders import activate_response_service, response_source
from .test_background_grants_postgresql import task_login
from .test_family_mail_preparation_postgresql import (
    allocate,
    claim,
    family_mail,  # noqa: F401
    handler,
)
from .test_recipient_suppressions_postgresql import refresh, refused, remember

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("resolved", [False, True])
def test_real_preparation_agrees_with_refusal_and_source_resolution(
    family_mail,  # noqa: F811
    resolved,
):
    """Python selection and the SQL write guard agree on multiple head addresses."""
    harness = activate_response_service(family_mail)
    complete_empty_catchup(harness.campaign, uuid4())
    due = ScheduleDefinition.objects.get().current_revision.due_at
    with campaign_clock(due):
        data = response_source()
        data.members[4] = dict(
            data.members[3], memberDUID=4, emailAddress="other@example.org"
        )
        refresh(harness, data)
        remember(refused(harness))
        if resolved:
            data.members[3]["emailAddress"] = "corrected@example.org"
            refresh(harness, data)
        ticket, owner = allocate(), handler(harness)
        with task_login(ServiceRole.WORKER, exact=True):
            owner.execute(claim(ticket, owner))
        message = OutboxMessage.objects.select_related("render").get(
            semantic_key=ticket.occurrence_id
        )
        assert message.render.intended_recipients == (
            ["corrected@example.org", "other@example.org"]
            if resolved
            else ["other@example.org"]
        )


@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_ticket_is_immutable_even_for_schema_owner(family_mail, operation):  # noqa: F811
    """ORM bypass cannot mutate or erase the original epoch/task binding."""
    due = ScheduleDefinition.objects.get().current_revision.due_at
    with campaign_clock(due):
        ticket = allocate()
        statement = (
            "UPDATE stewardship_family_mail_preparation SET task_id=%s WHERE id=%s"
            if operation == "UPDATE"
            else "DELETE FROM stewardship_family_mail_preparation WHERE id=%s"
        )
        arguments = (uuid4(), ticket.pk) if operation == "UPDATE" else (ticket.pk,)
        with (
            pytest.raises(IntegrityError, match="immutable"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement, arguments)


def test_reservation_without_live_preparation_is_denied(family_mail):  # noqa: F811
    """A valid campaign and old rehearsal credentials cannot authorize reservations."""
    with (
        task_login(ServiceRole.WORKER, exact=True),
        work_transaction(),
        pytest.raises(IntegrityError, match="current preparation"),
        transaction.atomic(),
    ):
        RehearsalCodeReservation.objects.create(
            campaign=family_mail.campaign, key_id="m1", digest="a" * 64
        )


@pytest.mark.parametrize("binding", ["foreign_type", "null_domain"])
def test_reconciliation_blocks_unverifiable_task_binding(family_mail, binding):  # noqa: F811
    """Install corrupt owner-only evidence; the normal read view fails closed."""
    from parishkit.stewardship.jobs.storage import enqueue

    due = ScheduleDefinition.objects.get().current_revision.due_at
    with campaign_clock(due):
        ticket = allocate()
        task_id = enqueue(
            task_type="schedule_occurrence"
            if binding == "null_domain"
            else "other_work",
            domain_request_id=None,
            actor_id=None,
            correlation_id=uuid4(),
            admit=lambda *args: True,
        ).run_id
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE stewardship_schedule_occurrence DISABLE TRIGGER USER"
            )
            cursor.execute(
                "UPDATE stewardship_schedule_occurrence SET task_id=%s WHERE id=%s",
                (task_id, ticket.occurrence_id),
            )
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cursor.execute(
                "ALTER TABLE stewardship_schedule_occurrence ENABLE TRIGGER USER"
            )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT blocking FROM stewardship_schedule_work_row WHERE id=%s",
                (ticket.occurrence_id,),
            )
            assert cursor.fetchone() == (True,)


def test_missing_occurrence_is_not_allocated(family_mail):  # noqa: F811
    """A stale producer hint cannot create an unattached preparation root."""
    due = ScheduleDefinition.objects.get().current_revision.due_at
    with (
        campaign_clock(due),
        task_login(ServiceRole.SCHEDULER, exact=True),
        scheduler_session() as guard,
    ):
        assert enqueue_preparation(guard, uuid4()) is None


def test_running_occurrence_rejects_null_task_domain(family_mail):  # noqa: F811
    """The claim guard itself rejects NULL, independently of restricted-role guards."""
    from django.db.models import F

    from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.ownership import database_now
    from parishkit.stewardship.jobs.storage import enqueue

    from .test_taskrun_postgresql import act

    due = ScheduleDefinition.objects.get().current_revision.due_at
    with campaign_clock(due):
        ticket = allocate()
        task = act(
            enqueue(
                task_type="schedule_occurrence",
                domain_request_id=None,
                actor_id=None,
                correlation_id=uuid4(),
                admit=lambda *args: True,
            ),
            "claim",
        )
        with (
            work_transaction(),
            pytest.raises(IntegrityError, match="current fenced work"),
            transaction.atomic(),
        ):
            ScheduleOccurrence.objects.filter(pk=ticket.occurrence_id).update(
                state="running",
                task_id=task.run_id,
                worker_id=task.worker_id,
                fence=task.fence,
                attempts=1,
                lease_expires_at=TaskRun.objects.get(pk=task.run_id).lease_expires_at,
                heartbeat_at=database_now(),
                version=F("version") + 1,
                actor_id=task.worker_id,
            )


def test_new_refusal_after_enqueue_skips_before_credentials(family_mail, monkeypatch):  # noqa: F811
    """The final unsuppressed recipient disappearing is a durable non-error skip."""
    from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
    from parishkit.stewardship.jobs import family_mail_preparation
    from parishkit.stewardship.jobs.models import TaskRun

    def forbidden(*args, **kwargs):
        """A skipped Family has no credential work or outbox preparation."""
        raise AssertionError("Skipped mail tried to load credentials")

    harness = activate_response_service(family_mail)
    complete_empty_catchup(harness.campaign, uuid4())
    due = ScheduleDefinition.objects.get().current_revision.due_at
    with campaign_clock(due):
        ticket, owner = allocate(), handler(harness)
        remember(refused(harness))
        monkeypatch.setattr(
            family_mail_preparation, "seal_current_credentials", forbidden
        )
        with task_login(ServiceRole.WORKER, exact=True):
            owner.execute(claim(ticket, owner))
    row = ScheduleOccurrence.objects.get(pk=ticket.occurrence_id)
    assert row.state == "skipped" and row.reason == "no_deliverable_recipient"
    assert TaskRun.objects.get(pk=ticket.task_id).state == "cancelled"
    assert row.outbox_id is None


def test_trigger_functions_do_not_have_public_execution_grants(family_mail):  # noqa: F811
    """Compiled triggers execute normally without direct runtime invocation rights."""
    with task_login(ServiceRole.WORKER, exact=True), connection.cursor() as cursor:
        for suffix in ("epoch", "ticket", "outbox_write", "receipt", "reservation"):
            cursor.execute(
                "SELECT has_function_privilege(current_user,%s,'EXECUTE')",
                (f"stewardship_family_mail_{suffix}_v1()",),
            )
            assert cursor.fetchone() == (False,)


@pytest.mark.parametrize("mismatch", ["mode", "revision"])
def test_allocator_rejects_mismatched_candidate(family_mail, monkeypatch, mismatch):  # noqa: F811
    """Exercise admission branches without installing invalid immutable fixtures."""
    from parishkit.stewardship.jobs import family_mail_tasks

    due = ScheduleDefinition.objects.get().current_revision.due_at
    with campaign_clock(due):
        ticket = allocate()
        row = family_mail_tasks._row(ticket.occurrence_id)
        if mismatch == "mode":
            row.mode = "production"
        else:
            row.revision_id = uuid4()
        monkeypatch.setattr(family_mail_tasks, "_row", lambda _: row)
        with (
            task_login(ServiceRole.SCHEDULER, exact=True),
            scheduler_session() as guard,
        ):
            assert enqueue_preparation(guard, ticket.occurrence_id) is None


@pytest.mark.parametrize("fault", ["no_lock", "wrong_correlation", "second_epoch"])
def test_scheduler_cannot_initialize_unowned_epoch(family_mail, fault):  # noqa: F811
    """A scheduler login alone cannot create mismatched or concurrent epoch scope."""
    from contextlib import nullcontext

    from parishkit.stewardship.campaigns.credential_models import RehearsalEpoch
    from parishkit.stewardship.campaigns.rehearsals import (
        cleanup_rehearsal,
        invalidate_rehearsal,
        release_rehearsal_gate,
    )

    if fault != "second_epoch":
        previous = invalidate_rehearsal(
            campaign_id=family_mail.campaign.pk, admit=lambda _: True
        )
        while cleanup_rehearsal(previous):
            pass
        release_rehearsal_gate(
            campaign_id=family_mail.campaign.pk, admit=lambda _: True
        )
    due = ScheduleDefinition.objects.get().current_revision.due_at
    with (
        campaign_clock(due),
        task_login(ServiceRole.SCHEDULER, exact=True),
        scheduler_session(),
        nullcontext() if fault == "no_lock" else work_transaction(),
        pytest.raises(IntegrityError, match="Scheduler"),
        transaction.atomic(),
    ):
        RehearsalEpoch.objects.create(
            campaign=family_mail.campaign,
            actor_id=uuid4(),
            correlation_id=uuid4()
            if fault == "wrong_correlation"
            else family_mail.campaign.pk,
        )


@pytest.mark.parametrize("proof", ["preclaim", "unknown_key"])
def test_live_claim_cannot_reserve_using_unrelated_credential_evidence(
    family_mail,  # noqa: F811
    proof,
):
    """Independently test reservation predicates under otherwise live ownership."""
    from django.db.models import F

    from parishkit.stewardship.campaigns.credential_models import (
        FamilyCampaign,
        RehearsalCredential,
    )
    from parishkit.stewardship.campaigns.lifecycle import CampaignWorkKind
    from parishkit.stewardship.campaigns.rehearsals import (
        cleanup_rehearsal,
        invalidate_rehearsal,
        prepare_rehearsals,
        release_rehearsal_gate,
    )

    if proof == "unknown_key":
        previous = invalidate_rehearsal(
            campaign_id=family_mail.campaign.pk, admit=lambda _: True
        )
        while cleanup_rehearsal(previous):
            pass
        release_rehearsal_gate(
            campaign_id=family_mail.campaign.pk, admit=lambda _: True
        )
    family = FamilyCampaign.objects.get(portal_eligible=True)
    due = ScheduleDefinition.objects.get().current_revision.due_at
    with campaign_clock(due):
        ticket, owner = allocate(), handler(family_mail)
        with task_login(ServiceRole.WORKER, exact=True):
            execution = claim(ticket, owner)
        if proof == "preclaim":
            # Owner-only fixture attribution makes every other SQL predicate
            # valid; the original immutable creation instant predates the claim.
            RehearsalCredential.objects.filter(family=family).update(
                actor_id=execution.claim.worker_id,
                correlation_id=execution.claim.run_id,
                version=F("version") + 1,
            )
        with task_login(ServiceRole.WORKER, exact=True), execution.effect():
            if proof == "unknown_key":
                prepare_rehearsals(
                    campaign_id=family_mail.campaign.pk,
                    family_ids=[family.pk],
                    general=family_mail.rings.general,
                    mac=family_mail.rings.mac,
                    public=family_mail.rings.public,
                    purpose=CampaignWorkKind.REHEARSAL,
                    admit=lambda *args: True,
                    actor_id=execution.claim.worker_id,
                    correlation_id=execution.claim.run_id,
                )
            with (
                pytest.raises(IntegrityError, match="current preparation") as error,
                transaction.atomic(),
            ):
                RehearsalCodeReservation.objects.create(
                    campaign=family_mail.campaign,
                    digest="b" * 64,
                    key_id="m1" if proof == "preclaim" else "absent-key",
                )
            assert error.value.__cause__.sqlstate == "23514"

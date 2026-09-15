"""Private cleanup authority cannot be recreated by direct runtime SQL."""

from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from parishkit.stewardship.campaigns.cleanup_batches import apply_checkpoint
from parishkit.stewardship.campaigns.cleanup_requests import request_cancellation
from parishkit.stewardship.campaigns.production_models import (
    ProductionTransitionRequest,
)
from parishkit.stewardship.campaigns.production_states import ProductionAction
from parishkit.stewardship.campaigns.rehearsals import cleanup_rehearsal
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.ownership import TaskClaim

from .test_background_grants_postgresql import task_login
from .test_cleanup_batches_postgresql import running_request
from .test_cleanup_tasks_postgresql import queued
from .test_production_journal_postgresql import act

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT target_id FROM stewardship_production_target",
        "SELECT session_data FROM django_session",
        "SELECT * FROM stewardship_cleanup_effect",
        "DELETE FROM stewardship_production_target",
        "DELETE FROM stewardship_rehearsal_credential",
        "DELETE FROM stewardship_outbox_message",
        "SELECT stewardship_cleanup_claim_v1(gen_random_uuid())",
        "INSERT INTO stewardship_cleanup_effect VALUES (pg_current_xact_id(),"
        "gen_random_uuid(),gen_random_uuid(),'submissions',gen_random_uuid())",
    ],
)
def test_worker_has_no_direct_private_cleanup_access(response_service, statement):
    """Runtime authority cannot read secrets, choose targets or forge proof."""
    queued(response_service)
    with (
        task_login(ServiceRole.WORKER, exact=True),
        pytest.raises(DatabaseError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(statement)


def test_other_retention_owner_cannot_steal_captured_targets(response_service):
    """Ordinary invalidated-rehearsal cleanup cannot strand the sealed manifest."""
    status = queued(response_service)
    request = ProductionTransitionRequest.objects.get(pk=status.request_id)
    with pytest.raises(IntegrityError, match="belongs to its cleanup worker"):
        cleanup_rehearsal(request.invalidated_epoch_id)


def test_completion_requires_actual_empty_manifest(response_service):
    """A caller's completion claim cannot replace verified row absence."""
    status = running_request(response_service)
    with pytest.raises(IntegrityError, match="verified absence"):
        act(status, ProductionAction.COMPLETE)


def test_batch_replay_does_not_delete_again(response_service):
    """One acknowledged command retains its original deletion result."""
    status = running_request(response_service)
    claim = TaskClaim(status.run_id, status.task_fence, status.worker_id)
    command = uuid4()
    with work_transaction():
        first = apply_checkpoint(
            status.request_id, claim, command_id=command, maximum=2
        )
    with work_transaction():
        assert (
            apply_checkpoint(status.request_id, claim, command_id=command, maximum=2)
            == first
        )


def test_durable_cancellation_blocks_even_direct_batch_commands(response_service):
    """A stale worker cannot skip its Python cancellation check and still delete."""
    status = running_request(response_service)
    request_cancellation(
        request_id=status.request_id,
        command_id=uuid4(),
        expected_version=status.version,
        actor_id=uuid4(),
        correlation_id=uuid4(),
        admit=lambda *args: True,
    )
    claim = TaskClaim(status.run_id, status.task_fence, status.worker_id)
    with (
        pytest.raises(IntegrityError, match="current command ownership"),
        work_transaction(),
    ):
        apply_checkpoint(status.request_id, claim)


def test_gate_denies_submission_from_an_already_open_form(response_service):
    """An old Family page cannot add a response after its epoch was invalidated."""
    from parishkit.stewardship.responses.baselines import FamilyAdmissionDenied

    from .test_response_submission_postgresql import form_and_answers, submit

    form, answers = form_and_answers(response_service)
    queued(response_service)
    with pytest.raises(FamilyAdmissionDenied):
        submit(response_service, form, answers)


@pytest.mark.parametrize("command", ["checkpoint", "complete"])
@pytest.mark.parametrize("table_owner", [False, True])
def test_runtime_cannot_bypass_manifest_with_foundation_journal(
    response_service, command, table_owner
):
    """Owner-only journal fixtures cannot become unverified runtime completion."""
    from .test_cleanup_batches_postgresql import delete_batch
    from .test_cleanup_inventory_postgresql import start_request
    from .test_production_journal_postgresql import start

    with work_transaction():
        status = start_request(response_service)
    status = start(status)
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user")
        original_owner = cursor.fetchone()[0]
    with task_login(ServiceRole.WORKER, exact=True):
        try:
            if table_owner:
                with connection.cursor() as cursor:
                    cursor.execute("RESET SESSION AUTHORIZATION")
                    cursor.execute(
                        "ALTER TABLE stewardship_production_request "
                        "OWNER TO pk_stewardship_worker"
                    )
                    cursor.execute("SET SESSION AUTHORIZATION pk_stewardship_worker")
            with pytest.raises(DatabaseError, match="sealed manifest"):
                if command == "checkpoint":
                    delete_batch(status)
                else:
                    act(status, ProductionAction.COMPLETE)
        finally:
            if table_owner:
                from psycopg import sql

                # Restore fixture ownership before task_login removes its role;
                # its DROP OWNED must never remove an application table.
                with connection.cursor() as cursor:
                    cursor.execute("RESET SESSION AUTHORIZATION")
                    cursor.execute(
                        sql.SQL(
                            "ALTER TABLE stewardship_production_request OWNER TO {}"
                        ).format(sql.Identifier(original_owner))
                    )


@pytest.mark.parametrize("claim", [None, object(), "not-a-claim"])
def test_checkpoint_rejects_untyped_claim_before_attribute_access(
    response_service, claim
):
    """Only a validated TaskClaim can select a request's locking identity."""
    from parishkit.stewardship.jobs.ownership import TaskOwnershipLost

    status = queued(response_service)
    with pytest.raises(TaskOwnershipLost), work_transaction():
        apply_checkpoint(status.request_id, claim)


@pytest.mark.parametrize(
    "invalid", ["request", "small_budget", "large_budget", "command"]
)
def test_checkpoint_rejects_invalid_command_shape(response_service, invalid):
    """Typed commands reject malformed IDs/budgets before looking up a claim."""
    status = queued(response_service)
    request_id = str(status.request_id) if invalid == "request" else status.request_id
    options = {}
    if invalid in {"small_budget", "large_budget"}:
        options["maximum"] = 1 if invalid == "small_budget" else 1001
    if invalid == "command":
        options["command_id"] = "not-a-uuid"
    with pytest.raises(ValueError), work_transaction():
        apply_checkpoint(request_id, TaskClaim(uuid4(), 1, uuid4()), **options)

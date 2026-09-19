"""Pinned inactive links use the actual cleanup, source and token storage owners."""

import json
from contextlib import nullcontext
from dataclasses import asdict
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import F

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.activation_claims import SETTING, token_claim
from parishkit.stewardship.campaigns.activation_cleanup import (
    disposed,
    request_cancellation,
)
from parishkit.stewardship.campaigns.activation_inputs import TokenPreparationInputs
from parishkit.stewardship.campaigns.activation_models import ProductionTokenPreparation
from parishkit.stewardship.campaigns.activation_tasks import token_handler
from parishkit.stewardship.campaigns.activation_tokens import (
    CLEANUP_TASK_TYPE,
    TASK_TYPE,
    current_inputs,
    prepare_batch,
    prepared_generation,
    request_preparation,
)
from parishkit.stewardship.campaigns.credential_models import (
    DeploymentCredentialState,
    FamilyAccessToken,
    FamilyCampaign,
)
from parishkit.stewardship.campaigns.link_tokens import token_context
from parishkit.stewardship.campaigns.production_models import (
    ProductionTransitionRequest,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint, recover_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import TaskClaim, TaskOwnershipLost
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status, enqueue, retry_failed
from parishkit.stewardship.storage import StaleRecordError

from .response_builders import response_source
from .test_background_grants_postgresql import task_login
from .test_cleanup_tasks_postgresql import queued, run
from .test_source_families_postgresql import prepare, promote
from .test_taskrun_postgresql import act, expire

pytestmark = pytest.mark.django_db(transaction=True)


def test_sql_writes_reject_an_old_run_even_while_retry_has_a_live_claim(
    response_service,
):
    """Direct restricted SQL must repeat exact fencing, not just find a live root."""
    cleanup = queued(response_service)
    assert run(cleanup)
    preparation = request_preparation(
        transition_id=cleanup.request_id,
        request_key=uuid4(),
        actor_id=uuid4(),
        correlation_id=uuid4(),
        admit=lambda *args: True,
    )
    first = act(_status(TaskRun.objects.get(pk=preparation.task_id)), "claim")
    with work_transaction():
        generation = prepare_batch(
            preparation.pk,
            TaskClaim(first.run_id, first.fence, first.worker_id),
            public=response_service.rings.public,
        )
    failed = act(first, "permanent_failure")
    with work_transaction():
        retry = retry_failed(
            run_id=failed.run_id,
            command_id=uuid4(),
            actor_id=uuid4(),
            correlation_id=uuid4(),
            admit=lambda *args: True,
        )
    current = act(retry, "claim")
    for evidence in (
        None,
        {
            "run": str(first.run_id),
            "fence": first.fence,
            "worker": str(first.worker_id),
        },
        {
            "run": str(current.run_id),
            "fence": current.fence + 1,
            "worker": str(current.worker_id),
        },
        {"run": str(current.run_id), "fence": current.fence, "worker": str(uuid4())},
    ):
        with (
            task_login(ServiceRole.WORKER, exact=True, reconnect=True),
            pytest.raises(DatabaseError),
            work_transaction(),
            connection.cursor() as cursor,
        ):
            if evidence is not None:
                cursor.execute(
                    "SELECT set_config(%s,%s,true)", [SETTING, json.dumps(evidence)]
                )
            cursor.execute(
                "UPDATE stewardship_family_token_generation "
                "SET version=version+1 WHERE id=%s",
                [generation.pk],
            )
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True), work_transaction():
        with (
            pytest.raises(DatabaseError) as failure,
            token_claim(TaskClaim(current.run_id, current.fence, current.worker_id)),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE stewardship_family_token_generation "
                "SET version=version WHERE id=%s",
                [generation.pk],
            )
        assert failure.value.__cause__.sqlstate == "23514"
        with (
            token_claim(TaskClaim(current.run_id, current.fence, current.worker_id)),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE stewardship_family_token_generation "
                "SET version=version+1 WHERE id=%s",
                [generation.pk],
            )
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting(%s,true)", [SETTING])
            assert cursor.fetchone()[0] in (None, "")

    act(current, "complete")
    cancellation = request_cancellation(
        preparation_id=preparation.pk,
        request_key=uuid4(),
        actor_id=uuid4(),
        correlation_id=uuid4(),
        admit=lambda *args: True,
    )
    first_disposal = act(_status(TaskRun.objects.get(pk=cancellation.task_id)), "claim")
    from parishkit.stewardship.campaigns.link_tokens import cancel_generation

    with (
        task_login(ServiceRole.WORKER, exact=True, reconnect=True),
        work_transaction(),
        token_claim(
            TaskClaim(
                first_disposal.run_id, first_disposal.fence, first_disposal.worker_id
            )
        ),
    ):
        cancel_generation(generation_id=generation.pk, admit=lambda *args: True)
    failed = act(first_disposal, "permanent_failure")
    with work_transaction():
        retry = retry_failed(
            run_id=failed.run_id,
            command_id=uuid4(),
            actor_id=uuid4(),
            correlation_id=uuid4(),
            admit=lambda *args: True,
        )
    current_disposal = act(retry, "claim")
    with (
        task_login(ServiceRole.WORKER, exact=True, reconnect=True),
        pytest.raises(DatabaseError),
        work_transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT set_config(%s,%s,true)",
            [
                SETTING,
                json.dumps(
                    {
                        "run": str(first_disposal.run_id),
                        "fence": first_disposal.fence,
                        "worker": str(first_disposal.worker_id),
                    }
                ),
            ],
        )
        cursor.execute(
            "UPDATE stewardship_family_token SET ciphertext=NULL,digest=NULL,"
            "destroyed_at=stewardship_campaign_now_v1(),version=version+1 "
            "WHERE generation_id=%s",
            [generation.pk],
        )
    from parishkit.stewardship.campaigns.activation_cleanup import scrub_batch

    with task_login(ServiceRole.WORKER, exact=True, reconnect=True), work_transaction():
        assert (
            scrub_batch(
                preparation,
                TaskClaim(
                    current_disposal.run_id,
                    current_disposal.fence,
                    current_disposal.worker_id,
                ),
            )
            == 1
        )


def test_retry_resumes_committed_batch_and_source_change_requires_disposal(
    response_service, monkeypatch
):
    """Real source promotion and durable retry preserve sealed earlier batches."""
    from parishkit.stewardship.campaigns import activation_tasks

    cleanup = queued(response_service)
    assert run(cleanup)
    data = response_source()
    for identifier in (4, 5):
        data.families[identifier] = data.families[1] | {"familyDUID": identifier}
        data.members[identifier * 10] = data.members[3] | {
            "memberDUID": identifier * 10,
            "familyDUID": identifier,
        }
    snapshot, source_claim = prepare(data)
    promote(snapshot, source_claim, response_service.campaign, response_service.rings)
    arguments = dict(
        transition_id=cleanup.request_id,
        request_key=uuid4(),
        actor_id=uuid4(),
        correlation_id=uuid4(),
        admit=lambda *args: True,
    )
    preparation = request_preparation(**arguments)
    assert preparation.eligible_count == 3
    task = act(_status(TaskRun.objects.get(pk=preparation.task_id)), "claim")
    with work_transaction():
        generation = prepare_batch(
            preparation.pk,
            TaskClaim(task.run_id, task.fence, task.worker_id),
            public=response_service.rings.public,
            maximum=1,
        )
    assert generation.state == "building"
    original = FamilyAccessToken.objects.get(generation=generation)
    sealed = original.ciphertext
    failed = act(task, "permanent_failure")
    with work_transaction():
        retry = retry_failed(
            run_id=failed.run_id,
            command_id=uuid4(),
            actor_id=uuid4(),
            correlation_id=uuid4(),
            admit=lambda *args: True,
        )
    batches = []

    def bounded(*args, **kwargs):
        """Keep the actual owner, forcing two further committed batches."""
        result = prepare_batch(*args, **kwargs, maximum=1)
        batches.append(result.state)
        return result

    monkeypatch.setattr(activation_tasks, "prepare_batch", bounded)
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            retry.run_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: token_handler(public=response_service.rings.public)},
        )
    original.refresh_from_db()
    assert original.ciphertext == sealed
    assert batches == ["building", "ready"]
    assert FamilyAccessToken.objects.filter(generation=generation).count() == 3
    snapshot, source_claim = prepare(response_source())
    promote(snapshot, source_claim, response_service.campaign, response_service.rings)
    with pytest.raises(StaleRecordError, match="no longer current"):
        request_preparation(**arguments)
    with pytest.raises(StaleRecordError, match="needs disposal"):
        request_preparation(**(arguments | {"request_key": uuid4()}))
    cancellation = request_cancellation(
        preparation_id=preparation.pk,
        request_key=uuid4(),
        actor_id=uuid4(),
        correlation_id=uuid4(),
        admit=lambda *args: True,
    )
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            cancellation.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={CLEANUP_TASK_TYPE: token_handler(cleanup=True)},
        )
    fresh = request_preparation(**(arguments | {"request_key": uuid4()}))
    assert fresh.pk != preparation.pk and fresh.eligible_count == 1


def test_prepare_after_cleanup_is_pinned_inactive_and_fenced(response_service):
    """Storage-level admission here is synthetic; the real runtime adapter follows."""
    cleanup = queued(response_service)
    actor, request_key = uuid4(), uuid4()
    arguments = dict(
        transition_id=cleanup.request_id,
        request_key=request_key,
        actor_id=actor,
        correlation_id=uuid4(),
        admit=lambda *args: True,
    )
    with pytest.raises(StaleRecordError, match="completed current cleanup"):
        request_preparation(**arguments)
    assert not TaskRun.objects.filter(task_type=TASK_TYPE).exists()
    assert run(cleanup)
    codes = list(
        FamilyCampaign.objects.order_by("pk").values_list("code_ciphertext", flat=True)
    )
    preparation = request_preparation(**arguments)
    assert request_preparation(**arguments).pk == preparation.pk
    assert ProductionTokenPreparation.objects.count() == 1
    with pytest.raises(PermissionError):
        request_preparation(**(arguments | {"admit": lambda *args: False}))
    with pytest.raises(PermissionError):
        request_preparation(**(arguments | {"actor_id": uuid4()}))
    with pytest.raises(StaleRecordError, match="already in progress"):
        request_preparation(**(arguments | {"request_key": uuid4()}))

    task = act(_status(TaskRun.objects.get(pk=preparation.task_id)), "claim")
    claim = TaskClaim(task.run_id, task.fence, task.worker_id)
    with (
        pytest.raises(IntegrityError, match="terminal domain proof"),
        work_transaction(),
    ):
        act(task, "complete")
    with work_transaction():
        with pytest.raises(TaskOwnershipLost):
            prepare_batch(
                preparation.pk,
                TaskClaim(task.run_id, task.fence + 1, task.worker_id),
                public=response_service.rings.public,
            )
        assert not FamilyAccessToken.objects.exists()
        generation = prepare_batch(
            preparation.pk, claim, public=response_service.rings.public, maximum=1
        )
        assert TokenPreparationInputs.retained(preparation).covers(generation)
        assert prepared_generation(preparation).pk == generation.pk
        assert (
            prepare_batch(
                preparation.pk, claim, public=response_service.rings.public, maximum=1
            ).pk
            == generation.pk
        )
    token = FamilyAccessToken.objects.get(generation=generation)
    value = response_service.rings.private.decrypt(
        token.ciphertext, context=token_context(token.pk)
    ).decode("ascii")
    assert value and len(value) > 6
    response_service.campaign.refresh_from_db()
    assert response_service.campaign.active_token_generation_id is None
    assert response_service.campaign.state == "draft"
    assert SystemConfiguration.objects.get().mode == "testing"
    assert (
        list(
            FamilyCampaign.objects.order_by("pk").values_list(
                "code_ciphertext", flat=True
            )
        )
        == codes
    )


@pytest.mark.parametrize("stale", [False, True])
def test_recovery_requires_current_manifest_or_proven_disposal(response_service, stale):
    """Crash recovery cannot mistake a stale ready manifest for current success."""
    cleanup = queued(response_service)
    assert run(cleanup)
    preparation = request_preparation(
        transition_id=cleanup.request_id,
        request_key=uuid4(),
        actor_id=uuid4(),
        correlation_id=uuid4(),
        admit=lambda *args: True,
    )
    task = act(_status(TaskRun.objects.get(pk=preparation.task_id)), "claim")
    with work_transaction():
        prepare_batch(
            preparation.pk,
            TaskClaim(task.run_id, task.fence, task.worker_id),
            public=response_service.rings.public,
        )
    if stale:
        with work_transaction():
            DeploymentCredentialState.objects.update(
                family_link_epoch=uuid4(), version=F("version") + 1
            )
        cancellation = request_cancellation(
            preparation_id=preparation.pk,
            request_key=uuid4(),
            actor_id=uuid4(),
            correlation_id=uuid4(),
            admit=lambda *args: True,
        )
        with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
            assert execute_hint(
                cancellation.task_id,
                queue=WorkQueue.GENERAL,
                worker_id=uuid4(),
                handlers={CLEANUP_TASK_TYPE: token_handler(cleanup=True)},
            )
        assert disposed(preparation)
    expire(act(task, "heartbeat", lease_seconds=1))
    with (
        task_login(ServiceRole.SCHEDULER, exact=True, reconnect=True),
        work_transaction(),
    ):
        plan = token_handler(scheduler=True).recover(
            _status(TaskRun.objects.get(pk=preparation.task_id))
        )
        assert plan.action == ("recovery_cancel" if stale else "recovery_complete")
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert recover_hint(
            preparation.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: token_handler(public=response_service.rings.public)},
        )
    assert TaskRun.objects.get(pk=preparation.task_id).state == (
        "cancelled" if stale else "succeeded"
    )
    response_service.campaign.refresh_from_db()
    assert response_service.campaign.active_token_generation_id is None


def test_preparation_sql_rejects_forged_inputs_and_unbound_work(response_service):
    """The SQL owner verifies actual current scope, not caller-supplied digests."""
    cleanup = queued(response_service)
    assert run(cleanup)
    transition = ProductionTransitionRequest.objects.get(pk=cleanup.request_id)
    with work_transaction():
        inputs = current_inputs(transition)
    for field, replacement in (
        ("source_snapshot_id", uuid4()),
        ("source_generation", inputs.source_generation + 1),
        ("credential_epoch", uuid4()),
        ("key_inventory_digest", "c" * 64),
        ("eligibility_digest", "c" * 64),
        ("eligible_count", inputs.eligible_count + 1),
    ):
        with pytest.raises(IntegrityError, match="stale scope"), work_transaction():
            identifier, actor, correlation = uuid4(), uuid4(), uuid4()
            task = enqueue(
                task_type=TASK_TYPE,
                domain_request_id=identifier,
                idempotency_key=identifier,
                actor_id=actor,
                correlation_id=correlation,
                admit=lambda *args: True,
            )
            ProductionTokenPreparation.objects.create(
                id=identifier,
                transition=transition,
                task_id=task.root_id,
                request_key=uuid4(),
                actor_id=actor,
                correlation_id=correlation,
                **(asdict(inputs) | {field: replacement}),
            )
    with pytest.raises(IntegrityError, match="committed intent"), transaction.atomic():
        enqueue(
            task_type=TASK_TYPE,
            domain_request_id=uuid4(),
            actor_id=uuid4(),
            correlation_id=uuid4(),
            admit=lambda *args: True,
        )
    preparation = request_preparation(
        transition_id=transition.pk,
        request_key=uuid4(),
        actor_id=uuid4(),
        correlation_id=uuid4(),
        admit=lambda *args: True,
    )
    with (
        pytest.raises(IntegrityError, match="immutable"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE stewardship_production_tokens "
            "SET eligible_count=eligible_count+1 WHERE id=%s",
            [preparation.pk],
        )


@pytest.mark.parametrize("finish_first", [False, True])
@pytest.mark.parametrize("restricted", [False, True])
def test_maintained_preparation_and_cancel_never_select_live_links(
    response_service, finish_first, restricted
):
    """Independent preparation/disposal hints remain idempotent in either order."""
    cleanup = queued(response_service)
    assert run(cleanup)
    preparation = request_preparation(
        transition_id=cleanup.request_id,
        request_key=uuid4(),
        actor_id=uuid4(),
        correlation_id=uuid4(),
        admit=lambda *args: True,
    )
    handlers = {
        TASK_TYPE: token_handler(public=response_service.rings.public),
        CLEANUP_TASK_TYPE: token_handler(cleanup=True),
    }

    def execute(task_id):
        """Exercise the maintained dispatcher, not a fabricated Execution control."""
        with (
            task_login(ServiceRole.WORKER, exact=True, reconnect=True)
            if restricted
            else nullcontext()
        ):
            return execute_hint(
                task_id, queue=WorkQueue.GENERAL, worker_id=uuid4(), handlers=handlers
            )

    if finish_first:
        assert execute(preparation.task_id)
        assert TaskRun.objects.get(pk=preparation.task_id).state == "succeeded"
        assert prepared_generation(preparation).state == "ready"
    options = dict(
        preparation_id=preparation.pk,
        request_key=uuid4(),
        actor_id=uuid4(),
        correlation_id=uuid4(),
        admit=lambda *args: True,
    )
    cancellation = request_cancellation(**options)
    assert request_cancellation(**options).pk == cancellation.pk
    assert execute(cancellation.task_id)
    assert TaskRun.objects.get(pk=cancellation.task_id).state == "succeeded"
    assert disposed(preparation)
    if not finish_first:
        assert execute(preparation.task_id)
        assert TaskRun.objects.get(pk=preparation.task_id).state == "cancelled"
    assert not execute(cancellation.task_id)
    assert not execute(preparation.task_id)
    assert not FamilyAccessToken.objects.filter(destroyed_at__isnull=True).exists()
    response_service.campaign.refresh_from_db()
    assert response_service.campaign.active_token_generation_id is None
    assert response_service.campaign.state == "draft"
    assert SystemConfiguration.objects.get().mode == "testing"

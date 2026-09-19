"""Pinned inactive links use the actual cleanup, source and token storage owners."""

from contextlib import nullcontext
from dataclasses import asdict
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import F

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
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
from parishkit.stewardship.jobs.storage import _status, enqueue
from parishkit.stewardship.storage import StaleRecordError

from .test_background_grants_postgresql import task_login
from .test_cleanup_tasks_postgresql import queued, run
from .test_taskrun_postgresql import act, expire

pytestmark = pytest.mark.django_db(transaction=True)


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

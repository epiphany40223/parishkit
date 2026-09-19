"""Pinned inactive links use the actual cleanup, source and token storage owners."""

from dataclasses import asdict
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.activation_inputs import TokenPreparationInputs
from parishkit.stewardship.campaigns.activation_models import ProductionTokenPreparation
from parishkit.stewardship.campaigns.activation_tokens import (
    TASK_TYPE,
    current_inputs,
    prepare_batch,
    prepared_generation,
    request_preparation,
)
from parishkit.stewardship.campaigns.credential_models import (
    FamilyAccessToken,
    FamilyCampaign,
)
from parishkit.stewardship.campaigns.link_tokens import token_context
from parishkit.stewardship.campaigns.production_models import (
    ProductionTransitionRequest,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import TaskClaim, TaskOwnershipLost
from parishkit.stewardship.jobs.storage import _status, enqueue
from parishkit.stewardship.storage import StaleRecordError

from .test_cleanup_tasks_postgresql import queued, run
from .test_taskrun_postgresql import act

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

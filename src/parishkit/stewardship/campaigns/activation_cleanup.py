"""Cancel inactive preparation without touching selected links or stable codes."""

from uuid import UUID, uuid4

from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.storage import TaskStatus, enqueue
from parishkit.stewardship.storage import StaleRecordError

from .activation_claims import token_claim
from .activation_models import ProductionTokenCancellation, ProductionTokenPreparation
from .activation_tokens import (
    CLEANUP_TASK_TYPE,
    TASK_TYPE,
    prepared_generation,
    require_current,
)
from .credential_models import FamilyAccessToken
from .link_tokens import cancel_generation, scrub_generation
from .work_locks import require_work_order, work_transaction


def request_cancellation(
    *, preparation_id, request_key, actor_id, correlation_id, admit
):
    """Record current Admin intent; a separate worker owns bounded staged disposal."""
    if any(
        not isinstance(value, UUID)
        for value in (preparation_id, request_key, actor_id, correlation_id)
    ) or not callable(admit):
        raise ValueError("Preparation cancellation requires exact identities.")
    with work_transaction():
        preparation = ProductionTokenPreparation.objects.select_related(
            "transition"
        ).get(pk=preparation_id)
        previous = ProductionTokenCancellation.objects.filter(
            preparation=preparation
        ).first()
        if admit(preparation, previous) is not True:
            raise PermissionError("Preparation cancellation is not admitted.")
        if previous is not None:
            if (previous.request_key, previous.actor_id) != (request_key, actor_id):
                raise StaleRecordError("Preparation cancellation already has intent.")
            return previous
        generation = prepared_generation(preparation)
        if generation is not None and (
            generation.state == "active"
            or preparation.transition.campaign.active_token_generation_id
            == generation.pk
        ):
            raise StaleRecordError("Selected live links cannot be cancelled.")
        identifier = uuid4()
        task = enqueue(
            task_type=CLEANUP_TASK_TYPE,
            domain_request_id=identifier,
            actor_id=actor_id,
            correlation_id=correlation_id,
            idempotency_key=identifier,
            admit=lambda action, status: action == "enqueue",
        )
        return ProductionTokenCancellation.objects.create(
            id=identifier,
            preparation=preparation,
            task_id=task.root_id,
            request_key=request_key,
            actor_id=actor_id,
            correlation_id=correlation_id,
        )


def owned_cancellation(status):
    """Resolve only the immutable intent belonging to this exact task execution."""
    require_work_order()
    if (
        not isinstance(status, TaskStatus)
        or status.task_type != CLEANUP_TASK_TYPE
        or not TaskRun.objects.filter(
            pk=status.run_id,
            root_id=status.root_id,
            task_type=CLEANUP_TASK_TYPE,
            domain_request_id=status.domain_request_id,
            state=status.state,
            version=status.version,
            fence=status.fence,
            worker_id=status.worker_id,
        ).exists()
    ):
        raise PermissionError("Preparation disposal task ownership is unavailable.")
    cancellation = (
        ProductionTokenCancellation.objects.filter(
            pk=status.domain_request_id, task_id=status.root_id
        )
        .select_related("preparation__transition")
        .first()
    )
    if cancellation is None:
        raise PermissionError("Preparation disposal has no immutable task binding.")
    return cancellation


def disposed(preparation):
    """A terminal staging state alone is not proof that ciphertext was scrubbed."""
    generation = prepared_generation(preparation)
    return generation is None or (
        generation.state in {"cancelled", "failed", "superseded"}
        and not FamilyAccessToken.objects.filter(
            generation=generation, destroyed_at__isnull=True
        ).exists()
    )


def scrub_batch(preparation, claim, *, maximum=500):
    """Fence either explicit cancellation or the original stale preparation owner."""
    require_work_order()
    if type(maximum) is not int or not 1 <= maximum <= 1000:
        raise ValueError("Preparation disposal requires a bounded batch.")
    task = lock_task_claim(claim)
    if task.task_type == TASK_TYPE:
        bound = (
            task.root_id == preparation.task_id
            and task.domain_request_id == preparation.pk
        )
    elif task.task_type == CLEANUP_TASK_TYPE:
        bound = ProductionTokenCancellation.objects.filter(
            pk=task.domain_request_id, task_id=task.root_id, preparation=preparation
        ).exists()
    else:
        bound = False
    if not bound:
        raise PermissionError("Preparation disposal belongs to another task.")
    if task.task_type == TASK_TYPE:
        try:
            require_current(preparation)
        except StaleRecordError:
            pass
        else:
            raise PermissionError("Current preparation has no cancellation intent.")
    generation = prepared_generation(preparation)
    if generation is None:
        return 0

    def admit(campaign, deployment, row):
        """Never scrub a selected generation, including after a lock wait."""
        lock_task_claim(claim)
        return (
            campaign.pk == preparation.transition.campaign_id
            and row.operation_id == preparation.pk
            and row.task_id == preparation.task_id
            and row.state != "active"
            and campaign.active_token_generation_id != row.pk
        )

    with token_claim(claim):
        cancel_generation(generation_id=generation.pk, admit=admit)
        count = scrub_generation(generation.pk, batch_size=maximum)
    lock_task_claim(claim)
    return count

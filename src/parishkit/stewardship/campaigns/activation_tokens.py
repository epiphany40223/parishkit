"""Internal intake and fenced effects for inactive Production link preparation.

The Admin adapter owns authorization; the compiled general-worker handler owns
TaskClaim admission. Neither a task hint nor a ready generation can activate a
campaign. No provider or private decryption key belongs in this module.
"""

from dataclasses import asdict
from uuid import UUID, uuid4

from parishkit.stewardship.jobs.admission import _scope
from parishkit.stewardship.jobs.models import NONTERMINAL_STATES, TaskRun
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.storage import TaskStatus, enqueue
from parishkit.stewardship.source.models import SourceCurrent
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from .activation_claims import token_claim
from .activation_inputs import TokenPreparationInputs
from .activation_models import ProductionTokenCancellation, ProductionTokenPreparation
from .credential_keys import inventory_digest
from .credential_models import (
    CampaignCredentialState,
    CredentialKeyState,
    DeploymentCredentialState,
    FamilyAccessToken,
    FamilyAccessTokenGeneration,
)
from .link_tokens import begin_generation, prepare_generation_batch
from .models import CampaignWorkGate
from .production_models import (
    ProductionCleanupCancellation,
    ProductionCleanupManifest,
    ProductionTransitionRequest,
)
from .work_locks import require_work_order, work_transaction

TASK_TYPE = "production_tokens"
CLEANUP_TASK_TYPE = "production_token_cleanup"


def preparation_available(transition, *, excluding=None):
    """Dispose prior staging before preparing a replacement revision."""
    require_work_order()
    identifiers = ProductionTokenPreparation.objects.filter(transition=transition)
    if excluding is not None:
        identifiers = identifiers.exclude(pk=excluding)
    identifiers = identifiers.values("id")
    return not (
        TaskRun.objects.filter(
            task_type=TASK_TYPE,
            domain_request_id__in=identifiers,
            state__in=NONTERMINAL_STATES,
        ).exists()
        or FamilyAccessTokenGeneration.objects.filter(
            operation_id__in=identifiers, state__in=("building", "ready", "active")
        ).exists()
        or FamilyAccessToken.objects.filter(
            generation__operation_id__in=identifiers, destroyed_at__isnull=True
        ).exists()
    )


def current_inputs(transition):
    """Read the complete current preparation scope without enumerating Families."""
    require_work_order()
    scope = _scope(transition.campaign_id)
    if (
        transition.state != "cleanup_complete"
        or transition.processed_count != transition.inventory_total
        or scope.runtime.mode != "testing"
        or scope.runtime.restore_review_required
        or scope.runtime.current_campaign_id != transition.campaign_id
        or scope.runtime.active_configuration_id != transition.configuration_id
        or scope.campaign.state != "draft"
        or scope.instant >= scope.campaign.active_configuration.ends_at
        or ProductionCleanupCancellation.objects.filter(request=transition).exists()
        or not ProductionCleanupManifest.objects.filter(request=transition).exists()
        or CampaignWorkGate.objects.filter(state__in=("preparing", "running")).exists()
    ):
        raise StaleRecordError("Link preparation requires completed current cleanup.")
    deployment = DeploymentCredentialState.objects.select_for_update().get()
    population = CampaignCredentialState.objects.select_for_update().get(
        campaign_id=transition.campaign_id
    )
    source = SourceCurrent.objects.first()
    keys = CredentialKeyState.objects.filter(kind="token_public").first()
    if (
        not population.go_live_gate
        or population.version < transition.gate_version
        or population.rehearsal_epoch_id is not None
        or population.population_dirty
        or source is None
        or source.snapshot_id is None
        or population.source_snapshot_id != source.snapshot_id
        or population.source_generation != source.generation
        or keys is None
    ):
        raise StaleRecordError("Link preparation inputs are not currently available.")
    return TokenPreparationInputs(
        configuration_id=scope.campaign.active_configuration_id,
        source_snapshot_id=source.snapshot_id,
        source_generation=source.generation,
        credential_epoch=deployment.family_link_epoch,
        key_inventory_digest=keys.inventory_digest,
        eligibility_digest=population.eligibility_digest,
        eligible_count=population.eligible_count,
    )


def request_preparation(*, transition_id, request_key, actor_id, correlation_id, admit):
    """Freeze inputs and enqueue one opaque task, with current admission on replay."""
    if any(
        not isinstance(value, UUID)
        for value in (transition_id, request_key, actor_id, correlation_id)
    ) or not callable(admit):
        raise ValueError("Link preparation requires exact identities and admission.")
    with work_transaction():
        transition = ProductionTransitionRequest.objects.get(pk=transition_id)
        previous = ProductionTokenPreparation.objects.filter(
            transition=transition, request_key=request_key
        ).first()
        if admit(transition, previous) is not True:
            raise PermissionError("Link preparation is not admitted.")
        current = current_inputs(transition)
        if previous is not None:
            if previous.actor_id != actor_id:
                raise PermissionError("Preparation intent belongs to another actor.")
            if (
                TokenPreparationInputs.retained(previous) != current
                or ProductionTokenCancellation.objects.filter(
                    preparation=previous
                ).exists()
            ):
                raise StaleRecordError("Preparation intent is no longer current.")
            return previous
        if not preparation_available(transition):
            raise StaleRecordError(
                "A link preparation is already in progress or needs disposal."
            )
        identifier = uuid4()
        task = enqueue(
            task_type=TASK_TYPE,
            domain_request_id=identifier,
            actor_id=actor_id,
            correlation_id=correlation_id,
            idempotency_key=identifier,
            admit=lambda action, status: action == "enqueue",
        )
        return ProductionTokenPreparation.objects.create(
            id=identifier,
            transition=transition,
            task_id=task.root_id,
            request_key=request_key,
            actor_id=actor_id,
            correlation_id=correlation_id,
            **asdict(current),
        )


def owned_preparation(status):
    """Require exact durable execution metadata, never a broker-supplied scope."""
    require_work_order()
    if (
        not isinstance(status, TaskStatus)
        or status.task_type != TASK_TYPE
        or not TaskRun.objects.filter(
            pk=status.run_id,
            root_id=status.root_id,
            task_type=TASK_TYPE,
            domain_request_id=status.domain_request_id,
            state=status.state,
            version=status.version,
            fence=status.fence,
            worker_id=status.worker_id,
        ).exists()
    ):
        raise PermissionError("Link preparation task ownership is unavailable.")
    preparation = (
        ProductionTokenPreparation.objects.filter(
            pk=status.domain_request_id, task_id=status.root_id
        )
        .select_related("transition")
        .first()
    )
    if preparation is None:
        raise PermissionError("Link preparation has no immutable task binding.")
    return preparation


def require_current(preparation):
    """Cancellation or changed inputs cannot silently rebind a retained request."""
    require_work_order()
    transition = ProductionTransitionRequest.objects.get(pk=preparation.transition_id)
    if ProductionTokenCancellation.objects.filter(
        preparation=preparation
    ).exists() or TokenPreparationInputs.retained(preparation) != current_inputs(
        transition
    ):
        raise StaleRecordError(
            "Link preparation inputs changed; prepare a new revision."
        )


def prepare_batch(preparation_id, claim, *, public, maximum=500):
    """Seal one bounded batch while holding the exact current task claim.

    The enclosing maintained execution owns work order and transaction lifetime.
    Existing generation primitives verify key/epoch/coverage and commit their
    checkpoint atomically with token rows. Final activation consumes only the
    completed manifest, not this per-Family operation.
    """
    require_work_order()
    if (
        not isinstance(preparation_id, UUID)
        or type(maximum) is not int
        or not 1 <= maximum <= 1000
    ):
        raise ValueError("Link preparation requires a bounded canonical batch.")
    task = lock_task_claim(claim)
    preparation = ProductionTokenPreparation.objects.select_related("transition").get(
        pk=preparation_id, task_id=task.root_id
    )
    if task.task_type != TASK_TYPE or task.domain_request_id != preparation.pk:
        raise PermissionError("Task claim belongs to another link preparation.")
    require_current(preparation)
    if inventory_digest(public) != preparation.key_inventory_digest:
        raise StaleRecordError("Mounted public token keys changed after preparation.")

    def admit(campaign, deployment, generation):
        """A nested primitive cannot retain admission after ownership/input changes."""
        lock_task_claim(claim)
        require_current(preparation)
        return (
            campaign.pk == preparation.transition.campaign_id
            and deployment.family_link_epoch == preparation.credential_epoch
            and (
                generation is None
                or (
                    generation.operation_id == preparation.pk
                    and generation.task_id == preparation.task_id
                )
            )
        )

    with token_claim(claim):
        generation = begin_generation(
            campaign_id=preparation.transition.campaign_id,
            operation_id=preparation.pk,
            source_snapshot_id=preparation.source_snapshot_id,
            source_generation=preparation.source_generation,
            public=public,
            actor_id=preparation.actor_id,
            task_id=preparation.task_id,
            admit=admit,
        )
        generation = prepare_generation_batch(
            generation_id=generation.pk, public=public, admit=admit, batch_size=maximum
        )
    lock_task_claim(claim)
    if generation.state == "ready" and not TokenPreparationInputs.retained(
        preparation
    ).covers(generation):
        raise StorageInvariantError("Prepared manifest differs from its pinned inputs.")
    return generation


def prepared_generation(preparation):
    """Find this operation's generation without following another task's output."""
    return FamilyAccessTokenGeneration.objects.filter(
        operation_id=preparation.pk,
        task_id=preparation.task_id,
        campaign_id=preparation.transition.campaign_id,
    ).first()

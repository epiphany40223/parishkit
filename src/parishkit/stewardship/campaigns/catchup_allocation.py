"""Atomic activation-to-task binding, without enumerating targets or sending mail."""

from uuid import UUID

from django.db.models import F

from parishkit.stewardship.jobs.storage import enqueue
from parishkit.stewardship.storage import StorageInvariantError

from .credential_models import FamilyAccessTokenGeneration
from .models import ActivationCatchUpDemand, CampaignTransition
from .work_locks import require_work_order

TASK_TYPE = "activation_catchup"


def allocate_activation(transition_id):
    """Bind only the demand just created by this locked activation transaction.

    The lifecycle trigger creates the demand and fixes its cutoff before this
    function runs. The task and source reference commit in that same outer
    transaction. Neither this function nor activation scans the Family corpus;
    the ordinary hint scanner will publish the committed canonical task.
    """
    require_work_order()
    if not isinstance(transition_id, UUID):
        raise TypeError("Catch-up allocation requires an immutable transition UUID.")
    transition = CampaignTransition.objects.get(pk=transition_id)
    if transition.action != "activate" or transition.after_state != "active":
        raise StorageInvariantError("Catch-up allocation requires direct activation.")
    demand = ActivationCatchUpDemand.objects.select_for_update().get(
        activation_id=transition.pk, campaign_id=transition.campaign_id
    )
    source = (
        FamilyAccessTokenGeneration.objects.filter(
            pk=transition.token_generation_id, campaign_id=transition.campaign_id
        )
        .values_list("source_snapshot_id", flat=True)
        .first()
    )
    if source is None:
        raise StorageInvariantError("Activation lacks its prepared source binding.")

    def admit(action, status):
        """Accept only this transition's root, not an arbitrary queue payload."""
        return (
            action == "enqueue"
            and status.task_type == TASK_TYPE
            and status.domain_request_id == demand.pk
            and status.root_id == status.run_id
            and demand.completed_at is None
            and demand.source_snapshot_id in (None, source)
            and demand.task_root_id in (None, status.root_id)
        )

    task = enqueue(
        task_type=TASK_TYPE,
        domain_request_id=demand.pk,
        actor_id=transition.actor_id,
        correlation_id=transition.correlation_id,
        idempotency_key=demand.pk,
        admit=admit,
    )
    if demand.task_root_id is None:
        ActivationCatchUpDemand.objects.filter(pk=demand.pk).update(
            task_root_id=task.root_id,
            source_snapshot_id=source,
            version=F("version") + 1,
        )
    return task

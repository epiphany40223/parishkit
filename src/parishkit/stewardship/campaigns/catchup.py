"""Bounded catch-up persistence; enumeration and external outcomes belong to BG-02."""

from uuid import UUID

from django.db.models import F

from parishkit.stewardship.storage import StorageInvariantError

from .models import ActivationCatchUpDemand, CatchUpCheckpoint
from .runtime import campaign_transaction


def bind_catchup(
    *, demand_id, task_root_id, source_snapshot_id, actor_id, correlation_id, admit
):
    """Bind durable worker inputs once, without changing activation's cutoff."""
    if not callable(admit) or any(
        not isinstance(value, UUID)
        for value in (
            demand_id,
            task_root_id,
            source_snapshot_id,
            actor_id,
        )
    ):
        raise TypeError("Catch-up binding requires attributed immutable identifiers.")
    demand = ActivationCatchUpDemand.objects.get(pk=demand_id)
    with campaign_transaction(demand.campaign_id, correlation_id=correlation_id) as (
        campaign,
        runtime,
    ):
        demand.refresh_from_db()
        admit("catchup_bind", campaign, runtime, demand)
        if demand.task_root_id is not None:
            if (demand.task_root_id, demand.source_snapshot_id) != (
                task_root_id,
                source_snapshot_id,
            ):
                raise StorageInvariantError("Catch-up inputs are already bound.")
            return demand
        ActivationCatchUpDemand.objects.filter(pk=demand.pk).update(
            task_root_id=task_root_id,
            source_snapshot_id=source_snapshot_id,
            version=F("version") + 1,
            actor_id=actor_id,
            correlation_id=correlation_id,
        )
        demand.refresh_from_db()
        return demand


def checkpoint_catchup(
    *,
    demand_id,
    group_key,
    cursor,
    items,
    phase,
    complete,
    task_id,
    fence,
    actor_id,
    correlation_id,
    admit,
):
    """Commit an exact group once; completion requires the owner's coverage proof.

    The callback must verify or insert the group's durable outcomes in this same
    transaction. It must raise on failure and never perform external delivery.
    A terminal TaskRun alone cannot release the catch-up hold.
    """
    if not callable(admit) or type(complete) is not bool:
        raise TypeError("Catch-up checkpoint requires an owning proof callback.")
    if type(items) is not int or items < 0 or type(fence) is not int or fence < 1:
        raise ValueError("Invalid catch-up progress or fence.")
    for text, maximum in ((group_key, 128), (cursor, 128), (phase, 32)):
        if type(text) is not str or not text or len(text) > maximum:
            raise ValueError("Invalid catch-up cursor metadata.")
    for value in (demand_id, task_id, actor_id):
        if not isinstance(value, UUID):
            raise TypeError("Catch-up identifiers must be UUIDs.")
    demand = ActivationCatchUpDemand.objects.get(pk=demand_id)
    with campaign_transaction(demand.campaign_id, correlation_id=correlation_id) as (
        campaign,
        runtime,
    ):
        demand.refresh_from_db()
        admit("catchup_checkpoint", campaign, runtime, demand)
        existing = CatchUpCheckpoint.objects.filter(
            demand=demand, group_key=group_key
        ).first()
        if existing:
            if (existing.cursor, existing.items, existing.phase, existing.complete) != (
                cursor,
                items,
                phase,
                complete,
            ):
                raise StorageInvariantError("Catch-up group key has different intent.")
            return existing
        return CatchUpCheckpoint.objects.create(
            demand=demand,
            sequence=demand.groups_completed + 1,
            group_key=group_key,
            cursor=cursor,
            items=items,
            phase=phase,
            complete=complete,
            task_id=task_id,
            fence=fence,
            actor_id=actor_id,
            correlation_id=correlation_id,
        )

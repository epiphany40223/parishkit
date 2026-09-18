"""Explicit Admin manual-report commands, never an automatic resend policy."""

from uuid import UUID

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.delivery_admin import authorize
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.storage import _status, enqueue

from .weekly_models import WeeklyManualRequest
from .weekly_ownership import TASK_TYPE


def request_manual_report(store, user_id, campaign_id, *, command_id, configuration_id):
    """Record one actor-bound command and atomically allocate its private owner.

    SQL derives the current schedule/mode/epoch, rejects unresolved prior work,
    creates an independent manual occurrence and begins at capture. Replays
    return the same root without recapturing data or opening another delivery.
    """
    if any(
        not isinstance(value, UUID)
        for value in (
            user_id,
            campaign_id,
            command_id,
            configuration_id,
        )
    ):
        raise ValueError("Manual reports require canonical identities.")
    with work_transaction():
        authorize(store, user_id)
        previous = WeeklyManualRequest.objects.filter(pk=command_id).first()
        if previous is not None:
            if (
                previous.actor_id != user_id
                or previous.campaign_id != campaign_id
                or previous.configuration_id != configuration_id
            ):
                raise ValueError("Manual report command is already bound.")
            return _status(TaskRun.objects.get(pk=previous.task_id))
        task = enqueue(
            task_type=TASK_TYPE,
            domain_request_id=command_id,
            actor_id=user_id,
            correlation_id=command_id,
            idempotency_key=command_id,
            admit=lambda *args: True,
        )
        WeeklyManualRequest.objects.create(
            id=command_id,
            campaign_id=campaign_id,
            configuration_id=configuration_id,
            task_id=task.run_id,
            actor_id=user_id,
            correlation_id=command_id,
        )
        return task

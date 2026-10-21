"""Send now: one Administrator's explicit send of a report email (#1786).

The report-emails spec's Send now (delivery plan item 5 adds the page
action and ``pk-admin report-email send-now``) records one request, keyed
by its command id; its database guard checks the report's current revision
and that no other send of it is open, and allocates the manual occurrence
and its preparation together, so replaying the command returns the same
send. Nothing calls this until that item ships.
"""

from uuid import UUID

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.delivery_admin import authorize
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.storage import _status, enqueue

from .report_email_models import ReportEmailManualRequest
from .report_email_ownership import TASK_TYPE


def request_send_now(
    store,
    user_id,
    campaign_id,
    definition_id,
    revision_id,
    *,
    command_id,
    configuration_id,
):
    """Record one actor-bound Send now and allocate its preparation's task.

    ``revision_id`` is the report revision the confirmation named; a
    changed report refuses the request in the database as stale.
    """
    identities = (
        user_id,
        campaign_id,
        definition_id,
        revision_id,
        command_id,
        configuration_id,
    )
    if any(not isinstance(value, UUID) for value in identities):
        raise ValueError("Send now requires canonical identities.")
    with work_transaction():
        authorize(store, user_id)
        previous = ReportEmailManualRequest.objects.filter(pk=command_id).first()
        if previous is not None:
            if (
                previous.actor_id,
                previous.campaign_id,
                previous.definition_id,
                previous.revision_id,
                previous.configuration_id,
            ) != (user_id, campaign_id, definition_id, revision_id, configuration_id):
                raise ValueError("Send now command is already bound.")
            return _status(TaskRun.objects.get(pk=previous.task_id))
        task = enqueue(
            task_type=TASK_TYPE,
            domain_request_id=command_id,
            actor_id=user_id,
            correlation_id=command_id,
            idempotency_key=command_id,
            admit=lambda *args: True,
        )
        ReportEmailManualRequest.objects.create(
            id=command_id,
            campaign_id=campaign_id,
            definition_id=definition_id,
            revision_id=revision_id,
            configuration_id=configuration_id,
            task_id=task.run_id,
            actor_id=user_id,
            correlation_id=command_id,
        )
        return task

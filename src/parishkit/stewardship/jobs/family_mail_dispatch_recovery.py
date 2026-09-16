"""Abandoned SMTP attempts become durable uncertainty, never an automatic resend."""

from uuid import uuid4

from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.storage import StorageInvariantError

from .delivery_states import DeliveryAction
from .family_mail_dispatch import bound_dispatch
from .outbox_storage import change_message
from .outbox_validation import DeliveryEvidence
from .ownership import database_now


def record_abandoned_submission(status, *, actor_id):
    """Recover only after Task abandonment and the finite provider deadline.

    This is called on the isolated MAIL consumer under recovery's work/root
    locks. Its fresh service-operation identity distinguishes reconciliation
    from the original worker, which can no longer report a fenced outcome.
    """
    require_work_order()
    row = bound_dispatch(status)
    if (
        status.state != "abandoned"
        or row.state != "submitting"
        or row.provider_deadline > database_now()
    ):
        raise PermissionError("Family provider recovery is not yet admitted.")

    def admit(action, identity, current, proposal):
        actual = bound_dispatch(status)
        return action is DeliveryAction.MARK_UNKNOWN and actual.version == row.version

    result = change_message(
        message_id=row.pk,
        action=DeliveryAction.MARK_UNKNOWN,
        command_id=uuid4(),
        expected_version=row.version,
        actor_id=actor_id,
        correlation_id=status.run_id,
        evidence=DeliveryEvidence(reason="recovery_unknown"),
        admit=admit,
    )
    occurrence = ScheduleOccurrence.objects.get(pk=row.semantic_key)
    updated = ScheduleOccurrence.objects.filter(
        pk=occurrence.pk, version=occurrence.version
    ).update(
        state="delivery_unknown",
        reason="recovery_unknown",
        lease_expires_at=None,
        actor_id=actor_id,
        correlation_id=status.run_id,
        version=occurrence.version + 1,
    )
    if updated != 1:
        raise StorageInvariantError("Family recovery lost its occurrence version.")
    return result

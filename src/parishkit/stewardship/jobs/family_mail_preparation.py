"""Atomically prepare the selected current Family occurrence in the durable outbox."""

from uuid import UUID

from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.family_schedule_planning import (
    _planning_scope,
    plan_family,
)
from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.storage import StorageInvariantError

from .family_mail_credentials import seal_current_credentials
from .family_mail_inputs import load_family_mail_source
from .family_mail_rendering import current_render
from .family_mail_tasks import _row, disposition, owned_preparation
from .outbox_storage import create_message
from .outbox_validation import DeliveryIdentity
from .ownership import database_now, lock_task_claim
from .storage import _status


def prepare_occurrence(ticket, claim, *, general, mac, public, public_origin):
    """No intermediate running occurrence survives a failed preparation transaction.

    Planning re-evaluates the complete overdue Family group before rendering.
    The final pending occurrence points to its outbox and completed preparation
    task; only a later admitted dispatcher may begin provider work.
    """
    require_work_order()
    task = lock_task_claim(claim)
    if (
        owned_preparation(_status(task)).pk != ticket.pk
        or disposition(ticket) is not None
    ):
        raise PermissionError("Family preparation is not currently admitted.")
    row = _row(ticket.occurrence_id)
    try:
        family_id = UUID(row.target.removeprefix("family:"))
    except (ValueError, AttributeError):
        raise StorageInvariantError("Family occurrence target is invalid.") from None
    if row.target != f"family:{family_id}":
        raise StorageInvariantError("Family occurrence target is invalid.")
    decision = plan_family(claim, family_id=family_id, worker_id=claim.worker_id)
    if decision.held:
        raise PermissionError("Family preparation is held by existing work.")
    if decision.selected != row.pk:
        # Planning records skips/coalescing before acknowledging this old hint.
        if disposition(ticket) != "safe_cancel":
            raise PermissionError("Family preparation selection changed.")
        return "safe_cancel"
    row.refresh_from_db()
    scope, epoch = _planning_scope(row.definition.campaign_id)
    if (None if epoch is None else epoch.pk) != ticket.rehearsal_epoch_id:
        raise PermissionError("Family preparation epoch changed.")
    family = FamilyCampaign.objects.get(pk=family_id, campaign=scope.campaign)
    source = load_family_mail_source(family)
    if not source.recipients.status.email_deliverable:
        raise PermissionError("Family recipients require current reconciliation.")
    if ticket.mode == "testing":
        from parishkit.stewardship.campaigns.lifecycle import CampaignWorkKind
        from parishkit.stewardship.campaigns.rehearsals import prepare_rehearsals

        def admit_credentials(campaign, purpose):
            """Provision only the already-bound epoch under this live claim."""
            lock_task_claim(claim)
            return (
                campaign.pk == scope.campaign.pk
                and purpose is CampaignWorkKind.REHEARSAL
                and disposition(ticket) is None
            )

        prepare_rehearsals(
            campaign_id=scope.campaign.pk,
            family_ids=[family.pk],
            general=general,
            mac=mac,
            public=public,
            purpose=CampaignWorkKind.REHEARSAL,
            admit=admit_credentials,
            actor_id=claim.worker_id,
            correlation_id=claim.run_id,
        )
    identity = DeliveryIdentity(
        scope_id=scope.campaign.pk,
        campaign_id=scope.campaign.pk,
        semantic_key=row.pk,
        family_id=family.pk,
        mode=ticket.mode,
        routing=row.routing,
        purpose=row.definition.kind,
        credential_namespace="production"
        if ticket.mode == "production"
        else "rehearsal",
        rehearsal_epoch_id=ticket.rehearsal_epoch_id,
    )
    render = current_render(
        identity,
        UUID(row.revision.values["template_version"]),
        scope,
        source,
        public_origin=public_origin,
    )
    sealed = seal_current_credentials(
        identity=identity,
        render=render,
        campaign=scope.campaign,
        family=family,
        general=general,
        public=public,
    )
    task = lock_task_claim(claim)
    updated = ScheduleOccurrence.objects.filter(pk=row.pk, version=row.version).update(
        state="running",
        task_id=claim.run_id,
        worker_id=claim.worker_id,
        fence=claim.fence,
        attempts=row.attempts + 1,
        lease_expires_at=task.lease_expires_at,
        heartbeat_at=database_now(),
        version=row.version + 1,
        actor_id=claim.worker_id,
        correlation_id=claim.run_id,
    )
    if updated != 1:
        raise StorageInvariantError("Family preparation lost its occurrence version.")

    def admit(action, candidate, status):
        """Allocation is allowed only under this exact live local-preparation claim."""
        lock_task_claim(claim)
        return (
            action in {"create", "create_task"}
            and candidate == identity
            and disposition(ticket) is None
        )

    message = create_message(
        identity=identity,
        render=render,
        sealed=sealed,
        actor_id=claim.worker_id,
        correlation_id=claim.run_id,
        command_id=ticket.pk,
        admit=admit,
    )
    lock_task_claim(claim)
    updated = ScheduleOccurrence.objects.filter(
        pk=row.pk, version=row.version + 1
    ).update(
        state="pending",
        outbox_id=message.message_id,
        lease_expires_at=None,
        reason="prepared",
        version=row.version + 2,
        actor_id=claim.worker_id,
        correlation_id=claim.run_id,
    )
    if updated != 1:
        raise StorageInvariantError("Family preparation lost its completion binding.")
    return "complete"

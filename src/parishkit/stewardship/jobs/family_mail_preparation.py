"""Atomically prepare the selected current Family occurrence in the durable outbox."""

from uuid import UUID

from django.db import connection

from parishkit.stewardship.accounts.cryptography import CryptographicError
from parishkit.stewardship.campaigns.credential_keys import key_set_lock
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.family_schedule_planning import (
    _planning_scope,
    plan_family,
)
from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.storage import StorageInvariantError

from .family_mail_builds import fingerprint
from .family_mail_credentials import seal_current_credentials
from .family_mail_inputs import load_family_mail_source
from .family_mail_rendering import current_render
from .family_mail_tasks import _row, disposition, owned_preparation
from .outbox_storage import create_message
from .outbox_validation import DeliveryIdentity
from .ownership import database_now, lock_task_claim
from .storage import _status


def prepare_occurrence(
    ticket,
    claim,
    *,
    general,
    mac,
    public,
    public_origin,
    prebuilt=None,
    report=None,
):
    """No intermediate running occurrence survives a failed preparation transaction.

    Planning re-evaluates the complete overdue Family group before rendering.
    The final pending occurrence points to its outbox and completed preparation
    task; only a later admitted dispatcher may begin provider work.

    ``prebuilt`` is a bulk build of this item made outside the lock
    (``family_mail_builds``, BG-12). After the same claim, ``disposition``
    and planning, its fingerprint is compared in one query: on a match its
    render and sealed credentials are written, through the same owners and
    guards; otherwise the item is read, rendered and sealed here as before.
    ``report(True)`` or ``report(False)`` then says which, for the batch's
    timing line. A reminder prepared before its due time gets a delivery
    task that waits for that time (``_delivery_not_before``).
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
    if prebuilt is not None and _current(
        prebuilt, ticket, identity, family, general=general, public=public
    ):
        render, sealed = prebuilt.render, prebuilt.sealed
    else:
        render, sealed = _build_here(
            ticket,
            claim,
            row,
            scope,
            family,
            identity,
            general=general,
            mac=mac,
            public=public,
            public_origin=public_origin,
        )
    if prebuilt is not None and report is not None:
        report(render is prebuilt.render)
    return _write(ticket, claim, row, identity, render, sealed)


def _current(prebuilt, ticket, identity, family, *, general, public):
    """Whether a bulk build may be written as is, checked under the lock.

    It must be this Production item's build, and everything it was built
    from must be unchanged: its fingerprint, read in one query in this
    transaction, still equals the build's (``family_mail_builds``).

    The credential key-set lock is taken first, shared and non-waiting with
    its inventory check, as sealing under the lock takes it. It is a
    transaction lock, so it is held from before the fingerprint check
    through ``create_message`` to the batch's commit: no key rotation can
    commit between the check and the write. If it is busy (a rotation in
    progress) or the inventory is not current, the build is not used and
    the item is rebuilt under the lock, which refuses it as before.
    """
    if (
        ticket.mode != "production"
        or prebuilt.occurrence_id != identity.semantic_key
        or prebuilt.identity != identity
    ):
        return False
    try:
        with key_set_lock(general, public):
            pass
    except CryptographicError:
        return False
    return fingerprint(identity.semantic_key, family.pk) == prebuilt.fingerprint


def _build_here(
    ticket, claim, row, scope, family, identity, *, general, mac, public, public_origin
):
    """Read, render and seal one item under the work-order lock, as always.

    Returns ``(render, sealed)``. A Testing item first writes its Family's
    rehearsal credential under this claim.
    """
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
    return render, sealed


def _delivery_not_before(row):
    """When the message's delivery task may first be claimed, or None for now.

    None for an occurrence already due, so its task is enqueued exactly as
    before. A reminder prepared ahead (BG-12) waits for its due time, so the
    mail consumers neither claim it early nor hold it in a retry loop; the
    dispatch guard refuses to send it before then in any case. The task
    clock is ``statement_timestamp()`` and so is the campaign clock in a
    deployment, so the result is the due time itself; the database tests
    move only the campaign clock, and the time left is carried over.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT CASE WHEN %s > stewardship_campaign_now_v1() "
            "THEN statement_timestamp() + (%s - stewardship_campaign_now_v1()) END",
            [row.due_at, row.due_at],
        )
        return cursor.fetchone()[0]


def _write(ticket, claim, row, identity, render, sealed):
    """Write the prepared message and its occurrence binding under the claim.

    The same rows, owners and guards whether the message was built here or
    outside the lock: the occurrence moves to running under this claim, the
    message, its task and first render are created, and the occurrence
    returns to pending, bound to the message.
    """
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
        not_before=_delivery_not_before(row),
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

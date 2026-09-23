"""General-worker preparation of chosen-Family Testing sends; no provider I/O.

An Administrator's ticket names one Family, template and rehearsal epoch. The
worker issues (or reuses) that Family's Testing credential, renders the real
template with the Testing banner, seals the code and link reference, and
allocates the outbox message under the ticket's own semantic key. Nothing here
touches schedule occurrences or fulfillment; dispatch is the shared MAIL owner.
"""

from django.db import connection
from django.db.models import F

from parishkit.stewardship.accounts.content_models import ContentVersion
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    FamilyCampaign,
    RehearsalEpoch,
)
from parishkit.stewardship.campaigns.models import CampaignWorkGate
from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.storage import StorageInvariantError

from .admission import _scope
from .dispatch import Handler, RecoveryPlan
from .family_mail_models import FamilyMailTest
from .models import TaskRun
from .queues import WorkQueue
from .storage import TaskStatus, _status

TASK_TYPE = "family_mail_test"
MAX_ATTEMPTS = 5


def owned_test(status):
    """A queue hint cannot substitute another ticket or replay a stale task view."""
    require_work_order()
    if not isinstance(status, TaskStatus) or status.task_type != TASK_TYPE:
        raise PermissionError("Family test ownership is unavailable.")
    if not TaskRun.objects.filter(
        pk=status.run_id,
        root_id=status.root_id,
        task_type=TASK_TYPE,
        domain_request_id=status.domain_request_id,
        state=status.state,
        version=status.version,
        fence=status.fence,
        worker_id=status.worker_id,
    ).exists():
        raise PermissionError("Family test ownership is unavailable.")
    return FamilyMailTest.objects.get(
        pk=status.domain_request_id, task_id=status.root_id
    )


def disposition(ticket):
    """Return completion/cancellation proof, or None while the ticket is still live.

    Mode, campaign, configuration and epoch are bound at intake and cannot
    change back; any of them moving cancels the task. Temporary gates (restore,
    go-live cleanup, purge) are checked by preparation itself, which refuses
    and leaves the task claimed for ordinary lease expiry and retry.
    """
    require_work_order()
    if ticket.state == "prepared":
        return "complete"
    if ticket.state != "queued":
        return "safe_cancel"
    runtime = SystemConfiguration.objects.get()
    population = CampaignCredentialState.objects.filter(
        campaign_id=ticket.campaign_id
    ).first()
    if (
        runtime.mode != "testing"
        or runtime.current_campaign_id != ticket.campaign_id
        or runtime.active_configuration_id != ticket.configuration_id
        or population is None
        or population.rehearsal_epoch_id != ticket.rehearsal_epoch_id
    ):
        return "safe_cancel"
    return None


def prepare_family_test(ticket, claim, *, general, mac, public, public_origin):
    """Credential, render, seal and outbox allocation commit together or not at all.

    Follows scheduled preparation minus every occurrence step. The ticket's
    Family link is scrubbed in the same transaction; only the outbox message,
    which Testing cleanup deletes, keeps the Family.
    """
    from parishkit.stewardship.campaigns.lifecycle import CampaignWorkKind
    from parishkit.stewardship.campaigns.rehearsals import prepare_rehearsals

    from .family_mail_credentials import seal_current_credentials
    from .family_mail_inputs import load_family_mail_source
    from .family_mail_rendering import current_render
    from .outbox_storage import create_message
    from .outbox_validation import DeliveryIdentity
    from .ownership import lock_task_claim

    require_work_order()
    task = lock_task_claim(claim)
    if owned_test(_status(task)).pk != ticket.pk or disposition(ticket) is not None:
        raise PermissionError("Family test preparation is not currently admitted.")
    scope = _scope(ticket.campaign_id)
    campaign, runtime = scope.campaign, scope.runtime
    population = CampaignCredentialState.objects.select_for_update().get(
        campaign=campaign
    )
    if (
        campaign.state != "draft"
        or runtime.restore_review_required
        or population.go_live_gate
        or CampaignWorkGate.objects.filter(campaign=campaign)
        .exclude(state="released")
        .exists()
        or not RehearsalEpoch.objects.filter(
            pk=ticket.rehearsal_epoch_id, campaign=campaign, state="active"
        ).exists()
    ):
        raise PermissionError("Family test preparation is held.")
    family = FamilyCampaign.objects.get(pk=ticket.family_id, campaign=campaign)
    source = load_family_mail_source(family)
    if not source.recipients.status.email_deliverable:
        raise PermissionError("Family recipients require current reconciliation.")

    def admit_credentials(candidate, purpose):
        """Issue a credential only for this campaign under this exact live claim."""
        lock_task_claim(claim)
        return (
            candidate.pk == campaign.pk
            and purpose is CampaignWorkKind.READINESS_TEST
            and disposition(ticket) is None
        )

    # The readiness-test purpose is not date-gated; an existing credential for
    # this epoch is reused, so staff and the real Testing invitation share it.
    prepare_rehearsals(
        campaign_id=campaign.pk,
        family_ids=[family.pk],
        general=general,
        mac=mac,
        public=public,
        purpose=CampaignWorkKind.READINESS_TEST,
        admit=admit_credentials,
        actor_id=claim.worker_id,
        correlation_id=claim.run_id,
    )
    identity = DeliveryIdentity(
        scope_id=campaign.pk,
        campaign_id=campaign.pk,
        semantic_key=ticket.pk,
        family_id=family.pk,
        mode="testing",
        routing="testing_override",
        purpose="family_test",
        credential_namespace="rehearsal",
        rehearsal_epoch_id=ticket.rehearsal_epoch_id,
    )
    template_record = ContentVersion.objects.values_list("record_id", flat=True).get(
        pk=ticket.template_id
    )
    render = current_render(
        identity, template_record, scope, source, public_origin=public_origin
    )
    sealed = seal_current_credentials(
        identity=identity,
        render=render,
        campaign=campaign,
        family=family,
        general=general,
        public=public,
    )

    def admit(action, candidate, status):
        """Allocation is allowed only under this exact live ticket claim."""
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
    updated = FamilyMailTest.objects.filter(
        pk=ticket.pk, version=ticket.version, state="queued"
    ).update(
        state="prepared",
        family_id=None,
        outbox_id=message.message_id,
        version=ticket.version + 1,
        actor_id=claim.worker_id,
        correlation_id=claim.run_id,
    )
    if updated != 1:
        raise StorageInvariantError("Family test lost its completion binding.")
    return "complete"


def admit_test(action, status):
    """All mutations recheck the exact ticket, terminal receipt and current scope."""
    ticket = owned_test(status)
    if action in {"lease_expired", "recovery_hint"}:
        return True
    terminal = disposition(ticket)
    if action in {"complete", "recovery_complete"}:
        return terminal == "complete"
    if action in {"safe_cancel", "recovery_cancel"}:
        return terminal == "safe_cancel"
    if action in {"recovery_retry", "recovery_fail"}:
        plan = recover_test(status)
        return plan is not None and plan.action == action
    return action in {"hint", "claim", "effect", "heartbeat", "progress"}


def recover_test(status):
    """Only a committed receipt completes abandoned work; no provider is involved."""
    if status.state != "abandoned":
        raise PermissionError("Family test recovery requires abandoned work.")
    terminal = disposition(owned_test(status))
    if terminal is not None:
        return RecoveryPlan(
            "recovery_complete" if terminal == "complete" else "recovery_cancel"
        )
    return (
        RecoveryPlan("recovery_fail")
        if status.attempt >= MAX_ATTEMPTS
        else RecoveryPlan(
            "recovery_retry", min(30 * 2 ** max(status.attempt - 1, 0), 600)
        )
    )


def family_test_handler(
    *, scheduler=False, general=None, mac=None, public=None, public_origin=None
):
    """Only the general worker may issue credentials and render Family content."""
    if type(scheduler) is not bool:
        raise TypeError("Family tests require a compiled service role.")
    if not scheduler and any(
        value is None for value in (general, mac, public, public_origin)
    ):
        raise TypeError("Family tests require admitted runtime dependencies.")

    def execute(execution):
        """Commit local preparation before separately acknowledging its receipt."""
        if scheduler:
            raise PermissionError("The scheduler cannot prepare Family tests.")
        from .ownership import lock_task_claim

        with execution.effect():
            ticket = owned_test(_status(lock_task_claim(execution.claim)))
            terminal = disposition(ticket)
            if terminal is None:
                terminal = prepare_family_test(
                    ticket,
                    execution.claim,
                    general=general,
                    mac=mac,
                    public=public,
                    public_origin=public_origin,
                )
        execution.transition(terminal)

    return Handler(
        WorkQueue.GENERAL,
        admit_test,
        execute,
        recover=recover_test,
        scope=work_transaction,
    )


def recover_pending():
    """Scheduler sweep: settle queued tickets whose task ended or scope went stale.

    A ticket whose Admin, configuration, Testing mode or epoch is no longer live
    is cancelled; one whose task failed without a message is marked failed. The
    Family link is scrubbed either way. Prepared tickets belong to the outbox.
    """
    with work_transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT q.id,public.stewardship_family_test_live_v1(q.configuration_id,"
                "q.campaign_id,q.template_id,q.requested_by_id,q.rehearsal_epoch_id) "
                "FROM public.stewardship_family_mail_test q "
                "WHERE q.state='queued' AND (NOT "
                "public.stewardship_family_test_live_v1(q.configuration_id,"
                "q.campaign_id,q.template_id,q.requested_by_id,q.rehearsal_epoch_id) "
                "OR EXISTS (SELECT 1 FROM public.stewardship_task_run original "
                "WHERE original.id=q.task_id "
                "AND original.state IN ('failed','cancelled'))) "
                "ORDER BY q.created_at,q.id LIMIT 100 FOR UPDATE OF q"
            )
            found = cursor.fetchall()
        for identifier, live in found:
            FamilyMailTest.objects.filter(pk=identifier, state="queued").update(
                state="failed" if live else "cancelled",
                family_id=None,
                actor_id=None,
                correlation_id=current_correlation(),
                version=F("version") + 1,
            )
        return len(found)

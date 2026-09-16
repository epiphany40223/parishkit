"""Epoch-bound, provider-free preparation ownership and crash reconciliation.

Tickets contain only metadata. A committed occurrence/outbox binding is the
completion receipt; a crash before it rolls back every local effect. Dispatch
has its own later owner and cannot infer permission from preparation success.
"""

from uuid import UUID, uuid4, uuid5

from parishkit.stewardship.campaigns.family_schedule_planning import _planning_scope
from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.storage import StorageInvariantError

from .dispatch import Handler, RecoveryPlan
from .family_mail_models import FamilyMailPreparation
from .models import TaskRun
from .queues import WorkQueue
from .scheduler import SchedulerGuard
from .storage import TaskStatus, enqueue

TASK_TYPE = "family_mail_prepare"


def _row(identifier):
    """Load the selected revision without implicitly taking unrelated row locks."""
    return (
        ScheduleOccurrence.objects.select_related("definition", "revision")
        .filter(pk=identifier, definition__kind__in=("initial", "reminder"))
        .first()
    )


def enqueue_preparation(guard, occurrence_id):
    """Bind the original mode/epoch once; repeat scheduling reuses its root."""
    if not isinstance(guard, SchedulerGuard) or not isinstance(occurrence_id, UUID):
        raise TypeError("Family preparation requires owned schedule production.")
    guard.check()
    with work_transaction():
        row = _row(occurrence_id)
        if row is None or row.state != "pending" or row.outbox_id is not None:
            return None
        scope, epoch = _planning_scope(row.definition.campaign_id)
        if (
            row.mode != scope.runtime.mode
            or row.revision_id != row.definition.current_revision_id
        ):
            return None
        epoch_id = None if epoch is None else epoch.pk
        identifier = uuid5(row.pk, "family-mail-prepare:" + str(epoch_id))
        previous = FamilyMailPreparation.objects.filter(pk=identifier).first()
        if previous is not None:
            return previous

        def admit(action, status):
            """Only this selected occurrence's metadata task may be allocated."""
            guard.check()
            return (
                action == "enqueue"
                and status.task_type == TASK_TYPE
                and status.domain_request_id == identifier
            )

        task = enqueue(
            task_type=TASK_TYPE,
            domain_request_id=identifier,
            actor_id=None,
            correlation_id=row.pk,
            idempotency_key=identifier,
            admit=admit,
        )
        result = FamilyMailPreparation.objects.create(
            id=identifier,
            occurrence_id=row.pk,
            task_id=task.root_id,
            mode=row.mode,
            rehearsal_epoch_id=epoch_id,
            correlation_id=row.pk,
        )
        guard.check()
        return result


def owned_preparation(status):
    """A queue hint cannot substitute another ticket or replay a stale task view."""
    require_work_order()
    if not isinstance(status, TaskStatus) or status.task_type != TASK_TYPE:
        raise PermissionError("Family preparation ownership is unavailable.")
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
        raise PermissionError("Family preparation ownership is unavailable.")
    return FamilyMailPreparation.objects.get(
        pk=status.domain_request_id, task_id=status.root_id
    )


def disposition(ticket, *, source_check=False):
    """Return completion/cancellation proof, or hold until live scope resumes."""
    require_work_order()
    row = _row(ticket.occurrence_id)
    if row is None or row.state in {"skipped", "coalesced"}:
        return "safe_cancel"
    if row.outbox_id is not None:
        if (
            row.task_id is not None
            and TaskRun.objects.filter(pk=row.task_id, root_id=ticket.task_id).exists()
        ):
            return "complete"
        return "safe_cancel"
    if row.revision_id != row.definition.current_revision_id:
        return "safe_cancel"
    # A pause/gate is a hold, not a cancellation. Mode and epoch changes are
    # irreversible for this ticket and must be checked before ordinary holds.
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
    from parishkit.stewardship.campaigns.credential_models import (
        CampaignCredentialState,
    )

    runtime = SystemConfiguration.objects.get()
    population = CampaignCredentialState.objects.filter(
        campaign_id=row.definition.campaign_id
    ).first()
    if (
        runtime.current_campaign_id != row.definition.campaign_id
        or runtime.mode != ticket.mode
        or (
            ticket.mode == "testing"
            and (
                population is None
                or population.rehearsal_epoch_id != ticket.rehearsal_epoch_id
            )
        )
    ):
        return "safe_cancel"
    scope, _ = _planning_scope(row.definition.campaign_id)
    if ticket.mode == "production" and scope.campaign.delivery_paused:
        raise PermissionError("Family preparation is paused.")
    if not source_check:
        # Source checks belong at claim, recovery and the actual preparation.
        # Other metadata actions reuse lifecycle checks without reloading PII;
        # explicit Admin retry also need not grant web private snapshot access.
        return None
    # Source promotion and refusal reconciliation are holds, not failed render
    # attempts. Admission and recovery must observe them before claiming work.
    from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
    from parishkit.stewardship.source.snapshot_models import SourceCurrent

    from .family_mail_inputs import load_family_mail_source

    try:
        family_id = UUID(row.target.removeprefix("family:"))
    except (ValueError, AttributeError):
        raise StorageInvariantError("Family occurrence target is invalid.") from None
    if row.target != f"family:{family_id}":
        raise StorageInvariantError("Family occurrence target is invalid.")
    family = FamilyCampaign.objects.get(pk=family_id, campaign=scope.campaign)
    if (
        population.population_dirty
        or family.source_generation != population.source_generation
        or not SourceCurrent.objects.filter(
            snapshot_id=population.source_snapshot_id,
            generation=population.source_generation,
        ).exists()
    ):
        raise PermissionError("Family mail requires current source reconciliation.")
    # Reconciliation retains absent/ineligible identities, not their source row.
    # Admit their metadata task so the planner can durably record its normal skip.
    if not family.active or not family.email_eligible:
        return None
    source = load_family_mail_source(family)
    if family.email_deliverable != source.recipients.status.email_deliverable:
        raise PermissionError("Family recipients require current reconciliation.")
    return None


def retry_preparation(store, user_id, ticket_id, *, command_id):
    """An Admin may retry corrected local preparation without replacing its ticket.

    The bounded automatic retry budget remains intact. Like report-job retries,
    this owning service is independent of the later background-job Admin UI.
    Every call reloads current authorization. An existing command returns its
    original status even after completion; only a new allocation needs current
    lifecycle scope. Neither path resurrects a superseded epoch or delivered mail.
    """
    from parishkit.stewardship.accounts.policy import (
        Capability,
        allows,
        current_principal,
    )

    from .storage import retry_failed

    with work_transaction():
        if not allows(current_principal(store, user_id), Capability.BACKGROUND_WORK):
            raise PermissionError("Family preparation retry requires an Administrator.")
        ticket = FamilyMailPreparation.objects.get(pk=ticket_id)
        runs = TaskRun.objects.filter(root_id=ticket.task_id)
        previous = runs.filter(retry_command_id=command_id).first()
        if previous is not None:
            # Returning an existing result grants no new execution authority.
            # Check its original actor even after completion or scope closure.
            if previous.initiated_by_id != user_id:
                raise ValueError("Task retry command is already bound.")
            from .storage import _status

            return _status(previous)
        if disposition(ticket) is not None:
            raise PermissionError("Family preparation is no longer retryable.")
        latest = runs.order_by("-retry_sequence").first()
        return retry_failed(
            run_id=latest.pk,
            command_id=command_id,
            actor_id=user_id,
            correlation_id=uuid4(),
            admit=admit_preparation,
        )


def admit_preparation(action, status):
    """All mutations recheck the exact ticket, terminal receipt and current scope."""
    ticket = owned_preparation(status)
    if action in {"lease_expired", "recovery_hint"}:
        return True
    terminal = disposition(ticket, source_check=action in {"hint", "claim"})
    if action in {"complete", "recovery_complete"}:
        return terminal == "complete"
    if action in {"safe_cancel", "recovery_cancel"}:
        return terminal == "safe_cancel"
    if action in {"recovery_retry", "recovery_fail"}:
        plan = recover_preparation(status)
        return plan is not None and plan.action == action
    if action not in {
        "hint",
        "claim",
        "effect",
        "heartbeat",
        "progress",
        "explicit_retry",
        "explicit_retry_replay",
    }:
        return False
    return terminal is None or action in {
        "hint",
        "claim",
        "effect",
        "heartbeat",
        "progress",
    }


def recover_preparation(status):
    """Only a committed receipt completes abandoned work; no provider is involved."""
    if status.state != "abandoned":
        raise PermissionError("Preparation recovery requires abandoned work.")
    try:
        terminal = disposition(owned_preparation(status), source_check=True)
    except PermissionError:
        return None
    if terminal is not None:
        return RecoveryPlan(
            "recovery_complete" if terminal == "complete" else "recovery_cancel"
        )
    return (
        RecoveryPlan("recovery_fail")
        if status.attempt >= 5
        else RecoveryPlan(
            "recovery_retry", min(30 * 2 ** max(status.attempt - 1, 0), 600)
        )
    )


def preparation_handler(
    *, scheduler=False, general=None, mac=None, public=None, public_origin=None
):
    """Only the general worker may prepare content and decrypt manual codes."""
    if type(scheduler) is not bool:
        raise TypeError("Preparation requires a compiled service role.")
    if not scheduler and any(
        value is None for value in (general, mac, public, public_origin)
    ):
        raise TypeError("Preparation requires admitted runtime dependencies.")

    def execute(execution):
        """Commit local preparation before separately acknowledging its receipt."""
        if scheduler:
            raise PermissionError("The scheduler cannot prepare Family content.")
        from .family_mail_preparation import prepare_occurrence
        from .ownership import lock_task_claim
        from .storage import _status

        with execution.effect():
            ticket = owned_preparation(_status(lock_task_claim(execution.claim)))
            terminal = disposition(ticket)
            if terminal is None:
                terminal = prepare_occurrence(
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
        admit_preparation,
        execute,
        recover=recover_preparation,
        scope=work_transaction,
    )

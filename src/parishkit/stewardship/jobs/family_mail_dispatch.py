"""Commit-before-send Family outbox transactions and truthful outcome settlement."""

from uuid import uuid4

from django.db import connection

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
)
from parishkit.stewardship.campaigns.family_schedule_planning import (
    _planning_scope,
    plan_family,
)
from parishkit.stewardship.campaigns.models import RestoreDeliveryHold
from parishkit.stewardship.campaigns.schedule_models import (
    ScheduleFulfillment,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.family_delivery import (
    FamilyDeliveryResult,
    FamilyDeliveryStatus,
)
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.storage import StorageInvariantError

from .admission import _scope
from .delivery_states import DeliveryAction
from .family_dispatch_grants import METADATA_FIELDS
from .family_mail_results import result_evidence
from .models import TaskRun
from .outbox_models import OutboxEvent, OutboxMessage
from .outbox_storage import (
    change_message,
    hold_message,
    prepare_message,
    release_message_hold,
)
from .outbox_validation import DeliveryEvidence
from .ownership import database_now, lock_task_claim
from .storage import TaskStatus, _status

TASK_TYPE = "outbox_delivery"
MAX_ATTEMPTS = 5
PROVIDER_SECONDS = 30
RETRY_BASE_SECONDS = 30


def retry_delay(attempt):
    """Use one bounded schedule for the provider journal and its Task hint."""
    return min(600, RETRY_BASE_SECONDS * 2 ** (attempt - 1))


def bound_dispatch(status):
    """Queue identity is valid only against the exact stored root and live view."""
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
        raise PermissionError("Family dispatch Task binding differs.")
    row = OutboxMessage.objects.only(*METADATA_FIELDS).get(
        pk=status.domain_request_id,
        task_id=status.root_id,
        purpose__in=("initial", "reminder"),
    )
    if not ScheduleOccurrence.objects.filter(
        pk=row.semantic_key,
        outbox_id=row.pk,
        definition__campaign_id=row.campaign_id,
        definition__kind=row.purpose,
        mode=row.mode,
        target=f"family:{row.family_id}",
    ).exists():
        raise PermissionError("Family dispatch occurrence binding differs.")
    return row


def disposition(message):
    """Terminal invalidation cancels unsent work; temporary gates leave it queued."""
    require_work_order()
    runtime = SystemConfiguration.objects.get()
    population = CampaignCredentialState.objects.filter(
        campaign_id=message.campaign_id
    ).first()
    occurrence = ScheduleOccurrence.objects.select_related("definition").get(
        pk=message.semantic_key
    )
    if (
        runtime.current_campaign_id != message.campaign_id
        or runtime.mode != message.mode
        or occurrence.revision_id != occurrence.definition.current_revision_id
        or occurrence.state in {"skipped", "coalesced"}
        or (
            message.mode == "testing"
            and (
                population is None
                or population.rehearsal_epoch_id != message.rehearsal_epoch_id
            )
        )
    ):
        return "scope_replaced"
    scope = _scope(message.campaign_id)
    if (
        scope.instant >= scope.campaign.active_configuration.ends_at
        or scope.campaign.state in {"closed", "archived"}
    ):
        return "campaign_closed"
    _planning_scope(message.campaign_id)
    if message.mode == "production" and scope.campaign.delivery_paused:
        return "delivery_paused"
    if population.population_dirty:
        raise PermissionError("Family delivery awaits source reconciliation.")
    if not SourceCurrent.objects.filter(
        snapshot_id=population.source_snapshot_id,
        generation=population.source_generation,
    ).exists():
        raise PermissionError("Family delivery awaits source reconciliation.")
    if (
        OutboxMessage.objects.filter(
            family_id=message.family_id,
            campaign_id=message.campaign_id,
            mode=message.mode,
            state__in=("submitting", "delivery_unknown"),
        )
        .exclude(pk=message.pk)
        .exists()
    ):
        raise PermissionError("Family delivery awaits an unresolved provider outcome.")
    if (
        message.purpose == "reminder"
        and RestoreDeliveryHold.objects.filter(
            definition__campaign_id=message.campaign_id,
            definition__kind="initial",
            mode=message.mode,
            target=occurrence.target,
            slot="once",
            state="unreviewed",
        ).exists()
    ):
        raise PermissionError("Family delivery awaits initial recovery review.")
    if message.not_before > database_now():
        raise PermissionError("Family delivery retry is not due.")
    return None


def cancel_unsent(identifier, claim, *, reason):
    """A live Family dispatcher may cancel only its own unsent Family group."""
    require_work_order()
    owner = bound_dispatch(_status(lock_task_claim(claim)))
    row = OutboxMessage.objects.get(pk=identifier)
    if (row.family_id, row.campaign_id, row.mode) != (
        owner.family_id,
        owner.campaign_id,
        owner.mode,
    ):
        raise PermissionError("Family cancellation scope differs.")

    def admit(action, identity, status, proposal):
        lock_task_claim(claim)
        return action is DeliveryAction.CANCEL_UNSENT and status.message_id == row.pk

    return change_message(
        message_id=row.pk,
        action=DeliveryAction.CANCEL_UNSENT,
        command_id=uuid4(),
        expected_version=row.version,
        actor_id=claim.worker_id,
        correlation_id=claim.run_id,
        evidence=DeliveryEvidence(reason=reason),
        admit=admit,
    )


def _occurrence_change(row, claim, **values):
    """Occurrence history shares the atomic outbox boundary and exact claim."""
    updated = ScheduleOccurrence.objects.filter(pk=row.pk, version=row.version).update(
        **values,
        actor_id=claim.worker_id,
        correlation_id=claim.run_id,
        version=row.version + 1,
    )
    if updated != 1:
        raise StorageInvariantError("Family delivery lost its occurrence version.")
    row.refresh_from_db()


def begin_submission(identifier, claim, *, private, public_origin):
    """Resolve private content only under final admission, then commit before IO.

    A None return means cancellation, coalescing or a pause, not acceptance.
    Rerendering is restricted to definitely unsent states; uncertain payloads
    remain immutable until explicit reconciliation.
    """
    if connection.in_atomic_block:
        raise StorageInvariantError("Family submission must commit independently.")
    from .family_mail_dispatch_content import current_content

    with work_transaction():
        task = lock_task_claim(claim)
        message = bound_dispatch(_status(task))
        if message.pk != identifier or message.state not in {"pending", "retry_wait"}:
            raise PermissionError("Family delivery is not unsent.")
        row = ScheduleOccurrence.objects.select_related("definition", "revision").get(
            pk=message.semantic_key
        )
        reason = disposition(message)
        if reason == "delivery_paused":
            scope = _scope(message.campaign_id)
            if (
                message.pause_hold_id is None
                or message.pause_version != scope.campaign.pause_version
            ):
                hold_message(
                    message_id=message.pk,
                    expected_version=message.version,
                    command_id=uuid4(),
                    actor_id=claim.worker_id,
                    correlation_id=claim.run_id,
                    pause_version=scope.campaign.pause_version,
                    admit=lambda action, identity, status: (
                        lock_task_claim(claim) is not None
                        and action == "hold"
                        and status.message_id == message.pk
                        and disposition(message) == "delivery_paused"
                    ),
                )
            return None
        if reason:
            cancel_unsent(message.pk, claim, reason=reason)
            if row.state == "pending":
                _occurrence_change(row, claim, state="skipped", reason=reason)
            return None
        decision = plan_family(
            claim, family_id=message.family_id, worker_id=claim.worker_id
        )
        if decision.held:
            raise PermissionError("Family delivery recovery is held.")
        if decision.selected != row.pk:
            return None
        scope, _ = _planning_scope(message.campaign_id)
        if message.pause_hold_id is not None:
            release_message_hold(
                message_id=message.pk,
                expected_version=message.version,
                command_id=uuid4(),
                actor_id=claim.worker_id,
                correlation_id=claim.run_id,
                admit=lambda action, identity, status: (
                    lock_task_claim(claim) is not None
                    and action == "release_hold"
                    and status.message_id == message.pk
                    and disposition(message) is None
                ),
            )
            message.refresh_from_db()
        render, sealed, mail = current_content(
            message, row, scope, private=private, public_origin=public_origin
        )

        def admit(action, identity, status, proposal=None):
            lock_task_claim(claim)
            return (
                action in {"prepared", DeliveryAction.SUBMIT}
                and status.message_id == message.pk
                and disposition(message) is None
            )

        prepared = prepare_message(
            message_id=message.pk,
            expected_version=message.version,
            command_id=uuid4(),
            actor_id=claim.worker_id,
            correlation_id=claim.run_id,
            render=render,
            sealed=sealed,
            admit=admit,
        )
        _occurrence_change(
            row,
            claim,
            state="running",
            task_id=claim.run_id,
            worker_id=claim.worker_id,
            fence=claim.fence,
            attempts=row.attempts + 1,
            heartbeat_at=database_now(),
            lease_expires_at=task.lease_expires_at,
            reason="provider_submission",
        )
        status = change_message(
            message_id=message.pk,
            action=DeliveryAction.SUBMIT,
            command_id=uuid4(),
            expected_version=prepared.version,
            actor_id=claim.worker_id,
            correlation_id=claim.run_id,
            run_id=claim.run_id,
            task_fence=claim.fence,
            provider_seconds=PROVIDER_SECONDS,
            admit=admit,
        )
        message.refresh_from_db()
        return mail, message.provider_deadline, render.configuration_id, status.attempt


def finish_submission(identifier, claim, result):
    """Keep actual observations even if configuration/lifecycle changed in flight."""
    if not isinstance(result, FamilyDeliveryResult):
        raise TypeError("An explicit Family provider observation is required.")
    with work_transaction():
        message = bound_dispatch(_status(lock_task_claim(claim)))
        if (
            message.pk != identifier
            or (message.state, message.run_id, message.task_fence, message.worker_id)
            != ("submitting", claim.run_id, claim.fence, claim.worker_id)
            or result.recipient_count != len(message.render.routed_recipients)
        ):
            raise PermissionError("Family outcome does not own its submitted attempt.")
        action = {
            FamilyDeliveryStatus.ACCEPTED: DeliveryAction.ACCEPT,
            FamilyDeliveryStatus.UNKNOWN: DeliveryAction.MARK_UNKNOWN,
            FamilyDeliveryStatus.PERMANENT: DeliveryAction.FAIL_UNACCEPTED,
            FamilyDeliveryStatus.SYSTEMIC: DeliveryAction.FAIL_UNACCEPTED,
            FamilyDeliveryStatus.TRANSIENT: DeliveryAction.RETRY_UNACCEPTED,
        }[result.status]
        row = ScheduleOccurrence.objects.get(pk=message.semantic_key)
        # Record definitive non-acceptance even when the original scope no longer
        # permits another attempt. Never relabel it as uncertain or accepted.
        if action is DeliveryAction.RETRY_UNACCEPTED:
            runtime = SystemConfiguration.objects.get()
            population = CampaignCredentialState.objects.get(
                campaign_id=message.campaign_id
            )
            if (
                message.attempt >= MAX_ATTEMPTS
                or runtime.mode != message.mode
                or (
                    message.mode == "testing"
                    and population.rehearsal_epoch_id != message.rehearsal_epoch_id
                )
                or row.revision_id != row.definition.current_revision_id
            ):
                action = DeliveryAction.FAIL_UNACCEPTED

        def admit(candidate, identity, status, proposal):
            lock_task_claim(claim)
            return candidate is action and status.message_id == message.pk

        result_status = change_message(
            message_id=message.pk,
            action=action,
            command_id=uuid4(),
            expected_version=message.version,
            actor_id=claim.worker_id,
            correlation_id=claim.run_id,
            evidence=result_evidence(result, semantic_key=message.semantic_key),
            admit=admit,
            **(
                {"retry_seconds": retry_delay(message.attempt)}
                if action is DeliveryAction.RETRY_UNACCEPTED
                else {}
            ),
        )
        target = {
            DeliveryAction.ACCEPT: "succeeded",
            DeliveryAction.MARK_UNKNOWN: "delivery_unknown",
            DeliveryAction.FAIL_UNACCEPTED: "failed",
            DeliveryAction.RETRY_UNACCEPTED: "pending",
        }[action]
        _occurrence_change(
            row,
            claim,
            state=target,
            lease_expires_at=None,
            reason="smtp_" + result.status.value,
        )
        if action is DeliveryAction.ACCEPT:
            ScheduleFulfillment.objects.create(
                definition_id=row.definition_id,
                mode=row.mode,
                target=row.target,
                slot=row.slot,
                disposition="delivered",
                occurrence_id=row.pk,
                actor_id=claim.worker_id,
                correlation_id=claim.run_id,
            )
        if message.mode == "production":
            from .recipient_suppressions import record_refusal

            event = OutboxEvent.objects.get(
                message_id=message.pk, version=result_status.version
            )
            for index in result.permanent:
                record_refusal(
                    event_id=event.pk,
                    address=message.render.routed_recipients[index],
                    actor_id=claim.worker_id,
                    correlation_id=claim.run_id,
                )
        return result_status

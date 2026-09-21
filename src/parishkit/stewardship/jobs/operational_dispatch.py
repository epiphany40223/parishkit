"""Administrator-routed SMTP transactions, distinct from campaign delivery admission.

The compiled owner names the purpose, the cohort and recipient tables, how a
recipient stays current and how the current content is rebuilt; the
transaction contract is the same for every owner.
"""

from uuid import uuid4

from django.db import connection

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.family_delivery import (
    FamilyDeliveryResult,
    FamilyDeliveryStatus,
)
from parishkit.stewardship.storage import StorageInvariantError

from .delivery_states import DeliveryAction
from .family_dispatch_grants import METADATA_FIELDS
from .family_mail_dispatch import (
    MAX_ATTEMPTS,
    PROVIDER_SECONDS,
    FamilyDeliveryHeld,
    retry_delay,
)
from .family_mail_results import result_evidence
from .models import TaskRun
from .operational_owner import OPERATIONAL
from .outbox_models import OutboxMessage
from .outbox_storage import change_message, prepare_message
from .outbox_validation import DeliveryEvidence
from .ownership import database_now, lock_task_claim
from .storage import TaskStatus, _status


def bound_operational(status, owner=OPERATIONAL):
    """An opaque queue hint requires its exact Task, recipient and cohort binding."""
    require_work_order()
    if not isinstance(status, TaskStatus) or status.task_type != "outbox_delivery":
        raise PermissionError(f"{owner.label} dispatch requires an outbox Task.")
    if not TaskRun.objects.filter(
        pk=status.run_id,
        root_id=status.root_id,
        task_type=status.task_type,
        domain_request_id=status.domain_request_id,
        state=status.state,
        version=status.version,
        fence=status.fence,
        worker_id=status.worker_id,
    ).exists():
        raise PermissionError(f"{owner.label} dispatch Task binding differs.")
    message = OutboxMessage.objects.only(*METADATA_FIELDS).get(
        pk=status.domain_request_id,
        task_id=status.root_id,
        purpose=owner.purpose,
        routing="operational",
        credential_namespace="none",
        family_id=None,
        campaign_id=None,
    )
    owner.recipient_model.objects.only("id").get(
        pk=message.semantic_key, outbox_id=message.pk
    )
    return message


def cohort_state(message, owner=OPERATIONAL):
    """Read completion and latest-parent failure in one metadata-only query."""
    require_work_order()
    # The table names are the owner's compiled constants, never caller input.
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT (SELECT count(*) FROM {owner.recipient_table} "
            "WHERE cohort_id=c.id)=c.recipient_count, "
            "(SELECT state FROM stewardship_task_run WHERE root_id=parent.root_id "
            "ORDER BY retry_sequence DESC LIMIT 1) IN ('failed','cancelled') "
            f"FROM {owner.recipient_table} r "
            f"JOIN {owner.cohort_table} c ON c.id=r.cohort_id "
            "JOIN stewardship_task_run parent ON parent.id=c.run_id "
            "WHERE r.outbox_id=%s",
            [message.pk],
        )
        result = cursor.fetchone()
    if result is None:
        raise PermissionError(f"{owner.label} cohort binding is unavailable.")
    return result


def complete_cohort(message, owner=OPERATIONAL):
    """Metadata-only completion never reads an address or a rendered message."""
    return cohort_state(message, owner)[0]


def preparation_failed(message, owner=OPERATIONAL):
    """An incomplete cohort whose latest preparation run is terminal cannot send."""
    complete, failed = cohort_state(message, owner)
    return failed and not complete


def recipient_current(message, owner=OPERATIONAL):
    """Only the private mail process needs the exact address reauthorization."""
    return owner.recipient_current(message)


def begin_submission(identifier, claim, *, store, configuration_id, owner=OPERATIONAL):
    """Commit current recipient/content and attempt before returning provider input."""
    if connection.in_atomic_block:
        raise StorageInvariantError(
            f"{owner.label} submission must commit independently."
        )
    with work_transaction():
        message = bound_operational(_status(lock_task_claim(claim)), owner)
        if message.pk != identifier or message.state not in {"pending", "retry_wait"}:
            raise PermissionError(f"{owner.label} delivery is not unsent.")
        if not complete_cohort(message, owner) or message.not_before > database_now():
            raise FamilyDeliveryHeld(
                f"{owner.label} delivery awaits preparation or retry."
            )
        if not recipient_current(message, owner):
            cancel_unsent(message, claim, owner=owner)
            return None
        if not SystemConfiguration.objects.filter(
            active_configuration_id=configuration_id
        ).exists():
            raise FamilyDeliveryHeld(f"{owner.label} delivery configuration changed.")
        recipient = owner.recipient_model.objects.select_related("cohort").get(
            outbox_id=message.pk
        )
        mail, render = owner.current_mail(
            store, recipient.cohort, recipient.address, message.semantic_key
        )

        def admit(action, identity, status, proposal=None):
            """Recheck exact claim and recipient for both preparation and submission."""
            current = bound_operational(_status(lock_task_claim(claim)), owner)
            return (
                action in {"prepared", DeliveryAction.SUBMIT}
                and status.message_id == current.pk == message.pk
                and recipient_current(current, owner)
                and complete_cohort(current, owner)
                and SystemConfiguration.objects.filter(
                    active_configuration_id=configuration_id
                ).exists()
            )

        prepared = prepare_message(
            message_id=message.pk,
            expected_version=message.version,
            command_id=uuid4(),
            actor_id=claim.worker_id,
            correlation_id=claim.run_id,
            render=render,
            admit=admit,
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
        return mail, message.provider_deadline, status.attempt


def cancel_unsent(message, claim, *, reason="recipient_revoked", owner=OPERATIONAL):
    """Terminal preparation or revocation cancels only an exact unsent intent."""
    require_work_order()
    if reason not in {"recipient_revoked", "preparation_failed"}:
        raise ValueError(f"Unknown {owner.label.lower()} cancellation reason.")

    def admit(action, identity, status, proposal):
        """No revoked-recipient cancellation can affect another message."""
        current = bound_operational(_status(lock_task_claim(claim)), owner)
        return (
            action is DeliveryAction.CANCEL_UNSENT
            and current.pk == status.message_id == message.pk
            and (
                preparation_failed(current, owner)
                if reason == "preparation_failed"
                else not recipient_current(current, owner)
            )
        )

    return change_message(
        message_id=message.pk,
        action=DeliveryAction.CANCEL_UNSENT,
        command_id=uuid4(),
        expected_version=message.version,
        actor_id=claim.worker_id,
        correlation_id=claim.run_id,
        evidence=DeliveryEvidence(reason=reason),
        admit=admit,
    )


def finish_submission(identifier, claim, result, owner=OPERATIONAL):
    """Record exact observed outcomes even after config, mode or Admin grants change."""
    if not isinstance(result, FamilyDeliveryResult) or result.recipient_count != 1:
        raise TypeError(
            f"{owner.label} delivery requires one explicit provider outcome."
        )
    with work_transaction():
        message = bound_operational(_status(lock_task_claim(claim)), owner)
        if message.pk != identifier or (
            message.state,
            message.run_id,
            message.task_fence,
            message.worker_id,
        ) != ("submitting", claim.run_id, claim.fence, claim.worker_id):
            raise PermissionError(
                f"{owner.label} outcome does not own its submitted attempt."
            )
        action = {
            FamilyDeliveryStatus.ACCEPTED: DeliveryAction.ACCEPT,
            FamilyDeliveryStatus.UNKNOWN: DeliveryAction.MARK_UNKNOWN,
            FamilyDeliveryStatus.PERMANENT: DeliveryAction.FAIL_UNACCEPTED,
            FamilyDeliveryStatus.SYSTEMIC: DeliveryAction.FAIL_UNACCEPTED,
            FamilyDeliveryStatus.TRANSIENT: DeliveryAction.RETRY_UNACCEPTED,
            FamilyDeliveryStatus.UNAVAILABLE: DeliveryAction.RETRY_UNACCEPTED,
        }[result.status]
        if (
            action is DeliveryAction.RETRY_UNACCEPTED
            and message.attempt >= MAX_ATTEMPTS
        ):
            action = DeliveryAction.FAIL_UNACCEPTED

        def admit(candidate, identity, status, proposal):
            """Provider facts settle only this live owner's already-started message."""
            current = bound_operational(_status(lock_task_claim(claim)), owner)
            return candidate is action and status.message_id == current.pk == message.pk

        return change_message(
            message_id=message.pk,
            action=action,
            command_id=uuid4(),
            expected_version=message.version,
            actor_id=claim.worker_id,
            correlation_id=claim.run_id,
            admit=admit,
            evidence=result_evidence(result, semantic_key=message.semantic_key),
            **(
                {"retry_seconds": retry_delay(message.attempt)}
                if action is DeliveryAction.RETRY_UNACCEPTED
                else {}
            ),
        )


def recover_submission(status, owner=OPERATIONAL):
    """An abandoned attempt past its deadline is uncertain, never auto-resent."""
    require_work_order()
    message = bound_operational(status, owner)
    if (
        status.state != "abandoned"
        or message.state != "submitting"
        or message.provider_deadline > database_now()
    ):
        raise PermissionError(f"{owner.label} recovery is not yet admitted.")

    def admit(action, identity, current, proposal):
        """Match retained attempt identity while preserving its uncertainty history."""
        actual = bound_operational(status, owner)
        return (
            action is DeliveryAction.MARK_UNKNOWN
            and current.message_id == actual.pk == message.pk
            and actual.version == message.version
        )

    return change_message(
        message_id=message.pk,
        action=DeliveryAction.MARK_UNKNOWN,
        command_id=uuid4(),
        expected_version=message.version,
        actor_id=uuid4(),
        correlation_id=status.run_id,
        evidence=DeliveryEvidence(reason="recovery_unknown"),
        admit=admit,
    )

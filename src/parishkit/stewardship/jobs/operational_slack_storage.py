"""Fenced Slack submission and outcome transactions, independent of email."""

from uuid import uuid4

from django.db import connection
from django.db.models import OuterRef, Subquery

from parishkit.stewardship.accounts.configuration_models import AppliedIntegration
from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.operational_delivery import OperationalSlack
from parishkit.stewardship.readiness_delivery import DeliveryOutcome
from parishkit.stewardship.runtime_background import mail_authority
from parishkit.stewardship.storage import StorageInvariantError

from .family_mail_dispatch import FamilyDeliveryHeld
from .models import TaskRun
from .operational_models import OperationalNotice
from .operational_routing import notice_alert
from .operational_slack_models import OperationalSlackAttempt, OperationalSlackResult
from .ownership import database_now, lock_task_claim
from .storage import _status

TASK_TYPE = "operational_slack"


def bound_task(status):
    """Require this stored notice's exact Task version, lease and worker identity."""
    require_work_order()
    if status.task_type != TASK_TYPE:
        raise PermissionError("Operational Slack Task type differs.")
    row = TaskRun.objects.get(
        pk=status.run_id,
        root_id=status.root_id,
        task_type=TASK_TYPE,
        domain_request_id=status.domain_request_id,
        state=status.state,
        version=status.version,
        fence=status.fence,
        worker_id=status.worker_id,
    )
    if (
        not OperationalNotice.objects.filter(pk=row.domain_request_id).exists()
        or not TaskRun.objects.filter(
            pk=row.root_id,
            task_type=TASK_TYPE,
            domain_request_id=row.domain_request_id,
            idempotency_key=str(row.domain_request_id),
        ).exists()
    ):
        raise PermissionError("Operational Slack notice binding differs.")
    return row


def latest_attempt(status):
    """Metadata-only recovery reads no channel, fingerprint, address or message."""
    require_work_order()
    return (
        OperationalSlackAttempt.objects.filter(run__root_id=status.root_id)
        .order_by("-run__retry_sequence", "-fence")
        .annotate(
            observed=Subquery(
                OperationalSlackResult.objects.filter(attempt_id=OuterRef("pk")).values(
                    "outcome"
                )[:1]
            )
        )
        .values("id", "run_id", "fence", "deadline_at", "observed")
        .first()
    )


def configured_channel(runtime):
    """A missing optional channel is a hold, not a provider failure."""
    return AppliedIntegration.objects.filter(
        configuration_id=runtime.active_configuration_id, kind="slack"
    ).exists()


def begin_submission(claim, *, store, configuration_id, fingerprint):
    """Commit current routing and a finite provider window before private IO."""
    from parishkit.stewardship.campaigns.domain import SystemMode

    if connection.in_atomic_block:
        raise StorageInvariantError("Operational Slack submission must commit.")
    with work_transaction():
        status = _status(lock_task_claim(claim))
        task = bound_task(status)
        runtime = mail_authority(store)
        channel = AppliedIntegration.objects.filter(
            configuration_id=runtime.active_configuration_id, kind="slack"
        ).first()
        if (
            runtime.active_configuration_id != configuration_id
            or channel is None
            or channel.credential_fingerprint != fingerprint
        ):
            raise FamilyDeliveryHeld("Operational Slack configuration changed.")
        previous = latest_attempt(status)
        if previous is not None and previous["observed"] != DeliveryOutcome.NOT_SENT:
            raise PermissionError("Operational Slack prior acceptance is not excluded.")
        attempt = OperationalSlackAttempt.objects.create(
            notice_id=task.domain_request_id,
            run=task,
            fence=claim.fence,
            worker_id=claim.worker_id,
            actor_id=claim.worker_id,
            correlation_id=claim.run_id,
            configuration_id=configuration_id,
            channel_id=channel.settings["channel_id"],
            fingerprint=fingerprint,
            mode=runtime.mode,
        )
        notification = OperationalSlack(
            attempt.pk,
            attempt.channel_id,
            notice_alert(attempt.notice_id, SystemMode(runtime.mode)),
        )
        return attempt.pk, attempt.deadline_at, notification


def finish_submission(identifier, claim, outcome):
    """Persist a provider fact only for the exact live owner that began it."""
    if not isinstance(outcome, DeliveryOutcome):
        raise TypeError("Operational Slack needs a typed provider outcome.")
    with work_transaction():
        status = _status(lock_task_claim(claim))
        bound_task(status)
        attempt = latest_attempt(status)
        if attempt is None or (
            attempt["id"],
            attempt["run_id"],
            attempt["fence"],
            attempt["observed"],
        ) != (identifier, claim.run_id, claim.fence, None):
            raise PermissionError("Operational Slack outcome binding differs.")
        return OperationalSlackResult.objects.create(
            attempt_id=identifier,
            outcome=outcome.value,
            reason="provider",
            actor_id=claim.worker_id,
            correlation_id=claim.run_id,
        )


def recover_submission(status):
    """Abandonment after the entire provider/drain window is uncertainty, not retry."""
    bound_task(status)
    attempt = latest_attempt(status)
    if (
        status.state != "abandoned"
        or attempt is None
        or attempt["run_id"] != status.run_id
        or attempt["observed"] is not None
        or attempt["deadline_at"] > database_now()
    ):
        raise PermissionError("Operational Slack recovery is not due.")
    return OperationalSlackResult.objects.create(
        attempt_id=attempt["id"],
        outcome=DeliveryOutcome.UNKNOWN.value,
        reason="recovery",
        actor_id=uuid4(),
        correlation_id=status.run_id,
    )

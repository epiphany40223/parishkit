"""Commit-before-send Family outbox transactions and truthful outcome settlement."""

import logging
from datetime import timedelta
from uuid import UUID, uuid4

from django.db import connection
from django.db.models import F, Func, IntegerField, Sum
from django.db.models.functions import Now

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
from .models import TaskRun, TaskRunEvent
from .outbox_models import OutboxEvent, OutboxMessage
from .outbox_storage import (
    change_message,
    hold_message,
    prepare_message,
    release_message_hold,
)
from .outbox_validation import DeliveryEvidence
from .ownership import database_now, lock_task_claim
from .phases import TaskPhase
from .storage import TaskStatus, _status

LOG = logging.getLogger(__name__)
TASK_TYPE = "outbox_delivery"
# Purposes with no schedule occurrence: they never write occurrence state or a
# fulfillment, whatever their provider outcome.
UNSCHEDULED_PURPOSES = frozenset(
    {"receipt", "family_test", "daily_digest", "weekly_digest"}
)
MAX_ATTEMPTS = 5
PROVIDER_SECONDS = 30
RETRY_BASE_SECONDS = 30
DAY = timedelta(hours=24)


# Google Workspace limits one mailbox, per rolling 24 hours, to about 2,000
# messages, 10,000 recipients in total, 3,000 external recipients and 2,000
# unique external recipients (support.google.com/a/answer/166852). Recipients
# are always at least messages, so Stewardship counts RECIPIENTS and stops a
# little short of the tightest (2,000 unique external), leaving staff some
# headroom. RESERVED_SENDS of that is held back for receipts, digests and
# alerts, so a large bulk send (invitations, reminders, Family tests) can never
# crowd out a Family's submission receipt.
DAILY_SEND_LIMIT = 1800
RESERVED_SENDS = 200
BULK_PURPOSES = frozenset({"initial", "reminder", "family_test"})
# How long a Gmail sending-limit refusal holds the refused message: the daily
# limit is re-probed hourly as the rolling day moves on; a rate limit clears
# within minutes. A message refused at a limit for LIMIT_GIVE_UP keeps failing
# for some other reason, so it then fails visibly like any exhausted retry.
LIMIT_RETRY_SECONDS = {"daily": 3600, "rate": 900}
LIMIT_GIVE_UP = timedelta(hours=48)
# How long a message found over this deployment's own daily limit waits
# before it is looked at again.
CAPPED_RETRY_SECONDS = 900


class FamilyDeliveryHeld(PermissionError):
    """Temporary admission loss is not a failed provider or rendering attempt."""


def retry_delay(attempt):
    """Use one bounded schedule for the provider journal and its Task hint."""
    return min(600, RETRY_BASE_SECONDS * 2 ** (attempt - 1))


def result_retry_seconds(result, attempt):
    """A sending-limit refusal waits for the limit; other retries back off."""
    if result.limit is not None:
        return LIMIT_RETRY_SECONDS[result.limit]
    return retry_delay(attempt)


def sends_in_last_day():
    """Recipients this deployment handed to Gmail in the last 24 hours.

    Accepted and uncertain submissions both count: an uncertain one may well
    have been sent. Definitive refusals are not counted, as Gmail does not.
    """
    total = OutboxEvent.objects.filter(
        previous_state="submitting",
        submitted_at__isnull=False,
        reason__in=("smtp_accepted", "smtp_delivery_unknown", "recovery_unknown"),
        created_at__gte=Now() - DAY,
    ).aggregate(
        total=Sum(
            Func(
                F("render__routed_recipients"),
                function="jsonb_array_length",
                output_field=IntegerField(),
            )
        )
    )["total"]
    return total or 0


def over_daily_limit(purpose, sent, recipients=1):
    """Whether ``recipients`` more of ``purpose`` would pass the daily limit."""
    limit = DAILY_SEND_LIMIT - (RESERVED_SENDS if purpose in BULK_PURPOSES else 0)
    return sent + recipients > limit


def limit_history(message):
    """Count a message's earlier sending-limit refusals and when the current run began.

    Stored evidence deliberately never names a limit. A limit refusal is
    recognized instead as a submission outcome whose Task then deferred in the
    RECONCILING phase (see family_mail_delivery_tasks._execute), matched by the
    attempt's (run, fence). Return ``(count, started)``: every limit refusal so
    far, and the time of the first one in the current unbroken run of them. Any
    other provider outcome, or a staff retry of a failed delivery, ends a run.
    """
    events = list(
        OutboxEvent.objects.filter(message_id=message.pk)
        .order_by("version")
        .values_list("action", "previous_state", "run_id", "task_fence", "created_at")
    )
    runs = {run for _, _, run, _, _ in events if run is not None}
    limited = set(
        TaskRunEvent.objects.filter(
            run_id__in=runs,
            action="retryable_failure",
            phase=TaskPhase.RECONCILING,
        ).values_list("run_id", "fence")
    )
    return limit_run(events, limited)


def limit_run(events, limited):
    """The pure walk behind ``limit_history`` (see there), kept separately testable.

    ``events`` are ``(action, previous_state, run, fence, created)`` in version
    order; ``limited`` is the set of ``(run, fence)`` pairs deferred at a limit.
    """
    count, started = 0, None
    for action, previous, run, fence, created in events:
        if action == DeliveryAction.RETRY_FAILED.value:
            started = None
        elif previous == "submitting":
            if (run, fence) in limited:
                count += 1
                started = started or created
            else:
                started = None
    return count, started


def budget_spent(message, result):
    """Whether this non-acceptance ends the message's automatic retries.

    Limit refusals are not the message's fault: they are left out of the
    attempt budget, and instead a message refused at a limit continuously for
    LIMIT_GIVE_UP fails visibly.
    """
    count, started = limit_history(message)
    if result.limit is None:
        return message.attempt - count >= MAX_ATTEMPTS
    if started is not None and database_now() - started > LIMIT_GIVE_UP:
        LOG.critical(
            "Mail was refused at a Google sending limit for over %d hours and has "
            "failed; review the mail provider and retry it.",
            LIMIT_GIVE_UP // timedelta(hours=1),
        )
        return True
    return False


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
        purpose__in=(
            "initial",
            "reminder",
            "receipt",
            "family_test",
            "daily_digest",
            "weekly_digest",
        ),
    )
    if row.purpose == "weekly_digest":
        from .weekly_dispatch import bound_weekly

        bound_weekly(row)
        return row
    if row.purpose == "daily_digest":
        from .digest_dispatch import bound_digest

        bound_digest(row)
        return row
    if row.purpose == "receipt":
        from .receipt_dispatch import bound_receipt

        bound_receipt(row)
        return row
    if row.purpose == "family_test":
        from .family_test_dispatch import bound_family_test

        bound_family_test(row)
        return row
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


def disposition(message, *, check_recipient=False):
    """Terminal invalidation cancels unsent work; temporary gates leave it queued."""
    require_work_order()
    if message.purpose == "weekly_digest":
        from .weekly_dispatch import weekly_disposition

        return weekly_disposition(message, check_recipient=check_recipient)
    if message.purpose == "daily_digest":
        from .digest_dispatch import digest_disposition

        return digest_disposition(message, check_recipient=check_recipient)
    if message.purpose == "receipt":
        from .receipt_dispatch import receipt_disposition

        return receipt_disposition(message)
    if message.purpose == "family_test":
        from .family_test_dispatch import family_test_disposition

        return family_test_disposition(message)
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
        message.mode == "production"
        and occurrence.production_cycle != scope.campaign.production_cycle
    ):
        return "scope_replaced"
    if (
        scope.instant >= scope.campaign.active_configuration.ends_at
        or scope.campaign.state in {"closed", "archived"}
    ):
        return "campaign_closed"
    try:
        _planning_scope(message.campaign_id)
    except PermissionError:
        raise FamilyDeliveryHeld("Family delivery awaits current scope.") from None
    if message.mode == "production" and scope.campaign.delivery_paused:
        return "delivery_paused"
    if population is None or population.population_dirty:
        raise FamilyDeliveryHeld("Family delivery awaits source reconciliation.")
    if not SourceCurrent.objects.filter(
        snapshot_id=population.source_snapshot_id,
        generation=population.source_generation,
    ).exists():
        raise FamilyDeliveryHeld("Family delivery awaits source reconciliation.")
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
        raise FamilyDeliveryHeld(
            "Family delivery awaits an unresolved provider outcome."
        )
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
        raise FamilyDeliveryHeld("Family delivery awaits initial recovery review.")
    if message.not_before > database_now():
        raise FamilyDeliveryHeld("Family delivery retry is not due.")
    return None


def cancel_unsent(identifier, claim, *, reason):
    """A live Family dispatcher may cancel only its own unsent Family group."""
    require_work_order()
    owner = bound_dispatch(_status(lock_task_claim(claim)))
    row = OutboxMessage.objects.get(pk=identifier)
    if owner.purpose in {"daily_digest", "weekly_digest"} and row.pk != owner.pk:
        raise PermissionError("Digest cancellation cannot affect another Admin.")
    # A chosen-Family test is not part of the Family's scheduled mail group.
    if "family_test" in {owner.purpose, row.purpose} and row.pk != owner.pk:
        raise PermissionError(
            "Family test cancellation cannot affect another delivery."
        )
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


def begin_submission(
    identifier,
    claim,
    *,
    private,
    public_origin,
    metadata_only=False,
    configuration_id=None,
):
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
        row = (
            None
            if message.purpose in UNSCHEDULED_PURPOSES
            else ScheduleOccurrence.objects.select_related(
                "definition", "revision"
            ).get(pk=message.semantic_key)
        )
        reason = disposition(message, check_recipient=True)
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
            if row is not None and row.state == "pending":
                _occurrence_change(row, claim, state="skipped", reason=reason)
            return None
        # The no-send caller has not loaded Workspace credentials. A resumed
        # scope must not promote that caller into a submitting provider attempt.
        if metadata_only:
            return None
        if (
            configuration_id is not None
            and not SystemConfiguration.objects.filter(
                active_configuration_id=configuration_id
            ).exists()
        ):
            raise FamilyDeliveryHeld("Family delivery configuration changed.")
        if row is None:
            scope = _scope(message.campaign_id)
        else:
            decision = plan_family(
                claim, family_id=message.family_id, worker_id=claim.worker_id
            )
            if decision.held:
                raise FamilyDeliveryHeld("Family delivery recovery is held.")
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
        if message.purpose == "weekly_digest":
            from .weekly_dispatch import current_weekly_content

            render, sealed, mail = current_weekly_content(message, scope)
        elif message.purpose == "daily_digest":
            from .digest_dispatch import current_digest_content

            render, sealed, mail = current_digest_content(message, scope)
        elif message.purpose == "receipt":
            from .receipt_dispatch import current_receipt_content

            render, sealed, mail = current_receipt_content(
                message, scope, public_origin=public_origin
            )
        else:
            # Scheduled mail renders the occurrence revision's template; a test
            # renders the template record its Admin ticket named.
            if row is None:
                from .family_test_dispatch import family_test_template

                template = family_test_template(message)
            else:
                template = UUID(row.revision.values["template_version"])
            render, sealed, mail = current_content(
                message, template, scope, private=private, public_origin=public_origin
            )

        def admit(action, identity, status, proposal=None):
            lock_task_claim(claim)
            return (
                action in {"prepared", DeliveryAction.SUBMIT}
                and status.message_id == message.pk
                and disposition(message, check_recipient=True) is None
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
        if row is not None:
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
            FamilyDeliveryStatus.UNAVAILABLE: DeliveryAction.RETRY_UNACCEPTED,
        }[result.status]
        row = (
            None
            if message.purpose in UNSCHEDULED_PURPOSES
            else ScheduleOccurrence.objects.get(pk=message.semantic_key)
        )
        # Record definitive non-acceptance even when the original scope no longer
        # permits another attempt. Never relabel it as uncertain or accepted.
        if action is DeliveryAction.RETRY_UNACCEPTED:
            runtime = SystemConfiguration.objects.get()
            population = CampaignCredentialState.objects.get(
                campaign_id=message.campaign_id
            )
            if (
                # A sending-limit refusal is not the message's fault, so it
                # does not spend the attempt budget.
                budget_spent(message, result)
                or runtime.mode != message.mode
                or (
                    message.mode == "testing"
                    and population.rehearsal_epoch_id
                    != (
                        message.rehearsal_epoch_id
                        if row is not None
                        else _report_epoch(message)
                    )
                )
                or (
                    row is not None
                    and row.revision_id != row.definition.current_revision_id
                )
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
                {"retry_seconds": result_retry_seconds(result, message.attempt)}
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
        if row is not None:
            _occurrence_change(
                row,
                claim,
                state=target,
                lease_expires_at=None,
                reason="smtp_" + result.status.value,
            )
        if action is DeliveryAction.ACCEPT and row is not None:
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
        if message.mode == "production" and message.family_id is not None:
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


def _report_epoch(message):
    """Read only the immutable response namespace while settling an attempt."""
    if message.purpose == "weekly_digest":
        from .weekly_dispatch import bound_weekly

        return bound_weekly(message).rehearsal_epoch_id
    if message.purpose == "daily_digest":
        from .digest_dispatch import bound_digest

        return bound_digest(message).rehearsal_epoch_id
    if message.purpose == "family_test":
        from .family_test_dispatch import bound_family_test

        return bound_family_test(message).rehearsal_epoch_id
    from .receipt_dispatch import bound_receipt

    return bound_receipt(message).rehearsal_epoch_id

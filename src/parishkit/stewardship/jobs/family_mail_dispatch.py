"""Commit-before-send Family outbox transactions and truthful outcome settlement."""

import logging
from datetime import timedelta
from uuid import UUID, uuid4

from django.db import connection, transaction
from django.db.models import F, Func, IntegerField, Sum
from django.db.models.functions import Now

from parishkit.logging import log_extra
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    FamilyCampaign,
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
    ProviderHealth,
)
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.storage import StorageInvariantError

from .admission import _scope
from .delivery_states import DeliveryAction
from .family_dispatch_grants import METADATA_FIELDS
from .family_mail_results import settle_with_stats
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
# within minutes. Only the mailbox-wide limits also pause all other sending; a
# rate limit answering one message's DATA ("message") holds that message only.
# A message refused at a limit for LIMIT_GIVE_UP while no mail
# was accepted in the last ACCEPTANCE_WINDOW keeps failing for some other
# reason, so it then fails visibly like any exhausted retry. Recent acceptances
# mean the queue is still draining past a real but partial limit, so the
# message keeps waiting, but never past LIMIT_GIVE_UP_ABSOLUTE.
LIMIT_RETRY_SECONDS = {"daily": 3600, "rate": 900, "message": 900}
MAILBOX_LIMITS = frozenset({"daily", "rate"})
LIMIT_GIVE_UP = timedelta(hours=48)
LIMIT_GIVE_UP_ABSOLUTE = timedelta(days=7)
ACCEPTANCE_WINDOW = timedelta(hours=24)
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


# Stored evidence is sorted compact JSON, so health is its first member (see
# mail_health.health_filter).
SHARED_FAULT = '{"health":"unavailable",'


def shared_fault(reason, evidence):
    """Whether a stored outcome was a definitely unsent shared outage.

    Only a retryable (unavailable or transient) result counts: an uncertain
    one with unavailable health is never retried, so it is never spared.
    """
    return reason in {"smtp_unavailable", "smtp_transient"} and (
        evidence or ""
    ).startswith(SHARED_FAULT)


def limit_history(message):
    """Count a message's earlier spared outcomes and when its limit run began.

    Two provider outcomes are not the message's fault and are spared from its
    attempt budget: a shared outage (stored with unavailable health) and a
    sending-limit refusal. The stored evidence itself never names a limit
    (only the optional send statistics may, for the send report, and they
    are never trusted for a decision), so a limit refusal is recognized
    instead as a healthy submission outcome whose Task then deferred in the
    RECONCILING phase (see
    family_mail_delivery_tasks._execute), matched by the attempt's (run,
    fence). Return ``(spared, started, first)``: every spared outcome so far,
    the time of the first limit refusal in the current unbroken run of them,
    and the time of the message's first provider outcome since it was last
    retried by staff. Any other provider outcome, or a staff retry of a
    failed delivery, ends a limit run.
    """
    events = list(
        OutboxEvent.objects.filter(message_id=message.pk)
        .order_by("version")
        .values_list(
            "action",
            "previous_state",
            "run_id",
            "task_fence",
            "created_at",
            "reason",
            "evidence_note",
        )
    )
    runs = {event[2] for event in events if event[2] is not None}
    limited = set(
        TaskRunEvent.objects.filter(
            run_id__in=runs,
            action="retryable_failure",
            phase=TaskPhase.RECONCILING,
        ).values_list("run_id", "fence")
    )
    return limit_run(
        [(*event[:5], shared_fault(*event[5:])) for event in events],
        limited,
    )


def limit_run(events, limited):
    """The pure walk behind ``limit_history`` (see there), kept separately testable.

    ``events`` are ``(action, previous_state, run, fence, created, shared)`` in
    version order, ``shared`` marking a shared-outage result; ``limited`` is
    the set of ``(run, fence)`` pairs whose Task deferred as a hold.
    """
    spared, started, first = 0, None, None
    for action, previous, run, fence, created, shared in events:
        if action == DeliveryAction.RETRY_FAILED.value:
            # A staff retry starts the message's give-up clocks afresh.
            started = first = None
        elif previous == "submitting":
            first = first or created
            if shared:
                # Outage deferrals are holds too, but never limit refusals.
                spared += 1
                started = None
            elif (run, fence) in limited:
                spared += 1
                started = started or created
            else:
                started = None
    return spared, started, first


def accepted_since(instant):
    """Whether any message was accepted by the provider since ``instant``."""
    return OutboxEvent.objects.filter(
        previous_state="submitting", reason="smtp_accepted", created_at__gte=instant
    ).exists()


def failure_identity(message_id):
    """The message id and Family DUID (None for staff mail) a failure log names.

    Neither is personal data, so an operator can find the failed message
    without the log ever carrying an address or a name.
    """
    family = (
        OutboxMessage.objects.filter(pk=message_id)
        .values_list("family_id", flat=True)
        .first()
    )
    duid = (
        FamilyCampaign.objects.filter(pk=family)
        .values_list("family_duid", flat=True)
        .first()
        if family
        else None
    )
    return {"message": message_id, "family_duid": duid}


def budget_spent(message, result):
    """Whether this non-acceptance ends the message's automatic retries.

    Limit refusals and shared outages are not the message's fault: they are
    left out of the attempt budget. Instead a message refused at a limit
    continuously for LIMIT_GIVE_UP fails visibly, unless other mail was
    accepted recently, and in any case after LIMIT_GIVE_UP_ABSOLUTE; a message
    kept unsent by outages fails LIMIT_GIVE_UP_ABSOLUTE after its first
    provider outcome since any staff retry. (The delivery circuit, not this
    budget, keeps a long outage from probing with every queued message.)
    """
    spared, started, first = limit_history(message)
    now = database_now()
    if result.limit is None and result.health is ProviderHealth.UNAVAILABLE:
        if first is None or now - first <= LIMIT_GIVE_UP_ABSOLUTE:
            return False
        _log_give_up(message, now - first, "the mail provider was unavailable")
        return True
    if result.limit is None:
        return message.attempt - spared >= MAX_ATTEMPTS
    if started is None:
        return False
    waited = now - started
    if waited <= LIMIT_GIVE_UP or (
        waited <= LIMIT_GIVE_UP_ABSOLUTE
        # While other mail is still being accepted, the limit is real but
        # partial (for example Google's cap is lower than ours): the queue is
        # draining, so this message keeps waiting its turn. Scoped like the
        # daily count, to the whole sending mailbox, and to recent acceptances
        # only, so one early acceptance cannot keep the message waiting.
        and accepted_since(max(started, now - ACCEPTANCE_WINDOW))
    ):
        return False
    _log_give_up(message, waited, "Google refused it at a sending limit")
    return True


def _log_give_up(message, waited, why):
    """Log, once the failure commits, that a message waited too long and failed.

    A claim that turns out stale (and rolls back) then raises no false alarm.
    The task id is the part the production log formatter keeps; the rest
    shows with debug logging.
    """
    identity = failure_identity(message.pk)
    transaction.on_commit(
        lambda: LOG.critical(
            "Mail message %s (Family DUID %s) has failed after %d hours in "
            "which %s; review the mail provider and retry it.",
            identity["message"],
            identity["family_duid"],
            waited // timedelta(hours=1),
            why,
            extra=log_extra({"task_id": message.task_id}),
        )
    )


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
    # Admission only: share the runtime and credential rows with Family
    # logins rather than make each login wait for this transaction (#147).
    scope = _scope(message.campaign_id, share=True)
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
        _planning_scope(message.campaign_id, share=True)
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


# Planner settings for the submission's guarded writes; see
# plan_submission_guards.
SUBMISSION_PLANNER = (("join_collapse_limit", "1"), ("from_collapse_limit", "1"))


def plan_submission_guards():
    """Make planning the Family live-scope guard cheap for this transaction.

    The render INSERT, the "prepared" UPDATE and the "submit" UPDATE each
    fire stewardship_family_dispatch_write_v1, which calls
    stewardship_family_dispatch_live_v1 (schema/family_dispatch.sql). That
    guard joins nine tables and nine EXISTS/NOT EXISTS checks; PostgreSQL
    spends almost all of its time choosing a join order: at launch scale
    (1,100 Families) a call executes in about 0.3 ms but plans in 200-300
    ms. A SQL function's plan is cached only per session, and the mail
    worker opens a fresh connection for every message, so each message paid
    that planning three times: about 0.7 s locally and most of the 2.4 s
    request_ms seen on the validation host (#343 statistics).

    The guard is written in its natural lookup order (the message by
    primary key, then each row it references), so planning it in the
    written order loses nothing: at launch scale the plan executes in the
    same 0.3 ms with the same buffer reads, and the three calls drop to
    about 25 ms. force_generic_plan would save about 10 ms more; it is
    left out to keep this change to the join order alone.

    The same settings also plan the receipt, digest and Family-test guards
    that this transaction's writes fire; each starts from the message by
    primary key too, so the written order costs them nothing.

    The settings are transaction-local (set_config's third argument), so
    they end at this submission's commit or rollback. Function plans built
    under them stay cached for the rest of the database session; that is
    harmless because the worker closes the connection right after the
    message, and needs a second look if connections are ever reused. They
    change how the guards are planned, never what they decide.
    """
    with connection.cursor() as cursor:
        for name, value in SUBMISSION_PLANNER:
            cursor.execute("SELECT set_config(%s, %s, true)", [name, value])


def begin_submission(
    identifier,
    claim,
    *,
    private,
    public_origin,
    metadata_only=False,
    configuration_id=None,
    nested=False,
    provider_seconds=None,
):
    """Resolve private content only under final admission, then commit before IO.

    A None return means cancellation, coalescing or a pause, not acceptance.
    Rerendering is restricted to definitely unsent states; uncertain payloads
    remain immutable until explicit reconciliation.

    ``nested`` lets the bulk send (#430, jobs/family_mail_bulk.py) run this
    inside its own lock transaction, which commits several submissions at
    once before any is sent; ``provider_seconds`` is then this message's
    deadline, later for messages further back in the batch.
    """
    if connection.in_atomic_block and not nested:
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
            scope = _scope(message.campaign_id, share=True)
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
        # The submission writes only its message, occurrence and Task rows,
        # never the runtime or credential rows, so admission shares them.
        if row is None:
            scope = _scope(message.campaign_id, share=True)
        else:
            decision = plan_family(
                claim, family_id=message.family_id, worker_id=claim.worker_id
            )
            if decision.held:
                raise FamilyDeliveryHeld("Family delivery recovery is held.")
            if decision.selected != row.pk:
                return None
            scope, _ = _planning_scope(message.campaign_id, share=True)
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

        # Only the guarded writes below need it; see plan_submission_guards.
        plan_submission_guards()
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
            provider_seconds=PROVIDER_SECONDS
            if provider_seconds is None
            else provider_seconds,
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

        # Statistics a database refuses are dropped, never the outcome.
        result_status = settle_with_stats(
            lambda evidence: change_message(
                message_id=message.pk,
                action=action,
                command_id=uuid4(),
                expected_version=message.version,
                actor_id=claim.worker_id,
                correlation_id=claim.run_id,
                evidence=evidence,
                admit=admit,
                **(
                    {"retry_seconds": result_retry_seconds(result, message.attempt)}
                    if action is DeliveryAction.RETRY_UNACCEPTED
                    else {}
                ),
            ),
            result,
            semantic_key=message.semantic_key,
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

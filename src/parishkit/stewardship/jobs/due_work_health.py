"""Admission-aware queue observations, without another thread or provider probe.

Only a complete, timely scan can prove recovery. A held or unreadable task is
unknown, not healthy; a live long-running task is not due and never looks like
an expired worker. PostgreSQL derives continuous windows across scheduler scans.

A task admitted more than ``MAX_GAP`` after it was due is late, except a bulk
Family send's backlog (#634). A send enqueues every delivery task at its due
time and the mail consumers work through them over tens of minutes, so most
of them start long after the 90-second limit by design. While that send is
in progress (``source.send_hold.family_send_active``: at least 10 pieces of
its work remain, the definition the delta wait uses), its waiting delivery
tasks are judged by the send's own progress instead: it is late only once
nothing has settled or been prepared for ``SEND_STALL``, or it is still
going ``SEND_BOUND`` after it fell due. Every other task, and a send's last
few messages, keep the per-task rule.

When the scan is late it says why, in a closed ``due_work`` context that the
checkpoint trigger copies into the CRITICAL ``due_work_lag`` entry: the late
task type, how many and the worst lateness against its limit, or the send's
schedule and revision ids, its remaining and done counts and how long it has
gone without progress (a duration, not a time, so System logs shows no raw
timestamp).
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from django.db import connection, transaction

from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.audit.schemas import ContextKind, sanitize
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.installer_health import MAX_AGE_SECONDS
from parishkit.stewardship.observability import Event, emit_failure

from .due_work_models import DueWorkHealth
from .operational_content import IncidentKind
from .operational_models import OperationalIncident, OperationalLogReceipt
from .operational_sources import configured_policy
from .operational_storage import record_recovery
from .ownership import database_now

MAX_GAP = timedelta(seconds=MAX_AGE_SECONDS)
RECOVERY_WINDOW = timedelta(minutes=5)
# The delivery task type (jobs.family_mail_dispatch.TASK_TYPE, pinned by a
# test); importing that module here would pull mail dispatch into the scan.
DELIVERY_TASK_TYPE = "outbox_delivery"
FAMILY_PURPOSES = ("initial", "reminder")
# A send in progress with nothing settled or prepared for this long has
# stalled. Twice the Send progress panel's five-minute "stalled" window, so
# a brief provider back-off the panel already shows does not by itself start
# the alarm; with the 15-minute escalation, CRITICAL follows 25 minutes with
# no progress at all.
SEND_STALL = timedelta(minutes=10)
# A send still in progress this long after it fell due is late however it
# progresses: the source-staleness allowance a send may hold deltas for
# (send_hold.SEND_ALLOWANCE). A launch-size send takes 30 to 75 minutes.
SEND_BOUND = timedelta(hours=2)
# The least a late entry says when nothing more could be recorded (#633):
# the per-task limit. The checkpoint trigger uses the same context when the
# setting is missing or refused (schema/due_work_health.sql).
FALLBACK_CONTEXT = {"limit_seconds": MAX_AGE_SECONDS}
# The transaction-local setting the checkpoint trigger reads the context from.
CONTEXT_SETTING = "parishkit.due_work_context"
# One send's messages, read through the occurrence's definition index and a
# primary-key lookup per message (as jobs.send_progress does): those still
# to send, those settled, and the send's newest progress. The scheduler may
# read a message's state and delivery task but not its timestamps, so a
# message's times are its delivery task's (by the root index): prepared when
# its first run was created, settled when its last run last changed.
_SETTLED = "('delivered','permanent_failure','delivery_unknown')"
_SEND_EVIDENCE = (
    "SELECT count(*) FILTER (WHERE m.state IN ('pending','retry_wait','submitting')), "
    "count(*) FILTER (WHERE m.state IN " + _SETTLED + "), "
    "GREATEST(max(t.changed) FILTER (WHERE m.state IN " + _SETTLED + "), "
    "max(t.prepared)) "
    "FROM stewardship_schedule_occurrence o "
    "JOIN LATERAL (SELECT m.state, m.task_id FROM stewardship_outbox_message m "
    "WHERE m.id=o.outbox_id LIMIT 1) m ON true "
    "LEFT JOIN LATERAL (SELECT min(r.created_at) AS prepared, "
    "max(r.updated_at) AS changed FROM stewardship_task_run r "
    "WHERE r.root_id=m.task_id) t ON true "
    "WHERE o.definition_id=%s AND o.revision_id=%s AND o.mode=%s "
    "AND o.production_cycle=%s"
)


def _seconds(delta):
    """Whole non-negative seconds, as the closed context stores them."""
    return max(0, int(delta.total_seconds()))


@dataclass(frozen=True)
class SendEvidence:
    """One Family send's progress, read once per sweep.

    ``remaining`` counts its messages still to send (pending, waiting to
    retry or being submitted) and ``done`` those settled (sent, failed or
    uncertain). ``last_progress_at`` is when the newest of them settled or
    was prepared, or None before any exists.
    """

    definition_id: UUID
    revision_id: UUID
    remaining: int
    done: int
    last_progress_at: datetime | None


def send_context(evidence, *, now, lag):
    """The send's ``due_work`` context if it is late by its progress, else None.

    ``lag`` is how long the send's oldest waiting delivery task has been due
    on the scan's clock, so the send fell due at ``now - lag``. Progress from
    before then (a reminder prepared ahead of its due time) does not count.
    Pure, so the thresholds are unit tested without a database.
    """
    started = now - lag
    since = max(evidence.last_progress_at or started, started)
    stall = now - since
    if stall <= SEND_STALL and lag <= SEND_BOUND:
        return None
    context = {
        "task_type": DELIVERY_TASK_TYPE,
        "definition_id": evidence.definition_id,
        "revision_id": evidence.revision_id,
        "remaining_count": evidence.remaining,
        "done_count": evidence.done,
        "stall_seconds": _seconds(stall),
        "elapsed_seconds": _seconds(lag),
        "limit_seconds": _seconds(SEND_STALL if stall > SEND_STALL else SEND_BOUND),
    }
    return sanitize(ContextKind.DUE_WORK, context)


def family_sends(message_ids):
    """Map each Family invitation or reminder message to its send.

    A send is one revision of one Family schedule in one mode and Production
    cycle (as in ``jobs.send_progress``); a Family message's semantic key is
    its occurrence. Other messages (receipts, digests, tests, alerts) are
    left out, so their tasks keep the per-task rule.
    """
    from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence

    from .outbox_models import OutboxMessage

    messages = dict(
        OutboxMessage.objects.filter(
            pk__in=message_ids, purpose__in=FAMILY_PURPOSES
        ).values_list("pk", "semantic_key")
    )
    occurrences = {
        pk: send
        for pk, *send in ScheduleOccurrence.objects.filter(
            pk__in=set(messages.values())
        ).values_list("pk", "definition_id", "revision_id", "mode", "production_cycle")
    }
    return {
        message: tuple(occurrences[key])
        for message, key in messages.items()
        if key in occurrences
    }


def send_evidence(definition_id, revision_id, mode, cycle):
    """Read one send's progress (see ``SendEvidence``)."""
    with connection.cursor() as cursor:
        cursor.execute(_SEND_EVIDENCE, [definition_id, revision_id, mode, cycle])
        remaining, done, last_progress_at = cursor.fetchone()
    return SendEvidence(definition_id, revision_id, remaining, done, last_progress_at)


def send_in_progress():
    """Whether a bulk Family send is in progress, as the delta wait decides it."""
    from parishkit.stewardship.source import send_hold

    return send_hold.family_send_active()


class DueWorkScan:
    """Accumulate one fair sweep; restart or failure discards incomplete proof."""

    def __init__(self):
        """No previous process's cursor or sample is positive progress evidence."""
        self.reset()

    def reset(self):
        """Forget partial progress without clearing any durable incident."""
        self.started_at = None
        self.late = False
        self.uncertain = False
        # Late tasks judged by the per-task rule: how many, and the worst
        # one's (task type, lateness).
        self.late_tasks = 0
        self.worst = None
        # The first late Family send's context, which says more than a count.
        self.stalled = None
        # This page's waiting delivery tasks past MAX_GAP, judged at the end
        # of the page: message id -> lateness of its task.
        self.deferred = {}
        self.deferred_at = None
        # Read at most once per sweep: whether a send is in progress, and
        # each send's verdict (context or None).
        self.active = None
        self.sends = {}

    def begin(self):
        """Use the authoritative clock before visiting the first page."""
        self.reset()
        with transaction.atomic():
            self.started_at = database_now()

    def admitted(self, row, instant):
        """Only freshly admitted overdue work contributes to queue lag.

        A waiting delivery task may be a bulk Family send's backlog, so it
        is set aside and judged with the rest of its page (``_judge``).
        """
        due_at = (
            row.lease_expires_at
            if row.state == "running"
            else row.updated_at
            if row.state == "abandoned"
            else row.not_before
        )
        lag = instant - due_at
        if lag <= MAX_GAP:
            return
        if (
            row.state in {"queued", "retry_wait"}
            and row.task_type == DELIVERY_TASK_TYPE
        ):
            message = row.domain_request_id
            self.deferred[message] = max(lag, self.deferred.get(message, lag))
            self.deferred_at = max(instant, self.deferred_at or instant)
            return
        self._late(row.task_type, lag)

    def _late(self, task_type, lag):
        """Count one task admitted more than ``MAX_GAP`` after it was due."""
        self.late = True
        self.late_tasks += 1
        if self.worst is None or lag > self.worst[1]:
            self.worst = (task_type, lag)

    def _judge(self):
        """Judge this page's set-aside delivery tasks; Family send work by its send.

        While a send is in progress its tasks are late only if the send is
        (``send_context``). Other delivery tasks (receipts, digests, tests,
        alerts), and every task once no send is in progress (its last few
        messages, or delivery paused), keep the per-task rule. Each send and
        the in-progress answer are read once per sweep; the caller owns the
        bounded transaction.

        Each read runs in a savepoint. If telling Family send work apart
        fails, every set-aside task is unknown. If only the send's own
        progress cannot be read, the other delivery tasks are still judged
        by the per-task rule and only the send's tasks are unknown.
        """
        if not self.deferred:
            return
        deferred, self.deferred = self.deferred, {}
        try:
            with transaction.atomic():
                sends = family_sends(list(deferred))
        except Exception as error:
            self._unreadable(error)
            return
        family = {}
        for message, lag in deferred.items():
            if message in sends:
                family[message] = (sends[message], lag)
            else:
                self._late(DELIVERY_TASK_TYPE, lag)
        if not family:
            return
        try:
            with transaction.atomic():
                self._judge_sends(family)
        except Exception as error:
            self._unreadable(error)

    def _judge_sends(self, family):
        """Judge Family send tasks ({message: (send, lag)}) by their sends."""
        if self.active is None:
            self.active = send_in_progress()
        if not self.active:
            for _, lag in family.values():
                self._late(DELIVERY_TASK_TYPE, lag)
            return
        lags = {}
        for send, lag in family.values():
            lags[send] = max(lag, lags.get(send, lag))
        for send, lag in lags.items():
            if send not in self.sends:
                self.sends[send] = send_context(
                    send_evidence(*send), now=self.deferred_at, lag=lag
                )
            if self.sends[send] is not None:
                self.late = True
                self.stalled = self.stalled or self.sends[send]

    def _unreadable(self, error):
        """A failed read leaves its tasks unknown: neither late nor healthy.

        Logged at WARNING, since the scan absorbs it and its cursor still
        advances; a sweep with unknown work cannot certify recovery.
        """
        emit_failure(error, level=logging.WARNING)
        self.unknown()

    def details(self):
        """The ``due_work`` context saying why this sweep is late.

        A late send's context is chosen over the per-task rule's, since it
        says more. When other tasks also broke the per-task rule in the same
        sweep, ``other_late_count`` adds how many. A context that fails its
        own validation, or a late sweep with nothing to name, is reported as
        ``FALLBACK_CONTEXT`` (the per-task limit only) rather than rolling
        back the checkpoint or leaving the entry empty (#633).
        """
        try:
            if self.stalled is not None:
                if not self.late_tasks:
                    return self.stalled
                return self.stalled | {"other_late_count": self.late_tasks}
            if self.worst is None:
                return dict(FALLBACK_CONTEXT)
            task_type, lag = self.worst
            return sanitize(
                ContextKind.DUE_WORK,
                {
                    "task_type": task_type,
                    "count": self.late_tasks,
                    "lag_seconds": _seconds(lag),
                    "limit_seconds": _seconds(MAX_GAP),
                },
            )
        except ValueError:
            return dict(FALLBACK_CONTEXT)

    def unknown(self):
        """An intentional hold or failed read cannot certify a recovered queue."""
        self.uncertain = True

    def interrupted(self, guard):
        """Invalidate positive proof, without rewinding the fair execution cursor."""
        self.begin()
        self.unknown()
        self.finish(guard, complete=True)

    def finish(self, guard, *, complete):
        """Persist negative evidence promptly, positive evidence only at sweep end.

        Partial pages never publish clear evidence. An overlong full sweep also
        cannot certify the current queue, even if its earlier pages were empty.
        """
        guard.check()
        if self.started_at is None:
            return
        if not complete and not self.late and not self.uncertain and not self.deferred:
            return
        with transaction.atomic(), connection.cursor() as cursor:
            # Monitoring must not indefinitely block the execution-hint loop.
            cursor.execute("SET LOCAL lock_timeout='2s'")
            cursor.execute("SET LOCAL statement_timeout='5s'")
            # Its reads use savepoints, so a failed or timed-out read leaves
            # this checkpoint transaction usable and the cursor advancing.
            self._judge()
            if not complete and not self.late and not self.uncertain:
                return
            instant = database_now()
            signal = (
                "late"
                if self.late
                else "unknown"
                if self.uncertain or instant - self.started_at > MAX_GAP
                else "clear"
            )
            # The checkpoint trigger copies this into a CRITICAL entry.
            cursor.execute(
                "SELECT set_config(%s,%s,true)",
                [
                    CONTEXT_SETTING,
                    json.dumps(self.details() if signal == "late" else {}),
                ],
            )
            cursor.execute(
                "INSERT INTO stewardship_due_work_health "
                "(singleton,signal,scan_started_at,escalation_seconds) "
                "VALUES (true,%s,%s,%s) "
                "ON CONFLICT (singleton) DO UPDATE SET signal=EXCLUDED.signal, "
                "scan_started_at=EXCLUDED.scan_started_at, "
                "escalation_seconds=EXCLUDED.escalation_seconds "
                "WHERE stewardship_due_work_health.signal<>EXCLUDED.signal "
                "OR stewardship_due_work_health.escalation_seconds<>"
                "EXCLUDED.escalation_seconds "
                "OR stewardship_due_work_health.observed_at<="
                "clock_timestamp()-interval '30 seconds'",
                [signal, self.started_at, configured_policy().escalation_seconds],
            )
        if complete:
            self.reset()


def needs_due_work_observation():
    """Idle healthy deployments do not create an endless series of intake tasks."""
    return OperationalIncident.objects.filter(
        kind=IncidentKind.SCHEDULER_LAG, resolved_at__isnull=True
    ).exists()


def observe_due_work_health():
    """A fenced collector evaluates current evidence; silence is never recovery."""
    require_work_order()
    if not needs_due_work_observation():
        return
    # The SQL checkpoint trigger takes the same lock. Workers need no UPDATE
    # privilege on a scheduler-owned record, even solely for a row lock.
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(736246,1)")
    sample = DueWorkHealth.objects.first()
    instant = database_now()
    if sample is None or not timedelta(0) <= instant - sample.observed_at <= MAX_GAP:
        return
    if (
        sample.signal == "clear"
        and sample.clear_since is not None
        and sample.scan_started_at - sample.clear_since >= RECOVERY_WINDOW
    ):
        failures = OperationalLog.objects.filter(
            # Boundary producers currently warn, but any CRITICAL input shares
            # this episode classification and must retain the same receipt fence.
            level="CRITICAL",
            event__in=(Event.DUE_WORK_LAG, Event.BOUNDARY_LAG),
        )
        if (
            failures.filter(created_at__gte=sample.clear_since).exists()
            or failures.exclude(
                pk__in=OperationalLogReceipt.objects.values("log_id")
            ).exists()
        ):
            return
        # The collector may be processing older logs after the worker returns;
        # compare original failures, not the later time of incident creation.
        record_recovery(IncidentKind.SCHEDULER_LAG)

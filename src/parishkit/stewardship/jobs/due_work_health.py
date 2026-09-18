"""Admission-aware queue observations, without another thread or provider probe.

Only a complete, timely scan can prove recovery. A held or unreadable task is
unknown, not healthy; a live long-running task is not due and never looks like
an expired worker. PostgreSQL derives continuous windows across scheduler scans.
"""

from datetime import timedelta

from django.db import connection, transaction

from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.installer_health import MAX_AGE_SECONDS
from parishkit.stewardship.observability import Event

from .due_work_models import DueWorkHealth
from .operational_content import IncidentKind
from .operational_models import OperationalIncident, OperationalLogReceipt
from .operational_sources import configured_policy
from .operational_storage import record_recovery
from .ownership import database_now

MAX_GAP = timedelta(seconds=MAX_AGE_SECONDS)
RECOVERY_WINDOW = timedelta(minutes=5)


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

    def begin(self):
        """Use the authoritative clock before visiting the first page."""
        self.reset()
        with transaction.atomic():
            self.started_at = database_now()

    def admitted(self, row, instant):
        """Only freshly admitted overdue work contributes to queue lag."""
        due_at = (
            row.lease_expires_at
            if row.state == "running"
            else row.updated_at
            if row.state == "abandoned"
            else row.not_before
        )
        self.late |= instant - due_at > MAX_GAP

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
        if not complete and not self.late and not self.uncertain:
            return
        with transaction.atomic(), connection.cursor() as cursor:
            # Monitoring must not indefinitely block the execution-hint loop.
            cursor.execute("SET LOCAL lock_timeout='2s'")
            cursor.execute("SET LOCAL statement_timeout='5s'")
            instant = database_now()
            signal = (
                "late"
                if self.late
                else "unknown"
                if self.uncertain or instant - self.started_at > MAX_GAP
                else "clear"
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

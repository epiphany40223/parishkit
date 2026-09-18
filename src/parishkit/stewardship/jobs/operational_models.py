"""Non-personal operational episodes and atomic, append-only notice intent.

SQL owns derived state and occurrence creation. These records grant neither
provider authority nor recipient access; dispatch has a separate owning boundary.
"""

from django.db import models
from django.db.models.functions import Now

from parishkit.stewardship.storage import (
    ImmutableRecord,
    MutableRecord,
    UTCDateTimeField,
)

from .operational_content import AlertPhase, IncidentKind, IncidentLevel


class OperationalIncident(MutableRecord):
    """One deployment-wide episode per closed kind, with policy pinned on opening.

    Producers write only signal_level/action after creation. A database trigger
    computes level/count/timestamps, and resolved episodes cannot be reopened.
    There are no Family identifiers, free-text payloads or recipient fields.
    """

    immutable_fields = MutableRecord.immutable_fields + (
        "kind",
        "first_seen",
        "suppression_seconds",
        "escalation_seconds",
    )
    kind = models.CharField(max_length=32)
    signal_level = models.CharField(max_length=8)
    action = models.CharField(max_length=8, default="observe", db_default="observe")
    suppression_seconds = models.PositiveIntegerField()
    escalation_seconds = models.PositiveIntegerField()
    level = models.CharField(max_length=8, default="WARNING", db_default="WARNING")
    first_seen = UTCDateTimeField(db_default=Now())
    last_seen = UTCDateTimeField(db_default=Now())
    occurrences = models.PositiveBigIntegerField(default=1, db_default=1)
    last_notice_at = UTCDateTimeField(null=True)
    resolved_at = UTCDateTimeField(null=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_ops_incident"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=["kind"],
                condition=models.Q(resolved_at__isnull=True),
                name="ops_incident_active_kind",
            ),
            models.CheckConstraint(
                condition=models.Q(kind__in=tuple(IncidentKind)),
                name="ops_incident_kind",
            ),
            models.CheckConstraint(
                condition=models.Q(level__in=tuple(IncidentLevel))
                & models.Q(signal_level__in=tuple(IncidentLevel)),
                name="ops_incident_levels",
            ),
            models.CheckConstraint(
                condition=models.Q(action__in=("observe", "resolve")),
                name="ops_incident_action",
            ),
            models.CheckConstraint(
                condition=models.Q(suppression_seconds__range=(60, 86400))
                & models.Q(escalation_seconds__range=(60, 86400)),
                name="ops_incident_windows",
            ),
            models.CheckConstraint(
                condition=models.Q(occurrences__gte=1)
                & models.Q(last_seen__gte=models.F("first_seen"))
                & (
                    models.Q(resolved_at__isnull=True)
                    | models.Q(resolved_at__gte=models.F("last_seen"))
                )
                & (
                    models.Q(level="WARNING", last_notice_at__isnull=True)
                    | models.Q(
                        level="CRITICAL", last_notice_at__gte=models.F("first_seen")
                    )
                    & models.Q(last_notice_at__isnull=False)
                ),
                name="ops_incident_shape",
            ),
        ]


class OperationalNotice(ImmutableRecord):
    """Exactly one immutable notice for a notifying episode version, not a send."""

    incident = models.ForeignKey(OperationalIncident, on_delete=models.PROTECT)
    incident_version = models.PositiveBigIntegerField()
    phase = models.CharField(max_length=9)
    level = models.CharField(max_length=8)
    first_seen = UTCDateTimeField()
    observed_at = UTCDateTimeField()
    occurrences = models.PositiveBigIntegerField()

    class Meta:
        db_table = "stewardship_ops_notice"
        constraints = [
            models.UniqueConstraint(
                fields=["incident", "incident_version"], name="ops_notice_version"
            ),
            models.CheckConstraint(
                condition=models.Q(phase__in=tuple(AlertPhase)),
                name="ops_notice_phase",
            ),
            models.CheckConstraint(
                condition=models.Q(level="CRITICAL")
                & models.Q(incident_version__gte=1)
                & models.Q(occurrences__gte=1)
                & models.Q(observed_at__gte=models.F("first_seen")),
                name="ops_notice_shape",
            ),
        ]


class OperationalLogReceipt(ImmutableRecord):
    """Exactly-once handoff of an immutable critical log, including SQL producers."""

    log = models.OneToOneField(
        "stewardship_audit.OperationalLog", on_delete=models.PROTECT
    )
    incident = models.ForeignKey(OperationalIncident, on_delete=models.PROTECT)
    incident_version = models.PositiveBigIntegerField()
    run = models.ForeignKey("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    fence = models.PositiveBigIntegerField()
    worker_id = models.UUIDField()

    class Meta:
        db_table = "stewardship_ops_log_receipt"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(incident_version__gte=1) & models.Q(fence__gte=1),
                name="ops_log_receipt_version",
            ),
        ]


class OperationalCohort(ImmutableRecord):
    """One frozen current-Admin cohort per notice, captured by its fenced worker."""

    notice = models.OneToOneField(OperationalNotice, on_delete=models.PROTECT)
    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion", on_delete=models.PROTECT
    )
    parish = models.ForeignKey("stewardship_accounts.Parish", on_delete=models.PROTECT)
    mode = models.CharField(max_length=16)
    addresses = models.JSONField()
    recipient_count = models.PositiveIntegerField()
    slack_channel = models.CharField(max_length=64, null=True)
    run = models.ForeignKey("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    fence = models.PositiveBigIntegerField()
    worker_id = models.UUIDField()

    class Meta:
        db_table = "stewardship_ops_cohort"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(mode__in=("testing", "production")),
                name="ops_cohort_mode",
            ),
            models.CheckConstraint(
                condition=models.Q(recipient_count__gte=1, fence__gte=1),
                name="ops_cohort_counts",
            ),
        ]


class OperationalRecipient(ImmutableRecord):
    """Atomic one-Admin email intent; current authorization is rechecked at send."""

    cohort = models.ForeignKey(OperationalCohort, on_delete=models.PROTECT)
    address = models.EmailField()
    outbox = models.OneToOneField(
        "stewardship_jobs.OutboxMessage", on_delete=models.PROTECT
    )

    class Meta:
        db_table = "stewardship_ops_recipient"
        constraints = [
            models.UniqueConstraint(
                fields=["cohort", "address"], name="ops_recipient_address"
            ),
        ]

"""One bounded, non-personal observation of the owned scheduler's due-work scan."""

from django.db import models
from django.db.models.functions import Now

from parishkit.stewardship.storage import UTCDateTimeField


class DueWorkHealth(models.Model):
    """Latest evidence only; durable incident/notice history has a separate owner.

    SQL derives observation times and continuous windows. The scheduler can
    submit a scan verdict, but cannot choose a healthy or unhealthy start time.
    No task identity, provider data or user content belongs in this record.
    """

    singleton = models.BooleanField(primary_key=True, default=True)
    signal = models.CharField(max_length=7)
    scan_started_at = UTCDateTimeField()
    observed_at = UTCDateTimeField(db_default=Now())
    late_since = UTCDateTimeField(null=True)
    clear_since = UTCDateTimeField(null=True)
    last_failure_at = UTCDateTimeField(null=True)
    escalation_seconds = models.PositiveIntegerField()

    class Meta:
        db_table = "stewardship_due_work_health"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(escalation_seconds__range=(60, 86400)),
                name="due_health_escalation",
            ),
            models.CheckConstraint(
                condition=models.Q(singleton=True), name="due_health_singleton"
            ),
            models.CheckConstraint(
                condition=models.Q(signal__in=("late", "clear", "unknown")),
                name="due_health_signal",
            ),
            models.CheckConstraint(
                condition=models.Q(scan_started_at__lte=models.F("observed_at"))
                & (
                    models.Q(late_since__isnull=True)
                    | models.Q(late_since__lte=models.F("observed_at"))
                )
                & (
                    models.Q(clear_since__isnull=True)
                    | models.Q(clear_since__lte=models.F("scan_started_at"))
                ),
                name="due_health_times",
            ),
        ]

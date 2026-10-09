"""The latest result of the scheduler's web liveness probe (#392 L1)."""

from django.db import models

from parishkit.stewardship.storage import UTCDateTimeField


class WebHealth(models.Model):
    """One row: whether web answered its liveness check, and for how long.

    The scheduler submits only ``healthy``. The SQL trigger installed by
    frozen file 0019 (``stewardship_web_health_v1``) sets the observation
    time, counts consecutive failed minutes and keeps when the current run of
    failures or passes began, so no caller can choose a count or a start
    time. It also writes the CRITICAL ``web_unhealthy`` entry from the third
    failed minute on. No request, address or response body is stored.
    """

    singleton = models.BooleanField(primary_key=True, default=True)
    healthy = models.BooleanField()
    observed_at = UTCDateTimeField()
    failures = models.PositiveIntegerField()
    failing_since = UTCDateTimeField(null=True)
    passing_since = UTCDateTimeField(null=True)

    class Meta:
        db_table = "stewardship_web_health"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(singleton=True), name="web_health_singleton"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        healthy=True,
                        failures=0,
                        failing_since__isnull=True,
                        passing_since__isnull=False,
                        passing_since__lte=models.F("observed_at"),
                    )
                    | models.Q(
                        healthy=False,
                        failures__gte=1,
                        passing_since__isnull=True,
                        failing_since__isnull=False,
                        failing_since__lte=models.F("observed_at"),
                    )
                ),
                name="web_health_shape",
            ),
        ]

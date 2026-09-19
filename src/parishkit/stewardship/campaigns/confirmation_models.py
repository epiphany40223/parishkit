"""Metadata guards for short, exact Production confirmation."""

from django.db import models


class ActivationImpactRevision(models.Model):
    """SQL-maintained change clock; no Family identities or message payloads.

    Relevant statement triggers create the singleton lazily and advance it in
    the writer's transaction. Runtime roles can read but cannot reset it. A
    global clock conservatively invalidates previews for historical mail changes
    too, avoiding missing cross-campaign acceptance/restore dependencies.
    """

    singleton = models.BooleanField(primary_key=True, default=True, editable=False)
    version = models.PositiveBigIntegerField(default=1, db_default=1)

    class Meta:
        db_table = "stewardship_activation_impact"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(singleton=True), name="activation_impact_singleton"
            ),
            models.CheckConstraint(
                condition=models.Q(version__gte=1), name="activation_impact_positive"
            ),
        ]

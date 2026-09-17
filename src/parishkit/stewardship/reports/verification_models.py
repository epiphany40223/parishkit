"""Durable check identities without permanent references to disposable facts."""

from django.db import models

from parishkit.stewardship.storage import ImmutableRecord


class FactVerificationRequest(ImmutableRecord):
    """One scheduled UTC-day check of an exact, protected ready generation."""

    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    task = models.OneToOneField("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    fact_set_id = models.UUIDField()
    scheduled_day = models.DateField()
    population_scope = models.CharField(max_length=12)
    source_id = models.UUIDField()
    source_generation = models.PositiveBigIntegerField()
    submission_watermark = models.PositiveBigIntegerField()
    timezone_configuration_id = models.UUIDField()
    through_date = models.DateField()

    class Meta:
        db_table = "stewardship_fact_verification_request"
        constraints = [
            models.UniqueConstraint(
                fields=("fact_set_id", "scheduled_day"), name="fact_verification_day"
            ),
            models.CheckConstraint(
                condition=models.Q(population_scope__in=("historical", "current")),
                name="fact_verification_scope",
            ),
        ]


class FactVerificationResult(ImmutableRecord):
    """A completed check, not an assertion that the task's facts necessarily match."""

    request = models.OneToOneField(FactVerificationRequest, on_delete=models.PROTECT)
    run = models.ForeignKey("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    fence = models.PositiveBigIntegerField()
    worker_id = models.UUIDField()
    outcome = models.CharField(max_length=8)
    differing_days = models.PositiveIntegerField()

    class Meta:
        db_table = "stewardship_fact_verification_result"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(outcome="matched", differing_days=0)
                | models.Q(outcome="drift", differing_days__gt=0),
                name="fact_verification_outcome",
            ),
        ]

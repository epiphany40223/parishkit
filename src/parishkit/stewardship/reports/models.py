"""Immutable report generations and durable demand, not a calculation worker/UI.

The exact input tuple includes the campaign-local through date required by the
graph's today bound. A midnight rollover cannot reuse yesterday's generation
merely because source/submission watermarks are unchanged. Child rows and ready
headers are immutable; reference/current/build ownership controls compaction.
"""

from django.db import models

from parishkit.stewardship.storage import (
    ImmutableRecord,
    MutableRecord,
    UTCDateTimeField,
)

POPULATION_SCOPES = ("historical", "current")


class FactCompactionRecord(ImmutableRecord):
    """Permanent non-PII generation keys/counts, never duplicate calculated rows."""

    campaign_id = models.UUIDField()
    fact_set_id = models.UUIDField(unique=True)
    population_scope = models.CharField(max_length=12)
    source_generation = models.PositiveBigIntegerField()
    submission_watermark = models.PositiveBigIntegerField()
    timezone_configuration_id = models.UUIDField()
    through_date = models.DateField()
    row_count = models.PositiveIntegerField()
    task = models.ForeignKey("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    task_fence = models.PositiveBigIntegerField()
    worker_id = models.UUIDField()

    class Meta:
        db_table = "stewardship_fact_compaction"


class CampaignDailyFactSet(MutableRecord):
    """A complete calculation for frozen inputs, with recoverable build ownership."""

    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    population_scope = models.CharField(max_length=12)
    source = models.ForeignKey(
        "stewardship_source.SourceSnapshot", on_delete=models.PROTECT
    )
    source_generation = models.PositiveBigIntegerField()
    submission_watermark = models.PositiveBigIntegerField()
    timezone_configuration = models.ForeignKey(
        "stewardship_campaigns.CampaignConfiguration", on_delete=models.PROTECT
    )
    through_date = models.DateField()
    first_date = models.DateField(null=True, blank=True)
    last_date = models.DateField(null=True, blank=True)
    expected_count = models.PositiveIntegerField()
    state = models.CharField(max_length=8, default="building")
    task = models.ForeignKey("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    task_fence = models.PositiveBigIntegerField()
    worker_id = models.UUIDField()
    ready_at = UTCDateTimeField(null=True, blank=True)
    failure_code = models.CharField(max_length=48, blank=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_daily_fact_set"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=(
                    "campaign",
                    "population_scope",
                    "source_generation",
                    "submission_watermark",
                    "timezone_configuration",
                    "through_date",
                ),
                name="daily_fact_exact_inputs",
            ),
            models.CheckConstraint(
                condition=models.Q(population_scope__in=POPULATION_SCOPES),
                name="daily_fact_scope",
            ),
            models.CheckConstraint(
                condition=models.Q(state__in=("building", "ready", "failed")),
                name="daily_fact_state",
            ),
            models.CheckConstraint(
                condition=models.Q(source_generation__gt=0, task_fence__gt=0),
                name="daily_fact_positive_fences",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    expected_count=0, first_date__isnull=True, last_date__isnull=True
                )
                | models.Q(
                    expected_count__gt=0,
                    first_date__isnull=False,
                    last_date__isnull=False,
                    last_date__gte=models.F("first_date"),
                ),
                name="daily_fact_expected_dates",
            ),
            models.CheckConstraint(
                condition=models.Q(state="ready", ready_at__isnull=False)
                | (~models.Q(state="ready") & models.Q(ready_at__isnull=True)),
                name="daily_fact_ready_instant",
            ),
            models.CheckConstraint(
                condition=models.Q(failure_code="")
                | models.Q(failure_code__regex=r"^[a-z][a-z0-9_]{0,47}$"),
                name="daily_fact_failure_code",
            ),
        ]
        indexes = [
            models.Index(
                fields=("campaign", "population_scope", "state"),
                name="daily_fact_lookup",
            )
        ]


class CampaignDailyFact(ImmutableRecord):
    """One local day's counts/percentage inputs and exact annual pledge total."""

    fact_set = models.ForeignKey(
        CampaignDailyFactSet, on_delete=models.PROTECT, related_name="days"
    )
    local_date = models.DateField()
    first_responses = models.PositiveBigIntegerField()
    cumulative_responses = models.PositiveBigIntegerField()
    cohort_denominator = models.PositiveBigIntegerField()
    source_generation = models.PositiveBigIntegerField(null=True, blank=True)
    source_as_of = UTCDateTimeField(null=True, blank=True)
    population_available = models.BooleanField()
    pledge_available = models.BooleanField()
    pledge_total = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )

    class Meta:
        db_table = "stewardship_daily_fact"
        constraints = [
            models.UniqueConstraint(
                fields=("fact_set", "local_date"), name="daily_fact_date"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    first_responses__lte=models.F("cumulative_responses"),
                    cumulative_responses__lte=models.F("cohort_denominator"),
                ),
                name="daily_fact_count_bounds",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    population_available=False,
                    source_generation__isnull=True,
                    source_as_of__isnull=True,
                    first_responses=0,
                    cumulative_responses=0,
                    cohort_denominator=0,
                )
                | models.Q(
                    population_available=True,
                    source_generation__gt=0,
                    source_generation__isnull=False,
                    source_as_of__isnull=False,
                ),
                name="daily_fact_source_availability",
            ),
            models.CheckConstraint(
                condition=models.Q(pledge_available=False, pledge_total__isnull=True)
                | models.Q(
                    pledge_available=True,
                    population_available=True,
                    pledge_total__isnull=False,
                    pledge_total__gte=0,
                    pledge_total__lte="9999999999999999.99",
                ),
                name="daily_fact_pledge_availability",
            ),
        ]


class CampaignFactPointer(MutableRecord):
    """Interactive selection advances only to complete, non-regressing inputs."""

    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    population_scope = models.CharField(max_length=12)
    fact_set = models.ForeignKey(CampaignDailyFactSet, on_delete=models.PROTECT)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_fact_pointer"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=("campaign", "population_scope"), name="fact_pointer_scope"
            ),
            models.CheckConstraint(
                condition=models.Q(population_scope__in=POPULATION_SCOPES),
                name="fact_pointer_known_scope",
            ),
        ]


class CampaignFactPin(MutableRecord):
    """An explicit retained report/digest/work parent; file expiry never ends it."""

    fact_set = models.ForeignKey(CampaignDailyFactSet, on_delete=models.PROTECT)
    parent_kind = models.CharField(max_length=16)
    parent_id = models.UUIDField()

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_fact_pin"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=("fact_set", "parent_kind", "parent_id"), name="fact_pin_parent"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    parent_kind__in=(
                        "export",
                        "digest",
                        "render",
                        "verification",
                        "work",
                        "operator",
                    )
                ),
                name="fact_pin_kind",
            ),
        ]


class CampaignFactRebuildDemand(MutableRecord):
    """Coalesce pending event windows without erasing demand arriving during a build."""

    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    population_scope = models.CharField(max_length=12)
    requested_source = models.ForeignKey(
        "stewardship_source.SourceSnapshot", on_delete=models.PROTECT
    )
    requested_source_generation = models.PositiveBigIntegerField()
    requested_submission_watermark = models.PositiveBigIntegerField()
    requested_timezone_configuration = models.ForeignKey(
        "stewardship_campaigns.CampaignConfiguration", on_delete=models.PROTECT
    )
    requested_through_date = models.DateField()
    pending_revision = models.PositiveBigIntegerField(default=1)
    pending_first_at = UTCDateTimeField(null=True, blank=True)
    pending_last_at = UTCDateTimeField(null=True, blank=True)
    pending_due_at = UTCDateTimeField(null=True, blank=True)
    claimed_revision = models.PositiveBigIntegerField(default=0)
    claimed_generation = models.ForeignKey(
        CampaignDailyFactSet, on_delete=models.PROTECT, null=True, blank=True
    )
    claimed_task = models.ForeignKey(
        "stewardship_jobs.TaskRun", on_delete=models.PROTECT, null=True, blank=True
    )
    claimed_task_fence = models.PositiveBigIntegerField(null=True, blank=True)
    claimed_worker_id = models.UUIDField(null=True, blank=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_fact_demand"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=("campaign", "population_scope"), name="fact_demand_scope"
            ),
            models.CheckConstraint(
                condition=models.Q(population_scope__in=POPULATION_SCOPES),
                name="fact_demand_known_scope",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    pending_revision__gte=models.F("claimed_revision"),
                    requested_source_generation__gt=0,
                ),
                name="fact_demand_revision_order",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    pending_first_at__isnull=True,
                    pending_last_at__isnull=True,
                    pending_due_at__isnull=True,
                )
                | models.Q(
                    pending_first_at__isnull=False,
                    pending_last_at__isnull=False,
                    pending_due_at__isnull=False,
                    pending_last_at__gte=models.F("pending_first_at"),
                    pending_due_at__gte=models.F("pending_first_at"),
                ),
                name="fact_demand_window_shape",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    claimed_generation__isnull=True,
                    claimed_task__isnull=True,
                    claimed_task_fence__isnull=True,
                    claimed_worker_id__isnull=True,
                )
                | models.Q(
                    claimed_generation__isnull=False,
                    claimed_task__isnull=False,
                    claimed_task_fence__isnull=False,
                    claimed_task_fence__gt=0,
                    claimed_worker_id__isnull=False,
                    claimed_revision__gt=0,
                ),
                name="fact_demand_claim_shape",
            ),
        ]
        indexes = [models.Index(fields=("pending_due_at",), name="fact_demand_due")]

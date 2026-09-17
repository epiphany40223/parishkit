"""Durable daily preparation, exact input ownership and per-Admin delivery intent.

Preparation metadata survives Testing cleanup so a delayed hint can be cancelled
without reviving an old epoch. Private snapshots, rendered content and recipient
bindings instead belong to their campaign/mode retention and cleanup owner.
"""

from django.db import models

from parishkit.stewardship.storage import (
    ImmutableRecord,
    MutableRecord,
    UTCDateTimeField,
)


class DailyDigestPreparation(MutableRecord):
    """One finite discovery/coverage/build/fanout operation under a stable task root.

    Discovery and coalescing advance in bounded transactions before any snapshot
    or provider work is released. UUID references intentionally retain only
    opaque cancellation evidence after their Testing owners are removed.
    """

    campaign_id = models.UUIDField()
    definition_id = models.UUIDField()
    revision_id = models.UUIDField()
    campaign_configuration_id = models.UUIDField()
    task_id = models.UUIDField(unique=True)
    mode = models.CharField(max_length=16)
    rehearsal_epoch_id = models.UUIDField(null=True)
    cutoff = UTCDateTimeField()
    cursor = models.DateField(null=True)
    phase = models.CharField(max_length=12, default="dates")
    occurrence_id = models.UUIDField(null=True, unique=True)
    run_id = models.UUIDField(null=True)
    task_fence = models.PositiveBigIntegerField(null=True)
    worker_id = models.UUIDField(null=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_daily_digest_preparation"
        constraints = MutableRecord.Meta.constraints + [
            models.CheckConstraint(
                condition=models.Q(
                    phase__in=(
                        "dates",
                        "cover",
                        "facts",
                        "fanout",
                        "complete",
                        "cancelled",
                    )
                ),
                name="daily_digest_phase",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(mode="production", rehearsal_epoch_id__isnull=True)
                    | models.Q(mode="testing", rehearsal_epoch_id__isnull=False)
                ),
                name="daily_digest_namespace",
            ),
        ]
        indexes = [
            models.Index(
                fields=("definition_id", "mode"), name="daily_digest_definition"
            )
        ]


class DailyDigestSnapshot(ImmutableRecord):
    """The once-captured private observation, not a pointer to live statistics."""

    preparation = models.OneToOneField(DailyDigestPreparation, on_delete=models.PROTECT)
    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion",
        on_delete=models.PROTECT,
    )
    source = models.ForeignKey(
        "stewardship_source.SourceSnapshot", on_delete=models.PROTECT
    )
    population_scope = models.CharField(max_length=12, default="historical")
    submission_watermark = models.PositiveBigIntegerField()
    timezone_configuration = models.ForeignKey(
        "stewardship_campaigns.CampaignConfiguration",
        on_delete=models.PROTECT,
    )
    through_date = models.DateField()
    observed_at = UTCDateTimeField()
    statistics_inputs = models.TextField()
    covered_dates = models.JSONField()
    run = models.ForeignKey("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    fence = models.PositiveBigIntegerField()
    worker_id = models.UUIDField()

    class Meta:
        db_table = "stewardship_daily_digest_snapshot"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(population_scope="historical"),
                name="daily_digest_historical_scope",
            )
        ]


class DailyDigestReady(ImmutableRecord):
    """One complete exact generation and compiled report before recipient fanout.

    The recipient selection is separately bound to its current configuration at
    generation; later dispatch rechecks each address's authority. The chart is
    retained as bytes, so retries do not silently change images after upgrades.
    """

    snapshot = models.OneToOneField(DailyDigestSnapshot, on_delete=models.PROTECT)
    fact_set = models.ForeignKey("CampaignDailyFactSet", on_delete=models.PROTECT)
    recipient_configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion",
        on_delete=models.PROTECT,
    )
    recipients = models.JSONField()
    subject = models.CharField(max_length=254)
    html = models.TextField()
    text = models.TextField()
    chart = models.BinaryField()
    run = models.ForeignKey("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    fence = models.PositiveBigIntegerField()
    worker_id = models.UUIDField()

    class Meta:
        db_table = "stewardship_daily_digest_ready"


class DailyDigestRecipient(ImmutableRecord):
    """One separately addressed semantic message; no implicit cross-Admin resend."""

    ready = models.ForeignKey(DailyDigestReady, on_delete=models.PROTECT)
    address = models.CharField(max_length=254)
    outbox = models.OneToOneField(
        "stewardship_jobs.OutboxMessage", on_delete=models.PROTECT
    )

    class Meta:
        db_table = "stewardship_daily_digest_recipient"
        constraints = [
            models.UniqueConstraint(
                fields=("ready", "address"),
                name="daily_digest_recipient_once",
            )
        ]

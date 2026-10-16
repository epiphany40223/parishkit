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

    ``comparison`` is the Compared with past campaigns section when the
    schedule turned it on and a past campaign was imported (#1018 slice 7):
    the section's protected figures, chart specifications and each imported
    year's checksum (``digest_comparison.section``), so the saved page shows
    what was sent whatever is imported or removed later. Its two charts are
    retained as PNG bytes like the participation chart (the pledges chart
    only with Financial stewardship). All three are null otherwise, as on
    every digest compiled before the section existed. A digest sent with
    its charts withheld (#477) keeps the section with no charts and no
    images.

    ``funnel_chart`` and ``activity_chart`` are the Response funnel
    section's two chart images (#477 slice 2), retained as PNG bytes for the
    same reason, by Production digests compiled while the section had them.
    #1370 removed them: every digest compiled since leaves both null (as do
    Testing digests, digests sent with their charts withheld and those
    compiled before slice 2), and the columns stay so an older ready row is
    still sent with its images.
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
    comparison = models.JSONField(null=True)
    comparison_families_chart = models.BinaryField(null=True)
    comparison_pledges_chart = models.BinaryField(null=True)
    funnel_chart = models.BinaryField(null=True)
    activity_chart = models.BinaryField(null=True)

    class Meta:
        db_table = "stewardship_daily_digest_ready"
        constraints = [
            # No section, no images; a pledges chart needs the Families
            # chart. A section may have none: a digest sent with its charts
            # withheld (#477) keeps its section without charts.
            models.CheckConstraint(
                condition=(
                    (
                        models.Q(comparison__isnull=False)
                        | models.Q(
                            comparison_families_chart__isnull=True,
                            comparison_pledges_chart__isnull=True,
                        )
                    )
                    & (
                        models.Q(comparison_pledges_chart__isnull=True)
                        | models.Q(comparison_families_chart__isnull=False)
                    )
                ),
                name="daily_digest_comparison_charts",
            ),
            # The funnel's two charts are kept together or not at all.
            models.CheckConstraint(
                condition=(
                    models.Q(funnel_chart__isnull=True, activity_chart__isnull=True)
                    | models.Q(funnel_chart__isnull=False, activity_chart__isnull=False)
                ),
                name="daily_digest_funnel_charts",
            ),
        ]

    def images(self):
        """Every server-drawn chart's (content id, PNG bytes) pair, in email order.

        The Response funnel section's two charts, when retained, then the
        Compared with past campaigns section's (``SERVER_CHART_IDS``).
        """
        from parishkit.stewardship.web.digest_content import FUNNEL_CHART_IDS

        funnel = ()
        if self.funnel_chart is not None:
            charts = (self.funnel_chart, self.activity_chart)
            funnel = tuple(
                (name, bytes(data))
                for name, data in zip(FUNNEL_CHART_IDS, charts, strict=True)
            )
        return funnel + self.comparison_images()

    def comparison_images(self):
        """The section's (content id, PNG bytes) pairs, in the email's order."""
        from parishkit.stewardship.web.digest_content import COMPARISON_CHART_IDS

        charts = (self.comparison_families_chart, self.comparison_pledges_chart)
        return tuple(
            (name, bytes(data))
            for name, data in zip(COMPARISON_CHART_IDS, charts, strict=True)
            if data is not None
        )


class DailyDigestRecipient(ImmutableRecord):
    """One Admin intent, or exact accepted-message coverage requiring no resend."""

    ready = models.ForeignKey(DailyDigestReady, on_delete=models.PROTECT)
    address = models.CharField(max_length=254)
    outbox = models.OneToOneField(
        "stewardship_jobs.OutboxMessage", on_delete=models.PROTECT, null=True
    )
    covered_messages = models.JSONField(default=list)

    class Meta:
        db_table = "stewardship_daily_digest_recipient"
        constraints = [
            models.UniqueConstraint(
                fields=("ready", "address"),
                name="daily_digest_recipient_once",
            )
        ]

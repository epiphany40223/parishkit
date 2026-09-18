"""Weekly reporting ownership, retained observation and per-Admin item coverage.

Opaque preparation survives Testing cleanup; private snapshots and compiled
recipient rows belong to the campaign/mode cleanup owner. Provider acceptance
stays in the existing outbox journal, never a mutable report boolean.
"""

from django.db import models

from parishkit.stewardship.storage import (
    ImmutableRecord,
    MutableRecord,
    UTCDateTimeField,
)


class WeeklyManualRequest(ImmutableRecord):
    """One explicit Admin command, sharing its UUID with the new preparation.

    This retains only opaque authority/task history, not report content. Its
    private SQL insertion owner allocates the independent manual occurrence and
    preparation together. Replaying the command cannot allocate another report.
    """

    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion", on_delete=models.PROTECT
    )
    task = models.OneToOneField("stewardship_jobs.TaskRun", on_delete=models.PROTECT)

    class Meta:
        db_table = "stewardship_weekly_manual_request"


class WeeklyDigestPreparation(MutableRecord):
    """One finite schedule discovery, capture and fanout under a stable task root."""

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
        db_table = "stewardship_weekly_digest_preparation"
        constraints = MutableRecord.Meta.constraints + [
            models.CheckConstraint(
                condition=models.Q(
                    phase__in=(
                        "dates",
                        "cover",
                        "capture",
                        "fanout",
                        "complete",
                        "cancelled",
                    )
                ),
                name="weekly_digest_phase",
            ),
            models.CheckConstraint(
                condition=models.Q(mode="production", rehearsal_epoch_id__isnull=True)
                | models.Q(mode="testing", rehearsal_epoch_id__isnull=False),
                name="weekly_digest_namespace",
            ),
        ]
        indexes = [
            models.Index(
                fields=("definition_id", "mode"), name="weekly_digest_definition"
            )
        ]


class WeeklyDigestSnapshot(ImmutableRecord):
    """Frozen report inputs, interval history and generation-time Admin cohort.

    Copied source names and immutable item text are self-contained; compilation does
    not depend on membership rows surviving source compaction. Information IDs and
    correction pairs identify the exact subset chosen from the coherent observation.
    """

    preparation = models.OneToOneField(
        WeeklyDigestPreparation, on_delete=models.PROTECT
    )
    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion", on_delete=models.PROTECT
    )
    timezone_configuration = models.ForeignKey(
        "stewardship_campaigns.CampaignConfiguration", on_delete=models.PROTECT
    )
    source = models.ForeignKey(
        "stewardship_source.SourceSnapshot", on_delete=models.PROTECT
    )
    observed_at = UTCDateTimeField()
    submission_watermark = models.PositiveBigIntegerField()
    after_watermark = models.PositiveBigIntegerField()
    observation = models.JSONField()
    information = models.JSONField()
    corrections = models.JSONField()
    recipients = models.JSONField()
    run = models.ForeignKey("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    fence = models.PositiveBigIntegerField()
    worker_id = models.UUIDField()

    class Meta:
        db_table = "stewardship_weekly_digest_snapshot"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    after_watermark__lte=models.F("submission_watermark")
                ),
                name="weekly_digest_watermark_order",
            )
        ]


class WeeklyDigestRecipient(ImmutableRecord):
    """One original Admin's selected subset and exact accepted-message coverage.

    The outbox is absent only when earlier accepted messages cover every selected
    item/disposition. A partial cover omits those rows from this recipient's compiled
    content without changing the full protected report. MAIL reads the bounded
    compiled fields, not raw live requests or other Families' submitted answers.
    """

    snapshot = models.ForeignKey(WeeklyDigestSnapshot, on_delete=models.PROTECT)
    address = models.CharField(max_length=254)
    information = models.JSONField()
    corrections = models.JSONField()
    covered_messages = models.JSONField(default=list)
    subject = models.CharField(max_length=254)
    html = models.TextField()
    text = models.TextField()
    outbox = models.OneToOneField(
        "stewardship_jobs.OutboxMessage", on_delete=models.PROTECT, null=True
    )

    class Meta:
        db_table = "stewardship_weekly_digest_recipient"
        constraints = [
            models.UniqueConstraint(
                fields=("snapshot", "address"), name="weekly_digest_recipient_once"
            )
        ]

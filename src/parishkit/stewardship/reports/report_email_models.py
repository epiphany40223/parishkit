"""Report email sends: preparation, snapshot, recipients and manual requests.

The report-emails spec (#1786, "Report send records" and "Report
pipeline") holds one send's work in these four tables, which replace the
daily and weekly digest tables for new sends. Their writes are admitted by
the guards of frozen migration file 0156, which also
owns the tables; nothing writes them until the conversion rewrites the
shared guards that admit a report's occurrence, outbox and pin writes.

A preparation is opaque ownership metadata, as a digest preparation is;
the snapshot is the send's only copy of Family data, and the recipient rows
hold addresses, never content (each message's own render does).
"""

from django.db import models

from parishkit.stewardship.storage import (
    ImmutableRecord,
    MutableRecord,
    UTCDateTimeField,
)

PHASES = ("dates", "cover", "inputs", "capture", "fanout", "complete", "cancelled")


class ReportEmailManualRequest(ImmutableRecord):
    """One Administrator's Send now, keyed by its request id.

    Its guard allocates the manual occurrence and the preparation together,
    so replaying the request returns the same send. ``revision_id`` is the
    report revision the confirmation was bound to.
    """

    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    definition_id = models.UUIDField()
    revision_id = models.UUIDField()
    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion", on_delete=models.PROTECT
    )
    task = models.OneToOneField("stewardship_jobs.TaskRun", on_delete=models.PROTECT)

    class Meta:
        db_table = "stewardship_report_email_manual_request"


class ReportEmailPreparation(MutableRecord):
    """One send's fenced progress, from its slots to its completed cohort.

    The input fields are recorded once, when the send records its inputs
    (``inputs`` to ``capture``), and never change: the covered slots, the
    as-of kind and moment, the capture instant, the report day (None
    before the campaign's first day has ended), and the source, watermark,
    zone configuration and fact ``through_date`` its fact blocks pin
    (``through_date`` None without a fact block or a report day).
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
    covered_slots = models.JSONField(null=True)
    as_of_kind = models.CharField(max_length=16, null=True)
    as_of = UTCDateTimeField(null=True)
    captured_at = UTCDateTimeField(null=True)
    report_day = models.DateField(null=True)
    source_id = models.UUIDField(null=True)
    submission_watermark = models.PositiveBigIntegerField(null=True)
    timezone_configuration_id = models.UUIDField(null=True)
    through_date = models.DateField(null=True)

    # The fact inputs (``facts.fact_inputs``) of a report are always the
    # historical population, as a digest's are.
    population_scope = "historical"

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_report_email_preparation"
        constraints = MutableRecord.Meta.constraints + [
            models.CheckConstraint(
                condition=models.Q(phase__in=PHASES), name="report_email_phase"
            ),
            models.CheckConstraint(
                condition=models.Q(mode="production", rehearsal_epoch_id__isnull=True)
                | models.Q(mode="testing", rehearsal_epoch_id__isnull=False),
                name="report_email_namespace",
            ),
            # The inputs are recorded together or not at all; the fact
            # pins only with a source.
            models.CheckConstraint(
                condition=models.Q(
                    covered_slots__isnull=True,
                    as_of_kind__isnull=True,
                    as_of__isnull=True,
                    captured_at__isnull=True,
                    report_day__isnull=True,
                    source_id__isnull=True,
                    submission_watermark__isnull=True,
                    timezone_configuration_id__isnull=True,
                    through_date__isnull=True,
                )
                | models.Q(
                    covered_slots__isnull=False,
                    as_of_kind__in=("previous_day_end", "send_time"),
                    as_of__isnull=False,
                    captured_at__isnull=False,
                    source_id__isnull=False,
                    submission_watermark__isnull=False,
                    timezone_configuration_id__isnull=False,
                ),
                name="report_email_inputs",
            ),
        ]
        indexes = [
            models.Index(
                fields=("definition_id", "mode"), name="report_email_definition"
            )
        ]


class ReportEmailSnapshot(ImmutableRecord):
    """One send's frozen report: values, block data, cohort and compiled email.

    ``values`` are the report's values as of its revision; ``blocks`` each
    block's ``{block, version, data}``; ``recipients`` the frozen cohort
    (``{address, kind, ref}``, by address); ``images`` the inline charts as
    ``[content id, base64 PNG]`` pairs, kept so a retry sends the same
    bytes. ``html`` and ``text`` are the email as every recipient gets it,
    before a Testing send's own first line. ``fact_set`` is the pinned fact
    generation, None without a fact block or a report day.
    """

    preparation = models.OneToOneField(ReportEmailPreparation, on_delete=models.PROTECT)
    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion", on_delete=models.PROTECT
    )
    fact_set = models.ForeignKey(
        "CampaignDailyFactSet", on_delete=models.PROTECT, null=True
    )
    values = models.JSONField()
    blocks = models.JSONField()
    has_content = models.BooleanField()
    recipients = models.JSONField()
    subject = models.CharField(max_length=254)
    html = models.TextField()
    text = models.TextField()
    images = models.JSONField()
    run = models.ForeignKey("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    fence = models.PositiveBigIntegerField()
    worker_id = models.UUIDField()

    class Meta:
        db_table = "stewardship_report_email_snapshot"


class ReportEmailRecipient(ImmutableRecord):
    """One cohort address: how it was chosen, and its message or its revocation.

    ``kind`` is ``user``, ``group`` or ``outside``; ``ref`` the user's record
    id or the group's name (None for an outside address). A ``queued``
    recipient has its own outbox message; a ``revoked`` one failed the
    recheck before its message was made and never gets one; a ``covered``
    one gets none because earlier messages the provider accepted already
    covered every slot of the send (#1835). ``covered_messages`` names those
    accepted messages, and is empty for the other two.
    """

    snapshot = models.ForeignKey(ReportEmailSnapshot, on_delete=models.PROTECT)
    address = models.CharField(max_length=254)
    kind = models.CharField(max_length=8)
    ref = models.CharField(max_length=64, null=True)
    disposition = models.CharField(max_length=8)
    outbox = models.OneToOneField(
        "stewardship_jobs.OutboxMessage", on_delete=models.PROTECT, null=True
    )
    covered_messages = models.JSONField(default=list)

    class Meta:
        db_table = "stewardship_report_email_recipient"
        constraints = [
            models.UniqueConstraint(
                fields=("snapshot", "address"), name="report_email_recipient_once"
            ),
            models.CheckConstraint(
                condition=models.Q(kind__in=("user", "group", "outside")),
                name="report_email_recipient_kind",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    disposition="queued", outbox__isnull=False, covered_messages=[]
                )
                | models.Q(
                    disposition="revoked", outbox__isnull=True, covered_messages=[]
                )
                | (
                    models.Q(disposition="covered", outbox__isnull=True)
                    & ~models.Q(covered_messages=[])
                ),
                name="report_email_recipient_coverage",
            ),
        ]

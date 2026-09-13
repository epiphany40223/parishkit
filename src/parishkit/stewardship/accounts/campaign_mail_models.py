"""Explicit fictional campaign test mail, never a scheduled Family delivery."""

from django.db import models

from parishkit.stewardship.storage import MutableRecord, UTCDateTimeField


class CampaignMailTest(MutableRecord):
    """One immutable preview binding and at most one finite provider submission.

    Rendered fictional content is erased on every terminal outcome. Retained
    configuration/template/credential identities let later readiness owners
    distinguish exact successful evidence from stale tests without retaining
    a message body, provider identifier, Family link or delivery error text.
    """

    immutable_fields = MutableRecord.immutable_fields + (
        "configuration_id",
        "campaign_id",
        "template_id",
        "requested_by_id",
        "request_key",
        "fingerprint",
        "task_id",
    )
    write_once_fields = (
        "submitted_at",
        "deadline_at",
        "finished_at",
        "run_id",
        "task_fence",
        "worker_id",
    )
    configuration = models.ForeignKey(
        "AppliedConfigurationVersion", on_delete=models.PROTECT
    )
    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    template = models.ForeignKey("ContentVersion", on_delete=models.PROTECT)
    requested_by_id = models.UUIDField()
    request_key = models.UUIDField()
    fingerprint = models.CharField(max_length=64)
    task = models.OneToOneField("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    mail = models.JSONField()
    state = models.CharField(max_length=24, default="queued")
    run = models.ForeignKey(
        "stewardship_jobs.TaskRun",
        on_delete=models.PROTECT,
        null=True,
        related_name="campaign_mail_tests",
    )
    task_fence = models.PositiveBigIntegerField(null=True)
    worker_id = models.UUIDField(null=True)
    submitted_at = UTCDateTimeField(null=True)
    deadline_at = UTCDateTimeField(null=True)
    finished_at = UTCDateTimeField(null=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_campaign_mail_test"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=["requested_by_id", "request_key"],
                name="campaign_mail_explicit_request",
            ),
            models.UniqueConstraint(
                fields=["campaign"],
                condition=models.Q(state__in=["queued", "submitting"]),
                name="campaign_mail_one_pending",
            ),
            models.CheckConstraint(
                condition=models.Q(fingerprint__regex=r"^[0-9a-f]{64}$"),
                name="campaign_mail_fingerprint",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    state__in=[
                        "queued",
                        "submitting",
                        "accepted",
                        "not_sent",
                        "delivery_unknown",
                        "cancelled",
                    ]
                ),
                name="campaign_mail_known_state",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        state__in=["queued", "cancelled"],
                        submitted_at=None,
                        deadline_at=None,
                        run=None,
                        task_fence=None,
                        worker_id=None,
                    )
                    | models.Q(
                        state__in=[
                            "submitting",
                            "accepted",
                            "not_sent",
                            "delivery_unknown",
                        ],
                        submitted_at__isnull=False,
                        deadline_at__isnull=False,
                        run__isnull=False,
                        task_fence__gte=1,
                        worker_id__isnull=False,
                    )
                ),
                name="campaign_mail_submission_shape",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(state__in=["queued", "submitting"], finished_at=None)
                    | (
                        models.Q(finished_at__isnull=False, mail={})
                        & ~models.Q(state__in=["queued", "submitting"])
                    )
                ),
                name="campaign_mail_terminal_scrub",
            ),
        ]

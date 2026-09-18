"""Append-only operational Slack attempts and observations, never message text."""

from django.db import models
from django.db.models.functions import Now

from parishkit.stewardship.storage import ImmutableRecord, UTCDateTimeField

from .operational_models import OperationalNotice


class OperationalSlackAttempt(ImmutableRecord):
    """One committed provider window owned by an exact live worker Task fence.

    The Task is the durable delivery intent. A new attempt requires definitive
    nonacceptance of every earlier attempt; loss never authorizes a resend.
    SQL pins the current channel/configuration and the finite provider deadline.
    """

    notice = models.ForeignKey(OperationalNotice, on_delete=models.PROTECT)
    run = models.ForeignKey("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    fence = models.PositiveBigIntegerField()
    worker_id = models.UUIDField()
    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion", on_delete=models.PROTECT
    )
    channel_id = models.CharField(max_length=64)
    fingerprint = models.CharField(max_length=64)
    mode = models.CharField(max_length=16)
    deadline_at = UTCDateTimeField(db_default=Now())

    class Meta:
        db_table = "stewardship_ops_slack_attempt"
        constraints = [
            models.UniqueConstraint(fields=["run", "fence"], name="ops_slack_fence"),
            models.CheckConstraint(
                condition=models.Q(fence__gte=1), name="ops_slack_positive_fence"
            ),
            models.CheckConstraint(
                condition=models.Q(mode__in=("testing", "production")),
                name="ops_slack_mode",
            ),
            models.CheckConstraint(
                condition=models.Q(channel_id__regex=r"^[CG][A-Z0-9]{1,63}$")
                & models.Q(fingerprint__regex=r"^[0-9a-f]{64}$"),
                name="ops_slack_target_shape",
            ),
        ]


class OperationalSlackResult(ImmutableRecord):
    """One final observation per attempt, with recovery explicitly uncertain."""

    attempt = models.OneToOneField(OperationalSlackAttempt, on_delete=models.PROTECT)
    outcome = models.CharField(max_length=24)
    reason = models.CharField(max_length=8)

    class Meta:
        db_table = "stewardship_ops_slack_result"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    outcome__in=("accepted", "not_sent", "delivery_unknown")
                ),
                name="ops_slack_outcome",
            ),
            models.CheckConstraint(
                condition=models.Q(reason="provider")
                | models.Q(reason="recovery", outcome="delivery_unknown"),
                name="ops_slack_result_reason",
            ),
        ]

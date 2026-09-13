"""Retained metadata for explicit initial-setup Slack notification attempts."""

from django.db import models

from parishkit.stewardship.storage import MutableRecord, UTCDateTimeField


class SetupSlackDelivery(MutableRecord):
    """One immutable request binding and one irreversible provider submission.

    Unlike Family email, this fixed test has no user-provided body or private
    payload to retain. The isolated Slack installer consumes its durable queue;
    no general worker receives the staged Slack credential.
    """

    immutable_fields = MutableRecord.immutable_fields + (
        "attempt_id",
        "attempt_version",
        "request_key",
        "candidate_digest",
        "credential_id",
        "credential_version",
        "fingerprint",
    )
    write_once_fields = ("worker_id", "submitted_at", "deadline_at", "finished_at")
    attempt = models.ForeignKey("SetupAttempt", on_delete=models.PROTECT)
    attempt_version = models.PositiveBigIntegerField()
    request_key = models.UUIDField()
    candidate_digest = models.CharField(max_length=64)
    credential = models.ForeignKey("SetupSealedCredential", on_delete=models.PROTECT)
    credential_version = models.PositiveBigIntegerField()
    fingerprint = models.CharField(max_length=64)
    state = models.CharField(max_length=24, default="queued")
    worker_id = models.UUIDField(null=True)
    submitted_at = UTCDateTimeField(null=True)
    deadline_at = UTCDateTimeField(null=True)
    finished_at = UTCDateTimeField(null=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_setup_slack_delivery"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=["attempt", "request_key"], name="setup_slack_explicit_request"
            ),
            models.UniqueConstraint(
                fields=["attempt"],
                condition=models.Q(state__in=["queued", "submitting"]),
                name="setup_slack_one_pending",
            ),
            models.CheckConstraint(
                condition=models.Q(attempt_version__gte=1, credential_version__gte=1),
                name="setup_slack_positive_bindings",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    candidate_digest__regex=r"^[0-9a-f]{64}$",
                    fingerprint__regex=r"^[0-9a-f]{64}$",
                ),
                name="setup_slack_digest_shape",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    state__in=[
                        "queued",
                        "cancelled",
                        "submitting",
                        "accepted",
                        "not_sent",
                        "delivery_unknown",
                    ]
                ),
                name="setup_slack_known_state",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    state__in=["queued", "cancelled"],
                    worker_id=None,
                    submitted_at=None,
                    deadline_at=None,
                )
                | models.Q(
                    state__in=[
                        "submitting",
                        "accepted",
                        "not_sent",
                        "delivery_unknown",
                    ],
                    worker_id__isnull=False,
                    submitted_at__isnull=False,
                    deadline_at__isnull=False,
                ),
                name="setup_slack_submission_shape",
            ),
            models.CheckConstraint(
                condition=models.Q(state__in=["queued", "submitting"], finished_at=None)
                | (
                    ~models.Q(state__in=["queued", "submitting"])
                    & models.Q(finished_at__isnull=False)
                ),
                name="setup_slack_terminal_shape",
            ),
        ]

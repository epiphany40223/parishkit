"""Single-submission setup mail journal, independent of scheduled Family mail."""

from django.db import models

from parishkit.stewardship.storage import MutableRecord, UTCDateTimeField


class SetupMailDelivery(MutableRecord):
    """Pin an explicit test to one draft, key revision, recipient and Task root.

    The provider boundary writes ``submitting`` before starting a helper. A
    lost result is permanently unknown, not queued again. A separate explicit
    request is required for another send. The fictional rendered sample is
    temporary; terminal setup scrubs it while retaining this safe outcome.
    """

    immutable_fields = MutableRecord.immutable_fields + (
        "attempt_id",
        "attempt_version",
        "request_key",
        "candidate_digest",
        "credential_id",
        "credential_version",
        "fingerprint",
        "task_id",
    )
    write_once_fields = (
        "submitted_at",
        "deadline_at",
        "finished_at",
        "scrubbed_at",
        "run_id",
        "task_fence",
        "worker_id",
    )
    attempt = models.ForeignKey("SetupAttempt", on_delete=models.PROTECT)
    attempt_version = models.PositiveBigIntegerField()
    request_key = models.UUIDField()
    candidate_digest = models.CharField(max_length=64)
    credential = models.ForeignKey("SetupSealedCredential", on_delete=models.PROTECT)
    credential_version = models.PositiveBigIntegerField()
    fingerprint = models.CharField(max_length=64)
    task = models.OneToOneField("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    mail = models.JSONField()
    state = models.CharField(max_length=24, default="queued")
    run = models.ForeignKey(
        "stewardship_jobs.TaskRun",
        on_delete=models.PROTECT,
        null=True,
        related_name="setup_mail_submissions",
    )
    task_fence = models.PositiveBigIntegerField(null=True)
    worker_id = models.UUIDField(null=True)
    submitted_at = UTCDateTimeField(null=True)
    deadline_at = UTCDateTimeField(null=True)
    finished_at = UTCDateTimeField(null=True)
    scrubbed_at = UTCDateTimeField(null=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_setup_mail_delivery"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=["attempt", "request_key"], name="setup_mail_explicit_request"
            ),
            models.UniqueConstraint(
                fields=["attempt"],
                condition=models.Q(state__in=["queued", "submitting"]),
                name="setup_mail_one_pending",
            ),
            models.CheckConstraint(
                condition=models.Q(attempt_version__gte=1, credential_version__gte=1),
                name="setup_mail_positive_bindings",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    candidate_digest__regex=r"^[0-9a-f]{64}$",
                    fingerprint__regex=r"^[0-9a-f]{64}$",
                ),
                name="setup_mail_digest_shape",
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
                name="setup_mail_known_state",
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
                name="setup_mail_submission_shape",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(state__in=["queued", "submitting"], finished_at=None)
                    | (
                        models.Q(finished_at__isnull=False)
                        & ~models.Q(state__in=["queued", "submitting"])
                    )
                ),
                name="setup_mail_terminal_time",
            ),
            models.CheckConstraint(
                condition=models.Q(scrubbed_at=None) | models.Q(mail={}),
                name="setup_mail_scrubbed_payload",
            ),
        ]

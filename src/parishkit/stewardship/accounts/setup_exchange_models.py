"""One ephemeral recipient bound to the exact original setup source invocation."""

from django.db import models

from parishkit.stewardship.storage import (
    ImmutableRecord,
    MutableRecord,
    UTCDateTimeField,
)


class SetupSourceExchange(MutableRecord):
    """An isolated installer can reply once, without installing a working file.

    The private recipient key is never persisted. Restarted workers create a
    new exchange under their new task/source fences. Terminal setup scrubs all
    encrypted replies while immutable recipient/ownership receipts are retained.
    """

    immutable_fields = MutableRecord.immutable_fields + (
        "attempt_id",
        "credential_id",
        "credential_version",
        "fingerprint",
        "task_id",
        "task_fence",
        "worker_id",
        "source_fence",
        "public_key",
    )
    write_once_fields = ("replied_at", "scrubbed_at")
    attempt = models.ForeignKey("SetupAttempt", on_delete=models.PROTECT)
    credential = models.ForeignKey("SetupSealedCredential", on_delete=models.PROTECT)
    credential_version = models.PositiveBigIntegerField()
    fingerprint = models.CharField(max_length=64)
    task = models.ForeignKey("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    task_fence = models.PositiveBigIntegerField()
    worker_id = models.UUIDField()
    source_fence = models.PositiveBigIntegerField()
    public_key = models.BinaryField(max_length=32)
    ciphertext = models.TextField(null=True)
    replied_at = UTCDateTimeField(null=True)
    scrubbed_at = UTCDateTimeField(null=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_setup_source_exchange"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=["task", "task_fence", "source_fence"],
                name="setup_exchange_one_recipient_per_claim",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    credential_version__gte=1,
                    task_fence__gte=1,
                    source_fence__gte=1,
                ),
                name="setup_exchange_positive_fences",
            ),
            models.CheckConstraint(
                condition=models.Q(fingerprint__regex=r"^[0-9a-f]{64}$"),
                name="setup_exchange_fingerprint",
            ),
            models.CheckConstraint(
                condition=models.Q(ciphertext__isnull=True, replied_at__isnull=True)
                | models.Q(
                    ciphertext__isnull=False,
                    replied_at__isnull=False,
                    scrubbed_at__isnull=True,
                )
                | models.Q(ciphertext__isnull=True, scrubbed_at__isnull=False),
                name="setup_exchange_reply_shape",
            ),
        ]


class SetupSourceResult(ImmutableRecord):
    """Validated staged corpus, not a promoted source pointer or configured marker."""

    exchange = models.OneToOneField(SetupSourceExchange, on_delete=models.PROTECT)
    snapshot = models.OneToOneField(
        "stewardship_source.SourceSnapshot", on_delete=models.PROTECT
    )

    class Meta:
        db_table = "stewardship_setup_source_result"

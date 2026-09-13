"""One public, ephemeral credential recipient per exact setup mail claim."""

from django.db import models

from parishkit.stewardship.storage import MutableRecord, UTCDateTimeField


class SetupMailExchange(MutableRecord):
    """Workspace replies once; no process can replace a lost private recipient.

    The delivery journal already binds the original attempt, key version and
    fingerprint. This row adds only the exact live worker and its public key.
    The matching private key exists solely in that mail worker's memory.
    """

    immutable_fields = MutableRecord.immutable_fields + (
        "delivery_id",
        "run_id",
        "task_fence",
        "worker_id",
        "public_key",
    )
    write_once_fields = ("replied_at", "scrubbed_at")
    delivery = models.ForeignKey("SetupMailDelivery", on_delete=models.PROTECT)
    run = models.ForeignKey("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    task_fence = models.PositiveBigIntegerField()
    worker_id = models.UUIDField()
    public_key = models.BinaryField(max_length=32)
    ciphertext = models.TextField(null=True)
    replied_at = UTCDateTimeField(null=True)
    scrubbed_at = UTCDateTimeField(null=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_setup_mail_exchange"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=["run", "task_fence"], name="setup_mail_exchange_one_recipient"
            ),
            models.CheckConstraint(
                condition=models.Q(task_fence__gte=1),
                name="setup_mail_exchange_positive_fence",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(ciphertext=None, replied_at=None)
                    | models.Q(
                        ciphertext__isnull=False,
                        replied_at__isnull=False,
                        scrubbed_at=None,
                    )
                    | models.Q(ciphertext=None, scrubbed_at__isnull=False)
                ),
                name="setup_mail_exchange_reply_shape",
            ),
        ]

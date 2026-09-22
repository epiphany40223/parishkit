"""Immutable Admin intent; ephemeral preparation is consumed before persistence."""

from django.db import models

from parishkit.stewardship.storage import ImmutableRecord

ACTIONS = (
    "note",
    "accept",
    "resend",
    "retry_failed",
    "retry_unsent",
    "confirm_unsent",
)


class DeliveryResolution(ImmutableRecord):
    """One idempotency key binds an exact version, actor, decision and evidence.

    The compiled INSERT trigger consumes ``preparation`` into the owning outbox
    and clears it before constraints/storage. No extra copy of sealed values or
    rendered mail survives here, and web cannot update the outbox itself.
    """

    message_id = models.UUIDField()
    expected_version = models.PositiveBigIntegerField()
    action = models.CharField(max_length=16)
    evidence_note = models.CharField(max_length=2000)
    duplicate_acknowledged = models.BooleanField(default=False)
    previous_task_id = models.UUIDField(null=True)
    retry_task_id = models.UUIDField(null=True)
    preparation = models.JSONField(null=True, default=None)

    class Meta:
        db_table = "stewardship_delivery_resolution"
        indexes = [
            models.Index(
                fields=["message_id", "created_at"], name="delivery_resolution_history"
            )
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(action__in=ACTIONS),
                name="delivery_resolution_action",
            ),
            models.CheckConstraint(
                condition=models.Q(expected_version__gt=0),
                name="delivery_resolution_version",
            ),
            models.CheckConstraint(
                condition=models.Q(preparation__isnull=True),
                name="delivery_resolution_scrubbed",
            ),
        ]

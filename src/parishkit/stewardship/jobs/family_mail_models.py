"""Opaque preparation tickets bind queued hints to their original mode/epoch."""

from django.db import models

from parishkit.stewardship.storage import ImmutableRecord


class FamilyMailPreparation(ImmutableRecord):
    """No recipients, content or secrets; execution outcomes live in the outbox.

    UUID references deliberately survive deletion of Testing work. They provide
    stale-hint cancellation evidence without retaining a credential or preventing
    the existing epoch/occurrence cleanup owner from removing its records.
    """

    occurrence_id = models.UUIDField()
    task_id = models.UUIDField(unique=True)
    mode = models.CharField(max_length=16)
    rehearsal_epoch_id = models.UUIDField(null=True)

    class Meta:
        db_table = "stewardship_family_mail_preparation"
        constraints = [
            models.UniqueConstraint(
                fields=["occurrence_id", "rehearsal_epoch_id"],
                nulls_distinct=False,
                name="family_mail_preparation_scope",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(mode="production", rehearsal_epoch_id__isnull=True)
                    | models.Q(mode="testing", rehearsal_epoch_id__isnull=False)
                ),
                name="family_mail_preparation_namespace",
            ),
        ]

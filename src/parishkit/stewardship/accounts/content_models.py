"""Immutable normalized content revisions belonging to an exact YAML snapshot."""

from django.db import models

from parishkit.stewardship.storage import ImmutableRecord


class ContentVersion(ImmutableRecord):
    """A stable revision ID keeps the same payload across immutable projections."""

    configuration = models.ForeignKey(
        "AppliedConfigurationVersion",
        on_delete=models.PROTECT,
        related_name="content_versions",
    )
    record_id = models.UUIDField(db_index=True)
    campaign_id = models.UUIDField(db_index=True)
    kind = models.CharField(max_length=8)
    slot = models.CharField(max_length=32)
    subject = models.CharField(max_length=254, null=True)
    html = models.TextField()
    text = models.TextField()

    class Meta:
        db_table = "stewardship_content_version"
        constraints = [
            models.UniqueConstraint(
                fields=["configuration", "record_id"],
                name="content_revision_projection",
            ),
            models.UniqueConstraint(
                fields=["configuration", "campaign_id", "kind", "slot"],
                condition=models.Q(kind="page"),
                name="content_selected_slot",
            ),
            models.CheckConstraint(
                condition=models.Q(kind="page", subject__isnull=True)
                | (
                    models.Q(kind="email", subject__isnull=False)
                    & ~models.Q(subject="")
                ),
                name="content_subject_kind",
            ),
        ]

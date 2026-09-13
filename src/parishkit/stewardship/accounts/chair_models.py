"""Retained identity evidence for an explicitly confirmed seeded assignment.

This is not an alternative policy writer. Confirmation remains owned by the
Admin configuration-request workflow; source refresh receives read access only.
Keeping the selected Member avoids silently transferring a seed when an email
address is shared, removed, or reused by another Member.
"""

from django.db import models

from parishkit.stewardship.storage import ImmutableRecord, MutableRecord


class ChairSeedEvidence(ImmutableRecord):
    """Exact original assignment, selected Member and promoted roster evidence.

    The source manifest remains after payload compaction. Retained roster keys
    describe the confirmed relationship without pinning a whole census forever.
    No source synchronization or ordinary policy edit may create this record.
    """

    assignment_record_id = models.UUIDField(unique=True)
    assignment = models.OneToOneField("MinistryAssignment", on_delete=models.PROTECT)
    snapshot = models.ForeignKey(
        "stewardship_source.SourceSnapshot", on_delete=models.PROTECT
    )
    organization_id = models.PositiveBigIntegerField()
    member_duid = models.PositiveBigIntegerField()
    roster_keys = models.JSONField()

    class Meta:
        db_table = "stewardship_chair_seed_evidence"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    organization_id__gt=0,
                    organization_id__lt=2**31,
                    member_duid__gt=0,
                    member_duid__lt=2**31,
                    actor_id__isnull=False,
                ),
                name="chair_seed_evidence_identity",
            ),
        ]


class ChairReconciliation(ImmutableRecord):
    """One atomic source/configuration effect and its complete decision evidence."""

    configuration = models.ForeignKey(
        "AppliedConfigurationVersion", on_delete=models.PROTECT
    )
    snapshot = models.ForeignKey(
        "stewardship_source.SourceSnapshot", on_delete=models.PROTECT
    )
    activation = models.OneToOneField(
        "ConfigurationActivation", on_delete=models.PROTECT, null=True, blank=True
    )
    source_owner = models.ForeignKey(
        "stewardship_jobs.TaskRun", on_delete=models.PROTECT, null=True, blank=True
    )
    source_fence = models.PositiveBigIntegerField(null=True, blank=True)
    decisions = models.JSONField()
    owner_transaction = models.PositiveBigIntegerField(
        db_default=models.Func(function="txid_current"), editable=False
    )

    class Meta:
        db_table = "stewardship_chair_reconciliation"
        constraints = [
            models.UniqueConstraint(
                fields=("configuration", "snapshot"), name="chair_reconciliation_input"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    activation__isnull=False,
                    source_owner__isnull=True,
                    source_fence__isnull=True,
                )
                | models.Q(
                    activation__isnull=True,
                    source_owner__isnull=False,
                    source_fence__isnull=False,
                    source_fence__gt=0,
                ),
                name="chair_reconciliation_owner",
            ),
        ]


class ChairAssignmentReview(MutableRecord):
    """One suspension episode, retained after source return or explicit removal."""

    immutable_fields = MutableRecord.immutable_fields + (
        "assignment_record_id",
        "opened_by_id",
    )
    write_once_fields = ("closed_by_id",)
    assignment_record_id = models.UUIDField(db_index=True)
    opened_by = models.ForeignKey(
        ChairReconciliation, on_delete=models.PROTECT, related_name="opened_reviews"
    )
    latest_by = models.ForeignKey(
        ChairReconciliation, on_delete=models.PROTECT, related_name="latest_reviews"
    )
    closed_by = models.ForeignKey(
        ChairReconciliation,
        on_delete=models.PROTECT,
        related_name="closed_reviews",
        null=True,
        blank=True,
    )
    close_reason = models.CharField(max_length=24, blank=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_chair_review"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=("assignment_record_id",),
                condition=models.Q(closed_by__isnull=True),
                name="chair_review_open_assignment",
            ),
            models.CheckConstraint(
                condition=models.Q(closed_by__isnull=True, close_reason="")
                | models.Q(
                    closed_by__isnull=False,
                    close_reason__in=("relationship_returned", "assignment_removed"),
                ),
                name="chair_review_closure",
            ),
        ]

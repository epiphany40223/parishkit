"""Permanent manifests, the single current pointer and protected source inputs."""

from django.db import models

from parishkit.stewardship.storage import (
    ImmutableRecord,
    MutableRecord,
    UTCDateTimeField,
)


class SourceSnapshot(MutableRecord):
    """A staged corpus is invisible until one validated promotion commits.

    Manifests survive compaction. Generation is assigned on promotion, not on
    download start; interrupted or rejected staging never advances source truth.
    Counts, cursor and digest describe this exact membership set, not caches.
    """

    organization_id = models.PositiveBigIntegerField()
    kind = models.CharField(max_length=8)
    state = models.CharField(max_length=12, default="staging")
    task = models.ForeignKey("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    source_fence = models.PositiveBigIntegerField()
    base = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="successors",
    )
    started_at = UTCDateTimeField()
    completed_at = UTCDateTimeField(null=True, blank=True)
    promoted_at = UTCDateTimeField(null=True, blank=True, db_index=True)
    compacted_at = UTCDateTimeField(null=True, blank=True)
    generation = models.PositiveBigIntegerField(null=True, blank=True, unique=True)
    counts = models.JSONField(default=dict)
    validation = models.JSONField(default=dict)
    cursor = models.JSONField(default=dict)
    content_digest = models.CharField(max_length=64, blank=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_source_snapshot"
        constraints = MutableRecord.Meta.constraints + [
            models.CheckConstraint(
                condition=models.Q(organization_id__gt=0, source_fence__gt=0),
                name="source_snapshot_identity",
            ),
            models.CheckConstraint(
                condition=models.Q(kind__in=("full", "delta")),
                name="source_snapshot_kind",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    state__in=("staging", "ready", "rejected", "promoted")
                ),
                name="source_snapshot_state",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        state="promoted",
                        generation__gt=0,
                        generation__isnull=False,
                        promoted_at__isnull=False,
                    )
                    | (
                        ~models.Q(state="promoted")
                        & models.Q(generation__isnull=True, promoted_at__isnull=True)
                    )
                ),
                name="source_snapshot_promotion_shape",
            ),
            models.CheckConstraint(
                condition=models.Q(compacted_at__isnull=True)
                | models.Q(state="promoted", compacted_at__gte=models.F("promoted_at")),
                name="source_snapshot_compaction_shape",
            ),
            models.CheckConstraint(
                condition=models.Q(content_digest="")
                | models.Q(content_digest__regex=r"^[0-9a-f]{64}$"),
                name="source_snapshot_digest",
            ),
        ]


class SourceCurrent(MutableRecord):
    """All indexed current-corpus queries resolve through this atomic pointer."""

    singleton = models.BooleanField(default=True, unique=True, editable=False)
    snapshot = models.OneToOneField(
        SourceSnapshot, on_delete=models.PROTECT, null=True, blank=True
    )
    generation = models.PositiveBigIntegerField(default=0)
    organization_id = models.PositiveBigIntegerField(null=True, blank=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_source_current"
        constraints = MutableRecord.Meta.constraints + [
            models.CheckConstraint(
                condition=models.Q(singleton=True), name="source_current_singleton"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    snapshot__isnull=True, generation=0, organization_id__isnull=True
                )
                | models.Q(
                    snapshot__isnull=False,
                    generation__gt=0,
                    organization_id__gt=0,
                    organization_id__isnull=False,
                ),
                name="source_current_shape",
            ),
        ]


class SourceSnapshotPin(MutableRecord):
    """A retained parent's explicit protection, independent of artifact expiry.

    References are soft ownership identifiers so future parent tables can use
    this guard without speculative foreign keys. Only expiring form baselines
    use expires_at; other retained parents release explicitly. Transient readers
    use database locks instead of heartbeat expiry as proof of completion.
    """

    snapshot = models.ForeignKey(SourceSnapshot, on_delete=models.PROTECT)
    parent_kind = models.CharField(max_length=32)
    parent_id = models.UUIDField()
    expires_at = UTCDateTimeField(null=True, blank=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_source_pin"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=("snapshot", "parent_kind", "parent_id"),
                name="source_pin_parent",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    parent_kind__in=(
                        "submission",
                        "report",
                        "digest",
                        "form_baseline",
                        "publication",
                        "audit",
                        "boundary",
                        "restore",
                        "delivery_hold",
                        "operator",
                        "facts",
                    )
                ),
                name="source_pin_kind",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(expires_at__isnull=True)
                    & ~models.Q(parent_kind="form_baseline")
                )
                | models.Q(parent_kind="form_baseline", expires_at__isnull=False),
                name="source_pin_expiry_owner",
            ),
        ]


class SourceCompactionBatch(ImmutableRecord):
    """Permanent cleanup evidence contains scope, cutoffs and aggregate counts only."""

    task = models.ForeignKey("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    source_fence = models.PositiveBigIntegerField()
    cutoff_at = UTCDateTimeField()
    recent_cutoff = UTCDateTimeField()
    yearly_cutoff = UTCDateTimeField()
    snapshot_count = models.PositiveBigIntegerField()
    membership_count = models.PositiveBigIntegerField()
    payload_count = models.PositiveBigIntegerField()

    class Meta:
        db_table = "stewardship_source_compaction"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(source_fence__gt=0)
                & models.Q(yearly_cutoff__lt=models.F("recent_cutoff"))
                & models.Q(recent_cutoff__lt=models.F("cutoff_at")),
                name="source_compaction_cutoffs",
            ),
        ]

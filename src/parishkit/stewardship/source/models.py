"""Durable ownership of source refresh and publication, shared across workers."""

from django.db import models

from parishkit.stewardship.storage import MutableRecord, UTCDateTimeField

from .refresh_models import (  # noqa: F401
    SourceRefreshAttempt,
    SourceRefreshCommand,
    SourceRefreshFallback,
    SourceRefreshRequest,
    SourceRefreshTick,
)
from .snapshot_models import (  # noqa: F401
    SourceCompactionBatch,
    SourceCurrent,
    SourceSnapshot,
    SourceSnapshotPin,
)
from .version_models import *  # noqa: F403


class SourceMutationLease(MutableRecord):
    """Singleton fencing record; an external-call safety window survives release.

    Task claims and this lease are separate fences. A replacement task worker
    cannot inherit its predecessor's source lease, even within one TaskRun.
    Request deadlines include the safety margin and never move backwards while
    an owner holds the lease. No network wait holds a database transaction.
    """

    singleton = models.BooleanField(default=True, unique=True, editable=False)
    owner = models.ForeignKey(
        "stewardship_jobs.TaskRun", on_delete=models.PROTECT, null=True, blank=True
    )
    task_fence = models.PositiveBigIntegerField(default=0)
    worker_id = models.UUIDField(null=True, blank=True)
    fence = models.PositiveBigIntegerField(default=0)
    phase = models.CharField(max_length=16, default="idle")
    acquired_at = UTCDateTimeField(null=True, blank=True)
    heartbeat_at = UTCDateTimeField(null=True, blank=True)
    expires_at = UTCDateTimeField(null=True, blank=True)
    external_deadline = UTCDateTimeField(null=True, blank=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_source_lease"
        constraints = MutableRecord.Meta.constraints + [
            models.CheckConstraint(
                condition=models.Q(singleton=True), name="source_lease_singleton"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        owner__isnull=True,
                        worker_id__isnull=True,
                        phase="idle",
                        expires_at__isnull=True,
                    )
                    | models.Q(
                        owner__isnull=False,
                        worker_id__isnull=False,
                        phase__in=("full", "delta", "publication", "compaction"),
                        acquired_at__isnull=False,
                        heartbeat_at__isnull=False,
                        expires_at__isnull=False,
                        task_fence__gt=0,
                        fence__gt=0,
                        expires_at__gt=models.F("heartbeat_at"),
                        heartbeat_at__gte=models.F("acquired_at"),
                    )
                ),
                name="source_lease_owner_shape",
            ),
        ]

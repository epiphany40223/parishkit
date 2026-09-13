"""Dedicated source-retention primitives, not generic cache cleanup or a cron job.

Owning housekeeping supplies concrete purge/restore admission. Source mutation
ownership prevents concurrent promotion; shared snapshot locks protect active
readers through lazy queries. Only explicit normalized source tables are targets.
"""

from django.db import connection, transaction
from django.db.models import Exists, OuterRef, Q

from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action

from .leases import _now, verify_source
from .retention_policy import retention_anchors, retention_cutoffs
from .snapshot_models import SourceCompactionBatch, SourceSnapshot, SourceSnapshotPin
from .snapshots import _admit, _current
from .version_models import ENTITY_MODELS


def _live_pins(now):
    """Only expiring form-input pins can lapse without explicit parent release."""
    return SourceSnapshotPin.objects.filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=now)
    )


def _select_compaction(current, now, limit):
    """Skip active readers and recheck late pins after winning each candidate lock."""
    recent, _ = retention_cutoffs(now)
    anchors = retention_anchors(
        SourceSnapshot.objects.filter(state="promoted", promoted_at__lt=recent)
        .values_list("id", "promoted_at", "generation")
        .iterator(chunk_size=1000),
        now=now,
    )
    pins = _live_pins(now).filter(snapshot_id=OuterRef("id"))
    candidates = list(
        SourceSnapshot.objects.filter(
            state="promoted", compacted_at__isnull=True, promoted_at__lt=recent
        )
        .exclude(pk=current.snapshot_id)
        .exclude(pk__in=anchors)
        .filter(~Exists(pins))
        .order_by("promoted_at", "generation")
        .select_for_update(skip_locked=True)[:limit]
    )
    return [
        row for row in candidates if not _live_pins(now).filter(snapshot=row).exists()
    ]


def _delete_corpora(identifiers, payload_limit):
    """Delete exact memberships, then bounded unreferenced payload versions."""
    membership_count, payload_count = 0, 0
    with connection.cursor() as cursor:
        for payload, membership in ENTITY_MODELS.values():
            member_table = connection.ops.quote_name(membership._meta.db_table)
            payload_table = connection.ops.quote_name(payload._meta.db_table)
            if identifiers:
                cursor.execute(
                    f"DELETE FROM {member_table} WHERE snapshot_id=ANY(%s)",
                    (identifiers,),
                )
                membership_count += cursor.rowcount
            orphans = list(
                payload.objects.filter(
                    ~Exists(membership.objects.filter(payload_id=OuterRef("id")))
                )
                .order_by("id")
                .values_list("id", flat=True)[:payload_limit]
            )
            if orphans:
                cursor.execute(
                    f"DELETE FROM {payload_table} WHERE id=ANY(%s)", (orphans,)
                )
                payload_count += cursor.rowcount
    return membership_count, payload_count


def compact_source(claim, *, admit, snapshot_limit=50, payload_limit=500):
    """Commit one bounded audited batch, retaining manifests, pins, readers and anchors.

    Every row lock remains held through deletion and final ownership verification.
    A pin that wins the race is rechecked in a fresh query after lock acquisition.
    Payload foreign keys provide a final independent protected-reference boundary.
    """
    if claim.phase != "compaction":
        raise ValueError("Source cleanup requires dedicated compaction ownership.")
    if any(
        type(value) is not int or not 1 <= value <= 1000
        for value in (snapshot_limit, payload_limit)
    ):
        raise ValueError("Source cleanup requires bounded positive batch sizes.")
    with transaction.atomic():
        verify_source(claim)
        _admit(admit, "compact", None)
        current = _current()
        now = _now()
        recent, yearly = retention_cutoffs(now)
        candidates = _select_compaction(current, now, snapshot_limit)
        for snapshot in candidates:
            snapshot.compacted_at = now
            snapshot.version += 1
            snapshot.actor_id = claim.worker_id
            snapshot.save()
        memberships, payloads = _delete_corpora(
            [row.pk for row in candidates], payload_limit
        )
        evidence = SourceCompactionBatch.objects.create(
            task_id=claim.task_id,
            source_fence=claim.fence,
            cutoff_at=now,
            recent_cutoff=recent,
            yearly_cutoff=yearly,
            snapshot_count=len(candidates),
            membership_count=memberships,
            payload_count=payloads,
            actor_id=claim.worker_id,
        )
        record_action(
            Action.SOURCE_COMPACTED,
            actor_kind=ActorKind.SYSTEM,
            actor_id=claim.worker_id,
            subject_id=evidence.pk,
            context={
                "count": memberships,
                "version": current.generation,
                "outcome": Outcome.SUCCEEDED,
            },
        )
        verify_source(claim)
        return evidence

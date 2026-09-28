"""Dedicated source-retention primitives, not generic cache cleanup or a cron job.

Owning housekeeping supplies concrete purge/restore admission. Source mutation
ownership prevents concurrent promotion; shared snapshot locks protect active
readers through lazy queries. Only explicit normalized source tables are targets.
"""

from django.db import connection, transaction
from django.db.models import Exists, OuterRef, Q
from django.db.models.expressions import RawSQL

from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action

from .leases import _now, verify_source
from .retention_policy import retention_anchors, retention_cutoffs
from .snapshot_models import SourceCompactionBatch, SourceSnapshot, SourceSnapshotPin
from .snapshots import _admit, _current
from .version_models import ENTITY_MODELS

# Snapshots still named by live state that may read their corpus later, even
# though no pin exists: an archived campaign's population, token generations and
# production token preparations, pending activation catch-up, ministry overlays,
# recipient refusal resolutions, pending fact demands and setup evidence. These
# tables hold few rows, so protecting everything they name retains few corpora.
# Provenance-only references (refresh attempts, fact sets, chair reconciliation)
# are deliberately absent: fact builds pin what they read, and the others keep
# their own copies of what they need.
# The union is wrapped so NULLs are filtered once: the candidate query uses NOT
# IN, and a single NULL there would silently exclude every snapshot.
LIVE_REFERENCES = (
    "SELECT id FROM ("
    "SELECT source_snapshot_id AS id FROM stewardship_campaign_credentials "
    "WHERE source_snapshot_id IS NOT NULL "
    "UNION SELECT source_snapshot_id FROM stewardship_family_token_generation "
    "UNION SELECT source_snapshot_id FROM stewardship_production_tokens "
    "UNION SELECT source_snapshot_id FROM stewardship_activation_catchup "
    "WHERE source_snapshot_id IS NOT NULL "
    "UNION SELECT source_snapshot_id FROM stewardship_assignment_overlay "
    "UNION SELECT source_snapshot_id FROM stewardship_recipient_resolution "
    "UNION SELECT requested_source_id FROM stewardship_fact_demand "
    "WHERE requested_source_id IS NOT NULL "
    "UNION SELECT snapshot_id FROM stewardship_setup_completion "
    "UNION SELECT snapshot_id FROM stewardship_setup_source_result "
    "UNION SELECT snapshot_id FROM stewardship_chair_seed_evidence"
    ") AS live WHERE id IS NOT NULL"
)

# One snapshot per batch keeps each deleting transaction to a few seconds; a
# refresh run then clears at most this many before it starts loading.
BATCHES_PER_REFRESH = 20


def _live_pins(now):
    """Only expiring form-input pins can lapse without explicit parent release."""
    return SourceSnapshotPin.objects.filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=now)
    )


def _rejected_corpora(limit):
    """Rejected staging never became source truth; its memberships are waste.

    Select rejected manifests that still own membership rows in any entity
    table (a load can fail after staging only some kinds).
    """
    owned = " UNION ALL ".join(
        f"SELECT 1 FROM {membership._meta.db_table} m WHERE m.snapshot_id=s.id"
        for _, membership in ENTITY_MODELS.values()
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id FROM stewardship_source_snapshot s WHERE s.state='rejected' "
            f"AND EXISTS ({owned}) ORDER BY s.created_at, s.id LIMIT %s",
            (limit,),
        )
        return [row[0] for row in cursor.fetchall()]


def _select_compaction(current, now, limit):
    """Skip active readers and recheck late pins after winning each candidate lock.

    A superseded snapshot whose content digest equals the current snapshot's
    is redundant even inside the recent window: most 15-minute refreshes find
    no change, and the current corpus reproduces exactly the same content.
    Such a snapshot still yields to pins, live references and daily anchors.
    """
    recent, _ = retention_cutoffs(now)
    current_digest = (
        SourceSnapshot.objects.filter(pk=current.snapshot_id)
        .values_list("content_digest", flat=True)
        .first()
    )
    eligible = Q(promoted_at__lt=recent)
    if current_digest:
        eligible |= Q(content_digest=current_digest)
    anchors = retention_anchors(
        SourceSnapshot.objects.filter(state="promoted", promoted_at__lt=recent)
        .values_list("id", "promoted_at", "generation")
        .iterator(chunk_size=1000),
        now=now,
    )
    pins = _live_pins(now).filter(snapshot_id=OuterRef("id"))
    candidates = list(
        SourceSnapshot.objects.filter(
            eligible, state="promoted", compacted_at__isnull=True
        )
        .exclude(pk=current.snapshot_id)
        .exclude(pk__in=anchors)
        .exclude(pk__in=RawSQL(LIVE_REFERENCES, ()))
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
        # Rejected manifests keep their state; only their memberships go.
        rejected = _rejected_corpora(snapshot_limit)
        memberships, payloads = _delete_corpora(
            [row.pk for row in candidates] + rejected, payload_limit
        )
        evidence = SourceCompactionBatch.objects.create(
            task_id=claim.task_id,
            source_fence=claim.fence,
            cutoff_at=now,
            recent_cutoff=recent,
            yearly_cutoff=yearly,
            snapshot_count=len(candidates) + len(rejected),
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


def _compact_superseded_facts(execution, limit=200):
    """Delete superseded, unused report fact generations in every campaign.

    Every refresh rebuilds report facts, and each generation pins its source
    snapshot with no expiry until fact compaction deletes it. v1 scheduled no
    fact compaction, so those pins protected every refreshed snapshot forever.
    compact_facts deletes only generations SQL reports disposable (a newer
    ready generation exists and no pointer, verification, export, digest, pin
    or demand still uses them) and releases exactly their source pins.
    """
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
    from parishkit.stewardship.reports.models import CampaignDailyFactSet
    from parishkit.stewardship.reports.retention import compact_facts

    # Plain transactions, not execution.effect(): the refresh handler's effect
    # scope is the global work lock, and retention must never hold it.
    # compact_facts takes its own task-claim, campaign, source and generation
    # locks in the same order fact builds use.
    execution.check()
    with transaction.atomic():
        if SystemConfiguration.objects.filter(restore_review_required=True).exists():
            return []
        campaigns = list(
            CampaignDailyFactSet.objects.order_by()
            .values_list("campaign_id", flat=True)
            .distinct()
        )
    removed = []
    for campaign_id in campaigns:
        execution.check()
        with transaction.atomic():
            removed += compact_facts(
                campaign_id,
                execution.claim,
                admit=lambda action, inputs: action == "compact",
                limit=limit,
            )
    return removed


def _admit_compaction(action, snapshot):
    """Retention runs only as compaction, and never during restore review."""
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

    return (
        action == "compact"
        and not SystemConfiguration.objects.filter(
            restore_review_required=True
        ).exists()
    )


def compact_before_refresh(execution, *, batches=BATCHES_PER_REFRESH):
    """Reclaim bounded old corpora under a compaction lease, then release it.

    Called by a running refresh task before it acquires its own refresh lease
    (the SQL guards require a live compaction lease on a running task). Each
    batch is its own short transaction outside the global work lock, so Admin
    pages and delivery never wait behind retention. Retention is housekeeping:
    any failure here is logged and never fails the refresh that just promoted.
    """
    from .leases import acquire_source, release_source

    try:
        with execution.effect():
            claim = acquire_source(
                task_id=execution.claim.run_id,
                task_fence=execution.claim.fence,
                worker_id=execution.claim.worker_id,
                phase="compaction",
            )
        try:
            with execution.maintain_source(claim):
                # Fact generations first: their source pins release only under
                # this live compaction lease, freeing the snapshots below.
                _compact_superseded_facts(execution)
                for _ in range(batches):
                    execution.check()
                    batch = compact_source(
                        claim,
                        admit=_admit_compaction,
                        snapshot_limit=1,
                        payload_limit=1000,
                    )
                    if not (batch.snapshot_count or batch.payload_count):
                        break
        finally:
            with execution.effect():
                release_source(claim)
    except Exception as error:
        # Classified without exception text; the next refresh retries.
        from parishkit.stewardship.observability import Event, emit_failure

        emit_failure(error, event=Event.SOURCE_RETENTION_SKIPPED)

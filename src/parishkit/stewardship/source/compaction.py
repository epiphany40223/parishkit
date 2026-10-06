"""Dedicated source-retention primitives, not generic cache cleanup or a cron job.

Owning housekeeping supplies concrete purge/restore admission. Source mutation
ownership prevents concurrent promotion; shared snapshot locks protect active
readers through lazy queries. Only explicit normalized source tables are targets.
"""

from contextlib import suppress
from dataclasses import dataclass
from time import monotonic

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

# A refresh run marks at most this many batches of snapshots before loading.
BATCHES_PER_REFRESH = 20

# Deletion runs in its own short transactions that hold neither the task row
# nor the source lease row, so the heartbeat (which waits at most two seconds
# for those rows) can always renew. Every deleted row still fires its SQL
# guard, which verifies the live compaction lease, so bound each chunk.
MEMBERSHIP_CHUNK = 2000
PAYLOAD_CHUNK = 500

# One refresh spends at most this long on retention; memberships of
# already-compacted or rejected corpora left over are reclaimed next run.
RETENTION_BUDGET_SECONDS = 60


def _live_pins(now):
    """Only expiring form-input pins can lapse without explicit parent release."""
    return SourceSnapshotPin.objects.filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=now)
    )


def _retained_anchors(recent, now):
    """The snapshots that keep each day's (or month's) final content reconstructable.

    Anchors are chosen over every promoted snapshot, compacted or not, so each
    UTC day or month is anchored at the state it really ended in. A snapshot
    identical to the current one is compacted at once, however, and that is
    often the day's latest. Its content still exists in a later, uncompacted
    snapshot with the same content digest, at worst the last one of that
    unchanged run, so that snapshot is kept instead (#320). Picking only
    uncompacted anchors would anchor such a day, or a whole unchanged weekend,
    at an earlier state and lose its end-of-day content once the run ends.
    """
    rows = list(
        SourceSnapshot.objects.filter(state="promoted", promoted_at__lt=recent)
        .values_list(
            "id", "promoted_at", "generation", "content_digest", "compacted_at"
        )
        .iterator(chunk_size=1000)
    )
    anchors = retention_anchors((row[:3] for row in rows), now=now)
    kept = {row[0] for row in rows if row[0] in anchors and row[4] is None}
    digests = {
        row[3] for row in rows if row[0] in anchors and row[4] is not None and row[3]
    }
    if digests:
        # The latest uncompacted snapshot of each such content, at any age.
        kept.update(
            SourceSnapshot.objects.filter(
                state="promoted", compacted_at__isnull=True, content_digest__in=digests
            )
            .order_by("content_digest", "-promoted_at", "-generation")
            .distinct("content_digest")
            .values_list("id", flat=True)
        )
    return kept


def _select_compaction(current, now, limit):
    """Skip active readers and recheck late pins after winning each candidate lock.

    A superseded snapshot whose content digest equals the current snapshot's
    is redundant even inside the recent window: most 15-minute refreshes find
    no change, and the current corpus reproduces exactly the same content.
    Such a snapshot still yields to pins, live references and retained anchors.
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
    anchors = _retained_anchors(recent, now)
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


def _reclaim_memberships(limit=MEMBERSHIP_CHUNK):
    """Delete one bounded chunk of memberships of compacted or rejected corpora.

    Runs in its own transaction and takes no task or lease row lock; the
    membership guard admits the worker only under the live compaction lease
    and only for a compacted manifest or rejected staging. Picks up rows a
    previous run left behind, so an interrupted run loses nothing.

    A rejected snapshot's memberships are deleted without the pin, live
    reference and anchor checks that ``_select_compaction`` applies before
    marking a promoted snapshot compacted (#269). That is safe because no
    reader can ever need a rejected corpus, and the schema enforces each
    reason:

    - ``rejected`` is terminal: the snapshot guard admits only
      staging/ready -> rejected, and nothing leaves ``rejected``, so the
      corpus can never become promoted or current later;
    - the pin guard admits a pin only on a promoted, uncompacted snapshot,
      so no pin, however long-lived, can name a rejected one;
    - ``read_snapshot`` (every corpus read) takes its shared lock only on a
      promoted, uncompacted snapshot and refuses anything else;
    - the few places that name a rejected snapshot (a delta fallback's
      attempt, a failed refresh's classification) read only its manifest
      row, which is permanent, never its memberships; setup cleanup only
      checks whether its memberships are gone, since it deletes them too.

    Payload versions a rejected corpus shared with a live one survive: the
    payload reclaimer deletes only versions no membership references.
    """
    deleted = 0
    with transaction.atomic(), connection.cursor() as cursor:
        for _, membership in ENTITY_MODELS.values():
            if deleted >= limit:
                break
            table = connection.ops.quote_name(membership._meta.db_table)
            # Probe per snapshot through the snapshot_id index, not a scan of
            # the whole membership table.
            cursor.execute(
                f"DELETE FROM {table} WHERE id IN (SELECT m.id FROM {table} m "
                "WHERE m.snapshot_id IN (SELECT s.id "
                "FROM stewardship_source_snapshot s "
                "WHERE (s.compacted_at IS NOT NULL OR s.state='rejected') "
                f"AND EXISTS (SELECT 1 FROM {table} x WHERE x.snapshot_id=s.id)) "
                "LIMIT %s)",
                (limit - deleted,),
            )
            deleted += cursor.rowcount
    return deleted


def _reclaim_payloads(limit=PAYLOAD_CHUNK):
    """Delete one bounded chunk of payload versions no membership references.

    Its own short transaction, like memberships; the payload guard requires
    the live compaction lease and foreign keys refuse any referenced version.
    """
    deleted = 0
    with transaction.atomic(), connection.cursor() as cursor:
        for payload, membership in ENTITY_MODELS.values():
            if deleted >= limit:
                break
            orphans = list(
                payload.objects.filter(
                    ~Exists(membership.objects.filter(payload_id=OuterRef("id")))
                )
                .order_by("id")
                .values_list("id", flat=True)[: limit - deleted]
            )
            if orphans:
                table = connection.ops.quote_name(payload._meta.db_table)
                cursor.execute(f"DELETE FROM {table} WHERE id=ANY(%s)", (orphans,))
                deleted += cursor.rowcount
    return deleted


def _drain(reclaim, deadline, between, tally, key):
    """Repeat one chunked reclaimer until it runs dry or the budget is spent.

    Each committed chunk is added to ``tally[key]`` as it commits, so a
    caller interrupted part-way still knows exactly what was deleted.
    """
    while monotonic() < deadline:
        between()
        count = reclaim()
        tally[key] += count
        if count == 0:
            break


def _mark_compacted(candidates, now, worker_id):
    """Mark locked candidates compacted; readers then refuse them for good."""
    for snapshot in candidates:
        snapshot.compacted_at = now
        snapshot.version += 1
        snapshot.actor_id = worker_id
        snapshot.save()


@dataclass(frozen=True)
class CompactionResult:
    """One batch's evidence: the marking row and, if rows went, the reclaim row."""

    marked: SourceCompactionBatch
    reclaimed: SourceCompactionBatch | None

    @property
    def snapshot_count(self):
        """Snapshots this batch marked compacted."""
        return self.marked.snapshot_count

    @property
    def membership_count(self):
        """Membership rows this batch deleted (from any compacted corpus)."""
        return self.reclaimed.membership_count if self.reclaimed else 0

    @property
    def payload_count(self):
        """Payload versions this batch deleted."""
        return self.reclaimed.payload_count if self.reclaimed else 0


def _record_batch(claim, cutoffs, *, generation, snapshots, memberships, payloads):
    """Write one immutable evidence row and its audit event; return the row.

    The caller supplies the transaction. The audit ``count`` is the
    membership rows the row reports, as it always has been.
    """
    now, recent, yearly = cutoffs
    evidence = SourceCompactionBatch.objects.create(
        task_id=claim.task_id,
        source_fence=claim.fence,
        cutoff_at=now,
        recent_cutoff=recent,
        yearly_cutoff=yearly,
        snapshot_count=snapshots,
        membership_count=memberships,
        payload_count=payloads,
        actor_id=claim.worker_id,
    )
    # Name no Parish here: the audit ownership trigger requires any named
    # parish_id to be the active configuration's, and a stale one would roll
    # back the compaction marking in this transaction too (see #344).
    record_action(
        Action.SOURCE_COMPACTED,
        actor_kind=ActorKind.SYSTEM,
        actor_id=claim.worker_id,
        subject_id=evidence.pk,
        context={
            "count": memberships,
            "version": generation,
            "outcome": Outcome.SUCCEEDED,
        },
    )
    return evidence


def compact_source(
    claim,
    *,
    admit,
    snapshot_limit=50,
    payload_limit=500,
    deadline=None,
    between=lambda: None,
):
    """Mark one bounded audited batch compacted, then reclaim rows in chunks.

    Marking runs in one short transaction under the verified source claim:
    candidate rows are locked, late pins are rechecked, and readers holding
    their shared lock are skipped. The same transaction writes the batch's
    evidence row (the snapshot count) and its audit event, so no snapshot is
    ever compacted without both, however the run ends (#269).

    Deletion then runs in separate bounded transactions that hold no task or
    lease row, so it can never starve the heartbeat; a compacted manifest is
    unreadable, so a partly reclaimed corpus is never observed. What it
    deleted is recorded afterwards in a second evidence row (only when rows
    went), counting exactly the chunks that committed. That row is written
    even when a chunk or liveness check fails part-way, as long as the claim
    is still verified; only a process killed outright, or a claim that was
    lost, leaves those chunks uncounted, and the next run reclaims and counts
    whatever they left. ``deadline`` (a monotonic instant) bounds the
    deletion and ``between`` runs before each chunk (the execution's liveness
    check).
    """
    if claim.phase != "compaction":
        raise ValueError("Source cleanup requires dedicated compaction ownership.")
    if any(
        type(value) is not int or not 1 <= value <= 1000
        for value in (snapshot_limit, payload_limit)
    ):
        raise ValueError("Source cleanup requires bounded positive batch sizes.")
    deadline = monotonic() + RETENTION_BUDGET_SECONDS if deadline is None else deadline
    with transaction.atomic():
        verify_source(claim)
        _admit(admit, "compact", None)
        current = _current()
        now = _now()
        recent, yearly = retention_cutoffs(now)
        candidates = _select_compaction(current, now, snapshot_limit)
        _mark_compacted(candidates, now, claim.worker_id)
        marked = _record_batch(
            claim,
            (now, recent, yearly),
            generation=current.generation,
            snapshots=len(candidates),
            memberships=0,
            payloads=0,
        )
    tally = {"memberships": 0, "payloads": 0}

    def record_reclaimed():
        """Record the committed chunks, if any, under the verified claim."""
        if not (tally["memberships"] or tally["payloads"]):
            return None
        with transaction.atomic():
            verify_source(claim)
            return _record_batch(
                claim,
                (now, recent, yearly),
                generation=current.generation,
                snapshots=0,
                memberships=tally["memberships"],
                payloads=tally["payloads"],
            )

    try:
        _drain(_reclaim_memberships, deadline, between, tally, "memberships")
        _drain(
            lambda: _reclaim_payloads(min(PAYLOAD_CHUNK, payload_limit)),
            deadline,
            between,
            tally,
            "payloads",
        )
    except BaseException:
        # Count what did commit before re-raising the original failure; a
        # refused or failed record must never replace it.
        with suppress(Exception):
            record_reclaimed()
        raise
    return CompactionResult(marked, record_reclaimed())


def _compact_superseded_facts(execution, limit=500, deadline=None):
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
    # compact_facts deletes one generation per short transaction, locking this
    # task's row before the campaign row (the heartbeat's order).
    execution.check()
    with transaction.atomic():
        if SystemConfiguration.objects.filter(restore_review_required=True).exists():
            return []
        campaigns = list(
            CampaignDailyFactSet.objects.order_by()
            .values_list("campaign_id", flat=True)
            .distinct()
        )
    deadline = monotonic() + RETENTION_BUDGET_SECONDS if deadline is None else deadline

    def proceed():
        """Stop at the retention budget or when the execution must drain."""
        execution.check()
        return monotonic() < deadline

    removed = []
    for campaign_id in campaigns:
        if not proceed():
            break
        removed += compact_facts(
            campaign_id,
            execution.claim,
            admit=lambda action, inputs: action == "compact",
            limit=limit,
            proceed=proceed,
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

    skipped = []

    def skip(error):
        """Log one failure now; the run's durable entry is written once, below."""
        _skipped(error)
        skipped.append(error)

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
                # this live compaction lease, freeing the snapshots below. A
                # fact cleanup failure (for example an outdated pin guard)
                # must not stop snapshot retention, which still reclaims
                # every snapshot no pin protects.
                deadline = monotonic() + RETENTION_BUDGET_SECONDS
                try:
                    _compact_superseded_facts(execution, deadline=deadline)
                except Exception as error:
                    skip(error)
                for _ in range(batches):
                    if monotonic() >= deadline:
                        _budget_reached(execution, deadline)
                        break
                    execution.check()
                    batch = compact_source(
                        claim,
                        admit=_admit_compaction,
                        snapshot_limit=5,
                        payload_limit=1000,
                        deadline=deadline,
                        between=execution.check,
                    )
                    if not (
                        batch.snapshot_count
                        or batch.membership_count
                        or batch.payload_count
                    ):
                        break
        finally:
            with execution.effect():
                release_source(claim)
    except Exception as error:
        skip(error)
    if skipped:
        _record_skipped(execution.claim.run_id, skipped)
    _observe_health()


def _budget_reached(execution, deadline):
    """Log that retention stopped at its time budget; the next refresh resumes.

    This is expected housekeeping, not an error (#293).
    """
    from parishkit.stewardship.audit.timeouts import record_timeout
    from parishkit.stewardship.observability import Event

    record_timeout(
        Event.WORK_BUDGET_REACHED,
        what="retention_budget",
        level="INFO",
        task_id=execution.claim.run_id,
        limit_seconds=RETENTION_BUDGET_SECONDS,
        elapsed_seconds=monotonic() - (deadline - RETENTION_BUDGET_SECONDS),
    )


def _record_skipped(task_id, errors):
    """Keep one durable entry per skipped run for the retention incident.

    The ``source_retention_failing`` incident counts these entries by run
    (``retention_health``). The entry names the refresh task, how many
    retention steps failed and the first failure's category (#633). Best
    effort, like the skip itself: a database that cannot take the entry
    already has the process-log line.
    """
    from parishkit.stewardship.audit.schemas import ContextKind, Outcome
    from parishkit.stewardship.audit.services import operational
    from parishkit.stewardship.observability import (
        Event,
        emit_failure,
        failure_kind_of,
    )

    try:
        with transaction.atomic():
            operational(
                Event.SOURCE_RETENTION_SKIPPED,
                level="ERROR",
                schema=ContextKind.FAILURE,
                context={
                    "failure": "source_retention",
                    "failure_kind": failure_kind_of(errors[0]),
                    "task_id": task_id,
                    "count": len(errors),
                    "outcome": Outcome.RETRY,
                },
            )
    except Exception as error:
        emit_failure(error, event=Event.SOURCE_RETENTION_SKIPPED)


def _observe_health():
    """Open or resolve the retention incident now that this run's outcome is known.

    Best effort: a failure here is logged and the next refresh observes again.
    """
    from parishkit.stewardship.observability import Event, emit_failure

    from .retention_health import observe_retention_health

    try:
        with transaction.atomic():
            observe_retention_health()
    except Exception as error:
        emit_failure(error, event=Event.TASK_FAILED)


def _skipped(error):
    """Log a classified retention failure without exception text; next run retries.

    This goes to the worker's structured log (level ERROR, message
    source_retention_skipped); the run then keeps one durable entry
    (``_record_skipped``).
    """
    from parishkit.stewardship.observability import Event, emit_failure

    emit_failure(error, event=Event.SOURCE_RETENTION_SKIPPED)

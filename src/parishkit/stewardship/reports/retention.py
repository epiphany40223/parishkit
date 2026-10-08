"""Explicit ready-generation pins and bounded, reference-safe derived cleanup."""

from time import monotonic
from uuid import UUID

from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.source.pins import release_snapshot_pin
from parishkit.stewardship.source.snapshot_models import (
    SourceSnapshot,
    SourceSnapshotPin,
)
from parishkit.stewardship.storage import StorageInvariantError

from .facts import FACT_READ_NAMESPACE, FactUnavailable, _admit, fact_inputs
from .models import CampaignDailyFactSet, CampaignFactPin, FactCompactionRecord

# How long one generation's cleanup may wait for a row lock (#387). The
# refresh heartbeat waits for this task's row under the global work lock with
# a 2 s limit, so cleanup that holds the row must give up well before that.
LOCK_SECONDS = 1


def pin_facts(fact_set_id, *, parent_kind, parent_id, admit):
    """Lock selection and durable protection together; never substitute inputs."""
    if not isinstance(parent_id, UUID):
        raise ValueError("Fact protection requires a parent UUID.")
    with transaction.atomic():
        record = (
            CampaignDailyFactSet.objects.select_for_update()
            .filter(pk=fact_set_id)
            .first()
        )
        if record is None or record.state != "ready":
            raise FactUnavailable("The exact fact generation is not ready or retained.")
        _admit(admit, "pin", fact_inputs(record))
        pin, _ = CampaignFactPin.objects.get_or_create(
            fact_set=record,
            parent_kind=parent_kind,
            parent_id=parent_id,
        )
        return pin


def release_fact_pin(pin_id, *, parent_kind, parent_id, admit):
    """Only the owning retained parent may release this specific durable pin."""
    if (
        not isinstance(parent_id, UUID)
        or type(parent_kind) is not str
        or not parent_kind
    ):
        raise ValueError("Fact release requires an explicit parent identity.")
    with transaction.atomic():
        selected = CampaignFactPin.objects.filter(
            pk=pin_id, parent_kind=parent_kind, parent_id=parent_id
        )
        pin = selected.first()
        if pin is None:
            _admit(admit, "unpin", None)
            return False
        CampaignDailyFactSet.objects.select_for_update().get(pk=pin.fact_set_id)
        _admit(admit, "unpin", pin)
        return selected.delete()[0] == 1


def compact_facts(campaign_id, claim, *, admit, limit=50, proceed=None):
    """Remove a bounded set of whole superseded generations and exact input pins.

    Owning housekeeping supplies concrete campaign purge/restore admission.
    Shared readers and late pins win over cleanup; no reader lease is inferred.

    The candidates are chosen once, before any lock: the disposability check
    is the slow part, and it is repeated under each generation's own lock.
    Each generation is then deleted in its own short transaction, locking
    task -> campaign -> source -> generation. That is the documented work
    order (task before domain rows), which the refresh heartbeat also uses,
    so the heartbeat waits for at most one generation and cannot deadlock
    with cleanup. A skipped candidate does not end the batch. ``proceed``, if
    given, is asked before each generation (e.g. a time budget or a drain
    check); returning False stops early and leaves the rest for later.

    Cleanup never waits long for a lock (#387): every lock in a generation's
    transaction, the Campaign row included, waits at most LOCK_SECONDS. On
    contention that generation rolls back, the batch stops, and the timeout
    log records it; the next refresh resumes. Without this, a cleanup
    waiting on the Campaign kept this task's row locked, and the heartbeat
    waited behind it while holding the global work lock.

    The Campaign is locked FOR NO KEY UPDATE, not FOR UPDATE: every insert
    that references the Campaign (a submission, a fact, a delivery) takes a
    FOR KEY SHARE lock on it, which FOR UPDATE would conflict with but FOR
    NO KEY UPDATE does not. Campaign transitions, which update the row,
    still exclude cleanup.
    """
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("Fact cleanup requires a bounded positive batch size.")
    if connection.in_atomic_block:
        # An outer transaction would keep every generation's task and campaign
        # locks until it commits, starving the heartbeat again.
        raise StorageInvariantError("Fact cleanup must run outside a transaction.")
    # Only UUIDs are selected here, with no lock held.
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "SELECT id,source_id FROM stewardship_daily_fact_set "
            "WHERE campaign_id=%s AND stewardship_fact_disposable(id) "
            "ORDER BY created_at,id LIMIT %s",
            (campaign_id, limit),
        )
        candidates = cursor.fetchall()
    removed = []
    for identifier, source_id in candidates:
        if proceed is not None and not proceed():
            break
        started = monotonic()
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(f"SET LOCAL lock_timeout = '{LOCK_SECONDS}s'")
                lock_task_claim(claim)
                Campaign.objects.select_for_update(no_key=True).get(pk=campaign_id)
                with transaction.atomic():
                    deleted = _compact_candidate(identifier, source_id, claim, admit)
                    if not deleted:
                        # Release this skipped candidate's source/generation
                        # locks, not merely its advisory lock, before the next.
                        transaction.set_rollback(True)
                # Fence the commit: a lease that expired during the deletion
                # rolls this generation back.
                lock_task_claim(claim)
        except DatabaseError as error:
            if not _lock_busy(error, claim, monotonic() - started):
                raise
            break
        if deleted:
            removed.append(identifier)
    return removed


def _lock_busy(error, claim, elapsed):
    """Log a generation that stopped on a busy lock; False for any other error.

    Recorded through the timeout log (``lock_timeout``, its limit and the
    time waited) at INFO, like the retention budget: contention is expected
    housekeeping, not a failure, and the next refresh resumes the cleanup.
    """
    from parishkit.stewardship.audit.timeouts import record_timeout
    from parishkit.stewardship.jobs.broker import sql_timeout_kind
    from parishkit.stewardship.observability import Event

    if sql_timeout_kind(error) != "lock_timeout":
        return False
    record_timeout(
        Event.WORK_BUDGET_REACHED,
        what="lock_timeout",
        level="INFO",
        task_id=claim.run_id,
        limit_seconds=LOCK_SECONDS,
        elapsed_seconds=elapsed,
    )
    return True


def _compact_candidate(identifier, source_id, claim, admit):
    """Delete one eligible generation inside its caller-owned candidate savepoint.

    False means skip and rollback that savepoint; any real error propagates and
    rolls back the entire batch. No network or unbounded calculation runs here.
    """
    if (
        not SourceSnapshot.objects.select_for_update(skip_locked=True)
        .filter(pk=source_id)
        .exists()
    ):
        return False
    record = (
        CampaignDailyFactSet.objects.select_for_update(skip_locked=True)
        .filter(pk=identifier)
        .first()
    )
    if record is None:
        return False
    _admit(admit, "compact", fact_inputs(record))
    with connection.cursor() as cursor:
        cursor.execute("SELECT stewardship_fact_disposable(%s)", (identifier,))
        if not cursor.fetchone()[0]:
            return False
        cursor.execute(
            "SELECT pg_try_advisory_xact_lock(%s, hashtext(%s))",
            (FACT_READ_NAMESPACE, str(identifier)),
        )
        if not cursor.fetchone()[0]:
            return False
        evidence = FactCompactionRecord.objects.create(
            campaign_id=record.campaign_id,
            fact_set_id=record.pk,
            population_scope=record.population_scope,
            source_generation=record.source_generation,
            submission_watermark=record.submission_watermark,
            timezone_configuration_id=record.timezone_configuration_id,
            through_date=record.through_date,
            row_count=record.expected_count,
            task_id=claim.run_id,
            task_fence=claim.fence,
            worker_id=claim.worker_id,
            actor_id=claim.worker_id,
        )
        cursor.execute(
            "DELETE FROM stewardship_daily_fact WHERE fact_set_id=%s", (identifier,)
        )
        cursor.execute(
            "DELETE FROM stewardship_daily_fact_set WHERE id=%s", (identifier,)
        )
    inputs = fact_inputs(record)
    for pin in SourceSnapshotPin.objects.filter(
        snapshot_id=source_id, parent_kind="facts", parent_id=identifier
    ):
        release_snapshot_pin(
            pin.pk,
            parent_kind="facts",
            parent_id=identifier,
            admit=lambda *args: admit("compact", inputs),
        )
    record_action(
        Action.FACTS_COMPACTED,
        actor_kind=ActorKind.SYSTEM,
        actor_id=claim.worker_id,
        subject_id=evidence.pk,
        context={"count": record.expected_count, "outcome": Outcome.SUCCEEDED},
    )
    return True

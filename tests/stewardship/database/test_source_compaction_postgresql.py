"""Source retention, explicit parent lifetime and reader/reference cleanup races."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from uuid import uuid4

import pytest
from django.db import connections, transaction

from parishkit.stewardship.audit.models import AuditContext
from parishkit.stewardship.source import compaction, snapshots
from parishkit.stewardship.source.canonical import InvalidSourcePayload
from parishkit.stewardship.source.compaction import compact_source
from parishkit.stewardship.source.leases import _now, acquire_source, release_source
from parishkit.stewardship.source.models import SourceMutationLease
from parishkit.stewardship.source.pins import pin_snapshot, release_snapshot_pin
from parishkit.stewardship.source.retention_policy import RECENT_RETENTION
from parishkit.stewardship.source.snapshot_models import (
    SourceCompactionBatch,
    SourceCurrent,
    SourceSnapshot,
)
from parishkit.stewardship.source.snapshots import (
    begin_snapshot,
    finish_snapshot,
    promote_snapshot,
    read_snapshot,
    reconstruct_snapshot,
    stage_entities,
)
from parishkit.stewardship.source.version_models import ENTITY_MODELS, SourceFamily

from .source_builders import running_source_task, source_corpus

pytestmark = pytest.mark.django_db(transaction=True)


def permit(*args):
    """Only synthetic internal admission is used by this disposable-data suite."""
    return True


@pytest.fixture
def history(monkeypatch):
    """Generate valid old promotion metadata while lease clocks remain real."""
    with transaction.atomic():
        now = _now()
    return promote_history(
        monkeypatch,
        [
            ((now - timedelta(days=100)).replace(hour=10, minute=0, second=0), 0),
            ((now - timedelta(days=100)).replace(hour=11, minute=0, second=0), 1),
            (now, 2),
        ],
    )


def promote_history(monkeypatch, plan):
    """Promote one synthetic corpus per (promotion instant, content name) pair."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)
    claim = acquire_source(**running_source_task(), phase="full")
    records = []
    for instant, name in plan:
        with monkeypatch.context() as context:
            context.setattr(snapshots, "_now", lambda instant=instant: instant)
            corpus = source_corpus(name=f"Synthetic {name}")
            snapshot = begin_snapshot(claim, organization_id=100, admit=permit)
            for kind, entities in corpus.items():
                stage_entities(
                    snapshot.pk, claim, kind=kind, entities=entities, admit=permit
                )
            finish_snapshot(
                snapshot.pk,
                claim,
                expected_counts={kind: len(rows) for kind, rows in corpus.items()},
                cursor={},
                admit=permit,
            )
            records.append(
                promote_snapshot(snapshot.pk, claim, admit=permit, reconcile=permit)
            )
    release_source(claim)
    return records


def cleanup(**kwargs):
    """A new real source claim guards each synthetic cleanup batch."""
    claim = acquire_source(**running_source_task(), phase="compaction")
    try:
        return compact_source(claim, admit=permit, **kwargs)
    finally:
        release_source(claim)


def test_compaction_preserves_manifests_anchors_current_and_shared_payloads(history):
    """Only a redundant old corpus and its newly unreferenced content disappear."""
    result = cleanup()
    assert (result.snapshot_count, result.membership_count, result.payload_count) == (
        1,
        9,
        1,
    )
    assert SourceSnapshot.objects.count() == 3
    assert SourceFamily.objects.count() == 2
    for kind, (payload, _) in ENTITY_MODELS.items():
        assert payload.objects.count() == (2 if kind == "family" else 1)
    assert reconstruct_snapshot(history[1].pk)["family"]["1"]["name"] == "Synthetic 1"
    assert reconstruct_snapshot()["family"]["1"]["name"] == "Synthetic 2"
    with pytest.raises(InvalidSourcePayload, match="unavailable"):
        reconstruct_snapshot(history[0].pk)
    again = cleanup()
    assert (again.snapshot_count, again.membership_count, again.payload_count) == (
        0,
        0,
        0,
    )
    assert SourceCompactionBatch.objects.count() == 2
    context = AuditContext.objects.get(event__subject_id=result.pk).context
    assert context == {"count": 9, "version": 3, "outcome": "succeeded"}
    assert result.recent_cutoff == result.cutoff_at - RECENT_RETENTION


def test_recent_history_is_retained_by_sql_not_materialized_uuid_sets(
    history, monkeypatch
):
    """Frequent recent polls do not enlarge the Python anchor set or SQL IN list."""
    original = compaction.retention_anchors
    observed = []

    def anchors(stamps, *, now):
        """Inspect only the real SQL iterator passed to the unchanged pure policy."""
        values = list(stamps)
        observed.extend(row[0] for row in values)
        return original(values, now=now)

    monkeypatch.setattr(compaction, "retention_anchors", anchors)
    assert cleanup().snapshot_count == 1
    assert set(observed) == {history[0].pk, history[1].pk}
    assert reconstruct_snapshot(history[2].pk)


@pytest.mark.parametrize(
    "kind",
    [
        "submission",
        "report",
        "digest",
        "publication",
        "audit",
        "boundary",
        "restore",
        "delivery_hold",
        "operator",
        "facts",
    ],
)
def test_retained_parent_pins_override_compaction_until_explicit_release(history, kind):
    """Artifact expiry alone never removes a retained parent's replay inputs."""
    pin = pin_snapshot(history[0].pk, parent_kind=kind, parent_id=uuid4(), admit=permit)
    assert cleanup().snapshot_count == 0
    assert reconstruct_snapshot(history[0].pk)
    assert not release_snapshot_pin(
        pin.pk, parent_kind=kind, parent_id=uuid4(), admit=permit
    )
    assert not release_snapshot_pin(
        pin.pk, parent_kind="different", parent_id=pin.parent_id, admit=permit
    )
    assert cleanup().snapshot_count == 0
    observed = []

    def owning_admission(action, selected):
        """The retained parent's actual pin is available for owning authorization."""
        observed.append((action, selected.pk, selected.parent_kind, selected.parent_id))
        return True

    assert release_snapshot_pin(
        pin.pk, parent_kind=kind, parent_id=pin.parent_id, admit=owning_admission
    )
    assert observed == [("unpin", pin.pk, kind, pin.parent_id)]
    assert not release_snapshot_pin(
        pin.pk, parent_kind=kind, parent_id=pin.parent_id, admit=permit
    )
    assert cleanup().snapshot_count == 1


def test_expiring_form_baseline_pin_must_have_bounded_lifetime(history):
    """A form input reference expires explicitly; other parent pins may not expire."""
    with pytest.raises(ValueError, match="expiry"):
        pin_snapshot(
            history[0].pk, parent_kind="form_baseline", parent_id=uuid4(), admit=permit
        )
    with transaction.atomic():
        expiry = _now() + timedelta(minutes=5)
    parent = uuid4()
    pin = pin_snapshot(
        history[0].pk,
        parent_kind="form_baseline",
        parent_id=parent,
        admit=permit,
        expires_at=expiry,
    )
    assert (
        pin_snapshot(
            history[0].pk,
            parent_kind="form_baseline",
            parent_id=parent,
            admit=permit,
            expires_at=expiry,
        ).pk
        == pin.pk
    )
    assert cleanup().snapshot_count == 0
    with pytest.raises(ValueError, match="explicit parent release"):
        pin_snapshot(
            history[0].pk,
            parent_kind="report",
            parent_id=uuid4(),
            admit=permit,
            expires_at=expiry,
        )


def test_pin_cannot_resurrect_compacted_input_or_bypass_admission(history):
    """Losing selection requires an exact rebuild or rebaseline, not another cutoff."""
    with pytest.raises(PermissionError):
        pin_snapshot(
            history[0].pk,
            parent_kind="report",
            parent_id=uuid4(),
            admit=lambda *args: False,
        )
    cleanup()
    with pytest.raises(InvalidSourcePayload, match="reconstructable"):
        pin_snapshot(
            history[0].pk, parent_kind="report", parent_id=uuid4(), admit=permit
        )


def test_active_reader_wins_over_compaction_and_later_cleanup_resumes(history):
    """No expired heartbeat is used to infer that a lazy source reader is done."""
    entered, finish = Event(), Event()

    def reader():
        """Hold a real shared row lock while the other connection attempts cleanup."""
        try:
            with read_snapshot(history[0].pk):
                entered.set()
                assert finish.wait(10)
                return reconstruct_snapshot(history[0].pk)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(reader)
        try:
            assert entered.wait(10)
            assert cleanup().snapshot_count == 0
        finally:
            finish.set()
        assert future.result()["family"]["1"]["name"] == "Synthetic 0"
    assert cleanup().snapshot_count == 1


def test_live_state_references_protect_old_corpora(history, monkeypatch):
    """A corpus still named by live state survives, even without a pin."""
    monkeypatch.setattr(
        compaction, "LIVE_REFERENCES", f"SELECT '{history[0].pk}'::uuid"
    )
    assert cleanup().snapshot_count == 0
    assert reconstruct_snapshot(history[0].pk)["family"]["1"]["name"] == "Synthetic 0"


def test_worker_login_compacts_without_the_global_work_lock(history, monkeypatch):
    """The real worker role may run retention, and never under 736220/1."""
    from parishkit.stewardship.deployment import ServiceRole

    from .test_background_grants_postgresql import task_login

    original = compaction._reclaim_memberships
    probed = []

    def delete(*args, **kwargs):
        """Probe this backend's advisory locks inside the deleting transaction."""
        with transaction.atomic():
            count = original(*args, **kwargs)
            with connections["default"].cursor() as cursor:
                cursor.execute(
                    "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() "
                    "AND locktype='advisory' AND classid=736220 AND objid=1 "
                    "AND granted)"
                )
                probed.append(cursor.fetchone()[0])
        return count

    monkeypatch.setattr(compaction, "_reclaim_memberships", delete)
    task = running_source_task()
    with task_login(ServiceRole.WORKER, exact=True):
        with connections["default"].cursor() as cursor:
            cursor.execute(compaction.LIVE_REFERENCES)
            cursor.fetchall()
        claim = acquire_source(**task, phase="compaction")
        try:
            result = compact_source(claim, admit=permit)
        finally:
            release_source(claim)
    assert (result.snapshot_count, result.membership_count) == (1, 9)
    assert probed and not any(probed)


class _Execution:
    """The slice of a live execution that pre-refresh retention uses."""

    def __init__(self, task):
        from types import SimpleNamespace

        self.claim = SimpleNamespace(
            run_id=task["task_id"],
            fence=task["task_fence"],
            worker_id=task["worker_id"],
        )

    def effect(self):
        """A plain transaction stands in for the handler's fenced effect scope."""
        return transaction.atomic()

    def maintain_source(self, claim):
        """Renewal is irrelevant to a sub-second synthetic batch."""
        from contextlib import nullcontext

        return nullcontext()

    def check(self):
        """The synthetic lifetime is always active."""


def test_pre_refresh_retention_compacts_then_releases_the_lease(history):
    """A refresh run reclaims superseded corpora and leaves the lease idle."""
    compaction.compact_before_refresh(_Execution(running_source_task()))
    assert SourceSnapshot.objects.filter(compacted_at__isnull=False).count() == 1
    assert SourceMutationLease.objects.get().phase == "idle"


def test_pre_refresh_retention_failure_never_fails_the_refresh(history, monkeypatch):
    """Retention is housekeeping: a failure is logged and the lease released."""

    def broken(*args, **kwargs):
        """Simulate an unexpected cleanup fault."""
        raise RuntimeError("synthetic retention fault")

    monkeypatch.setattr(compaction, "compact_source", broken)
    compaction.compact_before_refresh(_Execution(running_source_task()))
    assert SourceMutationLease.objects.get().phase == "idle"
    assert not SourceSnapshot.objects.filter(compacted_at__isnull=False).exists()


def test_unchanged_refreshes_are_reclaimed_inside_the_recent_window(monkeypatch):
    """Identical content is redundant at once; a real change stays recent."""
    with transaction.atomic():
        now = _now()
    minutes = [now - timedelta(minutes=value) for value in (45, 30, 15, 0)]
    records = promote_history(
        monkeypatch,
        [
            (minutes[0], "changed"),
            (minutes[1], "same"),
            (minutes[2], "same"),
            (minutes[3], "same"),
        ],
    )
    assert records[1].content_digest == records[3].content_digest
    assert records[0].content_digest != records[3].content_digest
    while cleanup().snapshot_count:
        pass
    compacted = set(
        SourceSnapshot.objects.filter(compacted_at__isnull=False).values_list(
            "pk", flat=True
        )
    )
    assert compacted == {records[1].pk, records[2].pk}
    assert reconstruct_snapshot(records[0].pk)["family"]["1"]["name"] == (
        "Synthetic changed"
    )
    assert reconstruct_snapshot()["family"]["1"]["name"] == "Synthetic same"


def test_a_null_live_reference_cannot_disable_retention(history, monkeypatch):
    """NOT IN over a NULL would exclude everything; NULLs are filtered first."""
    monkeypatch.setattr(
        compaction,
        "LIVE_REFERENCES",
        "SELECT id FROM (SELECT NULL::uuid AS id) AS live WHERE id IS NOT NULL",
    )
    assert cleanup().snapshot_count == 1


def _heartbeat_can_lock(task_id):
    """Try the heartbeat's row locks from another connection, as renew_once does.

    renew_once waits at most two seconds for this task's row and the source
    lease row; a retention transaction that held either would kill the task.
    """
    from django.db import OperationalError

    def attempt():
        """Take both rows FOR UPDATE with a short lock timeout, then release."""
        try:
            with transaction.atomic(), connections["default"].cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '1s'")
                cursor.execute(
                    "SELECT id FROM stewardship_task_run WHERE id=%s FOR UPDATE",
                    [task_id],
                )
                cursor.execute("SELECT id FROM stewardship_source_lease FOR UPDATE")
            return True
        except OperationalError:
            return False
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(attempt).result(timeout=10)


def test_deletion_chunks_never_hold_the_task_or_lease_row(history, monkeypatch):
    """A slow or large chunk can never block the heartbeat.

    On the validation deployment one batch held the task row while deleting
    a whole corpus, the heartbeat timed out on it, and the refresh died.
    """
    task = running_source_task()
    observed = []
    for name in ("_reclaim_memberships", "_reclaim_payloads"):
        original = getattr(compaction, name)

        def contended(*args, _original=original, _name=name, **kwargs):
            """Hold the chunk's transaction open while the heartbeat competes."""
            with transaction.atomic():
                count = _original(*args, **kwargs)
                if count:
                    observed.append((_name, _heartbeat_can_lock(task["task_id"])))
            return count

        monkeypatch.setattr(compaction, name, contended)
    claim = acquire_source(**task, phase="compaction")
    try:
        result = compact_source(claim, admit=permit)
    finally:
        release_source(claim)
    assert (result.snapshot_count, result.membership_count, result.payload_count) == (
        1,
        9,
        1,
    )
    assert observed == [("_reclaim_memberships", True), ("_reclaim_payloads", True)]


def test_spent_budget_marks_now_and_the_next_run_reclaims(history):
    """A run out of time leaves deletion to the next run and loses nothing."""
    from time import monotonic

    first = cleanup(deadline=monotonic() - 1)
    assert (first.snapshot_count, first.membership_count, first.payload_count) == (
        1,
        0,
        0,
    )
    with pytest.raises(InvalidSourcePayload, match="unavailable"):
        reconstruct_snapshot(history[0].pk)
    again = cleanup()
    assert (again.snapshot_count, again.membership_count, again.payload_count) == (
        0,
        9,
        1,
    )

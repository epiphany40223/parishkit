"""Real source fencing, stale-worker denial, safety windows and takeover races."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, connections, transaction
from django.db.models import F

from parishkit.stewardship.source.leases import (
    SourceFenceLost,
    SourceLeaseUnavailable,
    acquire_source,
    release_source,
    renew_source,
    reserve_source_request,
    verify_source,
)
from parishkit.stewardship.source.models import SourceMutationLease

from .source_builders import running_source_task as running_task

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def idle_lease():
    """Test flush truncates seeded singletons; recreate only an idle record."""
    return SourceMutationLease.objects.get_or_create(singleton=True)[0]


def delay(seconds):
    """Exercise server-clock expiry without weakening persistent SQL guards."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_sleep(%s)", (seconds,))


def test_claim_heartbeat_release_and_monotonic_fence():
    """Every owner receives a new fence and released credentials cannot revive."""
    owner = running_task()
    claim = acquire_source(**owner, phase="full")
    renew_source(claim)
    with transaction.atomic():
        assert verify_source(claim).owner_id == owner["task_id"]
    release_source(claim)
    next_claim = acquire_source(**owner, phase="delta")
    assert next_claim.fence == claim.fence + 1
    with pytest.raises(SourceFenceLost):
        renew_source(claim)
    release_source(next_claim)
    row = SourceMutationLease.objects.get()
    assert row.phase == "idle" and row.owner_id is None and row.fence == 2


def test_live_owner_excludes_other_tasks_and_all_source_phases():
    """Refresh and publication share exclusion, including a duplicate same owner."""
    owner, rival = running_task(), running_task()
    claim = acquire_source(**owner, phase="full")
    for task in (owner, rival):
        for phase in ("full", "delta", "publication", "compaction"):
            with pytest.raises(SourceLeaseUnavailable):
                acquire_source(**task, phase=phase)
    release_source(claim)


def test_external_request_deadline_survives_release_and_database_reconnect():
    """A released claim is not proof that an in-flight external request is done."""
    claim = acquire_source(**running_task(), phase="publication")
    deadline = reserve_source_request(claim, timeout_seconds=1, safety_seconds=1)
    release_source(claim)
    connections.close_all()
    assert SourceMutationLease.objects.get().external_deadline == deadline
    rival = running_task()
    with pytest.raises(SourceLeaseUnavailable):
        acquire_source(**rival, phase="full")
    delay(2.05)
    assert acquire_source(**rival, phase="full").fence == claim.fence + 1


def test_expired_lease_never_renews_and_waits_for_request_window():
    """An expired owner's call safety window still prevents unsafe takeover."""
    claim = acquire_source(**running_task(), phase="full", lease_seconds=1)
    reserve_source_request(claim, timeout_seconds=1, safety_seconds=2)
    delay(1.05)
    with pytest.raises(SourceFenceLost):
        renew_source(claim)
    with pytest.raises(SourceFenceLost):
        release_source(claim)
    with transaction.atomic(), pytest.raises(SourceFenceLost):
        verify_source(claim)
    rival = running_task()
    with pytest.raises(SourceLeaseUnavailable):
        acquire_source(**rival, phase="delta")
    delay(2.05)
    assert acquire_source(**rival, phase="delta").fence > claim.fence


@pytest.mark.parametrize(
    "field", ["task_id", "worker_id", "task_fence", "fence", "phase"]
)
def test_every_claim_binding_is_checked(field):
    """A task/worker/source claim cannot be mixed with another execution."""
    claim = acquire_source(**running_task(), phase="full")
    value = uuid4() if field.endswith("id") else 99 if "fence" in field else "delta"
    with pytest.raises(SourceFenceLost):
        renew_source(replace(claim, **{field: value}))


def test_deadline_never_shortens_with_later_shorter_requests():
    """Parallel provider work may complete out of order without early takeover."""
    claim = acquire_source(**running_task(), phase="full")
    first = reserve_source_request(claim, timeout_seconds=30)
    assert reserve_source_request(claim, timeout_seconds=1) == first


def test_sql_rejects_unfenced_or_premature_takeover_and_deletion():
    """Storage enforces live owners and fixed fence progression independently."""
    claim = acquire_source(**running_task(), phase="full")
    for changes in (
        {"fence": claim.fence + 1},
        {"fence": claim.fence + 3},
        {"worker_id": uuid4()},
        {"phase": "publication"},
    ):
        with pytest.raises(IntegrityError), transaction.atomic():
            SourceMutationLease.objects.update(version=F("version") + 1, **changes)
    with pytest.raises(IntegrityError), transaction.atomic():
        SourceMutationLease.objects.all().delete()


def test_two_workers_race_for_exactly_one_owner():
    """Row locking serializes claims while task transactions remain independent."""
    tasks = [running_task(), running_task()]
    barrier = Barrier(2)

    def claim(task):
        """Own each thread's connection and close it after the claim result."""
        try:
            barrier.wait(timeout=10)
            return acquire_source(**task, phase="full")
        except SourceLeaseUnavailable:
            return None
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, tasks))
    assert sum(item is not None for item in results) == 1
    assert SourceMutationLease.objects.get().fence == 1

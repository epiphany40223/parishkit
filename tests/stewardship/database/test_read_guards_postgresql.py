"""Pinned SQL reads, deadline/loss cleanup and cross-connection download limits."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from uuid import UUID

import pytest
from django.db import close_old_connections, connection, connections

from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.read_guards import (
    READ_NAMESPACE,
    CampaignReadGuard,
    DownloadBusy,
    DownloadPool,
    GuardedResponse,
    ReadLimits,
    ReadUnavailable,
    campaign_lock_key,
)

from .test_campaign_postgresql import add_draft
from .test_policy_postgresql import initialized

pytestmark = pytest.mark.django_db(transaction=True)


def family_campaign(tmp_path):
    """Use real configuration activation, never an unguarded campaign fixture."""
    store, root, actor = initialized(tmp_path)
    _, row, _ = add_draft(store, root, actor)
    return UUID(row["id"])


def guard(identifier, **kwargs):
    """Synthetic authorization/transport callbacks remain inside disposable tests."""
    return CampaignReadGuard(
        [identifier], authorize=lambda _: None, abort=lambda: None, **kwargs
    )


def test_guard_pins_orm_lazy_stream_and_blocks_exclusive_drain(tmp_path):
    """The shared guard survives yielding and closes only with the response."""
    identifier = family_campaign(tmp_path)
    reader = guard(identifier)
    observed = []

    def content():
        """Both generator construction and lazy ORM evaluation use the pinned DB."""
        observed.append(connection.connection)
        yield Campaign.objects.get(pk=identifier).state.encode()

    response = GuardedResponse(reader, content)
    assert next(response) == b"draft"
    assert observed == [reader._raw]
    other = connections["default"].copy(alias="probe")
    try:
        with other.cursor() as cursor:
            cursor.execute(
                "SELECT pg_try_advisory_lock(%s,%s)",
                [READ_NAMESPACE, campaign_lock_key(identifier)],
            )
            assert cursor.fetchone()[0] is False
        response.close()
        with other.cursor() as cursor:
            cursor.execute(
                "SELECT pg_try_advisory_lock(%s,%s)",
                [READ_NAMESPACE, campaign_lock_key(identifier)],
            )
            assert cursor.fetchone()[0] is True
    finally:
        response.close()
        other.close()


@pytest.mark.parametrize("failure", ["authorizer", "content", "connection", "deadline"])
def test_failure_closes_guard_and_restores_download_connection(tmp_path, failure):
    """No failed read can retain local capacity or continue on a reconnected DB."""
    identifier = family_campaign(tmp_path)
    original = connections["default"]
    pool = DownloadPool(ReadLimits(process_pool_size=1))
    reader = guard(identifier, pool=pool)

    def content():
        """Inject failure at lazy serialization, not before admission."""
        if failure == "content":
            raise ValueError("synthetic")
        yield b"test"

    if failure == "authorizer":

        def deny(_):
            raise ReadUnavailable("denied")

        reader.authorize = deny
        with pytest.raises(ReadUnavailable):
            GuardedResponse(reader, content)
    else:
        response = GuardedResponse(reader, content)
        assert connections["default"] is not original
        if failure == "connection":
            reader._raw.close()
        elif failure == "deadline":
            reader._expire()
        with pytest.raises((ReadUnavailable, ValueError)):
            next(response)
        response.close()
    assert connections["default"] is original
    with guard(identifier, pool=pool):
        assert Campaign.objects.get().pk == identifier


def test_download_capacity_is_global_across_independent_pools(tmp_path):
    """Five separately constructed pools cannot claim five deployment slots."""
    identifier = family_campaign(tmp_path)
    capacity = ReadLimits().download_capacity
    with connection.cursor() as cursor:
        cursor.execute("SELECT capacity FROM stewardship_download_policy WHERE id=1")
        assert cursor.fetchone()[0] == capacity
    barrier, release = Barrier(capacity + 1), Event()

    def hold(_):
        """Independent thread-local Django connections simulate separate workers."""
        close_old_connections()
        try:
            with guard(identifier, pool=DownloadPool()):
                barrier.wait(timeout=10)
                assert release.wait(timeout=10)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=capacity) as executor:
        futures = [executor.submit(hold, index) for index in range(capacity)]
        barrier.wait(timeout=10)
        try:
            with pytest.raises(DownloadBusy), guard(identifier, pool=DownloadPool()):
                pytest.fail("Deployment-wide admission was bypassed")
        finally:
            release.set()
        for future in futures:
            future.result(timeout=10)
    with guard(identifier, pool=DownloadPool()):
        pass


def test_readonly_transaction_cannot_modify_campaign(tmp_path):
    """Read guards cannot be reused as an accidental write workflow."""
    from django.db import InternalError

    identifier = family_campaign(tmp_path)
    with pytest.raises(InternalError), guard(identifier), connection.cursor() as cursor:
        cursor.execute("UPDATE stewardship_campaign SET version=version+1")


def test_exclusive_drain_rejects_new_readers_until_transaction_ends(tmp_path):
    """The shared destructive primitive and read admission use identical lock keys."""
    from django.db import OperationalError, transaction

    from parishkit.stewardship.campaigns.read_guards import acquire_campaign_drain

    identifier = family_campaign(tmp_path)

    def attempt():
        """Read from an independent session while the destructive barrier is held."""
        close_old_connections()
        try:
            with (
                pytest.raises(OperationalError),
                guard(identifier, limits=ReadLimits(lock_seconds=1)),
            ):
                pytest.fail("Reader crossed an exclusive campaign barrier")
        finally:
            close_old_connections()

    with transaction.atomic(), ThreadPoolExecutor(max_workers=1) as executor:
        acquire_campaign_drain([identifier, identifier])
        executor.submit(attempt).result(timeout=10)
    with guard(identifier):
        assert Campaign.objects.get().pk == identifier


def test_idle_response_deadline_aborts_without_consumer_activity(tmp_path):
    """A stalled consumer cannot keep the connection and guard alive indefinitely."""
    identifier = family_campaign(tmp_path)
    aborted = Event()
    limits = ReadLimits(
        interactive_seconds=2,
        download_seconds=2,
        lock_seconds=1,
        download_idle_seconds=3,
        drain_seconds=4,
        process_pool_size=1,
    )
    pool = DownloadPool(limits)
    reader = CampaignReadGuard(
        [identifier], authorize=lambda _: None, abort=aborted.set, pool=pool
    )
    response = GuardedResponse(reader, lambda: iter([b"not emitted"]))
    assert aborted.wait(timeout=5)
    with pytest.raises(ReadUnavailable):
        next(response)
    assert reader.closed.is_set()
    with guard(identifier, pool=pool):
        assert Campaign.objects.get().pk == identifier


def test_capacity_cannot_resize_while_a_dedicated_session_holds_slot(tmp_path):
    """Pool policy changes cannot orphan a live slot or increase active capacity."""
    from django.db import IntegrityError

    identifier = family_campaign(tmp_path)
    other = connections["default"].copy(alias="capacity-probe")
    try:
        with (
            guard(identifier, pool=DownloadPool()),
            other.cursor() as cursor,
            pytest.raises(IntegrityError),
        ):
            cursor.execute(
                "UPDATE stewardship_download_policy "
                "SET capacity=5,version=version+1 WHERE id=1"
            )
        with other.cursor() as cursor:
            cursor.execute(
                "SELECT capacity FROM stewardship_download_policy WHERE id=1"
            )
            assert cursor.fetchone()[0] == 4
    finally:
        other.close()


def test_remote_backend_termination_is_typed_and_releases_capacity(tmp_path):
    """A server-side disconnect must not escape as a raw psycopg exception."""
    identifier = family_campaign(tmp_path)
    pool = DownloadPool(ReadLimits(process_pool_size=1))
    other = connections["default"].copy(alias="termination-probe")
    try:
        reader = guard(identifier, pool=pool)
        response = GuardedResponse(reader, lambda: iter([b"not emitted"]))
        with other.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(%s,5000)", [reader._raw.info.backend_pid]
            )
            assert cursor.fetchone()[0]
        with pytest.raises(ReadUnavailable, match="connection was lost"):
            next(response)
        with guard(identifier, pool=pool):
            pass
    finally:
        other.close()


@pytest.mark.parametrize("cancel_fails", [False, True])
def test_expiry_releases_local_slot_without_further_iteration(
    tmp_path, monkeypatch, cancel_fails
):
    """A confirmed transport abort need not wait for a disconnected consumer."""
    identifier = family_campaign(tmp_path)
    pool = DownloadPool(ReadLimits(process_pool_size=1))
    reader = guard(identifier, pool=pool)
    response = GuardedResponse(reader, lambda: iter([b"not emitted"]))
    if cancel_fails:
        from psycopg import OperationalError

        def failed_cancel():
            """Cancellation of a lost backend may itself fail before close."""
            raise OperationalError("synthetic cancellation failure")

        monkeypatch.setattr(reader._raw, "cancel", failed_cancel)
    reader._expire()
    pool.acquire()
    pool.release()
    response.close()
    # Later close cannot over-release the bounded semaphore.
    pool.acquire()
    with pytest.raises(DownloadBusy):
        pool.acquire()
    pool.release()


@pytest.mark.parametrize("failure", ["deadline", "local_close", "remote_close"])
@pytest.mark.parametrize("download", [False, True])
def test_closed_raw_cleanup_never_opens_a_replacement_backend(
    tmp_path, monkeypatch, failure, download
):
    """Django atomic cleanup must not silently acquire an unbudgeted connection."""
    identifier = family_campaign(tmp_path)
    reader = guard(identifier, pool=DownloadPool() if download else None)
    other = connections["default"].copy(alias="cleanup-probe")
    response = GuardedResponse(reader, lambda: iter([b"not emitted"]))

    def forbid_new_backend(*args, **kwargs):
        """Fail if Atomic's autocommit restoration attempts a new driver handle."""
        pytest.fail("Guard cleanup reconnected after losing its pinned backend")

    monkeypatch.setattr(reader.db, "get_new_connection", forbid_new_backend)
    try:
        if failure == "deadline":
            reader._expire()
        elif failure == "local_close":
            reader._raw.close()
        else:
            with other.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_terminate_backend(%s,5000)",
                    [reader._raw.info.backend_pid],
                )
                assert cursor.fetchone()[0]
        with pytest.raises(ReadUnavailable):
            next(response)
        response.close()
        assert reader.db.connection is None
    finally:
        response.close()
        other.close()


def test_cross_thread_close_is_rejected_without_touching_owner_transaction(tmp_path):
    """The synchronous adapter must marshal disconnect cleanup to its owner."""
    from parishkit.stewardship.storage import StorageInvariantError

    identifier = family_campaign(tmp_path)
    reader = guard(identifier, pool=DownloadPool())
    with reader, ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(StorageInvariantError, match="owning thread"):
            executor.submit(reader.close).result(timeout=5)
        assert not reader.closed.is_set()
        reader.check()
    assert reader.closed.is_set()

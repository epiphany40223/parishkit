"""Fact cleanup never deadlocks with or starves the refresh heartbeat."""

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from uuid import uuid4

import pytest
from django.db import OperationalError, connection, connections, transaction

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs import admission
from parishkit.stewardship.jobs.ownership import (
    TaskClaim,
    TaskOwnershipLost,
    lock_task_claim,
)
from parishkit.stewardship.jobs.storage import change_run, enqueue
from parishkit.stewardship.reports import retention
from parishkit.stewardship.reports.facts import publish_fact_set
from parishkit.stewardship.reports.models import CampaignDailyFactSet
from parishkit.stewardship.source import compaction
from parishkit.stewardship.storage import StorageInvariantError

from .fact_builders import fact_fixture, staged_facts
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)


def generations(tmp_path, count):
    """Publish ``count`` generations; all but the newest become disposable."""
    inputs, owner, source = fact_fixture(tmp_path)
    for watermark in range(1, count + 1):
        record, _ = staged_facts(
            replace(inputs, submission_watermark=watermark), owner, source
        )
        publish_fact_set(record.pk, owner, admit=permit, interactive=True)
    return inputs, owner


def claimed_task(lease_seconds=60):
    """A running synthetic task whose lease lasts ``lease_seconds``."""
    status = enqueue(
        task_type="source_probe",
        domain_request_id=uuid4(),
        actor_id=None,
        correlation_id=uuid4(),
        admit=lambda *args: True,
    )
    worker = uuid4()
    status = change_run(
        run_id=status.run_id,
        expected_version=status.version,
        action="claim",
        actor_id=worker,
        correlation_id=uuid4(),
        lease_seconds=lease_seconds,
        admit=lambda *args: True,
    )
    return TaskClaim(status.run_id, status.fence, worker)


def remaining(inputs):
    """How many generations of the campaign still exist."""
    return CampaignDailyFactSet.objects.filter(campaign_id=inputs.campaign_id).count()


def _sqlstate(error):
    """The PostgreSQL error code behind a Django database error, if any."""
    cause = getattr(error, "__cause__", None)
    return getattr(cause, "sqlstate", None) or str(error)


def heartbeat_transaction(claim, campaign_id, *, lock_timeout="5s", held=None):
    """One heartbeat renewal, in the refresh heartbeat's own lock order.

    This mirrors renew_once for a source refresh: the handler scope's
    work-order lock, then this task's row (lock_task_claim), then the
    transition's admission hook, which locks runtime configuration and the
    campaign (jobs/admission._scope), then the heartbeat transition itself.
    ``held`` runs while only the task row is locked. Runs on its own
    connection and returns "ok" or the failing SQLSTATE.
    """
    try:
        with work_transaction():
            with connection.cursor() as cursor:
                cursor.execute(f"SET LOCAL lock_timeout = '{lock_timeout}'")
            row = lock_task_claim(claim)
            if held is not None:
                held()

            def admit(*args):
                """The refresh's admission locks runtime, then the campaign."""
                admission._scope(campaign_id)
                return True

            change_run(
                run_id=row.pk,
                expected_version=row.version,
                action="heartbeat",
                actor_id=claim.worker_id,
                correlation_id=uuid4(),
                fence=claim.fence,
                lease_seconds=60,
                admit=admit,
            )
        return "ok"
    except OperationalError as error:
        return _sqlstate(error)
    finally:
        connections.close_all()


def _waiting(pid):
    """Whether backend ``pid`` is blocked on a lock it has not been granted."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=%s AND NOT granted)",
            [pid],
        )
        return cursor.fetchone()[0]


def test_heartbeat_holding_its_task_row_never_deadlocks_cleanup(tmp_path):
    """The dangerous interleaving: the heartbeat has its task row, cleanup starts.

    The heartbeat locks its task row first, waits until cleanup is blocked
    on a lock, and only then takes the campaign. Cleanup that locked the
    campaign before the task row would deadlock here (40P01); in the shared
    task -> campaign order cleanup simply waits for the heartbeat.
    """
    inputs, owner = generations(tmp_path, 2)
    claim = claimed_task()
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        cleanup_pid = cursor.fetchone()[0]
    holding = Event()

    def wait_for_cleanup():
        """Let cleanup start, then wait until it is blocked on a lock."""
        holding.set()
        deadline = time.monotonic() + 10
        while not _waiting(cleanup_pid):
            assert time.monotonic() < deadline, "cleanup never blocked"
            time.sleep(0.05)

    with ThreadPoolExecutor(max_workers=1) as pool:
        beat = pool.submit(
            heartbeat_transaction, claim, inputs.campaign_id, held=wait_for_cleanup
        )
        assert holding.wait(10)
        try:
            removed = retention.compact_facts(
                inputs.campaign_id, claim, admit=permit, limit=5
            )
            cleanup = "ok"
        except OperationalError as error:
            removed, cleanup = [], _sqlstate(error)
        heartbeat = beat.result(timeout=30)
    assert (heartbeat, cleanup) == ("ok", "ok")  # Never 40P01 on either side.
    assert len(removed) == 1 and remaining(inputs) == 1


def test_heartbeat_waits_for_one_generation_not_the_batch(tmp_path, monkeypatch):
    """A heartbeat arriving mid-deletion renews before the whole batch ends.

    On the validation deployment cleanup held the task row across a batch of
    generations; the heartbeat's 2 s lock wait failed and killed the refresh.
    """
    inputs, owner = generations(tmp_path, 4)
    claim = claimed_task()
    original = retention._compact_candidate
    finished = {}
    pool = ThreadPoolExecutor(max_workers=1)
    futures = []

    def beat():
        """Renew with the heartbeat's real 2 s lock timeout."""
        outcome = heartbeat_transaction(claim, inputs.campaign_id, lock_timeout="2s")
        finished["heartbeat"] = time.monotonic()
        return outcome

    def slow(*args, **kwargs):
        """Each generation takes a while; the heartbeat arrives in the first."""
        result = original(*args, **kwargs)
        if not futures:
            futures.append(pool.submit(beat))
        time.sleep(0.6)
        return result

    monkeypatch.setattr(retention, "_compact_candidate", slow)
    started = time.monotonic()
    try:
        removed = retention.compact_facts(
            inputs.campaign_id, claim, admit=permit, limit=5
        )
        finished["batch"] = time.monotonic()
        outcome = futures[0].result(timeout=30)
    finally:
        pool.shutdown(wait=True)
    assert len(removed) == 3 and remaining(inputs) == 1
    assert outcome == "ok", outcome
    assert finished["heartbeat"] < finished["batch"]
    assert finished["heartbeat"] - started < 2


def test_cleanup_refuses_to_run_inside_a_transaction(tmp_path):
    """An outer transaction would hold every generation's locks until it ends."""
    inputs, owner = generations(tmp_path, 2)
    with pytest.raises(StorageInvariantError), transaction.atomic():
        retention.compact_facts(inputs.campaign_id, claimed_task(), admit=permit)
    assert remaining(inputs) == 2


def test_each_generation_releases_the_campaign_row(tmp_path, monkeypatch):
    """Family sign-in and link work wait for one generation, not a whole batch."""
    inputs, owner = generations(tmp_path, 3)
    original = retention.compact_facts
    observed = []

    def probe():
        """Lock the campaign from another connection, as link work does."""

        def attempt():
            try:
                with transaction.atomic(), connections["default"].cursor() as cur:
                    cur.execute("SET LOCAL lock_timeout = '1s'")
                    cur.execute(
                        "SELECT id FROM stewardship_campaign WHERE id=%s FOR UPDATE",
                        [inputs.campaign_id],
                    )
                return True
            except OperationalError:
                return False
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(attempt).result(timeout=10)

    def batch(*args, proceed=None, **kwargs):
        """Probe the campaign row before every generation's transaction."""

        def checked():
            observed.append(probe())
            return proceed() if proceed else True

        return original(*args, proceed=checked, **kwargs)

    class Execution:
        claim = claimed_task()

        def check(self):
            """The synthetic lifetime is always active."""

    monkeypatch.setattr(retention, "compact_facts", batch)
    removed = compaction._compact_superseded_facts(Execution())
    assert len(removed) == 2 and remaining(inputs) == 1
    assert len(observed) == 2 and all(observed)


def test_a_lease_that_expires_mid_deletion_rolls_the_generation_back(
    tmp_path, monkeypatch
):
    """The commit is still fenced by the real lease, so nothing is deleted."""
    inputs, owner = generations(tmp_path, 2)
    claim = claimed_task(lease_seconds=1)
    original = retention._compact_candidate

    def slow(*args, **kwargs):
        """This generation outlives the task's one-second lease."""
        result = original(*args, **kwargs)
        time.sleep(1.5)
        return result

    monkeypatch.setattr(retention, "_compact_candidate", slow)
    with pytest.raises(TaskOwnershipLost):
        retention.compact_facts(inputs.campaign_id, claim, admit=permit, limit=5)
    assert remaining(inputs) == 2


def test_a_stale_claim_deletes_nothing(tmp_path):
    """An already-replaced claim is refused before any generation is touched."""
    inputs, owner = generations(tmp_path, 2)
    stale = replace(claimed_task(), fence=2**40)
    with pytest.raises(TaskOwnershipLost):
        retention.compact_facts(inputs.campaign_id, stale, admit=permit, limit=5)
    assert remaining(inputs) == 2


def test_a_skipped_candidate_does_not_end_cleanup(tmp_path, monkeypatch):
    """One busy generation is skipped; later disposable ones are still deleted."""
    inputs, owner = generations(tmp_path, 3)
    original = retention._compact_candidate
    calls = []

    def skip_first(*args, **kwargs):
        """The oldest candidate is busy (e.g. a reader holds it)."""
        calls.append(args[0])
        return False if len(calls) == 1 else original(*args, **kwargs)

    monkeypatch.setattr(retention, "_compact_candidate", skip_first)
    removed = retention.compact_facts(
        inputs.campaign_id, claimed_task(), admit=permit, limit=5
    )
    assert len(calls) == 2 and removed == [calls[1]] and remaining(inputs) == 2

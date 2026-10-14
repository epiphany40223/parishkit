"""A refresh refreshes its staged snapshot's statistics off the work order (#895).

The ready transition's snapshot guard re-derives the corpus evidence under
the global work-order lock (736220,1) before the completion trigger analyzes
the membership tables, so with statistics that predate the new snapshot it
held the lock for about 1 s at 1,100 Families. A source refresh now calls
the worker-only definer ``stewardship_source_snapshot_analyze_v1`` (0086) in
a source step after its last staging batch. These tests prove the worker's
real compiled handler does that outside the lock, that the definer refuses
anything but a staging snapshot with its live owner and any login without
its grant, that a lock wait past its limit is logged and the refresh goes
on, and that 0086's DO block refuses a definer not installed as declared.
"""

from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.source import refreshing, snapshots
from parishkit.stewardship.source.attempts import begin_refresh_attempt
from parishkit.stewardship.source.models import SourceSnapshot

from .test_background_grants_postgresql import task_login
from .test_source_attempts_postgresql import setup, stage
from .test_source_refreshing_postgresql import (  # noqa: F401
    fake_provider,
    pages,
    run,
    source_singletons,
)
from .test_source_step_lock_postgresql import (
    Holder,
    holds_work_order,
    own_pid,
    separate_session,
)
from .test_source_unchanged_postgresql import Refreshes

pytestmark = pytest.mark.django_db(transaction=True)

FILE = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0086_snapshot_statistics.sql"
)
DEFINER = "SELECT public.stewardship_source_snapshot_analyze_v1(%s)"


@pytest.fixture
def holder():
    """One competing session holding the work order, always released."""
    value = Holder()
    yield value
    value.close()


def last_analyzed():
    """When the snapshot Family membership table was last analyzed, fresh."""
    with connection.cursor() as cursor:
        # Cumulative statistics are cached per transaction; read them anew.
        cursor.execute("SELECT pg_stat_clear_snapshot()")
        cursor.execute(
            "SELECT pg_stat_get_last_analyze_time("
            "'public.stewardship_snapshot_family'::regclass)"
        )
        return cursor.fetchone()[0]


def test_worker_refresh_analyzes_staging_outside_the_work_order(
    tmp_path, monkeypatch, holder
):
    """The real compiled handler, as the worker login, refreshes the
    statistics once per staged refresh, in a step that commits on its own,
    while a competing session holds the work order, and before the ready
    transition; the definer then refuses the promoted snapshot."""
    seen = []
    real = refreshing.analyze_staging

    def analyze(snapshot_id, claim, **options):
        """The real call, while the competing session holds the lock."""
        assert connection.in_atomic_block
        state = SourceSnapshot.objects.get(pk=snapshot_id).state
        before = last_analyzed()
        holder.take()
        try:
            result = real(snapshot_id, claim, **options)
            competing = holds_work_order(holder.pid)
        finally:
            holder.give()
        own = holds_work_order(own_pid())
        seen.append((state, result, competing, own, before, last_analyzed()))
        return result

    monkeypatch.setattr(refreshing, "analyze_staging", analyze)
    refreshes = Refreshes(tmp_path, monkeypatch)
    ((state, result, competing, own, before, after),) = seen
    assert state == "staging" and result is True
    assert competing and not own
    assert after is not None and (before is None or after > before)
    # Once promoted, the snapshot is nobody's staging any more.
    with (
        pytest.raises(DatabaseError, match="live staging ownership"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(DEFINER, [refreshes.full.pk])


def test_the_definer_refuses_without_staging_ownership_or_its_grant():
    """An unknown snapshot is refused; PUBLIC and web cannot execute it."""
    with (
        pytest.raises(DatabaseError, match="live staging ownership"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(DEFINER, [uuid4()])
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT has_function_privilege('public', "
            "'public.stewardship_source_snapshot_analyze_v1(uuid)', 'EXECUTE')"
        )
        assert cursor.fetchone()[0] is False
    with (
        task_login(ServiceRole.WEB),
        pytest.raises(DatabaseError, match="permission denied"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(DEFINER, [uuid4()])


def test_the_definer_refuses_a_ready_snapshot_while_its_owner_is_live(tmp_path):
    """The staging predicate on its own: with the lease and task still live,
    the definer analyzes the attempt's staging snapshot, then refuses the
    same snapshot once it is ready."""
    credential, execution, source_claim, *_ = setup(tmp_path)
    attempt = begin_refresh_attempt(execution, source_claim, credential)
    with connection.cursor() as cursor:
        cursor.execute(DEFINER, [attempt.snapshot_id])
        assert cursor.fetchone()[0] is True
    assert stage(attempt, execution, source_claim).state == "ready"
    with (
        pytest.raises(DatabaseError, match="live staging ownership"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(DEFINER, [attempt.snapshot_id])


def test_a_lock_wait_past_its_limit_is_logged_and_the_refresh_goes_on(
    tmp_path, monkeypatch
):
    """Another session's ANALYZE-class lock on a membership table stops the
    call at its lock timeout: the timeout log records what, limit and
    elapsed, nothing is analyzed, and the snapshot still becomes ready (the
    completion trigger analyzes once the lock is gone)."""
    from parishkit.stewardship.audit.models import OperationalLog

    monkeypatch.setattr(snapshots, "ANALYZE_LOCK_SECONDS", 1)
    credential, execution, lease, *_ = setup(tmp_path)
    fake_provider(monkeypatch, pages())
    results = []
    real = refreshing.analyze_staging

    def analyze(snapshot_id, claim, **options):
        """The real call while another session holds a conflicting lock."""
        blocker = separate_session()
        blocker.autocommit = False
        try:
            blocker.execute(
                "LOCK TABLE public.stewardship_snapshot_family "
                "IN SHARE UPDATE EXCLUSIVE MODE"
            )
            results.append(real(snapshot_id, claim, **options))
        finally:
            blocker.rollback()
            blocker.close()
        return results[-1]

    monkeypatch.setattr(refreshing, "analyze_staging", analyze)
    assert run(credential, execution, lease).state == "ready"
    assert results == [False]
    entry = OperationalLog.objects.get(event="task_timed_out")
    assert entry.level == "WARNING"
    assert entry.context["what"] == "lock_timeout"
    assert entry.context["limit_seconds"] == 1
    assert entry.context["task_id"] == str(execution.claim.run_id)
    assert 1 <= entry.context["elapsed_seconds"] < 10


def test_the_migration_check_refuses_a_definer_not_installed_as_declared():
    """0086's DO block raises when the definer has lost SECURITY DEFINER,
    PUBLIC may execute it, its owner cannot analyze the tables, it is
    missing, or the completion trigger is gone; all rolled back. The whole
    file installs on a database without it."""
    text = FILE.read_text(encoding="utf-8")
    check = text[text.index("DO $check$") :]
    function = "public.stewardship_source_snapshot_analyze_v1(uuid)"
    with connection.cursor() as cursor:
        cursor.execute(check)
        for statement in (
            f"ALTER FUNCTION {function} SECURITY INVOKER",
            f"GRANT EXECUTE ON FUNCTION {function} TO PUBLIC",
            # A role that owns none of the tables (created and rolled back).
            "CREATE ROLE pk_stewardship_analyze_probe NOLOGIN; "
            f"ALTER FUNCTION {function} OWNER TO pk_stewardship_analyze_probe",
            f"DROP FUNCTION {function}",
            "DROP TRIGGER stewardship_source_snapshot_statistics_v1 "
            "ON public.stewardship_source_snapshot",
        ):
            with pytest.raises(DatabaseError, match="0086"), transaction.atomic():
                cursor.execute(statement)
                cursor.execute(check)
        with transaction.atomic():
            cursor.execute(f"DROP FUNCTION {function}")
            cursor.execute(text)
            transaction.set_rollback(True)
        cursor.execute(check)

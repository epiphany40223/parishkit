"""Take a backup now's requests under the real logins and guard (ADM-13 PR 3).

The web login records a request only for a freshly signed-in Administrator,
one at a time; only the backup login moves it forward; nobody deletes it.
Request mode, the host cron's five-minute poll, stays silent with nothing to
do, waits out a bulk send and another backup, and claims, runs and settles a
waiting request; a scheduled run completes one too. The web reads the
newest backup's size and version and the request for System health.
"""

import json
from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship import (
    backup,
    backup_boundaries,
    backup_commands,
    backup_requests,
    operator_commands,
    runtime_database,
    runtime_paths,
    startup_interlock,
    system_health,
)
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.cli import main
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.backup_models import BackupRequest, BackupRun

from .auth_builders import signed_in
from .test_background_grants_postgresql import task_login
from .test_backup_postgresql import FACTS, backup_login, now, recorded

pytestmark = pytest.mark.django_db(transaction=True)


def session():
    """The signed-in Administrator's live session: id, principal and sign-in."""
    return PortalSession.objects.order_by("-authenticated_at").first()


def insert(login=None, **values):
    """Record one request as the web would, under the web login by default."""
    current = session()
    row = {
        "id": uuid4(),
        "actor_id": current.principal_id,
        "session_id": current.pk,
        "authenticated_at": current.authenticated_at,
    } | values
    with task_login(login or ServiceRole.WEB, exact=True), transaction.atomic():
        return BackupRequest.objects.create(**row)


def owner_request(*, age=timedelta(0), state="waiting", **values):
    """A request written directly as the schema owner, ``age`` in the past."""
    instant = now() - age
    return BackupRequest.objects.create(
        id=uuid4(),
        actor_id=uuid4(),
        session_id=uuid4(),
        authenticated_at=instant,
        created_at=instant,
        state=state,
        **values,
    )


def test_the_web_records_one_request_for_a_fresh_administrator(auth_service, google):
    """Fresh sign-in, one at a time, every time the database's."""
    signed_in()
    first = insert()
    assert first.state == "waiting" and first.held_at is None
    # A second while the first waits is refused, whoever asks.
    with pytest.raises(DatabaseError, match="already waiting or running"):
        insert()
    # A stale sign-in is refused: move the session's sign-in back past the
    # five-minute window (the owner may; the guard refuses it for others).
    BackupRequest.objects.filter(pk=first.pk).update(state="expired", finished_at=now())
    current = session()
    with pytest.raises(DatabaseError, match="fresh Administrator"):
        insert(authenticated_at=current.authenticated_at - timedelta(minutes=10))
    # Another login cannot record one.
    with pytest.raises(DatabaseError):
        insert(login=ServiceRole.WORKER)


def test_a_lapsed_request_does_not_block_a_new_one(auth_service, google):
    """A request that waited 30 minutes is not live, even before it is marked."""
    signed_in()
    owner_request(age=timedelta(minutes=31))
    assert insert().state == "waiting"


def test_only_the_backup_login_settles_requests_and_only_forward(auth_service, google):
    """Web cannot update; the backup login moves waiting to running to done."""
    signed_in()
    request = insert()
    with (
        task_login(ServiceRole.WEB, exact=True),
        pytest.raises(DatabaseError),
        transaction.atomic(),
    ):
        BackupRequest.objects.filter(pk=request.pk).update(state="running")
    run = recorded()
    with backup_login():
        # A fresh waiting request cannot be expired yet.
        with (
            pytest.raises(DatabaseError, match="after 30 minutes"),
            transaction.atomic(),
        ):
            BackupRequest.objects.filter(pk=request.pk).update(
                state="expired", finished_at=now()
            )
        backup_requests.held(request)
        assert request.held_at is not None and request.state == "waiting"
        backup_requests.claim(request)
        assert request.state == "running" and request.claimed_at is not None
        # Backwards is refused.
        with pytest.raises(DatabaseError, match="only forward"), transaction.atomic():
            BackupRequest.objects.filter(pk=request.pk).update(state="waiting")
        backup_requests.finish(request, run.pk)
        assert request.state == "finished" and request.backup_run_id == run.pk
        with pytest.raises(DatabaseError), transaction.atomic():
            BackupRequest.objects.filter(pk=request.pk).delete()


def test_lapsed_requests_are_expired_or_failed(auth_service):
    """Waiting 30 minutes expires; running two hours fails as did_not_finish."""
    old = owner_request(age=timedelta(minutes=31))
    held = owner_request(age=timedelta(minutes=40), held_at=now())
    stuck = owner_request(age=timedelta(hours=3), state="running", claimed_at=now())
    BackupRequest.objects.filter(pk=stuck.pk).update(
        claimed_at=now() - timedelta(hours=3)
    )
    with backup_login():
        assert backup_requests.settle_lapsed() == (1, 1)
    states = dict(BackupRequest.objects.values_list("id", "state"))
    assert states == {old.pk: "expired", held.pk: "waiting", stuck.pk: "failed"}
    stuck.refresh_from_db()
    assert stuck.failure_kind == "did_not_finish"


@pytest.fixture
def command(monkeypatch, capsys):
    """Run ``pk-stewardship backup`` with the host replaced and real SQL.

    Returns ``run(*options)`` giving ``(exit code, printed JSON or None)``.
    The backup itself records a run as the real one does.
    """

    def run_backup(configuration, *, record, recipient):
        """Record one run, as the real backup does."""
        with transaction.atomic():
            started = now()
        record(started_at=started, **FACTS)
        return {
            "database": {"plaintext_bytes": 1024},
            "files": {"plaintext_bytes": 512},
            "recipient_fingerprint": FACTS["recipient_fingerprint"],
        }

    monkeypatch.setattr(backup_commands, "configure_logging", lambda: None)
    monkeypatch.setattr(backup_commands, "load_deployment", lambda path: None)
    monkeypatch.setattr(backup_commands, "_admit_backup_identity", lambda: None)
    monkeypatch.setattr(backup_commands, "_copy_offsite", lambda c: {"state": "x"})
    monkeypatch.setattr(backup_boundaries, "admit_backup_service", lambda c: None)
    monkeypatch.setattr(
        operator_commands, "configure_operator_database", lambda c: None
    )
    monkeypatch.setattr(runtime_database, "require_current_schema", lambda: None)
    monkeypatch.setattr(
        runtime_paths, "RuntimeLayout", lambda c: SimpleNamespace(interlock=None)
    )
    monkeypatch.setattr(
        startup_interlock,
        "StartupLease",
        lambda path, offline: nullcontext(),
    )
    monkeypatch.setattr(backup, "run_backup", run_backup)

    def run(*options):
        """One command; its exit code and parsed JSON line, if it printed one."""
        with backup_login():
            code = main(["backup", "--config", "unused", *options])
            # The advisory lock is per session: release it as process exit would.
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock_all()")
        out = capsys.readouterr().out.strip()
        return code, json.loads(out) if out else None

    run.backup = run_backup
    return run


def test_request_mode_is_silent_with_nothing_waiting(command):
    """No request: exit 0, no output, no backup."""
    assert command("--request") == (0, None)
    assert BackupRun.objects.count() == 0


def test_request_mode_runs_a_waiting_request_and_settles_it(command):
    """Claimed, run and finished, linked to its run; the line names it."""
    request = owner_request()
    code, output = command("--request")
    assert code == 0 and output["request_id"] == str(request.pk)
    request.refresh_from_db()
    assert request.state == "finished"
    assert request.backup_run_id == BackupRun.objects.get().pk
    # The next poll has nothing to do.
    assert command("--request") == (0, None)


def test_request_mode_waits_out_a_bulk_send(command, monkeypatch):
    """During a send the request is stamped held and stays waiting."""
    from parishkit.stewardship.source import send_hold

    request = owner_request()
    monkeypatch.setattr(send_hold, "family_send_active", lambda: True)
    assert command("--request") == (0, None)
    request.refresh_from_db()
    assert request.state == "waiting" and request.held_at is not None
    assert BackupRun.objects.count() == 0
    # A scheduled run is not held, and completes the held request.
    code, output = command()
    assert code == 0 and output["request_id"] == str(request.pk)
    request.refresh_from_db()
    assert request.state == "finished"


def test_request_mode_stops_quietly_when_a_held_request_moved_on(
    command, monkeypatch, caplog
):
    """Stamping a request held that moved on warns, like a refused claim."""
    from parishkit.stewardship.source import send_hold

    request = owner_request()

    def stale():
        """The request as read before another process moved it on."""
        row = BackupRequest.objects.get(pk=request.pk)
        row.state = "running"
        return row

    monkeypatch.setattr(backup_requests, "waiting", stale)
    monkeypatch.setattr(send_hold, "family_send_active", lambda: True)
    assert command("--request") == (0, None)
    assert [record for record in caplog.records if record.levelname == "WARNING"]
    request.refresh_from_db()
    assert request.state == "waiting" and request.held_at is None
    assert BackupRun.objects.count() == 0


def test_request_mode_leaves_a_request_while_another_backup_runs(command):
    """The backup lock is taken elsewhere: quiet, and the request waits."""
    from django.db import connections

    request = owner_request()
    other = connections.create_connection("default")
    try:
        with other.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_lock(%s,%s)", backup_commands.BACKUP_LOCK
            )
        assert command("--request") == (0, None)
        request.refresh_from_db()
        assert request.state == "waiting"
    finally:
        other.close()
    assert command("--request")[1]["request_id"] == str(request.pk)


def test_a_failed_backup_fails_its_request(command, monkeypatch):
    """The request records the failure's category; the command exits 2."""
    request = owner_request()

    def broken(configuration, *, record, recipient):
        """The dump failed."""
        raise DatabaseError("dump failed")

    monkeypatch.setattr(backup, "run_backup", broken)
    assert command("--request") == (2, None)
    request.refresh_from_db()
    assert request.state == "failed"
    assert request.failure_kind == "database_unavailable"


def test_a_scheduled_run_gives_up_on_the_lock_after_its_limit(command, monkeypatch):
    """Held past the limit: a timeout entry with the limit, and exit 2."""
    from django.db import connections

    from parishkit.stewardship.audit.models import OperationalLog

    monkeypatch.setattr(backup_commands, "SCHEDULED_LOCK_SECONDS", 1)
    other = connections.create_connection("default")
    try:
        with other.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_lock(%s,%s)", backup_commands.BACKUP_LOCK
            )
        assert command() == (2, None)
    finally:
        other.close()
    entry = OperationalLog.objects.filter(event="task_timed_out").get()
    assert entry.context["what"] == "lock_timeout"
    assert entry.context["limit_seconds"] == 1
    assert BackupRun.objects.count() == 0


def test_the_web_reads_size_version_and_the_newest_request(auth_service):
    """System health's backup panel reads only what its grants allow."""
    recorded(database_bytes=2048, files_bytes=1024, application_version="1.5.0")
    owner_request(held_at=now())
    with task_login(ServiceRole.WEB, exact=True), transaction.atomic():
        at, matches, size, version = system_health._backup(
            SimpleNamespace(
                active_configuration=SimpleNamespace(
                    canonical_document={"sections": {"integrations": []}}
                )
            )
        )
        request = system_health._backup_request(now())
        # Digests stay unreadable.
        with pytest.raises(DatabaseError), transaction.atomic():
            BackupRun.objects.values_list("manifest_digest").first()
    assert (size, version) == (3072, "1.5.0")
    assert request.status == "held" and request.state == "waiting"


def test_no_request_is_recorded_or_claimed_during_a_restore_review(
    auth_service, google
):
    """Neither a new request nor a claim of a waiting one."""
    signed_in()
    waiting = owner_request()
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica")
        cursor.execute(
            "UPDATE stewardship_system_configuration SET restore_review_required=true"
        )
    BackupRequest.objects.filter(pk=waiting.pk).update(
        state="expired", finished_at=now()
    )
    with pytest.raises(DatabaseError, match="restore review"):
        insert()
    again = owner_request()
    with (
        backup_login(),
        pytest.raises(DatabaseError, match="can no longer be run"),
        transaction.atomic(),
    ):
        BackupRequest.objects.filter(pk=again.pk).update(state="running")


def test_a_lapsed_request_is_never_claimed(auth_service):
    """Thirty minutes without a claim: it can only expire."""
    lapsed = owner_request(age=timedelta(minutes=31))
    with (
        backup_login(),
        pytest.raises(DatabaseError, match="can no longer be run"),
        transaction.atomic(),
    ):
        BackupRequest.objects.filter(pk=lapsed.pk).update(state="running")


def test_another_session_a_revoked_one_or_staff_are_refused(auth_service, google):
    """The request must name the signed-in Administrator's own live session."""
    from parishkit.stewardship.accounts.models import PortalSession

    from ..policy_factory import address
    from .campaign_builders import change

    signed_in()
    with pytest.raises(DatabaseError, match="fresh Administrator"):
        insert(actor_id=uuid4())
    from django.db.models import F

    PortalSession.objects.filter(pk=session().pk).update(
        revoked_at=now(), version=F("version") + 1
    )
    with pytest.raises(DatabaseError, match="fresh Administrator"):
        insert()
    store = auth_service.store
    change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address("reader@example.org", roles=("staff",)),
            }
        ],
    )
    google[0]["email"] = "reader@example.org"
    signed_in()
    with pytest.raises(DatabaseError, match="fresh Administrator"):
        insert()
    assert BackupRequest.objects.count() == 0


def test_two_inserts_at_once_record_one_request(auth_service, google):
    """The guard's lock serializes them; the second sees the first and refuses."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    signed_in()
    current = session()
    barrier = Barrier(2)

    def attempt():
        """Insert under the web login in a separate connection."""
        from django.db import connections

        try:
            barrier.wait()
            insert(
                actor_id=current.principal_id,
                session_id=current.pk,
                authenticated_at=current.authenticated_at,
            )
            return "recorded"
        except DatabaseError:
            return "refused"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(2) as pool:
        results = sorted(pool.map(lambda _: attempt(), range(2)))
    assert results == ["recorded", "refused"]
    assert BackupRequest.objects.count() == 1


def test_the_backup_login_cannot_change_who_asked(auth_service):
    """Actor, session and sign-in are fixed (and outside its column grant)."""
    request = owner_request()
    for field, value in (
        ("actor_id", uuid4()),
        ("session_id", uuid4()),
        ("authenticated_at", now()),
    ):
        with (
            backup_login(),
            pytest.raises(DatabaseError, match="permission denied"),
            transaction.atomic(),
        ):
            BackupRequest.objects.filter(pk=request.pk).update(**{field: value})


def test_the_guard_keeps_who_asked_even_for_a_wider_grant(auth_service):
    """With an UPDATE grant on every column, the guard still refuses."""
    request = owner_request()
    name = "pk_stewardship_backup_worker"
    for field, value in (
        ("actor_id", uuid4()),
        ("session_id", uuid4()),
        ("authenticated_at", now()),
    ):
        with backup_login():
            with connection.cursor() as cursor:
                cursor.execute("RESET SESSION AUTHORIZATION")
                cursor.execute(f"GRANT UPDATE ON stewardship_backup_request TO {name}")
                cursor.execute(f"SET SESSION AUTHORIZATION {name}")
            with (
                pytest.raises(DatabaseError, match="keeps its identity"),
                transaction.atomic(),
            ):
                BackupRequest.objects.filter(pk=request.pk).update(**{field: value})


def test_a_scheduled_run_backs_up_without_a_request_it_cannot_claim(
    command, monkeypatch
):
    """A refused claim stops request mode, never a scheduled backup."""
    request = owner_request()

    def refused(row):
        """The guard refused the claim (it lapsed or a review began)."""
        raise DatabaseError("This backup request can no longer be run")

    monkeypatch.setattr(backup_requests, "claim", refused)
    assert command("--request") == (0, None)
    assert BackupRun.objects.count() == 0
    code, output = command()
    assert code == 0 and "request_id" not in output
    assert BackupRun.objects.count() == 1
    request.refresh_from_db()
    assert request.state == "waiting"


def test_a_request_that_moved_on_is_skipped_like_a_refused_claim(
    command, monkeypatch, caplog
):
    """A claim matching no row warns; request mode stops, a scheduled run backs up."""
    request = owner_request()

    def stale():
        """The request as read before another process moved it on."""
        row = BackupRequest.objects.get(pk=request.pk)
        row.state = "running"
        return row

    monkeypatch.setattr(backup_requests, "waiting", stale)
    assert command("--request") == (0, None)
    assert BackupRun.objects.count() == 0
    assert [record for record in caplog.records if record.levelname == "WARNING"]
    code, output = command()
    assert code == 0 and "request_id" not in output
    assert BackupRun.objects.count() == 1
    request.refresh_from_db()
    assert request.state == "waiting"


def test_during_a_restore_review_a_scheduled_run_still_backs_up(auth_service, command):
    """No request is claimed during a review; the set is still made."""
    request = owner_request()
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica")
        cursor.execute(
            "UPDATE stewardship_system_configuration SET restore_review_required=true"
        )
    assert command("--request") == (0, None)
    code, output = command()
    assert code == 0 and "request_id" not in output
    request.refresh_from_db()
    assert request.state == "waiting"


def test_request_mode_is_quiet_while_offline_work_holds_the_interlock(
    command, monkeypatch
):
    """The startup interlock is held: no backup, and the request waits."""

    class Busy:
        """A lease offline work already holds."""

        def __init__(self, path, *, offline):
            pass

        def __enter__(self):
            raise startup_interlock.StartupBusy("busy")

    request = owner_request()
    monkeypatch.setattr(startup_interlock, "StartupLease", Busy)
    assert command("--request") == (0, None)
    request.refresh_from_db()
    assert request.state == "waiting"
    assert BackupRun.objects.count() == 0


def test_request_mode_logs_a_refused_first_read(command, monkeypatch, caplog):
    """A missed database-grants step is visible in the cron output, once."""
    from django.db import ProgrammingError

    def refused():
        """The backup login lacks its request grant."""
        raise ProgrammingError("permission denied")

    monkeypatch.setattr(backup_requests, "waiting", refused)
    assert command("--request") == (0, None)
    assert [record for record in caplog.records if record.levelname == "WARNING"], (
        caplog.records
    )


def test_the_send_check_runs_under_the_real_backup_login(auth_service):
    """Request mode's bulk-send check reads only what the backup login can."""
    from parishkit.stewardship.source.send_hold import family_send_active

    with backup_login():
        assert family_send_active() is False


def test_settling_lapsed_requests_is_recorded_as_a_timeout(auth_service):
    """Each kind settled leaves one entry with its limit, time past it and count."""
    from parishkit.stewardship.audit.models import OperationalLog

    owner_request(age=timedelta(minutes=45))
    owner_request(age=timedelta(minutes=50))
    with backup_login():
        assert backup_requests.settle_lapsed() == (2, 0)
    entry = OperationalLog.objects.get(event="task_timed_out")
    assert entry.context["what"] == "lease"
    assert entry.context["limit_seconds"] == 1800
    assert entry.context["count"] == 2
    assert entry.context["elapsed_seconds"] >= 50 * 60


@pytest.mark.parametrize("extra", [None, "actor_id"])
def test_the_upgrade_check_admits_only_the_registered_request_columns(
    auth_service, extra
):
    """The backup login's request-column grants pass the no-excess check.

    One more column UPDATE (who asked) is excess and refused, as
    provisioning's reader admission refuses it.
    """
    from parishkit.stewardship.runtime_grants import runtime_grants
    from parishkit.stewardship.upgrade_check import no_excess

    name = "pk_stewardship_backup_worker"
    tables, columns = runtime_grants(ServiceRole.BACKUP_WORKER)
    with backup_login(), connection.cursor() as cursor:
        cursor.execute("RESET SESSION AUTHORIZATION")
        if extra:
            cursor.execute(
                f"GRANT UPDATE ({extra}) ON stewardship_backup_request TO {name}"
            )
        cursor.execute("SELECT " + no_excess(name, tables, columns, reader=True))
        assert cursor.fetchone() == (extra is None,)
        cursor.execute(f"SET SESSION AUTHORIZATION {name}")

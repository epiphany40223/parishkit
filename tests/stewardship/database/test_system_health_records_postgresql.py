"""The System health records under the real logins and guards (ADM-13 PR 1, #530).

Service status records: each online service's login writes only its own
service's rows through the reporter, every time comes from the database
clock, identities never change, and only the worker's housekeeping deletes a
row a day after its last report. Drop counts: only the worker records them,
only for a rejected attempt, with a failure flag that follows the loss rule,
and they never change.
"""

from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from psycopg import sql

from parishkit import __version__
from parishkit.stewardship.accounts.credential_database import (
    admit_installer_database,
)
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.service_status_models import ServiceStatus
from parishkit.stewardship.runtime_grants import runtime_grants
from parishkit.stewardship.service_status import (
    ServiceStatusReporter,
    prune_service_status,
)
from parishkit.stewardship.source.attempts import begin_refresh_attempt
from parishkit.stewardship.source.drop_models import SourceDropCount
from parishkit.stewardship.source.failures import settle_failed_read
from parishkit.stewardship.source.loading import CountCheck, DestructiveSourceChange
from parishkit.stewardship.source.models import (
    SourceCurrent,
    SourceMutationLease,
    SourceSnapshot,
)

from .role_grants import grant_runtime
from .test_background_grants_postgresql import task_login
from .test_runtime_auth_grants_postgresql import web_login
from .test_source_attempts_postgresql import setup

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def source_singletons():
    """Disposable flush recreates only idle source migration seeds."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)


@contextmanager
def credential_login(target):
    """An exact credential installer login with its real runtime grants."""
    name = "pk_stewardship_credential_" + target
    role = sql.Identifier(name)
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT").format(role))
    try:
        tables, columns = runtime_grants(
            ServiceRole.CREDENTIAL_INSTALLER, target=target
        )
        with connection.cursor() as cursor:
            grant_runtime(cursor, name, tables, columns)
            cursor.execute(sql.SQL("SET SESSION AUTHORIZATION {}").format(role))
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            cursor.execute(sql.SQL("DROP OWNED BY {}").format(role))
            cursor.execute(sql.SQL("DROP ROLE {}").format(role))


def database_now():
    """The database clock, which the guard writes every time from."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT statement_timestamp()")
        return cursor.fetchone()[0]


def raw(statement, values=()):
    """Run one statement in its own transaction (a refused one rolls back)."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(statement, values)
        return cursor.rowcount


def insert_row(service="web", *, process="main", target=None, sender=None, age=None):
    """Insert a status row directly, as a login's own INSERT would.

    ``age`` (the schema owner only) dates the row's last report that long ago.
    """
    identifier = uuid4()
    raw(
        "INSERT INTO stewardship_service_status (id,service,process,target,"
        "started_at,reported_at,application_version,debug_logging,sender_state) "
        "VALUES (%s,%s,%s,%s,now()-%s,now()-%s,'1.0.0',false,%s)",
        [
            identifier,
            service,
            process,
            target,
            age or timedelta(),
            age or timedelta(),
            sender,
        ],
    )
    return identifier


@pytest.mark.parametrize(
    "role,process,sender",
    [
        (ServiceRole.WORKER, "main", None),
        (ServiceRole.WORKER, "source", None),
        (ServiceRole.SCHEDULER, "main", None),
        (ServiceRole.MAIL_DISPATCH, "main", ("running", None)),
        (ServiceRole.MAIL_DISPATCH, "mail", ("gmail_held", 90.0)),
        (ServiceRole.CONFIG_INSTALLER, "main", None),
    ],
)
def test_each_background_login_reports_its_own_record(role, process, sender):
    """The reporter's real SQL runs under each login's exact grants."""
    reporter = ServiceStatusReporter(
        role.value,
        process=process,
        sender=None if sender is None else (lambda: sender),
    )
    with task_login(role):
        assert reporter.report(connect=True)
        # Another login's service is refused by the guard.
        with pytest.raises(DatabaseError, match="only its own status"):
            insert_row("web")
    row = ServiceStatus.objects.get(pk=reporter.id)
    now = database_now()
    assert (row.service, row.process, row.target) == (role.value, process, None)
    assert row.started_at == row.reported_at and now - row.reported_at < timedelta(
        minutes=1
    )
    assert row.application_version == __version__ and row.debug_logging is False
    if sender is None:
        assert row.sender_state is row.sender_since is row.sender_until is None
    else:
        assert row.sender_state == sender[0] and row.sender_since == row.reported_at
        if sender[1] is None:
            assert row.sender_until is None
        else:
            assert row.sender_until - row.reported_at == timedelta(seconds=sender[1])


def test_web_reports_and_reads_every_record():
    """Web writes its own rows and reads all of them for the page."""
    other = insert_row("worker")
    reporter = ServiceStatusReporter("web")
    with web_login():
        assert reporter.report(connect=True)
        assert set(ServiceStatus.objects.values_list("pk", flat=True)) == {
            other,
            reporter.id,
        }
        with pytest.raises(DatabaseError, match="only its own status"):
            raw(
                "UPDATE stewardship_service_status SET debug_logging=true WHERE id=%s",
                [other],
            )
        # Web has no delete grant at all.
        with pytest.raises(DatabaseError):
            raw("DELETE FROM stewardship_service_status WHERE id=%s", [reporter.id])


def test_a_credential_installer_is_admitted_and_reports_by_target():
    """The installers' new write grant passes their own admission."""
    reporter = ServiceStatusReporter("credential-installer", target="slack")
    with credential_login("slack"):
        admit_installer_database("slack")
        assert reporter.report(connect=True)
        # Another target's row is another login's.
        with pytest.raises(DatabaseError, match="only its own status"):
            insert_row("credential-installer", target="parishsoft")
        # Writers other than web read ids only.
        with pytest.raises(DatabaseError):
            raw("SELECT application_version FROM stewardship_service_status")
    row = ServiceStatus.objects.get(pk=reporter.id)
    assert (row.service, row.target) == ("credential-installer", "slack")


def test_refresh_keeps_identity_and_tracks_when_the_sender_state_began():
    """reported_at moves with the database clock; sender_since only on a change."""
    states = iter(
        [("running", None), ("running", None), ("halted", None), ("halted", None)]
    )
    reporter = ServiceStatusReporter("mail-dispatch", sender=lambda: next(states))
    seen = []
    for _ in range(4):
        reporter.due = 0.0
        with task_login(ServiceRole.MAIL_DISPATCH):
            assert reporter.report(connect=True)
        seen.append(ServiceStatus.objects.get(pk=reporter.id))
    assert seen[0].sender_since == seen[1].sender_since < seen[2].sender_since
    assert seen[2].sender_since == seen[3].sender_since
    assert seen[3].reported_at > seen[0].reported_at
    assert {row.started_at for row in seen} == {seen[0].started_at}
    with task_login(ServiceRole.MAIL_DISPATCH):
        # Times and identity are not even granted: the guard sets the times.
        for column, value in (
            ("reported_at", database_now() + timedelta(days=1)),
            ("sender_since", database_now()),
            ("application_version", "9"),
        ):
            with pytest.raises(DatabaseError, match="permission denied"):
                raw(
                    sql.SQL(
                        "UPDATE stewardship_service_status SET {}=%s WHERE id=%s"
                    ).format(sql.Identifier(column)),
                    [value, reporter.id],
                )
    # The owner's own reported_at is kept (it may date rows); a login's
    # refresh always takes the database clock, as checked above.
    assert ServiceStatus.objects.get(pk=reporter.id).reported_at <= database_now()
    # Not even the schema owner can change what the record is.
    for column, value in (
        ("application_version", "9.9.9"),
        ("process", "mail"),
        ("started_at", database_now() - timedelta(hours=1)),
    ):
        with pytest.raises(DatabaseError, match="keeps its identity"):
            raw(
                sql.SQL(
                    "UPDATE stewardship_service_status SET {}=%s WHERE id=%s"
                ).format(sql.Identifier(column)),
                [value, reporter.id],
            )


def test_only_housekeeping_deletes_and_only_a_day_after_the_last_report():
    """The worker prunes stale rows; fresh rows and other logins are refused."""
    fresh = insert_row("web", age=timedelta(hours=23, minutes=59))
    stale = insert_row("scheduler", age=timedelta(days=1, minutes=1))
    with task_login(ServiceRole.SCHEDULER), pytest.raises(DatabaseError):
        raw("DELETE FROM stewardship_service_status WHERE id=%s", [stale])
    with task_login(ServiceRole.WORKER):
        with pytest.raises(DatabaseError, match="a day after its last report"):
            raw("DELETE FROM stewardship_service_status WHERE id=%s", [fresh])
        with transaction.atomic():
            assert prune_service_status() == 1
    assert list(ServiceStatus.objects.values_list("pk", flat=True)) == [fresh]


def test_a_pruned_record_is_written_again_under_the_same_identity():
    """A process silent for a day reappears on its next report."""
    reporter = ServiceStatusReporter("scheduler")
    with task_login(ServiceRole.SCHEDULER):
        assert reporter.report(connect=True)
    ServiceStatus.objects.filter(pk=reporter.id).delete()
    reporter.due = 0.0
    with task_login(ServiceRole.SCHEDULER):
        assert reporter.report(connect=True)
    assert ServiceStatus.objects.filter(pk=reporter.id).exists()


@pytest.mark.parametrize(
    "values,constraint",
    [
        ({"service": "backup-worker"}, "service_status_names"),
        ({"process": "source", "service": "web"}, "service_status_process"),
        ({"service": "credential-installer"}, "service_status_process"),
        ({"target": "slack"}, "service_status_process"),
        ({"sender_state": "running"}, "service_status_sender"),
        ({"service": "mail-dispatch"}, "service_status_sender"),
    ],
)
def test_record_shape_is_constrained(values, constraint):
    """Names, process kinds, targets and sender states are closed lists."""
    row = {
        "service": "web",
        "process": "main",
        "target": None,
        "sender_state": None,
    } | values
    with pytest.raises(DatabaseError, match=constraint):
        insert_row(
            row["service"],
            process=row["process"],
            target=row["target"],
            sender=row["sender_state"],
        )


def refused_attempt(tmp_path):
    """A real full attempt whose staging the refusal has rejected."""
    credential, execution, lease, *_ = setup(tmp_path)
    attempt = begin_refresh_attempt(execution, lease, credential)
    return attempt, execution, lease


def test_a_refused_load_records_every_count_it_was_checked_on(tmp_path):
    """The refusal's own transaction writes all counts, failing or not."""
    attempt, execution, lease = refused_attempt(tmp_path)
    checks = (
        CountCheck("family", 100, 60, 25, True),
        CountCheck("member", 200, 190, 25, False),
        CountCheck("valid_email_contacts", None, 50, 25, False),
    )
    error = DestructiveSourceChange(
        "PRIVATE", measure="family", before=100, after=60, checks=checks
    )
    result = settle_failed_read(execution, error, source_claim=lease)
    assert result.state == "failed"
    assert SourceSnapshot.objects.get(pk=attempt.snapshot_id).state == "rejected"
    rows = SourceDropCount.objects.filter(attempt=attempt).order_by("measure")
    assert [
        (row.measure, row.before, row.after, row.limit_percent, row.failed)
        for row in rows
    ] == sorted(
        (check.measure, check.before, check.after, 25, check.failed) for check in checks
    )
    assert {row.correlation_id for row in rows} == {execution.correlation_id}
    # Append-only.
    with pytest.raises(DatabaseError, match="append-only"):
        raw("UPDATE stewardship_source_drop_count SET after=0")
    with pytest.raises(DatabaseError, match="append-only"):
        raw("DELETE FROM stewardship_source_drop_count")


def test_drop_count_guard_admits_only_the_worker_and_the_loss_rule(tmp_path):
    """The flag cannot disagree with the rule, nor precede the rejection."""
    attempt, execution, lease = refused_attempt(tmp_path)

    def record(measure, before, after, failed, limit=25):
        """Insert one count directly, as the worker's refusal would."""
        raw(
            "INSERT INTO stewardship_source_drop_count (id,created_at,"
            "correlation_id,attempt_id,measure,before,after,limit_percent,failed) "
            "VALUES (%s,now(),%s,%s,%s,%s,%s,%s,%s)",
            [uuid4(), uuid4(), attempt.pk, measure, before, after, limit, failed],
        )

    # Not yet rejected: the attempt is still staging.
    with pytest.raises(DatabaseError, match="rejected refresh attempt"):
        record("family", 100, 10, True)
    settle_failed_read(
        execution, DestructiveSourceChange("PRIVATE"), source_claim=lease
    )
    for measure, before, after, failed, limit in (
        ("family", 100, 74, False, 25),
        ("member", 100, 75, True, 25),
        ("ministry", 2, 0, False, 25),
        ("fund", 5, 0, True, 100),
    ):
        with pytest.raises(DatabaseError, match="loss rule|drop_count_failure"):
            record(measure, before, after, failed, limit)
    record("family", 100, 74, True)
    record("member", 100, 75, False)
    record("ministry", 2, 0, True)
    record("fund", 5, 0, False, 100)
    record("roster", None, 0, False)
    with task_login(ServiceRole.SCHEDULER), pytest.raises(DatabaseError):
        record("portal_eligible_families", 1, 1, False)
    with web_login(), pytest.raises(DatabaseError):
        record("portal_eligible_families", 1, 1, False)
    with task_login(ServiceRole.WORKER):
        record("portal_eligible_families", 1, 1, False)
        assert SourceDropCount.objects.filter(attempt=attempt).count() == 6
    with web_login():
        assert SourceDropCount.objects.filter(attempt=attempt).count() == 6


def test_the_worker_login_records_a_refusal_s_counts(tmp_path):
    """The real worker grants cover the settlement's inserts and returning read."""
    attempt, execution, lease = refused_attempt(tmp_path)
    error = DestructiveSourceChange(
        "PRIVATE",
        measure="member",
        before=200,
        after=20,
        checks=(CountCheck("member", 200, 20, 25, True),),
    )
    with task_login(ServiceRole.WORKER):
        settle_failed_read(execution, error, source_claim=lease)
    row = SourceDropCount.objects.get(attempt=attempt)
    assert (row.measure, row.failed, row.actor_id) == (
        "member",
        True,
        execution.claim.worker_id,
    )


def test_a_write_stopped_by_its_lock_limit_leaves_a_durable_timeout_entry():
    """#287: a real lock timeout, on a task's thread, records no task.

    Another connection holds the record's row; the refresh waits past its
    one-second lock limit, and the timeout log gets a WARNING task_timed_out
    naming the limit, its seconds and the time taken, but no task.
    """
    from threading import Event as ThreadEvent
    from threading import Thread

    from django.db import connections

    from parishkit.stewardship.audit.models import OperationalLog
    from parishkit.stewardship.observability import task_scope

    reporter = ServiceStatusReporter("scheduler")
    with task_login(ServiceRole.SCHEDULER):
        assert reporter.report(connect=True)
    held, release = ThreadEvent(), ThreadEvent()

    def hold():
        """Lock the record's row from another connection until released."""
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM stewardship_service_status WHERE id=%s FOR UPDATE",
                    [reporter.id],
                )
                held.set()
                assert release.wait(10)
        finally:
            connections.close_all()

    holder = Thread(target=hold)
    holder.start()
    try:
        assert held.wait(10)
        reporter.due = 0.0
        task = uuid4()
        with task_login(ServiceRole.SCHEDULER), task_scope(task):
            assert not reporter.report(connect=True)
    finally:
        release.set()
        holder.join(10)
    entry = OperationalLog.objects.get(event="task_timed_out")
    assert entry.level == "WARNING"
    assert entry.context["what"] == "lock_timeout"
    assert entry.context["limit_seconds"] == 1
    assert entry.context["elapsed_seconds"] >= 1
    assert "count" not in entry.context
    assert "task_id" not in entry.context

"""A refused Admin input's durable entry (#1403, migration 0106).

The real web login writes ``admin_input_refused`` at INFO or WARNING, and
only naming the refused user; the database's safe context admits exactly
the closed values Python builds; the writer keeps each actor to ``CAP``
entries per level in a fixed window under its advisory lock; a write inside
an atomic block, or stopped by its own limits, leaves the process-log line
(and, for a limit, a ``refusal_log_write`` timeout line) and no row. Django's
``TestCase`` wraps each test in an atomic block, in which the writer
correctly writes nothing, so these are transactional tests. The last test
runs the file's DO block against the installed objects and against each
object's earlier body.
"""

import json
import logging
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship import admin_cli
from parishkit.stewardship.audit import refusals
from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.audit.refusal_context import clean
from parishkit.stewardship.audit.refusals import CAP, RefusalRecord, record_refusal
from parishkit.stewardship.audit.timeouts import _private_connection
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.observability import (
    Event,
    SafeJsonFormatter,
    correlation,
)

from .test_background_grants_postgresql import task_login

pytestmark = pytest.mark.django_db(transaction=True)
MIGRATIONS = (
    Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema/migrations"
)
FROZEN = (MIGRATIONS / "0106_admin_input_refusal_log.sql").read_text(encoding="utf-8")
CHECK = FROZEN[FROZEN.index("DO $check$") :]
# What the DO block says when one of its four objects is not installed.
REFUSED = "0106|operational_event_safe|safe_context"
CONTEXT = '{"route": "admin:home", "reason": "denied"}'


def insert(level, *, actor_id, event="admin_input_refused", schema="refusal"):
    """One raw insert in its own transaction, as the code's raw path does."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO stewardship_operational_log "
            "(id,correlation_id,actor_id,event,level,schema,context) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)",
            [
                uuid4(),
                uuid4(),
                actor_id,
                event,
                level,
                schema,
                CONTEXT if schema == "refusal" else '{"failure": "admin_command"}',
            ],
        )


def refused(actor_id, *, reason="stale_page", route="admin:home"):
    """A typical refusal record for ``actor_id``."""
    return RefusalRecord(
        reason=reason,
        actor_id=actor_id,
        route=route,
        method="POST",
        status=409,
        form="campaign",
        fields=("name",),
        mode="testing",
    )


def lines(caplog, event=Event.ADMIN_INPUT_REFUSED):
    """The formatted process-log lines of ``event``."""
    return [
        json.loads(SafeJsonFormatter().format(record))
        for record in caplog.records
        if record.msg is event
    ]


def entries(actor_id):
    """``actor_id``'s refusal entries, oldest first."""
    return list(
        OperationalLog.objects.filter(
            event="admin_input_refused", actor_id=actor_id
        ).order_by("created_at", "id")
    )


def test_only_web_writes_a_refusal_and_only_naming_its_actor():
    """INFO and WARNING from web with an actor; nothing else, and still no
    actor on any other direct entry."""
    actor = uuid4()
    with task_login(ServiceRole.WEB, exact=True):
        insert("INFO", actor_id=actor)
        insert("WARNING", actor_id=actor)
        for level, actor_id in (("INFO", None), ("WARNING", None), ("ERROR", actor)):
            with pytest.raises(DatabaseError, match="may not record"):
                insert(level, actor_id=actor_id)
        with pytest.raises(DatabaseError, match="may not record"):
            insert(
                "ERROR",
                actor_id=actor,
                event="admin_command_failed",
                schema="failure",
            )
        # Its old tuple still needs no actor.
        insert("ERROR", actor_id=None, event="admin_command_failed", schema="failure")
    with (
        task_login(ServiceRole.WORKER, exact=True),
        pytest.raises(DatabaseError, match="may not record"),
    ):
        insert("INFO", actor_id=actor)
    assert len(entries(actor)) == 2


@pytest.mark.parametrize(
    "record",
    [
        RefusalRecord(
            reason="unexpected_fields",
            route="admin:setup_logo",
            method="post",
            status=400,
            form="logo",
            fields=("logo", "slots-0-time"),
            codes=("duplicate",),
            unexpected_count=4,
            mode="production",
        ),
        RefusalRecord(reason="unclassified", command="export create", error=KeyError()),
        RefusalRecord(reason="csrf_failed", route="admin:home", status=403),
    ],
)
def test_the_database_admits_what_python_builds(record):
    """Every context the allow-list builds passes the SQL check unchanged."""
    context = record.context()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT public.stewardship_safe_context_v1('refusal', %s::jsonb)",
            [json.dumps(context)],
        )
        assert cursor.fetchone() == (True,)
        cursor.execute(
            "SELECT public.stewardship_safe_context_v1('refusal', %s::jsonb)",
            [json.dumps({**context, "capped": True})],
        )
        assert cursor.fetchone() == (True,)


@pytest.mark.parametrize(
    "context",
    [
        {"route": "admin:home"},
        {"reason": "denied"},
        {"route": "admin:home", "command": "whoami", "reason": "denied"},
        {"route": "admin:home", "reason": "denied", "method": "POST"},
        {"route": "admin:home", "reason": "denied", "status": 500},
        {"route": "admin:home", "reason": "denied", "exception_class": "x"},
        {"route": "admin:home", "reason": "denied", "unexpected_count": 1},
        {"route": "admin:home", "reason": "denied", "expected": "jane@example.org"},
        {"route": "admin:home", "reason": "denied", "fields": []},
        {"route": "admin:home", "reason": "denied", "fields": ["Jane Q"]},
        {"route": "admin:home", "reason": "denied", "codes": [1]},
        {"route": "admin:home", "reason": "denied", "mode": "live"},
        {"route": "admin:home", "reason": "denied", "capped": "yes"},
        {"route": "admin:home", "reason": "denied", "outcome": "denied"},
        {"command": "export create; drop", "reason": "usage"},
    ],
)
def test_the_database_refuses_anything_else(context):
    """Raw SQL cannot store a refusal Python would not build."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT public.stewardship_safe_context_v1('refusal', %s::jsonb)",
            [json.dumps(context)],
        )
        assert cursor.fetchone() == (False,)


def test_a_refusal_is_recorded_after_its_line(caplog):
    """One line, then one durable entry with the same closed context, the
    actor and the request's correlation, written by the web login."""
    actor, request = uuid4(), uuid4()
    with (
        task_login(ServiceRole.WEB, exact=True),
        caplog.at_level(logging.INFO),
        correlation(request),
    ):
        record_refusal(refused(actor))
    (line,) = lines(caplog)
    (entry,) = entries(actor)
    assert entry.correlation_id == request
    assert line["extra"]["correlation_id"] == str(request)
    assert (
        line["extra"]["refusal"]
        == entry.context
        == clean(
            {
                "route": "admin:home",
                "method": "post",
                "status": 409,
                "form": "campaign",
                "fields": ["name"],
                "reason": "stale_page",
                "mode": "testing",
            }
        )
    )
    assert line["extra"]["actor_id"] == str(actor)
    assert (entry.level, entry.schema) == ("INFO", "refusal")


def test_each_level_has_its_own_capped_budget_per_actor(caplog):
    """The CAP-th entry is kept and marked; later ones keep their line only.
    WARNING refusals and other actors have budgets of their own."""
    actor, other = uuid4(), uuid4()
    with task_login(ServiceRole.WEB, exact=True), caplog.at_level(logging.INFO):
        for _ in range(CAP + 2):
            record_refusal(refused(actor))
        record_refusal(refused(actor, reason="denied"))
        record_refusal(refused(other))
    kept = entries(actor)
    info = [entry for entry in kept if entry.level == "INFO"]
    assert len(info) == CAP
    assert [entry.context.get("capped") for entry in info] == [None] * (CAP - 1) + [
        True
    ]
    assert [entry.level for entry in kept if entry.level == "WARNING"] == ["WARNING"]
    assert "capped" not in next(e for e in kept if e.level == "WARNING").context
    assert len(entries(other)) == 1
    # Every refusal has its line, and the ones past the budget say so.
    marks = [line["extra"]["refusal"].get("capped") for line in lines(caplog)]
    assert len(marks) == CAP + 4
    assert marks[CAP - 1 : CAP + 2] == [True, True, True]


def test_the_window_is_fixed_ten_minutes_of_utc_database_time():
    """Entries from the previous window do not count against this one."""
    actor = uuid4()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT date_bin('10 minutes', now(), TIMESTAMPTZ '2000-01-01 00:00+00')"
        )
        (window,) = cursor.fetchone()
        # As the schema owner, whose rows keep the time they are given.
        for _ in range(CAP):
            cursor.execute(
                "INSERT INTO stewardship_operational_log "
                "(id,correlation_id,actor_id,event,level,schema,context,created_at) "
                "VALUES (gen_random_uuid(),gen_random_uuid(),%s,"
                "'admin_input_refused','INFO','refusal',%s::jsonb,%s)",
                [actor, CONTEXT, window - timedelta(microseconds=1)],
            )
    assert window.minute % 10 == 0 and window.second == 0
    with task_login(ServiceRole.WEB, exact=True):
        record_refusal(refused(actor))
    newest = entries(actor)[-1]
    assert newest.created_at >= window and "capped" not in newest.context


def assert_connection_clean():
    """After a stopped write: autocommit, no atomic block, no limits left."""
    assert connection.get_autocommit() and not connection.in_atomic_block
    with connection.cursor() as cursor:
        for setting in ("statement_timeout", "lock_timeout"):
            cursor.execute(f"SHOW {setting}")
            assert cursor.fetchone() == ("0",), setting


def test_with_autocommit_off_only_the_line_is_written(caplog):
    """Outside atomic() but in a transaction: the entry would be only a
    savepoint in it, so the line alone is written and no limit leaks."""
    actor = uuid4()
    with task_login(ServiceRole.WEB, exact=True), caplog.at_level(logging.INFO):
        connection.set_autocommit(False)
        try:
            record_refusal(refused(actor))
            with connection.cursor() as cursor:
                cursor.execute("SHOW statement_timeout")
                assert cursor.fetchone() == ("0",)
        finally:
            connection.rollback()
            connection.set_autocommit(True)
    assert len(lines(caplog)) == 1
    assert not entries(actor)


def test_inside_an_atomic_block_only_the_line_is_written(caplog):
    """A refusal raised in a transaction that may roll back keeps its line."""
    actor = uuid4()
    with (
        task_login(ServiceRole.WEB, exact=True),
        caplog.at_level(logging.INFO),
        transaction.atomic(),
    ):
        record_refusal(refused(actor))
    assert len(lines(caplog)) == 1
    assert not entries(actor)


def test_an_anonymous_refusal_has_its_line_only(caplog):
    """No actor: the process log only, so public traffic allocates no row."""
    with task_login(ServiceRole.WEB, exact=True), caplog.at_level(logging.INFO):
        record_refusal(refused(None, reason="csrf_failed"))
    (line,) = lines(caplog)
    assert line["level"] == "WARNING" and "actor_id" not in line["extra"]
    assert not OperationalLog.objects.filter(event="admin_input_refused").exists()


def test_a_lock_wait_past_its_limit_logs_the_timeout(caplog):
    """Another holder of the actor's lock stops the write at 1 second: the
    refusal's line, then a WARNING refusal_log_write timeout, and no row."""
    actor = uuid4()
    holder = _private_connection(None)
    try:
        holder.set_autocommit(False)
        with holder.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s, hashtext(%s))",
                [refusals.LOCK_NAMESPACE, f"{actor}:INFO"],
            )
        with task_login(ServiceRole.WEB, exact=True), caplog.at_level(logging.INFO):
            record_refusal(refused(actor))
            assert_connection_clean()
    finally:
        holder.rollback()
        holder.close()
    (line,) = lines(caplog)
    # Stopped at the lock, before the count: the line carries no cap mark.
    assert "capped" not in line["extra"]["refusal"]
    (timeout,) = lines(caplog, Event.TASK_TIMED_OUT)
    assert timeout["level"] == "WARNING"
    assert timeout["extra"]["timeout"] == "refusal_log_write"
    assert timeout["extra"]["limit_seconds"] == refusals.LOCK_SECONDS
    assert timeout["extra"]["elapsed_seconds"] >= 1
    assert not entries(actor)
    # The timeout stays in the process log only.
    assert not OperationalLog.objects.filter(event="task_timed_out").exists()


def test_a_slow_statement_past_its_limit_logs_the_timeout(caplog, monkeypatch):
    """A count that runs past 2 seconds is stopped by its own limit."""
    monkeypatch.setattr(
        refusals,
        "_COUNT",
        "SELECT count(*) FROM (SELECT %s::text,%s::uuid,%s::text,%s::interval) p,"
        " pg_sleep(3)",
    )
    actor = uuid4()
    with task_login(ServiceRole.WEB, exact=True), caplog.at_level(logging.INFO):
        record_refusal(refused(actor))
        assert_connection_clean()
    (line,) = lines(caplog)
    assert "capped" not in line["extra"]["refusal"]
    (timeout,) = lines(caplog, Event.TASK_TIMED_OUT)
    assert timeout["extra"]["timeout"] == "refusal_log_write"
    assert timeout["extra"]["limit_seconds"] == refusals.STATEMENT_SECONDS
    assert not entries(actor)


def test_any_other_failure_never_raises(caplog, monkeypatch):
    """A refused insert leaves the line and a WARNING failure line."""
    monkeypatch.setattr(refusals, "_INSERT", "SELECT %s,%s,%s,%s,%s,%s,%s, 1/0")
    actor = uuid4()
    with task_login(ServiceRole.WEB, exact=True), caplog.at_level(logging.INFO):
        record_refusal(refused(actor))
    refusal, failure = lines(caplog)
    assert "refusal" in refusal["extra"]
    assert failure["level"] == "WARNING" and "failure_kind" in failure["extra"]
    assert not entries(actor)


def test_the_cap_count_reads_the_partial_index():
    """The count uses (actor_id, created_at) of the refusal rows.

    Other actors' refusals are seeded and analysed first: on an empty table
    every index costs the same, and the System logs search's pattern index
    on ``event`` (#765) would serve the event condition just as cheaply.
    """
    with transaction.atomic(), connection.cursor() as cursor:
        # The writer's trigger is skipped (replica role, superuser test
        # database only); the context check still applies.
        cursor.execute("SET LOCAL session_replication_role = replica")
        cursor.execute(
            "INSERT INTO stewardship_operational_log "
            "(id,correlation_id,actor_id,event,level,schema,context) "
            "SELECT gen_random_uuid(),gen_random_uuid(),gen_random_uuid(),"
            "'admin_input_refused','INFO','refusal',%s::jsonb "
            "FROM generate_series(1,2000)",
            [json.dumps(refused(uuid4()).context())],
        )
        cursor.execute("RESET session_replication_role")
        cursor.execute("ANALYZE stewardship_operational_log")
        cursor.execute("SET LOCAL enable_seqscan = off")
        cursor.execute(
            "EXPLAIN " + refusals._COUNT,
            ["admin_input_refused", uuid4(), "INFO", refusals.WINDOW],
        )
        plan = "\n".join(row[0] for row in cursor.fetchall())
        transaction.set_rollback(True)
    assert "operational_refusal_actor_time" in plan


def _body(text, name, end):
    """``name``'s CREATE OR REPLACE statement in a frozen file's ``text``."""
    start = text.index(f"CREATE OR REPLACE FUNCTION public.{name}(")
    return text[start : text.index(end, start) + len(end)]


def command_refused(actor_id):
    """A refused pk-admin command's record for ``actor_id`` (#1403, PR 6)."""
    return RefusalRecord(
        reason="unclassified",
        actor_id=actor_id,
        command="schedule edit",
        fields=("window-start",),
        codes=("invalid", "required"),
        mode="testing",
        error=ValueError,
    )


def test_a_refused_command_is_recorded_on_its_private_connection(caplog, monkeypatch):
    """The command line's writer closes the command's connections, then the
    web login records the entry on a private connection of its own, with
    the invocation's correlation, and closes that connection again."""
    from parishkit.stewardship.audit import timeouts

    made = []

    def private(settings):
        """The real private connection, kept so the test can check it."""
        made.append(_private_connection(settings))
        return made[-1]

    monkeypatch.setattr(timeouts, "_private_connection", private)
    actor, invocation = uuid4(), uuid4()
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        caplog.at_level(logging.INFO),
        correlation(invocation),
    ):
        admin_cli.write_refusal(command_refused(actor))
    # One private connection, opened and closed again.
    (db,) = made
    assert db.connection is None
    (line,) = lines(caplog)
    (entry,) = entries(actor)
    assert entry.correlation_id == invocation
    assert (entry.level, entry.schema) == ("INFO", "refusal")
    assert (
        line["extra"]["refusal"]
        == entry.context
        == {
            "command": "schedule edit",
            "fields": ["window-start"],
            "codes": ["invalid", "required"],
            "reason": "unclassified",
            "exception_class": "value_error",
            "mode": "testing",
        }
    )


def test_a_private_connection_lock_wait_rolls_back(caplog):
    """Stopped at its lock limit on the private connection, as the web
    login: the line, then the refusal_log_write timeout, no row, and the
    transaction rolled back, leaving the connection clean."""
    actor = uuid4()
    holder = _private_connection(None)
    db = None
    try:
        holder.set_autocommit(False)
        with holder.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s, hashtext(%s))",
                [refusals.LOCK_NAMESPACE, f"{actor}:INFO"],
            )
        with (
            task_login(ServiceRole.WEB, exact=True, reconnect=True),
            caplog.at_level(logging.INFO),
        ):
            db = _private_connection(None)
            record_refusal(command_refused(actor), db=db)
            # Rolled back, so the private connection is usable and clean.
            assert not db.in_atomic_block and db.get_autocommit() is False
            with db.cursor() as cursor:
                cursor.execute("SELECT current_user")
                assert cursor.fetchone() == ("pk_stewardship_web",)
                cursor.execute("SHOW lock_timeout")
                assert cursor.fetchone() == ("0",)
            db.close()
            assert db.connection is None
    finally:
        if db is not None:
            db.close()
        holder.rollback()
        holder.close()
    (line,) = lines(caplog)
    assert line["extra"]["refusal"]["command"] == "schedule edit"
    (timeout,) = lines(caplog, Event.TASK_TIMED_OUT)
    assert timeout["extra"]["limit_seconds"] == refusals.LOCK_SECONDS
    assert not entries(actor)


def test_the_migration_check_passes_installed_and_fails_otherwise():
    """The DO block accepts the installed objects and refuses the event
    check and the writer as 0089 left them, the safe context as 0104 left
    it, and a missing index."""
    with connection.cursor() as cursor:
        cursor.execute(CHECK)
    earlier = (MIGRATIONS / "0089_retry_refusal_log.sql").read_text(encoding="utf-8")
    last = "'retry_refusal_unrecorded'));"
    start = earlier.index("ALTER TABLE public.stewardship_operational_log DROP")
    events = earlier[start : earlier.index(last) + len(last)]
    context = (MIGRATIONS / "0104_setup_sign_in_hour.sql").read_text(encoding="utf-8")
    for statement in (
        # The refusal row the check's own samples never insert, so the old
        # event list validates.
        events,
        _body(context, "stewardship_safe_context_v1", "$_$;"),
        _body(earlier, "stewardship_operational_log_writer_v1", "\nEND;\n$$;"),
        "DROP INDEX public.operational_refusal_actor_time",
    ):
        with (
            pytest.raises(DatabaseError, match=REFUSED),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement)
            cursor.execute(CHECK)
    # Running the whole file again, as the users swap test does, is harmless.
    # Rolling it back keeps the installed (later) safe context, which 0109
    # (#1398) widens again, for the tests that follow in this transactional
    # database.
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(FROZEN)
        transaction.set_rollback(True)

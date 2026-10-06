"""When a process writes its service status record, and that it never fails it.

The record's SQL (guards, grants and the writes themselves) is exercised
against PostgreSQL in database/test_system_health_records_postgresql.py; these
tests cover the reporter's own rules with the write replaced.
"""

import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from parishkit.stewardship import runtime_auth_health as health
from parishkit.stewardship import runtime_process, service_status
from parishkit.stewardship.observability import Event
from parishkit.stewardship.service_status import ServiceStatusReporter


@pytest.fixture
def clock(monkeypatch):
    """A controllable monotonic clock for the reporter."""
    now = [1000.0]
    monkeypatch.setattr(service_status, "monotonic", lambda: now[0])
    return now


def database(monkeypatch, *, atomic=False, open_connection=True):
    """Replace Django's connection with one in a chosen state."""
    fake = SimpleNamespace(
        in_atomic_block=atomic,
        connection=object() if open_connection else None,
        alias="default",
    )
    monkeypatch.setattr("django.db.connection", fake)
    return fake


def test_reports_when_due_and_then_once_a_minute(monkeypatch, clock):
    """A write is skipped until REPORT_SECONDS pass since the last one."""
    database(monkeypatch)
    reporter = ServiceStatusReporter("worker")
    write = Mock()
    monkeypatch.setattr(reporter, "_write", write)
    assert reporter.report()
    assert not reporter.report()
    clock[0] += service_status.REPORT_SECONDS - 1
    assert not reporter.report()
    clock[0] += 1
    assert reporter.report()
    assert write.call_count == 2


def test_never_opens_a_connection_or_joins_a_transaction_unasked(monkeypatch, clock):
    """Busy threads skip, stay due, and write on the next suitable call."""
    reporter = ServiceStatusReporter("worker", process="source")
    write = Mock()
    monkeypatch.setattr(reporter, "_write", write)
    database(monkeypatch, open_connection=False)
    assert not reporter.report()
    database(monkeypatch, atomic=True)
    assert not reporter.report(connect=True)
    write.assert_not_called()
    database(monkeypatch, open_connection=False)
    assert reporter.report(connect=True)
    write.assert_called_once()


def test_a_failed_write_is_logged_once_and_never_raised(monkeypatch, clock, caplog):
    """The record is display only: the process carries on, quietly retrying."""
    database(monkeypatch)
    reporter = ServiceStatusReporter("scheduler")
    monkeypatch.setattr(
        reporter, "_write", Mock(side_effect=RuntimeError("private-detail"))
    )
    with caplog.at_level(logging.WARNING, logger="parishkit.stewardship"):
        assert not reporter.report()
        clock[0] += service_status.REPORT_SECONDS
        assert not reporter.report()
    lines = [r for r in caplog.records if r.msg == "service_status_failed"]
    assert len(lines) == 1 and "private-detail" not in caplog.text
    monkeypatch.setattr(reporter, "_write", Mock())
    clock[0] += service_status.REPORT_SECONDS
    assert reporter.report() and not reporter.failing


def test_a_concurrent_call_skips_instead_of_waiting(monkeypatch, clock):
    """Heartbeats on other threads never block behind a write in progress."""
    database(monkeypatch)
    reporter = ServiceStatusReporter("mail-dispatch", sender=lambda: ("running", None))
    write = Mock()
    monkeypatch.setattr(reporter, "_write", write)
    with reporter.lock:
        assert not reporter.report()
    write.assert_not_called()


def test_each_process_has_its_own_record_identity():
    """A restart, or the container's second process, is a separate row."""
    first, second = ServiceStatusReporter("web"), ServiceStatusReporter("web")
    assert first.id != second.id


def test_web_observer_reports_at_start_and_after_each_pass(monkeypatch):
    """The web worker's observer thread writes on its own, closed connection."""
    status = Mock()
    closes = Mock()
    monkeypatch.setattr("django.db.connections.close_all", closes)
    monkeypatch.setattr(health, "observe_once", Mock())
    monkeypatch.setattr(health, "randbelow", lambda bound: 0)
    value = health.PeriodicAuthenticationHealth(
        object(),
        check=Mock(),
        active=Mock(return_value=True),
        retire=Mock(),
        status=status,
    )
    monkeypatch.setattr(value.stop, "wait", Mock(side_effect=[False, False, True]))
    value.run()
    assert status.report.call_args_list == [((), {"connect": True})] * 2
    assert closes.call_count == 2


def test_web_workers_report_as_web(monkeypatch, settings):
    """Gunicorn's worker hook gives each worker's observer a web reporter."""
    settings.STEWARDSHIP_AUTH_RUNTIME = SimpleNamespace(limiter=object())
    settings.STEWARDSHIP_WEB_LEASE = Mock()
    created = []
    monkeypatch.setattr(runtime_process, "publish_worker_receipts", Mock())
    monkeypatch.setattr(
        health,
        "PeriodicAuthenticationHealth",
        lambda limiter, **kwargs: created.append(kwargs) or Mock(),
    )
    worker = SimpleNamespace(pid=0, alive=True, handle_exit=Mock())
    runtime_process.admitted_worker_started(worker)
    status = created[0]["status"]
    assert (status.service, status.process, status.target) == ("web", "main", None)


class SqlTimeout(Exception):
    """A psycopg-shaped error PostgreSQL raises at a SQL time limit."""

    def __init__(self, sqlstate, message=""):
        super().__init__("private-detail")
        self.sqlstate = sqlstate
        self.diag = SimpleNamespace(message_primary=message)


STATEMENT = SqlTimeout("57014", "canceling statement due to statement timeout")
LOCK = SqlTimeout("55P03", "lock timeout")


def stopped_writes(monkeypatch, clock, reporter, error):
    """Make each write run 2.2 seconds, then stop at its limit."""

    def slow(connection):
        clock[0] += 2.2
        raise error

    monkeypatch.setattr(reporter, "_write", slow)


@pytest.mark.parametrize(
    "error,what,limit",
    [(STATEMENT, "statement_timeout", 2), (LOCK, "lock_timeout", 1)],
)
def test_a_timed_out_write_is_a_durable_timeout_naming_no_task(
    monkeypatch, clock, caplog, error, what, limit
):
    """#287: the real record_timeout, inside a bound task, attributes no task.

    Every timed-out write gets its process-log line and its durable entry;
    REPORT_SECONDS alone spaces them, so nothing is counted or left out.
    """
    import json
    from uuid import uuid4

    from parishkit.stewardship.audit import timeouts
    from parishkit.stewardship.observability import SafeJsonFormatter, task_scope

    database(monkeypatch)
    durable = Mock()
    monkeypatch.setattr(timeouts, "_write", durable)
    reporter = ServiceStatusReporter("mail-dispatch", sender=lambda: ("running", None))
    stopped_writes(monkeypatch, clock, reporter, error)
    with (
        caplog.at_level(logging.WARNING, logger="parishkit.stewardship"),
        task_scope(uuid4()),
    ):
        assert not reporter.report()
        clock[0] += service_status.REPORT_SECONDS
        assert not reporter.report()
    assert durable.call_count == 2
    lines = [
        json.loads(SafeJsonFormatter().format(record))
        for record in caplog.records
        if record.msg == "task_timed_out"
    ]
    # The process log first, for each of them, naming no task.
    assert [line["extra"]["timeout"] for line in lines] == [what, what]
    assert all("task_id" not in line["extra"] for line in lines)
    first, second = (call.kwargs for call in durable.call_args_list)
    for entry in (first, second):
        assert entry["task_id"] is None
        assert (entry["what"], entry["limit_seconds"], entry["elapsed_seconds"]) == (
            what,
            limit,
            3,
        )
    assert first["count"] is None and second["count"] is None
    assert durable.call_args_list[0].args[:2] == (Event.TASK_TIMED_OUT, "WARNING")
    assert not reporter.failing


@pytest.mark.parametrize("service", ["config-installer", "credential-installer"])
def test_an_installer_s_timed_out_write_goes_to_the_process_log_only(
    monkeypatch, clock, caplog, service
):
    """Installers have no operational-log grant: the listed exception."""
    import json

    from parishkit.stewardship.audit import timeouts
    from parishkit.stewardship.observability import SafeJsonFormatter

    database(monkeypatch)
    durable = Mock()
    monkeypatch.setattr(timeouts, "_write", durable)
    reporter = ServiceStatusReporter(
        service, target="slack" if service == "credential-installer" else None
    )
    stopped_writes(monkeypatch, clock, reporter, STATEMENT)
    with caplog.at_level(logging.WARNING, logger="parishkit.stewardship"):
        assert not reporter.report()
    durable.assert_not_called()
    (line,) = [
        json.loads(SafeJsonFormatter().format(record))
        for record in caplog.records
        if record.msg == "task_timed_out"
    ]
    assert line["extra"]["timeout"] == "statement_timeout"
    assert (line["extra"]["limit_seconds"], line["extra"]["elapsed_seconds"]) == (2, 3)
    assert "private-detail" not in caplog.text

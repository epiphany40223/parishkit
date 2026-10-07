"""Operational PostgreSQL cannot silently negotiate an unverified remote link."""

from dataclasses import replace
from unittest.mock import MagicMock, Mock

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import database_provisioning, runtime_database

from .test_runtime_topology import configuration_at


@pytest.mark.parametrize("host,port", [("external.example", 5432), ("postgres", 55432)])
def test_external_sql_rejected_before_reading_secrets_or_connecting(
    tmp_path, monkeypatch, host, port
):
    """Both runtime and offline provisioning enforce the renderer's local profile."""
    configuration = configuration_at(tmp_path)
    configuration = replace(
        configuration, postgres=replace(configuration.postgres, host=host, port=port)
    )
    connect = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(database_provisioning.psycopg, "connect", connect)
    with pytest.raises(ConfigError, match="private Compose"):
        runtime_database.database_settings(configuration)
    with pytest.raises(ConfigError, match="private Compose"):
        database_provisioning._connection(configuration, "operator", b"synthetic")
    connect.assert_not_called()


@pytest.fixture(autouse=True)
def _no_startup_budget(monkeypatch):
    """Each test starts outside any startup window, whatever earlier ones set."""
    monkeypatch.setattr(runtime_database, "_startup_started", None)
    monkeypatch.setattr(runtime_database, "_wait_logged", False)
    monkeypatch.setattr(runtime_database, "_waiting", False)


SETTINGS = {
    "HOST": "postgres",
    "PORT": 5432,
    "NAME": "stewardship",
    "USER": "pk_stewardship_scheduler",
    "PASSWORD": "synthetic-password",
    "OPTIONS": {"connect_timeout": 5, "sslmode": "disable"},
}


class Clock:
    """A fake monotonic clock that only moves when the code sleeps."""

    def __init__(self, monkeypatch):
        self.now = 1000.0
        self.sleeps = []
        monkeypatch.setattr(runtime_database.time, "monotonic", lambda: self.now)
        monkeypatch.setattr(runtime_database.time, "sleep", self.sleep)

    def sleep(self, seconds):
        """Record the pause and advance the clock by it."""
        self.sleeps.append(seconds)
        self.now += seconds


PREFIX = 'connection failed: connection to server at "postgres", port 5432 failed: '


def _failure(reply):
    """A psycopg connection error as libpq raises it: no SQLSTATE, only text."""
    import psycopg

    return psycopg.OperationalError(PREFIX + reply)


def _connections(monkeypatch, outcomes):
    """Replace psycopg.connect; each call raises or connects per ``outcomes``."""
    import psycopg

    calls = []

    def connect(**options):
        options["at"] = runtime_database.time.monotonic()
        calls.append(options)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(psycopg, "connect", connect)
    monkeypatch.setattr(runtime_database, "database_settings", lambda value: SETTINGS)
    return calls


def _connected():
    """A connection stand-in that answers SELECT 1 as a context manager."""
    database = MagicMock()
    database.__enter__.return_value = database
    return database


@pytest.mark.parametrize(
    "reply",
    [
        "Connection refused",
        "server closed the connection unexpectedly",
        "timeout expired",
        "FATAL:  the database system is starting up",
        "FATAL:  the database system is shutting down",
        "FATAL:  the database system is not yet accepting connections",
        "FATAL:  the database system is not accepting connections",
        "FATAL:  the database system is in recovery mode",
        "FATAL:  sorry, too many clients already",
        "FATAL:  remaining connection slots are reserved for roles with the "
        "SUPERUSER attribute",
        'FATAL:  too many connections for role "pk_stewardship_scheduler"',
        "FATAL:  terminating connection due to administrator command",
        "FATAL:  terminating connection because of crash of another server process",
        "No route to host",
        "Network is unreachable",
        "Connection timed out",
        "Connection reset by peer",
    ],
)
def test_a_server_that_is_not_ready_yet_is_waited_for(reply):
    """No answer, starting up or out of slots means "not yet" (#453)."""
    assert runtime_database.database_not_ready(_failure(reply))


@pytest.mark.parametrize(
    "detail",
    [
        "Name or service not known",
        "Temporary failure in name resolution",
    ],
)
def test_an_unresolvable_compose_host_is_waited_for(detail):
    """The database container may not have joined the network yet."""
    import psycopg

    error = psycopg.OperationalError(
        'connection failed: could not translate host name "postgres" to address: '
        + detail
    )
    assert runtime_database.database_not_ready(error)


def test_a_psycopg_connection_timeout_is_waited_for():
    """psycopg's own timeout class needs no text."""
    from psycopg import errors

    assert runtime_database.database_not_ready(errors.ConnectionTimeout("x"))


@pytest.mark.parametrize(
    "reply",
    [
        'FATAL:  password authentication failed for user "pk_stewardship_web"',
        'FATAL:  database "stewardship" does not exist',
        'FATAL:  role "pk_stewardship_web" does not exist',
        'FATAL:  no pg_hba.conf entry for host "172.29.240.9", user "x"',
        # Unknown text, such as another language, refuses rather than waits.
        "FATAL:  la base de données « stewardship » n'existe pas",
        "an unrecognized client failure",
    ],
)
def test_a_server_refusal_is_never_waited_out(reply):
    """A wrong password, role, database or host rule, or unknown text, refuses."""
    assert not runtime_database.database_not_ready(_failure(reply))


@pytest.mark.parametrize(
    ("sqlstate", "waits"),
    [
        ("57P01", True),
        ("57P02", True),
        ("57P03", True),
        ("53300", True),
        ("08000", True),
        ("08003", True),
        ("08006", True),
        ("53000", False),
        ("53100", False),
        ("53200", False),
        ("08004", False),
        ("08P01", False),
        ("42501", False),
    ],
)
def test_an_error_with_a_sqlstate_is_judged_by_it(sqlstate, waits):
    """A failed SELECT 1 carries its SQLSTATE; only the closed list waits.

    57P01, 57P02, 57P03, 53300, 08000, 08003 and 08006 wait; the rest of
    class 53 (disk full, out of memory), 08004, 08P01 and others refuse.
    """
    from psycopg import errors

    error = errors.lookup(sqlstate)("FATAL:  private server text")
    assert runtime_database.database_not_ready(error) is waits


def test_a_non_operational_client_error_is_never_waited_out():
    """A psycopg programming error, or anything else, is a refusal too."""
    import psycopg

    assert not runtime_database.database_not_ready(psycopg.ProgrammingError("x"))
    assert not runtime_database.database_not_ready(ValueError("x"))


def test_startup_waits_for_a_database_still_starting(tmp_path, monkeypatch, caplog):
    """The service's own login connects once the server answers; nothing at ERROR."""
    clock = Clock(monkeypatch)
    database = _connected()
    calls = _connections(
        monkeypatch,
        [
            _failure("Connection refused"),
            _failure("FATAL:  the database system is starting up"),
            _failure("FATAL:  sorry, too many clients already"),
            database,
        ],
    )
    with caplog.at_level("DEBUG"):
        runtime_database.await_database(configuration_at(tmp_path))
    assert clock.sleeps == [0.5, 1.0, 2.0]
    assert len(calls) == 4
    assert calls[0].pop("at") == 1000.0
    assert calls[0] == {
        "host": "postgres",
        "port": 5432,
        "dbname": "stewardship",
        "user": "pk_stewardship_scheduler",
        "password": "synthetic-password",
        "sslmode": "disable",
        "connect_timeout": 5,
        "autocommit": True,
    }
    database.execute.assert_called_once_with("SELECT 1")
    assert not [record for record in caplog.records if record.levelname == "ERROR"]
    assert "Connection refused" not in caplog.text


@pytest.mark.parametrize(
    "reply",
    [
        'FATAL:  password authentication failed for user "private-user"',
        'FATAL:  database "private-name" does not exist',
    ],
)
def test_a_refused_login_is_refused_at_once(tmp_path, monkeypatch, caplog, reply):
    """A wrong password or missing database is never retried or reported as a wait."""
    import json

    from django.db.utils import OperationalError

    from parishkit.stewardship.observability import SafeJsonFormatter, emit_failure

    clock = Clock(monkeypatch)
    calls = _connections(monkeypatch, [_failure(reply)])
    with pytest.raises(OperationalError) as refused:
        runtime_database.await_database(configuration_at(tmp_path))
    assert clock.sleeps == []
    assert len(calls) == 1
    assert refused.value.__cause__ is None
    assert "private" not in str(refused.value)
    assert "task_timed_out" not in caplog.text
    # The caller's startup_rejected then names the database category.
    with caplog.at_level("ERROR", logger="parishkit.stewardship"):
        emit_failure(refused.value)
    events = [json.loads(SafeJsonFormatter().format(r)) for r in caplog.records]
    assert [event["extra"].get("failure_kind") for event in events] == [
        "database_unavailable"
    ]


def test_the_wait_is_bounded_and_logs_its_timeout(tmp_path, monkeypatch, caplog):
    """A database that never answers is refused after the limit, timeout logged."""
    import json

    from django.db.utils import OperationalError

    from parishkit.stewardship.observability import SafeJsonFormatter

    clock = Clock(monkeypatch)
    calls = _connections(
        monkeypatch, [_failure("Connection refused") for _ in range(100)]
    )
    with (
        caplog.at_level("ERROR", logger="parishkit.stewardship"),
        pytest.raises(OperationalError),
    ):
        runtime_database.await_database(configuration_at(tmp_path))
    # 0.5, 1, 2, 4, then 5-second pauses, never past the 60-second limit.
    assert clock.sleeps[:5] == [0.5, 1.0, 2.0, 4.0, 5]
    assert set(clock.sleeps[4:]) == {5}
    assert sum(clock.sleeps) <= runtime_database.STARTUP_DATABASE_WAIT_SECONDS
    assert sum(clock.sleeps) + 5 > runtime_database.STARTUP_DATABASE_WAIT_SECONDS
    assert len(calls) == len(clock.sleeps) + 1
    events = [json.loads(SafeJsonFormatter().format(r)) for r in caplog.records]
    assert [event["message"] for event in events] == ["task_timed_out"]
    [event] = events
    assert event["level"] == "ERROR"
    assert event["extra"]["timeout"] == "startup_database_wait"
    assert event["extra"]["limit_seconds"] == 60
    assert event["extra"]["elapsed_seconds"] == round(sum(clock.sleeps))


def test_an_external_database_is_refused_before_any_connection(tmp_path, monkeypatch):
    """The wait reuses database_settings, so its private-host rule applies first."""
    configuration = configuration_at(tmp_path)
    configuration = replace(
        configuration,
        postgres=replace(configuration.postgres, host="external.example"),
    )
    connect = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr("psycopg.connect", connect)
    with pytest.raises(ConfigError, match="private Compose"):
        runtime_database.await_database(configuration)
    connect.assert_not_called()


def test_each_attempt_stops_near_the_budget(tmp_path, monkeypatch):
    """A long configured connect_timeout is capped at what is left (minimum 2)."""
    import math

    clock = Clock(monkeypatch)
    calls = _connections(
        monkeypatch, [_failure("Connection refused") for _ in range(100)]
    )
    settings = SETTINGS | {"OPTIONS": {"connect_timeout": 60, "sslmode": "disable"}}
    monkeypatch.setattr(runtime_database, "database_settings", lambda value: settings)
    with pytest.raises(Exception, match="in time"):
        runtime_database.await_database(configuration_at(tmp_path))
    assert calls[0]["connect_timeout"] == 60
    for call in calls:
        left = 60 - (call["at"] - 1000.0)
        assert call["connect_timeout"] == min(60, max(2, math.ceil(left)))
    assert calls[-1]["connect_timeout"] < 10
    assert clock.sleeps


def test_debug_logging_records_each_failure(tmp_path, monkeypatch, caplog):
    """With debug logging on, what failed is kept on the debug logger only."""
    monkeypatch.setenv("PARISHKIT_DEBUG_LOGGING", "1")
    Clock(monkeypatch)
    _connections(monkeypatch, [_failure("Connection refused"), _connected()])
    with caplog.at_level("DEBUG", logger="parishkit.stewardship.debug"):
        runtime_database.await_database(configuration_at(tmp_path))
    [record] = [r for r in caplog.records if r.name == "parishkit.stewardship.debug"]
    assert record.levelname == "DEBUG"
    assert "Connection refused" in str(record.exc_info[1])


def _admission_failure(reply):
    """A Django OperationalError over a psycopg error, as a failed query raises."""
    from django.db.utils import OperationalError

    error = OperationalError("private")
    error.__cause__ = _failure(reply)
    return error


def _steps(outcomes):
    """An admission step that raises or returns per ``outcomes``, counting calls."""
    calls = []

    def step():
        calls.append(True)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return step, calls


def _info_lines(caplog):
    """The reviewed stewardship lines, formatted as the process log writes them."""
    import json

    from parishkit.stewardship.observability import SafeJsonFormatter

    return [
        json.loads(SafeJsonFormatter().format(record))
        for record in caplog.records
        if record.name == "parishkit.stewardship"
    ]


def test_a_wait_logs_once_when_it_begins_and_when_it_ends(
    tmp_path, monkeypatch, caplog
):
    """One INFO line when the first pause begins, one when the database answers.

    Three retries still give a single startup_waiting line (#541), so a
    reboot where every service waits is not noisy; each carries what waited,
    its limit and the seconds spent.
    """
    clock = Clock(monkeypatch)
    _connections(
        monkeypatch,
        [
            _failure("Connection refused"),
            _failure("FATAL:  the database system is starting up"),
            _failure("FATAL:  sorry, too many clients already"),
            _connected(),
        ],
    )
    with caplog.at_level("INFO", logger="parishkit.stewardship"):
        runtime_database.await_database(configuration_at(tmp_path))
    lines = _info_lines(caplog)
    assert [(line["message"], line["level"]) for line in lines] == [
        ("startup_waiting", "INFO"),
        ("startup_wait_ended", "INFO"),
    ]
    for line in lines:
        assert line["extra"]["timeout"] == "startup_database_wait"
        assert line["extra"]["limit_seconds"] == 60
    assert lines[0]["extra"]["elapsed_seconds"] == 0
    assert lines[1]["extra"]["elapsed_seconds"] == round(sum(clock.sleeps))


def test_a_database_that_answers_at_once_logs_no_wait(tmp_path, monkeypatch, caplog):
    """No pause, no waiting lines."""
    Clock(monkeypatch)
    _connections(monkeypatch, [_connected()])
    with caplog.at_level("INFO", logger="parishkit.stewardship"):
        runtime_database.await_database(configuration_at(tmp_path))
    assert _info_lines(caplog) == []


def test_admission_retries_after_a_wait_do_not_log_waiting_again(
    tmp_path, monkeypatch, caplog
):
    """startup_waiting is once per process; each wait that ends still says so."""
    clock = Clock(monkeypatch)
    _connections(monkeypatch, [_failure("Connection refused"), _connected()])
    monkeypatch.setattr("django.db.connection.close", lambda: None)
    step, _ = _steps([_admission_failure("timeout expired"), "admitted"])
    with caplog.at_level("INFO", logger="parishkit.stewardship"):
        runtime_database.await_database(configuration_at(tmp_path))
        assert runtime_database.during_startup(step) == "admitted"
    assert [line["message"] for line in _info_lines(caplog)] == [
        "startup_waiting",
        "startup_wait_ended",
        "startup_wait_ended",
    ]
    assert clock.sleeps == [0.5, 0.5]


def test_a_wait_that_runs_out_logs_waiting_then_the_timeout(
    tmp_path, monkeypatch, caplog
):
    """A database that never answers gives no wait-ended line."""
    from django.db.utils import OperationalError

    Clock(monkeypatch)
    _connections(monkeypatch, [_failure("Connection refused") for _ in range(100)])
    with (
        caplog.at_level("INFO", logger="parishkit.stewardship"),
        pytest.raises(OperationalError),
    ):
        runtime_database.await_database(configuration_at(tmp_path))
    assert [line["message"] for line in _info_lines(caplog)] == [
        "startup_waiting",
        "task_timed_out",
    ]


def test_admission_is_not_retried_outside_a_startup_wait(monkeypatch):
    """Operator commands and diagnostics never waited, so they never retry."""
    from django.db.utils import OperationalError

    clock = Clock(monkeypatch)
    step, calls = _steps([_admission_failure("Connection refused")])
    with pytest.raises(OperationalError):
        runtime_database.during_startup(step)
    assert len(calls) == 1
    assert clock.sleeps == []


def test_admission_is_retried_while_the_database_is_not_ready(monkeypatch):
    """Inside the startup budget, a "not yet" query failure runs admission again."""
    clock = Clock(monkeypatch)
    monkeypatch.setattr(runtime_database, "_startup_started", clock.now)
    closes = []
    monkeypatch.setattr("django.db.connection.close", lambda: closes.append(True))
    step, calls = _steps(
        [
            _admission_failure("server closed the connection unexpectedly"),
            _admission_failure("FATAL:  sorry, too many clients already"),
            "admitted",
        ]
    )
    assert runtime_database.during_startup(step) == "admitted"
    assert len(calls) == 3
    assert clock.sleeps == [0.5, 1.0]
    assert len(closes) == 2


@pytest.mark.parametrize(
    "error",
    [
        "refusal",
        "integrity",
        "config",
    ],
)
def test_an_admission_refusal_is_never_retried(monkeypatch, error):
    """A refusal, a non-operational database error or a ConfigError raises at once."""
    from django.db.utils import IntegrityError

    clock = Clock(monkeypatch)
    monkeypatch.setattr(runtime_database, "_startup_started", clock.now)
    failure = {
        "refusal": _admission_failure('FATAL:  role "x" does not exist'),
        "integrity": IntegrityError("private"),
        "config": ConfigError("Runtime database column grants are excessive."),
    }[error]
    step, calls = _steps([failure])
    with pytest.raises(type(failure)):
        runtime_database.during_startup(step)
    assert len(calls) == 1
    assert clock.sleeps == []


def test_admission_retries_share_the_bounded_budget(monkeypatch, caplog):
    """Admission stops at the same limit as the connection wait, timeout logged."""
    import json

    from django.db.utils import OperationalError

    from parishkit.stewardship.observability import SafeJsonFormatter

    clock = Clock(monkeypatch)
    # The connection wait already spent 50 of the 60 seconds.
    monkeypatch.setattr(runtime_database, "_startup_started", clock.now - 50)
    monkeypatch.setattr("django.db.connection.close", lambda: None)
    step, calls = _steps([_admission_failure("timeout expired") for _ in range(50)])
    with (
        caplog.at_level("ERROR", logger="parishkit.stewardship"),
        pytest.raises(OperationalError),
    ):
        runtime_database.during_startup(step)
    assert 50 + sum(clock.sleeps) <= 60
    events = [json.loads(SafeJsonFormatter().format(r)) for r in caplog.records]
    assert [(e["message"], e["extra"]["timeout"]) for e in events] == [
        ("task_timed_out", "startup_database_wait")
    ]


def test_admission_long_after_startup_refuses_without_a_timeout_line(
    monkeypatch, caplog
):
    """A web worker re-forked hours later is not "stopped by the startup limit"."""
    from django.db.utils import OperationalError

    clock = Clock(monkeypatch)
    monkeypatch.setattr(runtime_database, "_startup_started", clock.now - 3600)
    monkeypatch.setattr("django.db.connection.close", lambda: None)
    step, calls = _steps([_admission_failure("Connection refused")])
    with (
        caplog.at_level("ERROR", logger="parishkit.stewardship"),
        pytest.raises(OperationalError),
    ):
        runtime_database.during_startup(step)
    assert len(calls) == 1
    assert clock.sleeps == []
    assert "task_timed_out" not in caplog.text


def test_runtime_admission_goes_through_the_startup_retry(monkeypatch):
    """admit_runtime_database is the step during_startup retries."""
    from parishkit.stewardship import runtime_grants

    seen = []
    monkeypatch.setattr(
        runtime_database, "during_startup", lambda step: seen.append(step) or "ok"
    )
    assert runtime_grants.admit_runtime_database(object()) == "ok"
    [step] = seen
    monkeypatch.setattr(
        runtime_grants, "_admit_runtime_database", lambda value: ("admitted", value)
    )
    assert step()[0] == "admitted"


def test_a_final_attempt_that_times_out_past_the_limit_still_logs(
    tmp_path, monkeypatch, caplog
):
    """An attempt begun inside the window but ending past it is still logged.

    Each connection uses its whole connect_timeout and times out, so the last
    attempt starts before 60 seconds and ends after them; the refusal must
    still be preceded by the timeout line (the timeout-logging rule).
    """
    import json

    from django.db.utils import OperationalError
    from psycopg import errors

    from parishkit.stewardship.observability import SafeJsonFormatter

    clock = Clock(monkeypatch)
    monkeypatch.setattr(runtime_database, "database_settings", lambda value: SETTINGS)

    def connect(**options):
        clock.now += options["connect_timeout"]
        raise errors.ConnectionTimeout("connection timeout expired")

    monkeypatch.setattr("psycopg.connect", connect)
    with (
        caplog.at_level("ERROR", logger="parishkit.stewardship"),
        pytest.raises(OperationalError, match="in time"),
    ):
        runtime_database.await_database(configuration_at(tmp_path))
    assert clock.now - 1000.0 > runtime_database.STARTUP_DATABASE_WAIT_SECONDS
    events = [json.loads(SafeJsonFormatter().format(r)) for r in caplog.records]
    assert [(e["message"], e["extra"]["timeout"]) for e in events] == [
        ("task_timed_out", "startup_database_wait")
    ]


def test_an_admission_step_that_ends_past_the_limit_still_logs(monkeypatch, caplog):
    """The same holds for an admission pass begun inside the window."""
    from django.db.utils import OperationalError

    clock = Clock(monkeypatch)
    monkeypatch.setattr(runtime_database, "_startup_started", clock.now - 58)
    monkeypatch.setattr("django.db.connection.close", lambda: None)

    def step():
        clock.now += 5
        raise _admission_failure("timeout expired")

    with (
        caplog.at_level("ERROR", logger="parishkit.stewardship"),
        pytest.raises(OperationalError),
    ):
        runtime_database.during_startup(step)
    assert "task_timed_out" in caplog.text

"""Work stopped by a time limit is logged: what, which limit, how long (#293)."""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from parishkit.stewardship.audit import timeouts
from parishkit.stewardship.audit.schemas import Outcome
from parishkit.stewardship.campaigns.read_guards import (
    BACKGROUND_LIMITS,
    background_abort,
)
from parishkit.stewardship.jobs.broker import record_sql_timeout, sql_timeout_kind
from parishkit.stewardship.observability import Event
from parishkit.stewardship.source.transport import source_timeout_recorder


def test_timeout_context_keeps_only_reviewed_values():
    """Seconds become whole non-negative numbers; unknown values are left out."""
    task = uuid4()
    context = timeouts.timeout_context(
        what="read_guard",
        task_id=task,
        task_type="report_fact_verification",
        attempt=2,
        limit_seconds=300.4,
        elapsed_seconds=-0.2,
        outcome=Outcome.FAILED,
    )
    assert context == {
        "what": "read_guard",
        "task_id": str(task),
        "task_type": "report_fact_verification",
        "attempt": 2,
        "limit_seconds": 300,
        "elapsed_seconds": 0,
        "outcome": "failed",
    }
    assert timeouts.timeout_context(what="lease") == {"what": "lease"}


@pytest.mark.parametrize(
    "values",
    [
        {"what": "anything"},
        {"what": "lease", "task_type": "Private Name"},
        {"what": "lease", "outcome": "failed"},
        {"what": "lease", "attempt": -1},
        {"what": "mail_helper", "helper": "some_other_worker"},
        {"what": "control_lock", "count": -1},
        {"what": "control_lock", "count": True},
    ],
)
def test_timeout_context_rejects_unreviewed_values(values):
    """Free text, unknown limits and negative counters never reach the log."""
    with pytest.raises(ValueError):
        timeouts.timeout_context(**values)


def test_recording_never_raises(monkeypatch):
    """An unavailable database or a bad value is logged, never raised."""
    failures = []
    monkeypatch.setattr(
        timeouts, "emit_failure", lambda error, **kwargs: failures.append(kwargs)
    )
    monkeypatch.setattr(
        timeouts, "_private_connection", Mock(side_effect=OSError("down"))
    )
    timeouts.record_timeout(Event.TASK_TIMED_OUT, what="read_guard")
    timeouts.record_timeout(Event.TASK_FAILED, what="read_guard")
    timeouts.record_timeout(Event.TASK_TIMED_OUT, what="read_guard", level="DEBUG")
    assert failures == [
        {"event": Event.TASK_TIMED_OUT},
        {"event": Event.TASK_FAILED},
        {"event": Event.TASK_TIMED_OUT},
    ]


def sql_error(state, message=""):
    """A driver error carrying only a SQLSTATE and primary message."""
    error = Exception("private")
    error.sqlstate = state
    error.diag = SimpleNamespace(message_primary=message)
    return error


@pytest.mark.parametrize(
    "state,message,expected",
    [
        ("57014", "canceling statement due to statement timeout", "statement_timeout"),
        ("57014", "canceling statement due to user request", None),
        ("55P03", "canceling statement due to lock timeout", "lock_timeout"),
        ("25P04", "transaction timeout", "transaction_timeout"),
        ("40001", "could not serialize access", None),
    ],
)
def test_sql_timeouts_are_named_through_wrapped_errors(state, message, expected):
    """Django wraps the driver error; a read guard's own cancel is not counted."""
    wrapped = RuntimeError("wrapper")
    wrapped.__cause__ = sql_error(state, message)
    assert sql_timeout_kind(wrapped) == expected
    assert sql_timeout_kind(ValueError("plain")) is None


def test_sql_timeout_names_the_task_from_the_broker_hint(monkeypatch):
    """The broker hint's task id is kept; any other payload is ignored."""
    recorded = Mock()
    monkeypatch.setattr(timeouts, "record_timeout", recorded)
    task = uuid4()
    error = sql_error("57014", "canceling statement due to statement timeout")
    record_sql_timeout(error, (str(task),))
    record_sql_timeout(error, ("not-a-uuid",))
    record_sql_timeout(ValueError("plain"), (str(task),))
    assert [call.kwargs for call in recorded.call_args_list] == [
        {"what": "statement_timeout", "task_id": task},
        {"what": "statement_timeout", "task_id": None},
    ]
    assert recorded.call_args.args == (Event.TASK_TIMED_OUT,)


def test_source_helper_timeout_names_its_task(monkeypatch):
    """A ParishSoft request stopped at its deadline records the owning task."""
    recorded = Mock()
    monkeypatch.setattr(timeouts, "record_timeout", recorded)
    task = uuid4()
    record = source_timeout_recorder(
        SimpleNamespace(claim=SimpleNamespace(run_id=task))
    )
    record(30, 30.2)
    recorded.assert_called_once_with(
        Event.HELPER_TIMED_OUT,
        what="source_helper",
        helper="parishsoft_http_worker",
        task_id=task,
        limit_seconds=30,
        elapsed_seconds=30.2,
    )


def test_background_guards_fail_visibly_instead_of_exiting():
    """A background read's deadline leaves the worker running (#290 follow-up)."""
    assert background_abort() is None
    assert BACKGROUND_LIMITS.interactive_seconds == 300


def test_a_helper_inside_a_task_names_the_task(monkeypatch):
    """A helper killed deep inside a task is attributed to the bound task."""
    from parishkit.stewardship.observability import task_scope

    written = []
    monkeypatch.setattr(
        timeouts, "_private_connection", Mock(side_effect=OSError("down"))
    )
    monkeypatch.setattr(
        timeouts, "emit", lambda event, **kwargs: written.append(kwargs["task_id"])
    )
    monkeypatch.setattr(timeouts, "emit_failure", lambda *args, **kwargs: None)
    task = uuid4()
    with task_scope(task):
        timeouts.record_timeout(
            Event.HELPER_TIMED_OUT, what="mail_helper", helper="family_delivery_worker"
        )
    timeouts.record_timeout(Event.HELPER_TIMED_OUT, what="mail_helper")
    assert written == [task, None]


def test_a_bounded_write_keeps_the_callers_correlation_and_task(monkeypatch):
    """The writer thread sees the caller's context, not a fresh one (#302)."""
    from parishkit.stewardship.observability import (
        correlation,
        current_correlation,
        current_task,
        task_scope,
    )

    seen = []

    def record(event, **values):
        """Capture what the writer thread's context says."""
        seen.append((event, values, current_correlation(), current_task()))

    monkeypatch.setattr(timeouts, "record_timeout", record)
    task = uuid4()
    with correlation() as identifier, task_scope(task):
        timeouts.record_timeout_within(5, Event.TASK_TIMED_OUT, what="read_guard")
    assert seen == [(Event.TASK_TIMED_OUT, {"what": "read_guard"}, identifier, task)]


def test_a_summary_entry_counts_its_occurrences():
    """One entry can stand for several skips of a process-local lock wait."""
    assert timeouts.timeout_context(what="control_lock", count=7) == {
        "what": "control_lock",
        "count": 7,
    }


@pytest.mark.parametrize(
    "what", ["drive_retry_budget", "drive_request", "drive_probe_wait"]
)
def test_backup_drive_limits_are_reviewed_names(what):
    """The off-site backup's Drive limits can each be named (#324)."""
    assert timeouts.timeout_context(what=what) == {"what": what}

"""Fast application-policy cases complement, not replace, durable ownership tests."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from parishkit.stewardship.jobs.operational_content import AlertPhase, IncidentLevel
from parishkit.stewardship.jobs.operational_policy import (
    MAX_OCCURRENCES,
    IncidentPolicy,
    IncidentState,
    observe_incident,
    resolve_incident,
)

START = datetime(2026, 9, 17, 12, tzinfo=UTC)
POLICY = IncidentPolicy()


def observation(current=None, level=IncidentLevel.CRITICAL, seconds=0):
    """Supply a deterministic clock at the boundary normally owned by PostgreSQL."""
    return observe_incident(current, level, START + timedelta(seconds=seconds), POLICY)


def test_critical_open_repeat_suppression_and_one_recovery():
    """Count suppressed observations while allocating only the permitted notices."""
    opened = observation()
    assert opened.notification is AlertPhase.OPENED
    assert opened.state.occurrences == 1
    suppressed = observation(opened.state, seconds=899)
    assert suppressed.notification is None
    assert suppressed.state.occurrences == 2
    repeated = observation(suppressed.state, seconds=900)
    assert repeated.notification is AlertPhase.REPEATED
    assert repeated.state.last_notice_at == START + timedelta(seconds=900)
    recovered = resolve_incident(repeated.state, START + timedelta(seconds=901))
    assert recovered.notification is AlertPhase.RESOLVED
    assert recovered.state.occurrences == 3
    again = resolve_incident(recovered.state, START + timedelta(seconds=902))
    assert again.state is recovered.state
    assert again.notification is None


@pytest.mark.parametrize("seconds,expected", [(899, None), (900, AlertPhase.ESCALATED)])
def test_warning_escalates_at_exact_sustained_boundary(seconds, expected):
    """Warning evidence alone is durable but silent until sustained or made critical."""
    first = observation(level=IncidentLevel.WARNING)
    assert first.notification is None
    result = observation(first.state, IncidentLevel.WARNING, seconds)
    assert result.notification is expected
    assert result.state.level is (
        IncidentLevel.CRITICAL if expected else IncidentLevel.WARNING
    )


def test_explicit_critical_escalation_is_immediate_and_cannot_be_downgraded():
    """An important severity change need not wait for a warning or repeat window."""
    warning = observation(level=IncidentLevel.WARNING)
    critical = observation(warning.state, seconds=1)
    assert critical.notification is AlertPhase.ESCALATED
    later = observation(critical.state, IncidentLevel.WARNING, seconds=2)
    assert later.state.level is IncidentLevel.CRITICAL
    assert later.notification is None


def test_silent_warning_recovery_does_not_create_an_unprompted_notice():
    """An episode that never needed Admin notification may resolve without email."""
    warning = observation(level=IncidentLevel.WARNING)
    result = resolve_incident(warning.state, START + timedelta(seconds=1))
    assert result.notification is None
    assert result.state.resolved_at is not None


def test_resolved_history_is_not_reopened_or_counted_again():
    """Recurrence is a fresh durable episode, not mutation of the old resolution."""
    resolved = resolve_incident(observation().state, START).state
    with pytest.raises(ValueError, match="current ordered"):
        observation(resolved, seconds=1)
    new = observation(seconds=1)
    assert new.notification is AlertPhase.OPENED
    assert new.state.first_seen > resolved.first_seen
    assert new.state.occurrences == 1


def test_counter_saturates_without_losing_critical_notification_work():
    """An exhausted counter cannot break the ongoing incident's delivery decision."""
    full = replace(observation().state, occurrences=MAX_OCCURRENCES)
    result = observation(full, seconds=900)
    assert result.state.occurrences == MAX_OCCURRENCES
    assert result.notification is AlertPhase.REPEATED


@pytest.mark.parametrize("name", ["suppression_seconds", "escalation_seconds"])
@pytest.mark.parametrize("value", [True, None, "900", 0, 59, 86401])
def test_policy_windows_are_bounded_configuration_values(name, value):
    """Reject untyped or storm-prone operational window settings."""
    with pytest.raises(ValueError):
        IncidentPolicy(**{name: value})


@pytest.mark.parametrize("value", [60, 86400])
def test_policy_window_limits_are_inclusive(value):
    """Both documented finite configuration endpoints remain usable."""
    assert IncidentPolicy(value, value).suppression_seconds == value


@pytest.mark.parametrize(
    "changes",
    [
        {"level": "CRITICAL"},
        {"occurrences": True},
        {"occurrences": 0},
        {"occurrences": MAX_OCCURRENCES + 1},
        {"first_seen": START.replace(tzinfo=None)},
        {"last_seen": START - timedelta(seconds=1)},
        {"last_notice_at": START - timedelta(seconds=1)},
        {"last_notice_at": None},
        {"level": IncidentLevel.WARNING},
        {"resolved_at": START - timedelta(seconds=1)},
    ],
)
def test_invalid_stored_state_never_allocates_a_notice(changes):
    """An impossible snapshot must fail before owning code creates external work."""
    with pytest.raises(ValueError):
        replace(observation().state, **changes)


def test_reversed_clock_and_untyped_calls_fail_without_mutating_state():
    """Wrong inputs cannot silently reset suppression or retroactively resolve."""
    state = observation().state
    with pytest.raises(ValueError):
        observation(state, seconds=-1)
    with pytest.raises(ValueError):
        resolve_incident(state, START - timedelta(seconds=1))
    with pytest.raises(TypeError):
        observe_incident(state, "CRITICAL", START, POLICY)
    with pytest.raises(TypeError):
        observe_incident(state, IncidentLevel.CRITICAL, START, {})
    with pytest.raises(TypeError):
        observation({})
    with pytest.raises(TypeError):
        resolve_incident({}, START)
    assert state == IncidentState(IncidentLevel.CRITICAL, START, START, 1, START)

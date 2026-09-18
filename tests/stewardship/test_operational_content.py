"""Operational alert privacy is enforced by our closed, typed content contract."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from parishkit.stewardship.campaigns.domain import SystemMode
from parishkit.stewardship.jobs.operational_content import (
    TITLES,
    AlertPhase,
    IncidentKind,
    IncidentLevel,
    OperationalAlert,
    render_alert,
)


@pytest.fixture
def alert():
    """Use a synthetic incident reference, not any Family or credential identity."""
    return OperationalAlert(
        UUID("4d154ac0-a8ce-4d76-8803-f64d103b203a"),
        IncidentKind.SOURCE_STALE,
        IncidentLevel.CRITICAL,
        AlertPhase.OPENED,
        SystemMode.TESTING,
        datetime(2026, 9, 17, 1, tzinfo=UTC),
        datetime(2026, 9, 17, 2, tzinfo=UTC),
        12345,
    )


@pytest.mark.parametrize("kind", list(IncidentKind))
def test_every_compiled_kind_has_fixed_content(alert, kind):
    """All allowed application conditions have usable bounded message alternatives."""
    body = render_alert(replace(alert, kind=kind))
    assert body.subject == f"[TESTING] CRITICAL: {TITLES[kind]}"
    assert len(body.subject) <= 254
    assert TITLES[kind] in body.text and TITLES[kind] in body.html
    assert "12,345" in body.text and "12,345" in body.html
    assert "09/17/2026 01:00:00 UTC" in body.text
    assert str(alert.incident_id) in body.text
    assert "href=" not in body.html and "http" not in body.text


@pytest.mark.parametrize("mode", list(SystemMode))
@pytest.mark.parametrize("phase", list(AlertPhase))
def test_mode_and_recovery_are_visible_without_campaign_routing(alert, mode, phase):
    """Testing keeps urgency; recovery is explicit in both message alternatives."""
    body = render_alert(replace(alert, mode=mode, phase=phase))
    status = "RESOLVED" if phase is AlertPhase.RESOLVED else "CRITICAL"
    assert body.subject.startswith(f"[{mode.value.upper()}] {status}:")
    assert f"Status: {status}" in body.text
    assert f"<dd>{status}</dd>" in body.html
    assert ("has recovered" in body.text) is (phase is AlertPhase.RESOLVED)


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "private supplied message"),
        ("level", "CRITICAL"),
        ("phase", "opened"),
        ("mode", "testing"),
        ("incident_id", "private@example.org"),
        ("occurrences", True),
        ("occurrences", 0),
        ("occurrences", -1),
        ("occurrences", 1.0),
        ("occurrences", 9_223_372_036_854_775_808),
        ("first_seen", datetime(2026, 9, 17)),
        ("observed_at", datetime(2026, 9, 17, tzinfo=timezone(timedelta(hours=1)))),
        ("observed_at", "private supplied timestamp"),
    ],
)
def test_untyped_private_or_unbounded_values_are_rejected(alert, field, value):
    """Rejected caller-controlled values never leak through validation messages."""
    with pytest.raises(ValueError) as error:
        replace(alert, **{field: value})
    assert "private" not in str(error.value)
    assert "@" not in str(error.value)


def test_invalid_state_order_and_warning_escalation_are_rejected(alert):
    """Clock reversal and a non-escalated severity cannot form an alert snapshot."""
    with pytest.raises(ValueError, match="state"):
        replace(alert, observed_at=alert.first_seen - timedelta(seconds=1))
    with pytest.raises(ValueError, match="state"):
        replace(alert, phase=AlertPhase.ESCALATED, level=IncidentLevel.WARNING)


def test_renderer_never_accepts_arbitrary_template_or_mapping(alert):
    """A caller cannot pass general campaign data as operational prose."""
    with pytest.raises(TypeError, match="Typed"):
        render_alert({"subject": "private", "text": "private@example.org"})
    with pytest.raises(TypeError):
        replace(alert, family_code="ABCDEF")


def test_database_utc_timezone_representation_has_identical_content(alert):
    """PostgreSQL's UTC zone object must not change our timestamp presentation."""
    other = replace(
        alert,
        first_seen=alert.first_seen.astimezone(ZoneInfo("UTC")),
        observed_at=alert.observed_at.astimezone(ZoneInfo("UTC")),
    )
    assert other.first_seen.tzinfo is UTC
    assert render_alert(other) == render_alert(alert)

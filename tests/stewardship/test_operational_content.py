"""Operational alert privacy is enforced by our closed, typed content contract."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from parishkit.stewardship.campaigns.domain import SystemMode
from parishkit.stewardship.jobs.operational_content import (
    INSTRUCTIONS,
    RESOLVED_INSTRUCTIONS,
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
        "America/New_York",
        "us_long",
    )


@pytest.mark.parametrize("kind", list(IncidentKind))
def test_every_compiled_kind_has_fixed_content(alert, kind):
    """All allowed application conditions have usable bounded message alternatives."""
    body = render_alert(replace(alert, kind=kind))
    assert body.subject == f"[TESTING] CRITICAL: {TITLES[kind]}"
    assert len(body.subject) <= 254
    assert TITLES[kind] in body.text and TITLES[kind] in body.html
    assert "12,345" in body.text and "12,345" in body.html
    # Parish local time with the zone named, never UTC (#967).
    assert "First observed: September 16, 2026 at 9:00 PM EDT" in body.text
    assert "Latest observation: September 16, 2026 at 10:00 PM EDT" in body.text
    assert "UTC" not in body.text and "UTC" not in body.html
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
        ("timezone", "private/zone"),
        ("timezone", ""),
        ("timezone", None),
        ("date_format", None),
        ("date_format", "private format"),
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


@pytest.mark.parametrize(
    "instant,shown",
    [
        # US fall back, November 1, 2026: 1:30 AM happens twice; the
        # abbreviation tells the two hours apart.
        (datetime(2026, 11, 1, 5, 30, tzinfo=UTC), "November 1, 2026 at 1:30 AM EDT"),
        (datetime(2026, 11, 1, 6, 30, tzinfo=UTC), "November 1, 2026 at 1:30 AM EST"),
        # US spring forward, March 8, 2026: 1:59 AM EST is followed by 3:00 AM EDT.
        (datetime(2026, 3, 8, 6, 59, tzinfo=UTC), "March 8, 2026 at 1:59 AM EST"),
        (datetime(2026, 3, 8, 7, 0, tzinfo=UTC), "March 8, 2026 at 3:00 AM EDT"),
    ],
)
def test_times_name_the_parish_zone_across_daylight_saving_changes(
    alert, instant, shown
):
    """Each shown time uses the offset in force at that instant, never UTC."""
    body = render_alert(replace(alert, first_seen=instant, observed_at=instant))
    assert f"First observed: {shown}\nLatest observation: {shown}\n" in body.text
    assert f"<dt>First observed</dt><dd>{shown}</dd>" in body.html


@pytest.mark.parametrize(
    "zone,style,shown",
    [
        ("America/Chicago", "us_medium", "Sep 16, 2026 at 8:00 PM CDT"),
        ("America/New_York", "us_numeric", "09/16/2026 9:00 PM EDT"),
        ("Europe/Dublin", "eu_long", "17 September 2026, 02:00 IST"),
        ("Europe/Berlin", "eu_medium", "17 Sep 2026, 03:00 CEST"),
        ("Australia/Sydney", "eu_numeric", "17/09/2026 11:00 AEST"),
        ("Europe/Warsaw", "eu_dot", "17.09.2026 03:00 CEST"),
        ("America/Sao_Paulo", "iso", "2026-09-16 22:00 -03"),
    ],
)
def test_times_follow_the_parish_zone_and_date_format(alert, zone, style, shown):
    """The configured zone and date format, not a hard-coded parish, decide the text."""
    body = render_alert(replace(alert, timezone=zone, date_format=style))
    assert f"First observed: {shown}\n" in body.text


@pytest.mark.parametrize("kind", sorted(INSTRUCTIONS))
def test_kinds_the_log_cannot_explain_carry_their_own_instruction(alert, kind):
    """The backup key and retention alerts say what to do in the message itself."""
    body = render_alert(replace(alert, kind=kind))
    assert INSTRUCTIONS[kind] in body.text
    assert "Review the operational log for details" not in body.text
    resolved = render_alert(replace(alert, kind=kind, phase=AlertPhase.RESOLVED))
    if kind in RESOLVED_INSTRUCTIONS:
        # Two quiet days are not a recovery: nobody has confirmed the key.
        assert RESOLVED_INSTRUCTIONS[kind] in resolved.text
        assert "has recovered" not in resolved.text
    else:
        assert "has recovered" in resolved.text

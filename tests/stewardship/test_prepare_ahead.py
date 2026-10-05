"""Preparing Production reminders ahead of their due time (BG-12, #447).

Fast tests of the pure parts: the planning horizon, the nightly-refresh
warning's time arithmetic, the lead window against the guard's cap, and the
forward migration that installs that cap. The PostgreSQL behavior is in
``database/test_family_mail_prepare_ahead_postgresql.py``.
"""

import re
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest

from parishkit.stewardship.campaigns import migrations as campaign_migrations
from parishkit.stewardship.campaigns.family_schedule_planning import (
    PREPARE_AHEAD,
    _horizon,
)
from parishkit.stewardship.campaigns.schedule_production import (
    refresh_in_lead_window,
)

SCHEMA = Path(campaign_migrations.__file__).parents[2] / "schema"
# The module name starts with a digit, so it cannot be imported by statement.
forward = import_module(f"{campaign_migrations.__name__}.0003_occurrence_prepare_ahead")
FROZEN = forward.FROZEN_SQL.read_text(encoding="utf-8")
NAME = "stewardship_occurrence_guard_v1"
NOW = datetime(2054, 10, 3, 11, 0, tzinfo=UTC)
CAP = re.compile(r"NEW\.due_at<=instant\+interval '(\d+) hours'")


def scope(mode="production"):
    """A planning scope with only the runtime mode ``_horizon`` reads."""
    return SimpleNamespace(runtime=SimpleNamespace(mode=mode))


def occurrence(due_at, kind="reminder", mode="production"):
    """A preparation's own occurrence, as ``_row`` returns it."""
    return SimpleNamespace(
        due_at=due_at, mode=mode, definition=SimpleNamespace(kind=kind)
    )


def test_the_bulk_sweep_looks_ahead_in_production_only():
    """The scheduler's bulk sweep plans Production reminders a lead window ahead."""
    assert _horizon(scope(), NOW, ahead=True, occurrence=None) == NOW + PREPARE_AHEAD
    assert _horizon(scope("testing"), NOW, ahead=True, occurrence=None) == NOW
    assert _horizon(scope(), NOW, ahead=False, occurrence=None) == NOW


@pytest.mark.parametrize(
    "row,extended",
    [
        # A Production reminder not yet due but due within the window.
        (occurrence(NOW + timedelta(minutes=1)), True),
        (occurrence(NOW + PREPARE_AHEAD), True),
        # Already due: plans exactly as before.
        (occurrence(NOW), False),
        (occurrence(NOW - timedelta(hours=1)), False),
        # Beyond the window: never prepared, so never looked ahead for.
        (occurrence(NOW + PREPARE_AHEAD + timedelta(seconds=1)), False),
        # Initial invitations and Testing never look ahead.
        (occurrence(NOW + timedelta(minutes=1), kind="initial"), False),
        (occurrence(NOW + timedelta(minutes=1), mode="testing"), False),
    ],
)
def test_preparation_extends_its_horizon_by_its_own_occurrence(row, extended):
    """A preparation claim's horizon follows its occurrence, not the bulk switch."""
    expected = NOW + PREPARE_AHEAD if extended else NOW
    assert _horizon(scope(), NOW, ahead=False, occurrence=row) == expected


def test_the_lead_window_is_within_the_guards_cap():
    """PREPARE_AHEAD never exceeds what the occurrence guard admits."""
    baseline = (SCHEMA / "functions.sql").read_text(encoding="utf-8")
    start = baseline.index(f"CREATE FUNCTION public.{NAME}()")
    body = baseline[start : baseline.index("END $_$;", start)]
    (hours,) = CAP.findall(body)
    assert timedelta(0) < PREPARE_AHEAD <= timedelta(hours=int(hours))
    assert CAP.findall(FROZEN) == [hours]


def test_forward_migration_is_the_frozen_file():
    """The migration reads one frozen file whole and replaces only the guard."""
    assert forward.FROZEN_SQL == SCHEMA / "migrations" / (
        "0005_occurrence_prepare_ahead.sql"
    )
    assert len(re.findall(r"^CREATE OR REPLACE", FROZEN, re.M)) == 1
    assert f"CREATE OR REPLACE FUNCTION public.{NAME}() RETURNS trigger" in FROZEN
    (operation,) = forward.Migration.operations
    assert operation.sql == FROZEN
    assert operation.reverse_sql is None
    assert set(forward.Migration.dependencies) == {
        ("stewardship_campaigns", "0002_family_engagement"),
        ("stewardship_accounts", "0004_automation_sessions"),
    }


def test_forward_migration_changes_one_condition_and_restores_the_guards_rights():
    """Only the not-yet-due condition differs, and the guard stays SECURITY DEFINER.

    The replaced body is the baseline's (test_schema_migration_files), so
    this pins what the baseline change itself was: the old condition is
    gone, and the new one names only Production preparation.
    """
    body = FROZEN[FROZEN.index("CREATE OR REPLACE FUNCTION") : FROZEN.index("END $_$;")]
    assert "OR NEW.due_at>instant\n" not in body
    assert (
        "OR (NEW.due_at>instant AND NOT (t.task_type='family_mail_prepare'\n"
        "                   AND NEW.mode='production' "
        "AND NEW.due_at<=instant+interval '2 hours'))"
    ) in body
    restore = FROZEN.index(f"ALTER FUNCTION public.{NAME}() SECURITY DEFINER;")
    check = FROZEN.index("DO $check$")
    assert FROZEN.index("END $_$;") < restore < check
    verify = FROZEN[check:]
    assert "RAISE EXCEPTION" in verify
    assert f"p.proname='{NAME}'" in verify
    assert "p.prosecdef" in verify
    assert "NOT has_function_privilege('public',p.oid,'EXECUTE')" in verify
    assert "NOT (t.task_type=''family_mail_prepare''" in verify
    assert "NEW.due_at<=instant+interval ''2 hours''" in verify


ZONE = "America/New_York"


def local(day, clock):
    """A New York wall time as a UTC instant (EDT in October 2054)."""
    hours, minutes = map(int, clock.split(":"))
    return datetime(2054, 10, day, hours + 4, minutes, tzinfo=UTC)


@pytest.mark.parametrize(
    "nightly,due,inside",
    [
        # The default 02:00 refresh precedes an 08:00 reminder's window.
        ("02:00", local(3, "08:00"), False),
        ("06:00", local(3, "08:00"), True),
        ("07:59", local(3, "08:00"), True),
        # The window ends at the due time itself.
        ("08:00", local(3, "08:00"), False),
        ("05:59", local(3, "08:00"), False),
        # A window that crosses midnight: 00:30 is due, 23:00 the day before
        # is inside it.
        ("23:00", local(3, "00:30"), True),
        ("22:29", local(3, "00:30"), False),
    ],
)
def test_a_nightly_refresh_inside_a_lead_window_is_found(nightly, due, inside):
    """The nightly time on the due day or the day before, in the campaign zone."""
    assert refresh_in_lead_window([nightly], ZONE, [due]) is inside


def test_every_configured_full_refresh_time_is_checked():
    """A later configured full refresh time (#465) can fall in the window too."""
    due = local(3, "08:00")
    assert refresh_in_lead_window(["02:00"], ZONE, [due]) is False
    assert refresh_in_lead_window(["02:00", "07:00"], ZONE, [due]) is True
    assert refresh_in_lead_window(["02:00", "12:00"], ZONE, [due]) is False


def test_no_reminders_means_no_warning():
    """Nothing to warn about without a reminder still to come."""
    assert refresh_in_lead_window(["07:00"], ZONE, []) is False

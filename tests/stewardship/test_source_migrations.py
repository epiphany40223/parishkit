"""The refresh-tick guard forward migration installs the baseline's function text."""

import re
from importlib import import_module
from pathlib import Path

from parishkit.stewardship.source import migrations as source_migrations

# The module name starts with a digit, so it cannot be imported by statement.
forward = import_module(f"{source_migrations.__name__}.0002_refresh_tick_times")
FUNCTIONS = Path(source_migrations.__file__).parents[2] / "schema" / "functions.sql"
NAME = "stewardship_refresh_tick_guard_v1"


def baseline_function():
    """The guard's text in the fresh-install baseline, from CREATE to its $$;.

    The function is located by its CREATE line, never by its header comment
    (a stray header once left an in-place file empty), and must be a
    plausible size.
    """
    match = re.search(
        rf"^CREATE FUNCTION public\.{NAME}\(\).*?^\$\$;$",
        FUNCTIONS.read_text(encoding="utf-8"),
        re.S | re.M,
    )
    assert match is not None and len(match.group(0)) > 3000
    return match.group(0)


def test_forward_migration_matches_the_fresh_install_guard():
    """A fresh install and an upgraded deployment run the same guard (#465)."""
    expected = baseline_function().replace(
        "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"
    )
    assert forward.GUARD.strip() == expected
    assert "full_refresh_times" in forward.GUARD
    assert "applied delta cadence" in forward.GUARD


def test_forward_migration_verifies_its_own_result():
    """The migration refuses to commit unless the new body is installed."""
    assert "DO $check$" in forward.VERIFY
    assert "RAISE EXCEPTION" in forward.VERIFY
    for marker in ("full_refresh_times", "applied delta cadence"):
        assert f"LIKE '%{marker}%'" in forward.VERIFY
    (operation,) = forward.Migration.operations
    assert operation.sql == [forward.GUARD, forward.VERIFY]
    # Forward-only: there is no reverse SQL to pretend with.
    assert operation.reverse_sql is None

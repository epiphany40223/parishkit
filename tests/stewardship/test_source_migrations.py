"""The refresh-tick guard forward migration installs one frozen, self-verifying file.

The frozen file's digest and its agreement with the fresh-install guard in
``schema/functions.sql`` are checked for every ``schema/migrations/*.sql`` by
``test_schema_migration_files.py``; this module covers what is specific to
``stewardship_source.0002_refresh_tick_times``.
"""

from importlib import import_module
from pathlib import Path

from parishkit.stewardship.source import migrations as source_migrations

# The module name starts with a digit, so it cannot be imported by statement.
forward = import_module(f"{source_migrations.__name__}.0002_refresh_tick_times")
SCHEMA = Path(source_migrations.__file__).parents[2] / "schema"
FROZEN = forward.FROZEN_SQL.read_text(encoding="utf-8")
NAME = "stewardship_refresh_tick_guard_v1"


def test_forward_migration_is_the_frozen_file():
    """The migration reads one frozen file whole and replaces only the guard (#465)."""
    assert forward.FROZEN_SQL == SCHEMA / "migrations" / "0003_refresh_tick_times.sql"
    # No inline SQL remains to drift from the file.
    assert not hasattr(forward, "GUARD")
    assert not hasattr(forward, "VERIFY")
    assert FROZEN.count("CREATE OR REPLACE") == 1
    assert f"CREATE OR REPLACE FUNCTION public.{NAME}() RETURNS trigger" in FROZEN
    assert "full_refresh_times" in FROZEN
    assert "applied delta cadence" in FROZEN
    (operation,) = forward.Migration.operations
    assert operation.sql == FROZEN
    # Forward-only: there is no reverse SQL to pretend with.
    assert operation.reverse_sql is None


def test_forward_migration_verifies_its_own_result():
    """The migration refuses to commit unless the new body is installed."""
    check = FROZEN.index("DO $check$")
    assert check > FROZEN.index(f"CREATE OR REPLACE FUNCTION public.{NAME}(")
    verify = FROZEN[check:]
    assert "RAISE EXCEPTION" in verify
    assert f"p.proname='{NAME}'" in verify
    for marker in ("full_refresh_times", "applied delta cadence"):
        assert f"LIKE '%{marker}%'" in verify

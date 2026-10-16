"""Forward migration: a finished setup load can be discarded (#1353).

Its SQL is the frozen file ``schema/migrations/0102_setup_load_void.sql``
(prefix 0102, assigned in the repository-wide file sequence), read whole and
never parsed. It replaces the setup attempt's guard, audit and mutable-record
trigger functions so a load that fails for good returns setup to collecting
and the owning Admin can discard a finished load (audited as
``setup_source_voided``), lets the setup source cleanup dispose of every
load root of the attempt (``stewardship_setup_disposable_snapshot_v1``), and
lets the action context carry the discard's closed words
(``stewardship_safe_context_v1``). It ends with a DO block that refuses to
commit unless all five are installed as declared. ``SetupAttempt``'s
``write_once_fields`` is a class attribute, not model state, so there is no
state operation. Reversing needs its own forward migration, so, like the
baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0102_setup_load_void.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The accounts app's previous migration.
        ("stewardship_accounts", "0022_drop_ministry_activity"),
        # The migration that installs the previous frozen file (0101, the
        # census publication tasks), so the files apply in prefix order.
        ("stewardship_responses", "0007_publication_tasks"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]

"""Forward migration: column-limit admin-recovery's portal session grants.

Its SQL is the frozen file ``schema/migrations/0034_recovery_session_grants.sql``
(#389 L6), read whole and never parsed. It revokes the whole-table SELECT and
UPDATE that the offline admin-recovery login held on
``stewardship_portal_session`` when that role exists, so ``database-grants``
can then add the column grants ``runtime_database.offline_columns`` declares,
and ends with a DO block that refuses to commit unless the whole-table grants
are gone. It changes no table, so there is no state operation. Reversing
needs its own forward migration, so it has no reverse operation.

The dependency on the migration that installed the previous frozen file keeps
the files in prefix order; it moves to the installer of 0033 when the files
before this one land (see the PR's renumbering plan).
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0034_recovery_session_grants.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0004_automation_sessions"),
        # The migration that installed the previous frozen file, so the
        # files apply in prefix order.
        ("stewardship_jobs", "0009_task_type_index"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]

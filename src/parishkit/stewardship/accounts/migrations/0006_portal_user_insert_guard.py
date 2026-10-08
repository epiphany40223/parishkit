"""Forward migration: guard PortalUser inserts (#389, the #352 residual).

Its SQL is the frozen file ``schema/migrations/0036_portal_user_insert_guard.sql``,
read whole and never parsed. It adds ``stewardship_portal_user_insert_v1`` and
its BEFORE INSERT trigger: only the web login records a portal user, with
``verified_at`` inside the inserting transaction (as the sign-in stamps it;
SQL cannot see the sign-in itself), for an enabled, unattributed, version-1
row with a non-empty subject and email. The file ends with a DO block that
refuses to commit unless the trigger is installed as declared. It changes
no table, so there is no state operation. Reversing needs its own forward
migration, so it has no reverse operation.

The dependency on the migration that installed the previous frozen file keeps
the files in prefix order (see the PR's renumbering plan).
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0036_portal_user_insert_guard.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0005_recovery_session_grants"),
        # The migration that installed the previous frozen file (0035), so
        # the files apply in prefix order.
        ("stewardship_jobs", "0010_log_writer_allowlist"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]

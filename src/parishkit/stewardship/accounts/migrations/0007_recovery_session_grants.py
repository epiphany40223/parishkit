"""Forward migration: column-limit admin-recovery's portal session grants.

Its SQL is the frozen file
``schema/migrations/0027_recovery_session_grants.sql`` (#389 L6), read
whole and never parsed. It revokes the whole-table SELECT and UPDATE that
the offline admin-recovery login held on ``stewardship_portal_session`` when
that role exists, so ``database-grants`` can then add the column grants
``runtime_database.offline_columns`` declares, and ends with a DO block that
refuses to commit unless the whole-table grants are gone. It changes no
table, so there is no state operation. Reversing needs its own forward
migration, so it has no reverse operation.

It depends on its app's latest migration, which also installed the previous
frozen file (0026), so the files apply in prefix order.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0027_recovery_session_grants.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # Its app's latest migration, which also installed the previous
        # frozen file (0026).
        ("stewardship_accounts", "0006_chair_decisions_plan"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]

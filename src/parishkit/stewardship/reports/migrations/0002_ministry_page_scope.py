"""Forward migration: SQL-derived scope for the Ministry pages (#389 L3).

Its SQL is the frozen file ``schema/migrations/0038_ministry_page_scope.sql``,
read whole and never parsed. It adds ``stewardship_ministry_report_for_v1``
and ``stewardship_ministry_followup_for_v1``, which take only the actor and
read its current scope from ``stewardship_ministry_scope_v1`` (as the export
captures do) before calling the unchanged report functions; the file ends
with a DO block that refuses to commit unless both are installed as
declared. It changes no table, so there is no state operation. Reversing
needs its own forward migration, so it has no reverse operation.

The dependency on the migration that installed the previous frozen file
(0037) keeps the files in prefix order; see the PR's renumbering plan.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0038_ministry_page_scope.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_reports", "0001_initial"),
        # The migration that installed the previous frozen file, so the
        # files apply in prefix order.
        ("stewardship_audit", "0002_critical_ack_guard"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]

"""Forward migration: count an export's read-guard stops for recovery (#386).

Its SQL is the frozen file ``schema/migrations/0023_read_guard_kills.sql``
(number 0023 of the repository-wide file sequence), read whole and never
parsed. It creates the SECURITY DEFINER function
``stewardship_read_guard_kills_v1(uuid)``, which returns how many
``read_guard`` timeout entries name a task, so the general worker's export
recovery can stop retrying a render that always overruns without reading
the operational log's context. It ends with a DO block that refuses to
commit unless the function is installed as declared. No model state
changes. Reversing needs its own forward migration, so, like the baseline,
it has no reverse operation.

It depends on its app's latest migration and on the migration that
installed the previous frozen file (0022), so the files apply in prefix
order.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0023_read_guard_kills.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_jobs", "0012_web_health"),
        # The migration that installed the previous frozen file (0022).
        ("stewardship_reports", "0005_directory_member_search"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]

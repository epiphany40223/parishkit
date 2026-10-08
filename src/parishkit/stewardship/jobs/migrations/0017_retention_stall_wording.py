"""Forward migration: the retention incident's instruction covers stalls (#833).

Its SQL is the frozen file ``schema/migrations/0033_retention_stall_wording.sql``,
read whole and never parsed. It replaces ``stewardship_ops_content_v1`` with
the ``source_retention_failing`` instruction changed to match
``jobs/operational_content.py``, and ends with a DO block that refuses to
commit unless the new wording and the function's attributes are installed.
No model state changes. Reversing needs its own forward migration, so, like
the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0033_retention_stall_wording.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # Its app's latest migration, which also installed the previous
        # frozen file (0032), so the files apply in prefix order.
        ("stewardship_jobs", "0016_load_budget_timeout"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]

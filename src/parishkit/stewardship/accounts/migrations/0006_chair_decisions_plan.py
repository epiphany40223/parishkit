"""Forward migration: chair decisions read the current chairs once (#147).

Its SQL is the frozen file ``schema/migrations/0026_chair_decisions_plan.sql``,
read whole and never parsed. It depends on its app's latest migration and on
the migration that installed the previous frozen file (0025), so the files
apply in prefix order.

The file replaces ``stewardship_chair_decisions_v1`` with a body that reads
``stewardship_current_chair`` once, as a materialized CTE. The result is
unchanged; the old body spent 100-200 ms planning on every call, twice per
source promotion and once per configuration activation, all under the
work-order lock. It ends with a DO block that refuses to commit unless the
new body is installed with the function's unchanged attributes. No model
changes, so there is no state operation. Reversing needs its own forward
migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0026_chair_decisions_plan.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0005_automation_fresh_guards"),
        # The migration that installed the previous frozen file (0025).
        ("stewardship_campaigns", "0006_scheduler_link_preparation"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]

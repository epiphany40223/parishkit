"""Forward migration: Ministry leaders come from ParishSoft roster roles (#922).

Its SQL is the frozen file ``schema/migrations/0042_ministry_leaders_from_roles.sql``,
read whole and never parsed. It adds the campaign's leader-role accessor, the
one definition of who leads which Ministry and one user's role-derived scope,
and replaces the Ministry scope, the configuration pointer guard (one more
live-editable campaign key) and the chair receipt requirement. It ends with
a DO block that refuses to commit unless all of them are installed as
declared. No model changes, so there is no state operation. Reversing needs
its own forward migration, so, like the baseline, it has no reverse
operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0042_ministry_leaders_from_roles.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0009_setup_without_campaign"),
        # The migration that installed the previous frozen file (0041,
        # #389), so the files apply in prefix order.
        ("stewardship_jobs", "0020_task_type_creators"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]

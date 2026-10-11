"""Forward migration: report date filters in the viewer's browser zone (#558).

Its SQL is the frozen file ``schema/migrations/0064_report_local_days.sql``,
read whole and never parsed. It adds ``stewardship_information_report_v2``,
``stewardship_ministry_report_v3`` and its actor-scoped wrapper
``stewardship_ministry_report_v4``, which take the filters' new ``zone`` key
and place each From and To day in that zone, and v2 export capture functions
that the two export snapshot triggers now call. The earlier versions stay
installed. It ends with a DO block that refuses to commit unless everything is
installed as declared. No model changes, so there is no state operation.
Reversing needs its own forward migration, so, like the baseline, it has no
reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0064_report_local_days.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The migration that installs the previous frozen file this branch
        # sits on (0056, #1022). Frozen files 0057 to 0063 are planned to
        # land first; re-chain this onto whichever migration installs 0063
        # when this merges, so the files apply in prefix order.
        ("stewardship_reports", "0013_retire_talents"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]

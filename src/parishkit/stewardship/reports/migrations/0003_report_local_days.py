"""Forward migration: report date filters in the viewer's browser zone (#558).

Its SQL is the frozen file ``schema/migrations/0017_report_local_days.sql``,
read whole and never parsed. It adds ``stewardship_information_report_v2`` and
``stewardship_ministry_report_v2``, which take the filters' new ``zone`` key
and place each From and To day in that zone, and v2 export capture functions
that the two export snapshot triggers now call. The v1 functions stay
installed, so the previous release still works against this schema. It ends
with a DO block that refuses to commit unless everything is installed as
declared. No model changes, so there is no state operation. Reversing needs
its own forward migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0017_report_local_days.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_reports", "0002_directory_member_search"),
        # The migration that installed the previous frozen file (0016), so
        # the files apply in prefix order.
        ("stewardship_jobs", "0011_backup_request"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]

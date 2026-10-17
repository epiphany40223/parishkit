"""Forward migration: Financial stewardship dates in the browser's zone (#558).

Its SQL is the frozen file ``schema/migrations/0110_financial_local_days.sql``
(prefix 0110, assigned in the repository-wide file sequence), read whole and
never parsed. It replaces ``stewardship_financial_report_v1`` in place with
0094's body plus an optional ``zone`` filter key: with it, the first and
latest response date filters are days in that zone (0064's
``stewardship_browser_day_start_v1``); without it, they keep the campaign's
days, so an older release and every retained export capture read as before.
It ends with a DO block that refuses to commit unless the selection is
installed as declared with its grants, owner and settings unchanged. No
model changes, so there is no state operation. Reversing needs its own
forward migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0110_financial_local_days.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The previous reports migration, which also installs the previous
        # frozen file (0109), so the files apply in prefix order.
        ("stewardship_reports", "0029_point_in_time_exports"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]

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

It depends on the reports app's previous migration, which installs the
previous frozen file (0063, the Family timeline export), so the files apply
in prefix order and Django keeps a single leaf. That puts it in the users
swap's closure (#952): `migrate --users-upgrade-base` never applies it. It
creates no granted table or definer routine, so nothing is registered in
``runtime_grants``.
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
        # The reports app's previous migration, which installs the previous
        # frozen file (0063, #463), so the files apply in prefix order. It
        # follows the users swap, so `migrate --users-upgrade-base` never
        # applies this file.
        ("stewardship_reports", "0015_timeline_exports"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]

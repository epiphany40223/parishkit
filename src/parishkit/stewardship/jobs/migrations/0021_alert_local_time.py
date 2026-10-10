"""Forward migration: alert emails show parish local time, never UTC (#967).

Its SQL is the frozen file ``schema/migrations/0043_alert_local_time.sql``,
read whole and never parsed. It adds ``stewardship_alert_time_v1`` (one
instant in the parish profile's time zone and date format, naming the zone)
and the operational and security alert content functions' v2, which take
the routing configuration and use it for their times; replaces both render
admission functions to call them; and drops the two UTC v1 content
functions. It ends with a DO block that refuses to commit unless all of
them are installed as declared. No model changes, so there is no state
operation. Reversing needs its own forward migration, so, like the
baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0043_alert_local_time.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_jobs", "0020_task_type_creators"),
        # The migration that installed the previous frozen file (0042,
        # #922), so the files apply in prefix order.
        ("stewardship_accounts", "0010_ministry_leaders_from_roles"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]

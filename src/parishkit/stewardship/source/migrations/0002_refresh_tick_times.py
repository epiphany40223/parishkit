"""Forward migration: the refresh-tick guard accepts configured full-refresh times.

Before #465 the guard required a scheduled full tick's time to equal the
applied ``nightly_time``. Full refreshes may now also run at the other local
times listed in ``full_refresh_times``, and deltas may run hourly or not at
all (``delta_refresh``), so the guard checks a full tick's time against the
list and uses that time in the slot identity and the DST check.

Its SQL is the frozen file ``schema/migrations/0003_refresh_tick_times.sql``
(the next prefix in the repository-wide file sequence after campaigns'
``0002_family_engagement.sql``, hence the dependency on that migration),
read whole and never parsed: ``CREATE OR REPLACE`` of
``stewardship_refresh_tick_guard_v1`` with the fresh-install baseline's text
(schema/functions.sql) as it stands at this release, then a DO block that
refuses to commit unless the new body is installed, so an upgrade cannot
report success while the old guard is still in place. A digest test pins the
frozen file, and a second test checks that the latest migration's copy of the
guard still equals the baseline's, so the two cannot drift apart unnoticed; a
later change to the guard gets its own numbered migration. The frozen text
starts with ``SET LOCAL check_function_bodies = false``, so a body that names
an object a later migration owns cannot break this one.

A fresh install runs the baseline (0001, whose functions.sql already carries
this body) and then this migration, which re-creates the guard unchanged; an
upgraded database ends in the same catalog. Reversing would also have to
remove the ticks the new guard admitted, so, like the baseline, it has no
reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0003_refresh_tick_times.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_source", "0001_initial"),
        # The migration that installed the previous frozen file, so the
        # files apply in prefix order.
        ("stewardship_campaigns", "0002_family_engagement"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]

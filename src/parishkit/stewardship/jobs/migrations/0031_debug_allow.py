"""Forward migration: Allow debug logging again (ADM-13 PR 6c, #1207).

Its SQL is the frozen file ``schema/migrations/0088_debug_allow.sql`` (prefix
0088 in the repository-wide file sequence), read whole and never parsed. It
replaces the debug-off switch's guard (``stewardship_debug_off_switch_guard_v1``,
which 0078 created) with 0078's body plus one admitted update: the web login
may clear a set switch for a freshly signed-in, enabled Administrator, and
only while the global mode is Testing. It ends with a DO block that refuses
to commit unless the new guard is installed as declared. No model changes,
so there are no state operations. Reversing needs its own forward migration.

It follows the users swap (#952) through 0028, so `migrate
--users-upgrade-base` never applies it. The web login's column UPDATE grant
on the switch is declared in runtime_grants; the table itself is already in
runtime_grants.CLOSURE_TABLES.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0088_debug_allow.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_jobs", "0030_bounce_report"),
        # The migration that installs the previous frozen file (0087,
        # publication plans), so the files apply in prefix order. This
        # follows the users swap, so `migrate --users-upgrade-base` never
        # applies it.
        ("stewardship_responses", "0004_publication_plans"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]

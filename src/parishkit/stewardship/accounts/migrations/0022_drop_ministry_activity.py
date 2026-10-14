"""Forward migration: the local Ministry activity leaves the database (#1267).

Its SQL is the frozen file ``schema/migrations/0091_drop_ministry_activity.sql``
(prefix 0091 in the repository-wide file sequence), read whole and never
parsed. It refuses to run while the active configuration still marks a
Ministry inactive, drops ``stewardship_ministry_activity`` with its two
trigger functions, and replaces every SQL function that read the table with
its newest body minus only that test. It ends with a DO block that refuses
to commit unless all of that is installed as declared. The state operation
removes the model from Django's model state, so ``makemigrations --check``
stays clean. Reversing needs its own forward migration.

It depends on its own app's latest migration and on the one that installs
the previous frozen file (0090), so the files apply in prefix order. It
follows the users swap (#952) and creates no routine or table, so nothing
is registered in ``runtime_grants``; the grants that named the table are
gone with it.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0091_drop_ministry_activity.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0021_branding_bundle_mode"),
        # The migration that installs the previous frozen file (0090, the
        # publication worker), so the files apply in prefix order. It
        # follows the users swap, so `migrate --users-upgrade-base` never
        # applies it.
        ("stewardship_responses", "0005_publication_worker"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[migrations.DeleteModel(name="MinistryActivity")],
        )
    ]

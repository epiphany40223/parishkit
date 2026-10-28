"""Forward migration: Ministry checks read the roster as of the later of
the refresh date and the commitment period start (#2293).

Its SQL is the frozen file ``schema/migrations/0189_commitment_roster_date.sql``
(number 0189, reserved for this change), read whole and never parsed. It
creates the Ministry roster date read (the later of a snapshot's refresh
date and its campaign's commitment period start) and a roster read as of a
date, and replaces the refresh check guard so a decided Ministry request's
check is judged by the roster as of that date. It ends with a DO block
that refuses to commit unless all of it is installed with the guard's old
grants and settings. No model changes, so there are no state operations.
Reversing needs its own forward migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0189_commitment_roster_date.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # This app's previous migration (0170, #2008).
        ("stewardship_responses", "0032_parish_added_shown"),
        # The migration that installs the previous frozen file (0188, the
        # staff open-after-close change), so this file applies after every
        # earlier one.
        ("stewardship_campaigns", "0020_staff_open_after_close"),
    ]

    operations = [
        migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8")),
    ]

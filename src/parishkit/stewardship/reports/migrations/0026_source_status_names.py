"""Forward migration: one phrase for Members and Families no longer in ParishSoft.

Its SQL is the frozen file
``schema/migrations/0094_source_status_names.sql`` (prefix 0094, assigned in
the repository-wide file sequence), read whole and never parsed (#1216). It
replaces the Ministry, financial, Limitations, information, directory and
weekly selections so a Member or Family the current ParishSoft data no
longer has gets no name (the pages show the shared status badge), one with
a blank name reads "Name not recorded in ParishSoft", and the retired
"Unavailable Member" and "Unavailable Family" are gone. It ends with a DO
block that refuses to commit unless every selection is installed as
declared with its grants and settings unchanged. No model changes, so there
is no state operation. Reversing needs its own forward migration, so, like
the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0094_source_status_names.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration, which installs 0093 (#1219),
        # the previous frozen file, so the files apply in prefix order.
        ("stewardship_reports", "0025_removed_ministry_gone_members"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]

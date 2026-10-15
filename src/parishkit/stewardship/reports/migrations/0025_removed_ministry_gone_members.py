"""Forward migration: a removed Ministry is not kept only by gone Members.

Its SQL is the frozen file
``schema/migrations/0093_removed_ministry_gone_members.sql`` (prefix 0093,
assigned in the repository-wide file sequence), read whole and never parsed
(#1219). It replaces the follow-up, packet and Ministry report selections so
a Ministry the campaign no longer selects stays listed only while it has a
current request for a Member still in ParishSoft or one the Family added on
the form; requests for Members no longer in ParishSoft (#1217) no longer
keep it listed, empty. It ends with a DO block that refuses to commit unless
every selection is installed as declared with its grants unchanged. No model
changes, so there is no state operation. Reversing needs its own forward
migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0093_removed_ministry_gone_members.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration, which installs 0092 (#1217),
        # the previous frozen file, so the files apply in prefix order.
        ("stewardship_reports", "0024_hide_gone_members"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]

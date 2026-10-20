"""Forward migration: correcting a New Member's ParishSoft Member ID (#1773).

Its SQL is the frozen file ``schema/migrations/0139_new_member_id_correction.sql``
(number 0139, provisional until it is assigned in merge order), read whole
and never parsed. It replaces the resolution guard, from 0138's body, so
Staff and Administrators may mark a New Member Entered by hand again with
another ParishSoft Member ID while no refresh has confirmed it. It ends
with a DO block that refuses to commit unless the guard is installed. No
table changes, so there is no state operation. Reversing needs its own
forward migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0139_new_member_id_correction.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The migration that installs 0138, whose resolution guard body
        # this file replaces.
        ("stewardship_responses", "0019_published_refresh_conflicts"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]

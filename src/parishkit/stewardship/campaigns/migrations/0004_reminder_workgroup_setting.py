"""Forward migration: a live campaign's Reminder WorkGroup stays editable (#861).

A campaign's optional ``reminder_workgroup`` value names the ParishSoft
Family WorkGroup whose Families get no Reminders. Staff mark those Families
while the campaign runs, so the Administrator must be able to change the
name after go-live. The configuration pointer guard
(``stewardship_campaign_pointer_v1``) refuses changes to a live campaign's
values except its exempt keys; this adds ``reminder_workgroup`` to them, as
the Python admission check (``campaigns.admission``) does.

Its SQL is the frozen file
``schema/migrations/0015_reminder_workgroup_setting.sql``, read whole and
never parsed: ``CREATE OR REPLACE`` of the guard with the fresh-install
baseline's text (schema/functions.sql) as it stands at this release, then a
DO block that refuses to commit unless the new body is installed. It depends
on the migration that installed the previous frozen file, so the files apply
in prefix order. A fresh install runs the baseline (whose functions.sql
already carries this body) and then this migration, which re-creates the
guard unchanged; an upgraded database ends in the same catalog. Reversing
needs its own forward migration, so, like the baseline, it has no reverse
operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0015_reminder_workgroup_setting.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_campaigns", "0003_occurrence_prepare_ahead"),
        # The migration that installed the previous frozen file, so the
        # files apply in prefix order.
        ("stewardship_source", "0006_slot_decisions"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]

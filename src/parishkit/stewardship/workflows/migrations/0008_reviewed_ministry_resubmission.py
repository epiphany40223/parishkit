"""Forward migration: Ministry resubmission of a reviewed Family form (#1705).

Its SQL is the frozen file ``schema/migrations/0217_reviewed_ministry_resubmission.sql``
(number 0217, provisional until it is assigned in merge order), read whole
and never parsed. It replaces the Ministry request guard and the Ministry
submission effects check so a response drawn as the reviewed form makes no
request for a choice left as the form showed it, and may replace or
withdraw a decided request the form showed. It ends with a DO block that
refuses to commit unless both are installed. No model changes, so there
are no state operations. Reversing needs its own forward migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0217_reviewed_ministry_resubmission.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # This app's previous migration, so the app keeps one leaf.
        ("stewardship_workflows", "0007_ministry_terminal_undo"),
        # The migration that installs the previous frozen file (0216), so
        # the files apply in prefix order.
        ("stewardship_responses", "0027_reviewed_census_resubmission"),
    ]

    operations = [
        migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8")),
    ]

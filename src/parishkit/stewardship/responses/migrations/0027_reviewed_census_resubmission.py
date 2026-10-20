"""Forward migration: census resubmission of a reviewed Family form (#1705).

Its SQL is the frozen file ``schema/migrations/0216_reviewed_census_resubmission.sql``
(number 0216, provisional until it is assigned in merge order), read whole
and never parsed. It replaces the derived guard and the submission effects
check so a response drawn as the reviewed form makes no row for an answer
left as the form showed it, keeps the change it showed in force, and never
carries a decision to a new change. It ends with a DO block that refuses to
commit unless both are installed. No model changes, so there are no state
operations. Reversing needs its own forward migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0216_reviewed_census_resubmission.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The migration that installs the previous frozen file (0215,
        # #1840), which is also this app's previous migration, so the files
        # apply in prefix order and the app keeps one leaf.
        ("stewardship_responses", "0026_limitation_handled_undo"),
    ]

    operations = [
        migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8")),
    ]

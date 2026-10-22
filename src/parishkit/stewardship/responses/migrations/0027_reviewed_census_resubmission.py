"""Forward migration: census resubmission of a reviewed Family form (#1705).

Its SQL is the frozen file ``schema/migrations/0153_reviewed_census_resubmission.sql``
(number 0153 in the repository-wide file sequence), read whole
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
    / "0153_reviewed_census_resubmission.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # This app's previous migration (0151, #1840), so the app keeps one
        # leaf.
        ("stewardship_responses", "0026_limitation_handled_undo"),
        # The migration that installs the previous frozen file (0152,
        # #1866), so the files apply in prefix order.
        ("stewardship_audit", "0007_audit_actor_time"),
    ]

    operations = [
        migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8")),
    ]

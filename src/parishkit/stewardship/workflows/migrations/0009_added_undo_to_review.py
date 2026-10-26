"""Forward migration: undoing an added request's decision reopens it (#2173).

Its SQL is the frozen file ``schema/migrations/0180_added_undo_to_review.sql``
(prefix 0180, assigned in the repository-wide file sequence), read whole and
never parsed. It replaces the Ministry revision guard, so Back to review of
a request the parish added sends it to In progress (To review) instead of
withdrawing it, and the Ministry submission effects check, so a Family
response may leave such an open request in force (an untick never
withdraws parish work). It ends with a DO block that refuses to commit
unless both are installed with their grants, settings and triggers. No row
changes and no model changes, so there is no state operation. Reversing
needs its own forward migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0180_added_undo_to_review.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # This app's previous migration (0154's), so the app keeps one leaf;
        # it depends, transitively, on the one installing 0158, the last file
        # whose revision guard body this one replaces.
        ("stewardship_workflows", "0008_reviewed_ministry_resubmission"),
        # The migration that installs the previous frozen file (0179, the
        # definer operators, #2168), so the files apply in prefix order.
        ("stewardship_accounts", "0037_definer_operators"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]

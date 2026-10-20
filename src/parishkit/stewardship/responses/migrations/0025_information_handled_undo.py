"""Forward migration: Back to review undoes a Handled information request (#1840).

Its SQL is the frozen file ``schema/migrations/0214_information_handled_undo.sql``
(number 0214, provisional until the coordinator assigns it in merge order),
read whole and never parsed. It re-creates the information revision guard
from its latest body (0126's) so that clearing a completed follow-up, which
undoes Handled, needs a new note, and ends with a DO block that refuses to
commit unless the new body is installed. No model changes, so there are no
state operations. Reversing needs its own forward migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0214_information_handled_undo.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # This app's previous migration, so the app keeps one leaf.
        ("stewardship_responses", "0024_census_addition_undo"),
        # The migration that installs the previous frozen file (0213,
        # #1840), so the files apply in prefix order.
        ("stewardship_reports", "0038_pledge_terminal_undo"),
    ]

    operations = [
        migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8")),
    ]

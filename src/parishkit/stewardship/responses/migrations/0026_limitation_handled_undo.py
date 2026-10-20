"""Forward migration: Back to review undoes a Handled Limitations answer (#1840).

Its SQL is the frozen file ``schema/migrations/0215_limitation_handled_undo.sql``
(number 0215, provisional until the coordinator assigns it in merge order),
read whole and never parsed. It re-creates the limitation review guard from
its latest body (0126's) so that a Handled answer may take an ``accepted``
review with a note, which returns it to To handle, and ends with a DO block
that refuses to commit unless the new body is installed. No model changes,
so there are no state operations. Reversing needs its own forward migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0215_limitation_handled_undo.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The migration that installs the previous frozen file (0214), which
        # is also this app's previous migration, so the files apply in
        # prefix order and the app keeps one leaf.
        ("stewardship_responses", "0025_information_handled_undo"),
    ]

    operations = [
        migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8")),
    ]

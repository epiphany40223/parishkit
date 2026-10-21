"""Forward migration: pledge resubmission of a reviewed Family form (#1705).

Its SQL is the frozen file ``schema/migrations/0155_reviewed_pledge_resubmission.sql``
(number 0155 in the repository-wide file sequence), read whole
and never parsed. It replaces the pledge item selection so an edited pledge
a reviewed form showed, sent back as shown, keeps its decision in force. It
ends with a DO block that refuses to commit unless it is installed. No
model changes, so there are no state operations. Reversing needs its own
forward migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0155_reviewed_pledge_resubmission.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # This app's previous migration (0149's, #1840), so the app keeps
        # one leaf.
        ("stewardship_reports", "0038_pledge_terminal_undo"),
        # The migration that installs the previous frozen file (0154), so
        # the files apply in prefix order.
        ("stewardship_workflows", "0008_reviewed_ministry_resubmission"),
    ]

    operations = [
        migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8")),
    ]

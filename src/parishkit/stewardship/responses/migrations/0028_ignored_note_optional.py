"""Forward migration: reopening an Ignored item no longer needs a note (#1889).

Its SQL is the frozen file ``schema/migrations/0158_ignored_note_optional.sql``
(number 0158, reserved by the coordinator), read whole and never parsed. It
re-creates six review guards from their latest bodies (the census review and
resolution guards, and the Ministry, pledge, Additional information and
Limitations guards) with only their Ignored-note check dropped, so Back to
review from Ignored takes an optional note while every terminal step's undo
still needs one, and ends with a DO block that refuses to commit unless the
new bodies are installed with their grants, settings and triggers. No model
changes, so there are no state operations. Reversing needs its own forward
migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0158_ignored_note_optional.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # This app's previous migration (#1705's 0216), so the app keeps one
        # leaf; it depends, transitively, on the one installing 0215, the
        # last file whose guard bodies this one replaces, and so on those
        # installing 0130, 0210, 0212, 0213 and 0214.
        ("stewardship_responses", "0027_reviewed_census_resubmission"),
        # The migration that installs #1705's last frozen file (0218), so
        # this file applies after every file merged before it.
        ("stewardship_reports", "0039_reviewed_pledge_resubmission"),
    ]

    operations = [
        migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8")),
    ]

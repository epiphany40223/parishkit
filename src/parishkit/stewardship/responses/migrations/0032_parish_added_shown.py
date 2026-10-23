"""Forward migration: a parish-added census value on the reviewed form (#2008).

Its SQL is the frozen file ``schema/migrations/0170_parish_added_shown.sql``
(number 0170, reserved for this change), read whole and never parsed. It
re-creates the submission effects check and the derived guard from their
latest bodies so that a Family response drawn as the reviewed form, which
left a census change the parish added as shown (its value in the field, a
``parish`` shown item naming this Family's addition for that field, not
withdrawn), makes no census change for that field and leaves an earlier
change of the field as it is, and one that edited it makes a Family change
To review, even back to ParishSoft's value. It ends with a DO block that
refuses to commit unless both new bodies are installed with their old
grants and settings. No model changes, so there are no state operations.
Reversing needs its own forward migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0170_parish_added_shown.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # This app's previous migration (0163, #1909).
        ("stewardship_responses", "0031_edit_replacement"),
        # The migration that installs the previous frozen file (0169, the
        # Ministry leader coverage, #1594), so this file applies after
        # every earlier one.
        ("stewardship_reports", "0045_ministry_leader_coverage"),
    ]

    operations = [
        migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8")),
    ]

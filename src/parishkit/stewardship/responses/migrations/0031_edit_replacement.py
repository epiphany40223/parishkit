"""Forward migration: a later response finds a reviewer's Edit it showed (#1909).

Its SQL is the frozen file ``schema/migrations/0163_edit_replacement.sql``
(number 0163, reserved for this change), read whole and never parsed. It
re-creates the census derived guard from its latest body so that a Family
response not drawn as the reviewed form, after one that was and left a
change as shown (the Family's answer, a reviewer's Edit, or an Ignored
change shown as ParishSoft's value), finds that
change as ``effective.proposal_index`` does: as the predecessor a same
answer carries, and as the change it may supersede or withdraw. It ends
with a DO block that refuses to commit unless the new body is installed
with its old grants and settings. No model changes, so there are no state
operations. Reversing needs its own forward migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0163_edit_replacement.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The migration that installs the latest frozen file of the stack
        # (0160, #1895), this app's previous migration and the stack's one
        # leaf, so this file applies after every earlier one.
        ("stewardship_responses", "0030_resubmission_phase_order"),
    ]

    operations = [
        migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8")),
    ]

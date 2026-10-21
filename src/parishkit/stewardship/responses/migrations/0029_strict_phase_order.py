"""Forward migration: strict phase order in the review pipeline (#1890).

Its SQL is the frozen file ``schema/migrations/0159_strict_phase_order.sql``
(number 0159 in the repository-wide file sequence),
read whole and never parsed. It re-creates seven guards and selections from
their latest bodies so that a refresh confirms an item only after its
processing step (Updated in ParishSoft, Entered by hand) and Entered by hand
follows an Accept: the census resolution and derived guards, the
publication confirmation guard, the Ministry request guard, the refresh
check guard, the pledge stage selection and the pledge refresh check guard.
It ends with a DO block that refuses to commit unless the new bodies are
installed with their old grants and settings. No model changes, so there
are no state operations. Reversing needs its own forward migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0159_strict_phase_order.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The migration that installs the latest frozen file of the stack
        # (0158, #1889), which is also this app's previous migration and
        # depends on every app's latest one (#1705's reports 0039 among
        # them), so this file applies after all of them and the app keeps
        # one leaf.
        ("stewardship_responses", "0028_ignored_note_optional"),
    ]

    operations = [
        migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8")),
    ]

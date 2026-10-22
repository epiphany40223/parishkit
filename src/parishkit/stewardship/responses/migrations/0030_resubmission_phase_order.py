"""Forward migration: an unchanged resubmission keeps a held change (#1895).

Its SQL is the frozen file
``schema/migrations/0160_resubmission_phase_order.sql`` (number 0160,
reserved for this change), read whole and
never parsed. It re-creates the census derived guard and the submission
effects check from their latest bodies so that a Family that answers again
unchanged keeps an open census change ParishSoft already holds in its stage
(strict phase order), instead of resolving it upstream (Confirmed) with no
review and no step, and so that a later response finds that change on its
earlier response. It ends with a DO block that refuses to commit unless the
new bodies are installed with their old grants and settings. No model
changes, so there are no state operations. Reversing needs its own forward
migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0160_resubmission_phase_order.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The migration that installs the latest frozen file of the stack
        # (0159, #1890), this app's previous migration and the stack's one
        # leaf, so this file applies after every earlier one.
        ("stewardship_responses", "0029_strict_phase_order"),
    ]

    operations = [
        migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8")),
    ]

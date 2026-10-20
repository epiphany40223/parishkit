"""Forward migration: Back to review undoes a Ministry request's terminal step (#1840).

Its SQL is the frozen file ``schema/migrations/0212_ministry_terminal_undo.sql``
(number 0212, provisional until the coordinator assigns it in merge order),
read whole and never parsed. It replaces the Ministry request guard and
revision guard (baseline functions) and the roster entry guard (0061's) in
place, so that Back to review, with its required note and by Staff or an
Administrator only, undoes Entered in ParishSoft (the revision keeps the
decision and the tick is cleared by a paired roster entry), a Confirmed
refresh check (the same revision, which advances the request's version) and
a refresh's own resolution (back to In progress). It ends with a DO block
that refuses to commit unless the new bodies are installed with their
grants and settings unchanged.

It follows the users swap (through the previous frozen file's migration),
so it is in the swap's closure: `migrate --users-upgrade-base` never
applies it. It creates no table, column or routine, so nothing is
registered in ``runtime_grants``; the swap test runs the file again after
the files it replaces bodies of run again (``CLOSURE_REPLACED``). No model
changes, so there are no state operations. Reversing needs its own forward
migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0212_ministry_terminal_undo.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # This app's previous migration, so the app keeps one leaf.
        ("stewardship_workflows", "0006_refresh_check_conflict"),
        # The migration that installs the previous frozen file (0211,
        # #1840), so the files apply in prefix order.
        ("stewardship_responses", "0024_census_addition_undo"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]

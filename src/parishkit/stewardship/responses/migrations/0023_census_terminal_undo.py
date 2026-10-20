"""Forward migration: Back to review undoes a census change's terminal step (#1840).

Its SQL is the frozen file ``schema/migrations/0210_census_terminal_undo.sql``
(number 0210, provisional until it is assigned in merge order), read whole
and never parsed. It replaces the resolution guard (from 0139's body), the
resolution effect check (from 0060's) and the census guard (from 0138's) so
a Staff member or an Administrator may undo Entered by hand, Updated in
ParishSoft or Already in ParishSoft with a ``reopened`` resolution and a
note: the change goes back to pending with the decision behind the step
kept. It ends with a DO block that refuses to commit unless all of it is
installed. No table changes, so there is no state operation. Reversing
needs its own forward migration, so, like the baseline, it has no reverse
operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0210_census_terminal_undo.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The responses app's previous migration.
        ("stewardship_responses", "0022_reviewed_form_shown_items"),
        # The migration that installs the previous frozen file (0144), so
        # the files apply in prefix order.
        ("stewardship_reports", "0037_staff_timeline_export"),
        # The refresh check table's latest state, which the guards read.
        ("stewardship_workflows", "0006_refresh_check_conflict"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]

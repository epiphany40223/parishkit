"""Forward migration: the upcoming-year pledge a Family answered against (#1753).

Its SQL is the frozen file
``schema/migrations/0136_submission_upcoming_pledge.sql`` (prefix 0136 in the
repository-wide file sequence, provisional until the coordinator assigns it
at merge), read whole and never parsed. It adds ``upcoming_pledge`` to each
response (ParishSoft's upcoming-year pledge in its validation snapshot, or
NULL), installs the INSERT guard that checks a recorded amount against that
snapshot, and replaces 0135's pledge stage selection in place so the
financial stream reads A from the responses. It ends with a DO block that
refuses to commit unless all of it is installed. The state operation records
the column in Django's model state, so ``makemigrations --check`` stays
clean. Reversing needs its own forward migration, so, like the baseline, it
has no reverse operation.

It depends on this app's previous migration, so the app keeps one leaf, and
on the reports migration that installs the previous frozen file (0135),
whose selection it replaces, so the files apply in prefix order.
"""

from pathlib import Path

from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0136_submission_upcoming_pledge.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_responses", "0017_entered_incorrectly_fixes"),
        ("stewardship_reports", "0034_pledge_conflicts"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.AddField(
                    model_name="submission",
                    name="upcoming_pledge",
                    field=models.DecimalField(
                        decimal_places=2, max_digits=24, null=True
                    ),
                ),
            ],
        ),
    ]

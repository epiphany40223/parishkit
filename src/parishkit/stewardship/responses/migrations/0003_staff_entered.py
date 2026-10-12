"""Forward migration: mark a response Staff entered for a Family (#529).

Its SQL is the frozen file ``schema/migrations/0065_staff_entered.sql``, read
whole and never parsed. It adds the nullable ``entered_by_id`` column to
``stewardship_submission`` (the portal user who entered the response through
an Open form handoff; NULL for every Family's own response), replaces
``stewardship_safe_context_v1`` so the audit context admits the response
lists' new "Entered by Staff" sort tokens, and ends with a DO block that
refuses to commit unless both are installed as described.

It depends on the reports app's migration that installs the previous frozen
file (0064, the report local days), so the files apply in prefix order and
Django keeps a single leaf. That puts it in the users swap's closure (#952):
`migrate --users-upgrade-base` never applies it. It creates no table and no
definer routine, and the web login's table-level grants on submissions
already cover the new column, so nothing is registered in ``runtime_grants``.
The state operation records the column in Django's model state, so
``makemigrations --check`` stays clean. Reversing needs its own forward
migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0065_staff_entered.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_responses", "0002_proposal_resolution"),
        # The migration that installs the previous frozen file (0064, #558),
        # so the files apply in prefix order. It follows the users swap, so
        # `migrate --users-upgrade-base` never applies this file.
        ("stewardship_reports", "0016_report_local_days"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.AddField(
                    model_name="submission",
                    name="entered_by_id",
                    field=models.UUIDField(null=True),
                )
            ],
        )
    ]

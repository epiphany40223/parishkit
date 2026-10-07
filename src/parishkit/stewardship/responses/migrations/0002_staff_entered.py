"""Forward migration: mark a response Staff entered for a Family (#529).

Its SQL is the frozen file ``schema/migrations/0065_staff_entered.sql``, read
whole and never parsed. It adds the nullable ``entered_by_id`` column to
``stewardship_submission`` (the portal user who entered the response through
an Open form handoff; NULL for every Family's own response), replaces
``stewardship_safe_context_v1`` so the audit context admits the response
lists' new "Entered by Staff" sort tokens, and ends with a DO block that
refuses to commit unless both are installed as described.
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
        ("stewardship_responses", "0001_initial"),
        # The migration that installed the previous frozen file, so the
        # files apply in prefix order. 0050 (#933) is main's latest today;
        # 0051-0064 are reserved for PRs planned to land first, so this
        # dependency is re-chained to the migration installing 0064 when
        # this lands after them.
        ("stewardship_reports", "0012_directory_response_filters"),
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

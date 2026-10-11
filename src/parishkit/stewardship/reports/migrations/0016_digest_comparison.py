"""Forward migration: the daily digest's comparison section (#1018, slice 7).

Its SQL is the frozen file ``schema/migrations/0072_digest_comparison.sql``
(number 0072, assigned centrally), read whole and never parsed. It adds the
section's three nullable columns to ``stewardship_daily_digest_ready`` with
the check that keeps them together, adds the chart engine renderer's timeout
name and helper to ``stewardship_safe_context_v1``, and lets the general
worker's login call the two campaign comparison functions. It ends with a DO
block that refuses to commit unless all of that is installed. The state
operations record the same fields and constraint, so ``makemigrations
--check`` stays clean. Reversing needs its own forward migration, so it has
no reverse operation.

It depends on the migration that installs the latest frozen file before
it, so the files apply in prefix order. Files below 0072 may be assigned to
other pull requests that were not merged when this was written; whichever
of them merges last before this one becomes its dependency (a re-chain), so
the files still apply in prefix order and Django keeps a single leaf.
"""

from pathlib import Path

from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0072_digest_comparison.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration, which also installs the
        # latest frozen file on this branch (0071). Re-chain it onto the
        # migration that installs the latest file below 0072 before this
        # merges; see the docstring.
        ("stewardship_reports", "0015_comparison_exports"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.AddField(
                    model_name="dailydigestready",
                    name="comparison",
                    field=models.JSONField(null=True),
                ),
                migrations.AddField(
                    model_name="dailydigestready",
                    name="comparison_families_chart",
                    field=models.BinaryField(null=True),
                ),
                migrations.AddField(
                    model_name="dailydigestready",
                    name="comparison_pledges_chart",
                    field=models.BinaryField(null=True),
                ),
                migrations.AddConstraint(
                    model_name="dailydigestready",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("comparison__isnull", True),
                                ("comparison_families_chart__isnull", True),
                                ("comparison_pledges_chart__isnull", True),
                            ),
                            models.Q(
                                ("comparison__isnull", False),
                                ("comparison_families_chart__isnull", False),
                            ),
                            _connector="OR",
                        ),
                        name="daily_digest_comparison_charts",
                    ),
                ),
            ],
        ),
    ]

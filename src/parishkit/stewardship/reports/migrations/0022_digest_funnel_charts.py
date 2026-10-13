"""Forward migration: the daily digest's Response funnel charts (#477, slice 2).

Its SQL is the frozen file ``schema/migrations/0083_digest_funnel_charts.sql``
(number 0083, the next free number when the train was built), read whole
and never parsed. It adds the funnel and activity charts' two nullable PNG
columns to ``stewardship_daily_digest_ready`` with the check that keeps them
together, lets a retained comparison section have no chart images (a digest
sent with its charts withheld), and adds the withheld charts' failure words
to ``stewardship_safe_context_v1``. It ends with a DO block that refuses to
commit unless all of that is installed. The state operations record the
same fields and constraints, so ``makemigrations --check`` stays clean.
Reversing needs its own forward migration, so it has no reverse operation.

It depends on the migration that installs the previous frozen file (0075,
``stewardship_accounts.0020_campaign_series``), so the files apply in prefix
order, and on its own app's latest migration. Like every migration after the
users swap, it is in the swap's closure (#952), so web's grant on the new
``funnel_chart`` column is registered in ``runtime_grants.CLOSURE_COLUMNS``
and given only once it is applied.
"""

from pathlib import Path

from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0083_digest_funnel_charts.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # Its own app's latest migration (0072's file, #1018 slice 7).
        ("stewardship_reports", "0021_digest_comparison"),
        # The migration that installs the previous frozen file (0082),
        # so the files apply in prefix order.
        ("stewardship_accounts", "0021_branding_bundle_mode"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.AddField(
                    model_name="dailydigestready",
                    name="funnel_chart",
                    field=models.BinaryField(null=True),
                ),
                migrations.AddField(
                    model_name="dailydigestready",
                    name="activity_chart",
                    field=models.BinaryField(null=True),
                ),
                migrations.AddConstraint(
                    model_name="dailydigestready",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("activity_chart__isnull", True),
                                ("funnel_chart__isnull", True),
                            ),
                            models.Q(
                                ("activity_chart__isnull", False),
                                ("funnel_chart__isnull", False),
                            ),
                            _connector="OR",
                        ),
                        name="daily_digest_funnel_charts",
                    ),
                ),
                migrations.RemoveConstraint(
                    model_name="dailydigestready",
                    name="daily_digest_comparison_charts",
                ),
                migrations.AddConstraint(
                    model_name="dailydigestready",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("comparison__isnull", False),
                                models.Q(
                                    ("comparison_families_chart__isnull", True),
                                    ("comparison_pledges_chart__isnull", True),
                                ),
                                _connector="OR",
                            ),
                            models.Q(
                                ("comparison_pledges_chart__isnull", True),
                                ("comparison_families_chart__isnull", False),
                                _connector="OR",
                            ),
                        ),
                        name="daily_digest_comparison_charts",
                    ),
                ),
            ],
        ),
    ]

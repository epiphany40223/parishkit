"""Forward migration: a report send skips already-accepted recipients (#1835).

Its SQL is the frozen file
``schema/migrations/0162_report_recovery_coverage.sql``, read whole and
never parsed. It adds ``covered_messages`` and the
``covered`` disposition to the report email recipient, the coverage
comparison ``stewardship_report_email_prior_v1``, and replaces frozen file
0156's recipient guard and 0161's completion view so a covered recipient is
admitted only with exactly its accepted messages and settles as delivered.
It ends with a DO block that refuses to commit unless all of it is installed
as declared. The state operations record the same field and check, so
``makemigrations --check`` stays clean. Reversing needs its own forward
migration.

It follows the users swap (#952), as 0156's migration does.
"""

from pathlib import Path

from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0162_report_recovery_coverage.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration, the report conversion
        # (#1839, earlier in the report email stack), which installs the
        # previous frozen file (0161), so the files apply in prefix order
        # and 0161's completion view is the one this file replaces.
        ("stewardship_reports", "0041_report_conversion"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="reportemailrecipient",
                    name="report_email_recipient_disposition",
                ),
                migrations.AddField(
                    model_name="reportemailrecipient",
                    name="covered_messages",
                    field=models.JSONField(default=list),
                ),
                migrations.AddConstraint(
                    model_name="reportemailrecipient",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("covered_messages", []),
                                ("disposition", "queued"),
                                ("outbox__isnull", False),
                            ),
                            models.Q(
                                ("covered_messages", []),
                                ("disposition", "revoked"),
                                ("outbox__isnull", True),
                            ),
                            models.Q(
                                ("disposition", "covered"),
                                ("outbox__isnull", True),
                                models.Q(("covered_messages", []), _negated=True),
                            ),
                            _connector="OR",
                        ),
                        name="report_email_recipient_coverage",
                    ),
                ),
            ],
        ),
    ]

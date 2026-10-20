"""Forward migration: show and settle a report send's wait (#1872).

Its SQL is the frozen file
``schema/migrations/0260_report_coverage_decisions.sql`` (prefix 0260,
provisional until the repository-wide file sequence assigns the final
number), read whole and never parsed. It creates the Administrator's
coverage decision about an earlier report message that was once unknown and
then cancelled, with its insert guard, replaces frozen file 0250's coverage
comparison so it reads those decisions and names what an uncertain answer
waits on (the read of the sends that wait is 0037's, frozen file 0261). It
ends with a DO block that refuses to commit unless all of it is installed
as declared. The state operation records the same model, so
``makemigrations --check`` stays clean. Reversing needs its own forward
migration.

It follows the users swap (#952), as 0156's and 0250's migrations do.
"""

import uuid
from pathlib import Path

import django.db.models.functions.datetime
from django.db import migrations, models

import parishkit.stewardship.observability
import parishkit.stewardship.storage

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0260_report_coverage_decisions.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration, which installs the previous
        # frozen file (0250, provisional), so the files apply in prefix order.
        ("stewardship_reports", "0035_report_recovery_coverage"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="ReportEmailCoverageDecision",
                    fields=[
                        (
                            "id",
                            models.UUIDField(
                                default=uuid.uuid4,
                                editable=False,
                                primary_key=True,
                                serialize=False,
                            ),
                        ),
                        (
                            "created_at",
                            parishkit.stewardship.storage.UTCDateTimeField(
                                db_default=django.db.models.functions.datetime.Now(),
                                editable=False,
                            ),
                        ),
                        (
                            "actor_id",
                            models.UUIDField(blank=True, editable=False, null=True),
                        ),
                        (
                            "correlation_id",
                            models.UUIDField(
                                db_index=True,
                                default=parishkit.stewardship.observability.current_correlation,
                                editable=False,
                            ),
                        ),
                        ("message_id", models.UUIDField(unique=True)),
                        ("decision", models.CharField(max_length=16)),
                    ],
                    options={
                        "db_table": "stewardship_report_email_coverage_decision",
                        "constraints": [
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("decision__in", ("delivered", "unsent"))
                                ),
                                name="report_email_coverage_decision_known",
                            )
                        ],
                    },
                ),
            ],
        ),
    ]

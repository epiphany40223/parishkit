"""Forward migration: Send a test to me for report emails (#2036).

Its SQL is the frozen file ``schema/migrations/0173_report_email_test.sql``
(prefix 0173 in the repository-wide file sequence), read whole and never
parsed. It creates the report email test request table with its guards
and settlement, the test's outbox write, render and dispatch checks, adds
``report_email_test`` to the two task-type maps, and replaces the outbox
insert gate, mail dispatch's write gate, the provider result check and the
delivery resolution guard with one more owner each. It ends with a DO
block that refuses to commit unless all of it is installed as declared.
The state operation records the model in Django's model state, so
``makemigrations --check`` stays clean. Reversing needs its own forward
migration.

It follows the users swap (#952), so its table is granted only once it is
applied (runtime_grants.CLOSURE_TABLES).
"""

import uuid
from pathlib import Path

import django.db.models.deletion
import django.db.models.functions.datetime
from django.db import migrations, models

import parishkit.stewardship.observability
import parishkit.stewardship.storage

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0173_report_email_test.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration (0171, the digest
        # retirement, #1834), so the app keeps one leaf. It follows the
        # users swap, so `migrate --users-upgrade-base` never applies it.
        ("stewardship_reports", "0046_retire_digests"),
        ("stewardship_accounts", "0034_setup_test_binding"),
        # The migration that installs the previous frozen file (0172, the
        # Family campaign statistics, #1932), so the files apply in prefix
        # order.
        ("stewardship_campaigns", "0018_family_statistics"),
        ("stewardship_jobs", "0037_report_conversion"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="ReportEmailTest",
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
                        (
                            "updated_at",
                            parishkit.stewardship.storage.UTCDateTimeField(
                                db_default=django.db.models.functions.datetime.Now(),
                                editable=False,
                            ),
                        ),
                        (
                            "version",
                            models.PositiveBigIntegerField(default=1, editable=False),
                        ),
                        ("definition_id", models.UUIDField(null=True)),
                        ("requested_by_id", models.UUIDField()),
                        ("mode", models.CharField(max_length=16)),
                        (
                            "testing_recipient",
                            models.CharField(max_length=254, null=True),
                        ),
                        ("address", models.CharField(max_length=254)),
                        ("subject", models.CharField(max_length=254)),
                        ("html", models.TextField()),
                        ("text", models.TextField()),
                        ("images", models.JSONField()),
                        (
                            "state",
                            models.CharField(
                                db_default="queued", default="queued", max_length=16
                            ),
                        ),
                        (
                            "campaign",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                to="stewardship_campaigns.campaign",
                            ),
                        ),
                        (
                            "configuration",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                to="stewardship_accounts.appliedconfigurationversion",
                            ),
                        ),
                        (
                            "task",
                            models.OneToOneField(
                                on_delete=django.db.models.deletion.PROTECT,
                                to="stewardship_jobs.taskrun",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_report_email_test",
                        "abstract": False,
                        "constraints": [
                            models.CheckConstraint(
                                condition=models.Q(("version__gte", 1)),
                                name="stewardship_reports_reportemailtest_positive_version",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    models.Q(
                                        ("mode", "production"),
                                        ("testing_recipient", None),
                                    ),
                                    models.Q(
                                        ("mode", "testing"),
                                        ("testing_recipient__isnull", False),
                                    ),
                                    _connector="OR",
                                ),
                                name="report_email_test_mode",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("state__in", ("queued", "settled"))
                                ),
                                name="report_email_test_known_state",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("state", "queued"),
                                    models.Q(
                                        ("html", ""),
                                        ("images", []),
                                        ("subject", ""),
                                        ("text", ""),
                                    ),
                                    _connector="OR",
                                ),
                                name="report_email_test_scrubbed",
                            ),
                            models.UniqueConstraint(
                                condition=models.Q(("state", "queued")),
                                fields=("requested_by_id", "definition_id"),
                                name="report_email_test_pending",
                                nulls_distinct=False,
                            ),
                        ],
                    },
                ),
            ],
        )
    ]

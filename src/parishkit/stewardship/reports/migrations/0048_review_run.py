"""Forward migration: the review wizard's runs (#2123).

Its SQL is the frozen file ``schema/migrations/0178_review_run.sql``, read
whole and never parsed. It creates ``stewardship_review_run`` (one row per
review wizard run) and the guard
that keeps a started run's sequence fixed. It ends with a DO block that
refuses to commit unless all of it is installed as declared. The state
operation records the same model, so ``makemigrations --check`` stays
clean. Reversing needs its own forward migration.

It follows the users swap (#952), as the reports app's other migrations do.
"""

import uuid
from pathlib import Path

import django.db.models.deletion
import django.db.models.functions.datetime
from django.db import migrations, models

import parishkit.stewardship.storage

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0178_review_run.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0034_setup_test_binding"),
        # The accounts migration that installs the previous frozen file
        # (0177, the setup load check as definer, #2163), so the files apply
        # in prefix order.
        ("stewardship_accounts", "0036_setup_load_definer"),
        # The reports app's previous migration (0173).
        ("stewardship_reports", "0047_report_email_test"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="ReviewRun",
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
                        ("stream", models.CharField(max_length=16)),
                        ("filters", models.JSONField(default=dict)),
                        ("sort", models.CharField(max_length=8)),
                        ("subjects", models.JSONField(default=list)),
                        ("position", models.PositiveIntegerField(default=1)),
                        (
                            "finished_at",
                            parishkit.stewardship.storage.UTCDateTimeField(
                                blank=True, null=True
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
                            "updated_at",
                            parishkit.stewardship.storage.UTCDateTimeField(
                                db_default=django.db.models.functions.datetime.Now()
                            ),
                        ),
                        (
                            "campaign",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="+",
                                to="stewardship_campaigns.campaign",
                            ),
                        ),
                        (
                            "user",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="+",
                                to="stewardship_accounts.portaluser",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_review_run",
                        "indexes": [
                            models.Index(
                                fields=["user", "campaign"], name="review_run_reader"
                            )
                        ],
                        "constraints": [
                            models.CheckConstraint(
                                condition=models.Q(
                                    (
                                        "stream__in",
                                        (
                                            "census",
                                            "ministry",
                                            "pledges",
                                            "information",
                                            "limitations",
                                        ),
                                    )
                                ),
                                name="review_run_stream",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("sort__in", ("name", "oldest", "newest"))
                                ),
                                name="review_run_sort",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(("position__gte", 1)),
                                name="review_run_position",
                            ),
                        ],
                    },
                ),
            ],
        ),
    ]

"""Forward migration: the chosen-Family test names export (#817).

Its SQL is the frozen file ``schema/migrations/0037_family_test_names.sql``
(prefix 0037, assigned in the repository-wide file sequence), read whole and
never parsed. It creates ``stewardship_family_test_names_export_snapshot``
with its capture and binding guards, adds the request's
``family_test_names_snapshot_id`` with its key and index, widens
``export_report_known`` with the ``family_test_names`` kind, and replaces the
request and publication guards with the baseline's bodies (``exports.sql``)
plus one branch each for the new kind. It ends with a DO block that refuses
to commit unless all of it is installed. The state operations record the
same model changes in Django's state, so ``makemigrations --check`` stays
clean. Reversing needs its own forward migration, so, like the baseline, it
has no reverse operation.
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
    / "0037_family_test_names.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0009_setup_without_campaign"),
        ("stewardship_campaigns", "0008_workgroup_recovery_skip"),
        # The reports app's previous migration.
        ("stewardship_reports", "0005_directory_member_search"),
        # The migration that installed the previous frozen file (0036,
        # #462), so the files apply in prefix order.
        ("stewardship_jobs", "0018_go_live_log_events"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="FamilyTestNamesSnapshot",
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
                        ("actor_id", models.UUIDField(editable=False)),
                        ("correlation_id", models.UUIDField(editable=False)),
                        ("parameters", models.JSONField()),
                        ("document", models.JSONField()),
                        ("row_count", models.PositiveIntegerField()),
                    ],
                    options={
                        "db_table": "stewardship_family_test_names_export_snapshot",
                    },
                ),
                migrations.RemoveConstraint(
                    model_name="exportrequest",
                    name="export_report_known",
                ),
                migrations.AddField(
                    model_name="familytestnamessnapshot",
                    name="campaign",
                    field=models.ForeignKey(
                        db_index=False,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_campaigns.campaign",
                    ),
                ),
                migrations.AddField(
                    model_name="familytestnamessnapshot",
                    name="configuration",
                    field=models.ForeignKey(
                        db_index=False,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_accounts.appliedconfigurationversion",
                    ),
                ),
                migrations.AddField(
                    model_name="exportrequest",
                    name="family_test_names_snapshot",
                    field=models.ForeignKey(
                        db_index=False,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_reports.familytestnamessnapshot",
                    ),
                ),
                migrations.AddIndex(
                    model_name="exportrequest",
                    index=models.Index(
                        fields=["family_test_names_snapshot"],
                        name="export_family_test_names",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="exportrequest",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("directory_snapshot__isnull", True),
                                ("fact_set__isnull", False),
                                ("family_test_names_snapshot__isnull", True),
                                ("financial_snapshot__isnull", True),
                                ("information_snapshot__isnull", True),
                                ("ministry_snapshot__isnull", True),
                                ("report", "participation"),
                            ),
                            models.Q(
                                ("directory_snapshot__isnull", True),
                                ("fact_set__isnull", True),
                                ("family_test_names_snapshot__isnull", True),
                                ("financial_snapshot__isnull", True),
                                ("format__in", ("csv", "xlsx", "pdf")),
                                ("information_snapshot__isnull", False),
                                ("ministry_snapshot__isnull", True),
                                ("report", "additional_information"),
                            ),
                            models.Q(
                                ("directory_snapshot__isnull", False),
                                ("fact_set__isnull", True),
                                ("family_test_names_snapshot__isnull", True),
                                ("financial_snapshot__isnull", True),
                                ("format__in", ("csv", "xlsx", "pdf")),
                                ("information_snapshot__isnull", True),
                                ("ministry_snapshot__isnull", True),
                                ("report__in", ("family_directory", "postal_outreach")),
                            ),
                            models.Q(
                                ("directory_snapshot__isnull", True),
                                ("fact_set__isnull", True),
                                ("family_test_names_snapshot__isnull", True),
                                ("financial_snapshot__isnull", True),
                                ("format__in", ("csv", "xlsx", "pdf")),
                                ("information_snapshot__isnull", True),
                                ("ministry_snapshot__isnull", False),
                                ("report", "ministry"),
                            ),
                            models.Q(
                                ("directory_snapshot__isnull", True),
                                ("fact_set__isnull", True),
                                ("family_test_names_snapshot__isnull", True),
                                ("financial_snapshot__isnull", False),
                                ("format__in", ("csv", "xlsx", "pdf")),
                                ("information_snapshot__isnull", True),
                                ("ministry_snapshot__isnull", True),
                                ("report", "financial"),
                            ),
                            models.Q(
                                ("directory_snapshot__isnull", True),
                                ("fact_set__isnull", True),
                                ("family_test_names_snapshot__isnull", False),
                                ("financial_snapshot__isnull", True),
                                ("format", "csv"),
                                ("information_snapshot__isnull", True),
                                ("ministry_snapshot__isnull", True),
                                ("report", "family_test_names"),
                            ),
                            _connector="OR",
                        ),
                        name="export_report_known",
                    ),
                ),
                migrations.AddIndex(
                    model_name="familytestnamessnapshot",
                    index=models.Index(
                        fields=["correlation_id"], name="family_test_names_correlation"
                    ),
                ),
                migrations.AddIndex(
                    model_name="familytestnamessnapshot",
                    index=models.Index(
                        fields=["campaign"], name="family_test_names_campaign"
                    ),
                ),
                migrations.AddIndex(
                    model_name="familytestnamessnapshot",
                    index=models.Index(
                        fields=["configuration"], name="family_test_names_config"
                    ),
                ),
            ],
        ),
    ]

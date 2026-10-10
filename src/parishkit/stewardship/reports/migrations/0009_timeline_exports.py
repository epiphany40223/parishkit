"""Forward migration: the Family timeline export kind (ADM-11 PR 8g, #463).

Its SQL is the frozen file ``schema/migrations/0044_timeline_exports.sql``
(number 0044, assigned centrally), read whole and never parsed. It creates
the immutable ``stewardship_timeline_export_snapshot`` with its capture and
binding triggers, adds ``timeline_snapshot_id`` to
``stewardship_export_request`` with its foreign key and index, re-adds
``export_report_known`` with a ``family_timeline`` branch, and re-creates
the export request guard, the publication guard and the request
authorization with only the timeline branch added. It ends with a DO block
that refuses to commit unless all of that is installed. The state operations
record the same model, field, constraint and indexes, so
``makemigrations --check`` stays clean. Reversing needs its own forward
migration, so it has no reverse operation.

It depends on the migration that installs the latest frozen file before it,
so the files apply in prefix order. Its name and number in this app are
assigned when it merges.
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
    / "0044_timeline_exports.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0004_automation_sessions"),
        ("stewardship_campaigns", "0003_occurrence_prepare_ahead"),
        # The reports app's previous migration.
        ("stewardship_reports", "0008_ministry_report_actor_scope"),
        # The migration that installed the previous frozen file (0041 on
        # main when this was written; re-point it at 0042's or 0043's
        # migration if either merges first), so the files apply in prefix
        # order.
        ("stewardship_jobs", "0020_task_type_creators"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="TimelineExportSnapshot",
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
                        ("document", models.JSONField(default=dict)),
                        ("row_count", models.PositiveIntegerField(default=0)),
                    ],
                    options={
                        "db_table": "stewardship_timeline_export_snapshot",
                    },
                ),
                migrations.RemoveConstraint(
                    model_name="exportrequest",
                    name="export_report_known",
                ),
                migrations.AddField(
                    model_name="timelineexportsnapshot",
                    name="campaign",
                    field=models.ForeignKey(
                        db_index=False,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_campaigns.campaign",
                    ),
                ),
                migrations.AddField(
                    model_name="timelineexportsnapshot",
                    name="configuration",
                    field=models.ForeignKey(
                        db_index=False,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_accounts.appliedconfigurationversion",
                    ),
                ),
                migrations.AddField(
                    model_name="timelineexportsnapshot",
                    name="family",
                    field=models.ForeignKey(
                        db_index=False,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_campaigns.familycampaign",
                    ),
                ),
                migrations.AddField(
                    model_name="exportrequest",
                    name="timeline_snapshot",
                    field=models.ForeignKey(
                        db_index=False,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_reports.timelineexportsnapshot",
                    ),
                ),
                migrations.AddIndex(
                    model_name="exportrequest",
                    index=models.Index(
                        fields=["timeline_snapshot"], name="export_timeline_snapshot"
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
                                ("timeline_snapshot__isnull", True),
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
                                ("timeline_snapshot__isnull", True),
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
                                ("timeline_snapshot__isnull", True),
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
                                ("timeline_snapshot__isnull", True),
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
                                ("timeline_snapshot__isnull", True),
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
                                ("timeline_snapshot__isnull", True),
                            ),
                            models.Q(
                                ("directory_snapshot__isnull", True),
                                ("fact_set__isnull", True),
                                ("family_test_names_snapshot__isnull", True),
                                ("financial_snapshot__isnull", True),
                                ("format__in", ("csv", "xlsx", "pdf")),
                                ("information_snapshot__isnull", True),
                                ("ministry_snapshot__isnull", True),
                                ("report", "family_timeline"),
                                ("timeline_snapshot__isnull", False),
                            ),
                            _connector="OR",
                        ),
                        name="export_report_known",
                    ),
                ),
                migrations.AddIndex(
                    model_name="timelineexportsnapshot",
                    index=models.Index(
                        fields=["correlation_id"], name="timeline_export_correlation"
                    ),
                ),
                migrations.AddIndex(
                    model_name="timelineexportsnapshot",
                    index=models.Index(
                        fields=["campaign"], name="timeline_export_campaign"
                    ),
                ),
                migrations.AddIndex(
                    model_name="timelineexportsnapshot",
                    index=models.Index(
                        fields=["family"], name="timeline_export_family"
                    ),
                ),
                migrations.AddIndex(
                    model_name="timelineexportsnapshot",
                    index=models.Index(
                        fields=["configuration"], name="timeline_export_config"
                    ),
                ),
            ],
        ),
    ]

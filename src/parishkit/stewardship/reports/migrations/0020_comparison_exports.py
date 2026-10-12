"""Forward migration: the Campaign comparison downloads (#1018, slice 6).

Its SQL is the frozen file ``schema/migrations/0071_comparison_exports.sql``
(number 0071, assigned centrally), read whole and never parsed. It creates
the immutable ``stewardship_comparison_export_snapshot`` with its capture
and binding triggers, adds ``comparison_snapshot_id`` to
``stewardship_export_request`` with its foreign key and index, re-adds
``export_report_known`` with a ``campaign_comparison`` branch, and
re-creates the export request guard and the publication guard with only
the comparison branch added. It ends with a DO block that refuses to commit
unless all of that is installed. The state operations record the same
model, field, constraint and indexes, so ``makemigrations --check`` stays
clean. Reversing needs its own forward migration, so it has no reverse
operation.

It depends on the migration that installs the previous frozen file
(0070, ``stewardship_accounts.0018_leader_changes``, #1034) and on the
reports app's previous migration (0019), so the files apply in prefix order
and Django keeps a single leaf. That puts it in the users swap's closure
(#952): `migrate --users-upgrade-base` never applies it, so its definer
functions are registered in ``runtime_grants.CLOSURE_FUNCTIONS`` and granted
only once it is applied, and the users swap test puts 0063's export guards
and ``export_report_known`` back while the swap is pending
(``before_comparison_exports``).
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
    / "0071_comparison_exports.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration (0069, #932).
        ("stewardship_reports", "0019_member_names_last_first"),
        # The migration that installs the previous frozen file (0070, #1034),
        # so the files apply in prefix order. It follows the users swap, so
        # `migrate --users-upgrade-base` never applies this file.
        ("stewardship_accounts", "0018_leader_changes"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="ComparisonExportSnapshot",
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
                        "db_table": "stewardship_comparison_export_snapshot",
                    },
                ),
                migrations.RemoveConstraint(
                    model_name="exportrequest",
                    name="export_report_known",
                ),
                migrations.AddField(
                    model_name="comparisonexportsnapshot",
                    name="campaign",
                    field=models.ForeignKey(
                        db_index=False,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_campaigns.campaign",
                    ),
                ),
                migrations.AddField(
                    model_name="comparisonexportsnapshot",
                    name="configuration",
                    field=models.ForeignKey(
                        db_index=False,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_accounts.appliedconfigurationversion",
                    ),
                ),
                migrations.AddField(
                    model_name="exportrequest",
                    name="comparison_snapshot",
                    field=models.ForeignKey(
                        db_index=False,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_reports.comparisonexportsnapshot",
                    ),
                ),
                migrations.AddIndex(
                    model_name="exportrequest",
                    index=models.Index(
                        fields=["comparison_snapshot"],
                        name="export_comparison_snapshot",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="exportrequest",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("comparison_snapshot__isnull", True),
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
                                ("comparison_snapshot__isnull", True),
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
                                ("comparison_snapshot__isnull", True),
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
                                ("comparison_snapshot__isnull", True),
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
                                ("comparison_snapshot__isnull", True),
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
                                ("comparison_snapshot__isnull", True),
                                ("directory_snapshot__isnull", True),
                                ("fact_set__isnull", True),
                                ("family_test_names_snapshot__isnull", False),
                                ("financial_snapshot__isnull", True),
                                ("format__in", ("csv", "xlsx", "pdf")),
                                ("information_snapshot__isnull", True),
                                ("ministry_snapshot__isnull", True),
                                ("report", "family_test_names"),
                                ("timeline_snapshot__isnull", True),
                            ),
                            models.Q(
                                ("comparison_snapshot__isnull", True),
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
                            models.Q(
                                ("comparison_snapshot__isnull", False),
                                ("directory_snapshot__isnull", True),
                                ("fact_set__isnull", True),
                                ("family_test_names_snapshot__isnull", True),
                                ("financial_snapshot__isnull", True),
                                ("format__in", ("csv", "xlsx", "pdf")),
                                ("information_snapshot__isnull", True),
                                ("ministry_snapshot__isnull", True),
                                ("report", "campaign_comparison"),
                                ("timeline_snapshot__isnull", True),
                            ),
                            _connector="OR",
                        ),
                        name="export_report_known",
                    ),
                ),
                migrations.AddIndex(
                    model_name="comparisonexportsnapshot",
                    index=models.Index(
                        fields=["correlation_id"], name="comparison_export_correlation"
                    ),
                ),
                migrations.AddIndex(
                    model_name="comparisonexportsnapshot",
                    index=models.Index(
                        fields=["campaign"], name="comparison_export_campaign"
                    ),
                ),
                migrations.AddIndex(
                    model_name="comparisonexportsnapshot",
                    index=models.Index(
                        fields=["configuration"], name="comparison_export_config"
                    ),
                ),
            ],
        ),
    ]

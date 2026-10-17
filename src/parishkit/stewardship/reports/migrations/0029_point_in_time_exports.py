"""Forward migration: exports are point-in-time (#1398).

Its SQL is the frozen file
``schema/migrations/0109_point_in_time_exports.sql`` (number 0109, assigned
centrally), read whole and never parsed. It creates
``stewardship_export_discard``, the durable record of each ended export whose
saved data was discarded, with the SQL that decides when an export has ended
(its window from ``reports.export_window``) and the owner-run discard
functions the scheduler calls; it makes export file cleanup and fact
compaction use the computed end, lets an ended export release its pins and its
link to its fact generation (so the resolution's ``fact_set`` becomes nullable
and ``export_report_known`` no longer requires the request's), adds the
``report`` and ``format`` audit keys, and discards, at deploy, every export
that has already ended. It ends with a DO block that refuses to commit unless
all of it is installed. The state operations record the new model and the two
relaxed declarations. Reversing needs its own forward migration, so, like the
baseline, it has no reverse operation.
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
    / "0109_point_in_time_exports.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_reports", "0028_information_status_counts"),
        # The migration that installs the previous frozen file (0108), so
        # the files apply in prefix order.
        ("stewardship_responses", "0009_conflict_resolution"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="ExportDiscard",
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
                        ("documents", models.PositiveSmallIntegerField()),
                        ("pins", models.PositiveIntegerField()),
                        ("by_deploy", models.BooleanField()),
                        (
                            "exact_request",
                            models.OneToOneField(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.PROTECT,
                                to="stewardship_reports.exactexportrequest",
                            ),
                        ),
                        (
                            "request",
                            models.OneToOneField(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.PROTECT,
                                to="stewardship_reports.exportrequest",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_export_discard",
                        "constraints": [
                            models.CheckConstraint(
                                condition=models.Q(
                                    models.Q(
                                        ("exact_request__isnull", True),
                                        ("request__isnull", False),
                                    ),
                                    models.Q(
                                        ("exact_request__isnull", False),
                                        ("request__isnull", True),
                                    ),
                                    _connector="OR",
                                ),
                                name="export_discard_one_request",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(("documents__lte", 1)),
                                name="export_discard_documents",
                            ),
                        ],
                    },
                ),
                # A discard clears the link to the generation an ended
                # export used (the frozen file's ALTERs).
                migrations.RemoveConstraint(
                    model_name="exportrequest",
                    name="export_report_known",
                ),
                migrations.AlterField(
                    model_name="exactexportresolution",
                    name="fact_set",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_reports.campaigndailyfactset",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="exportrequest",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("comparison_snapshot__isnull", True),
                                ("directory_snapshot__isnull", True),
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
                            models.Q(
                                ("comparison_snapshot__isnull", True),
                                ("directory_snapshot__isnull", True),
                                ("family_test_names_snapshot__isnull", True),
                                ("financial_snapshot__isnull", True),
                                ("format", "xlsx"),
                                ("information_snapshot__isnull", True),
                                ("ministry_snapshot__isnull", True),
                                ("report", "campaign_archive"),
                                ("timeline_snapshot__isnull", True),
                            ),
                            _connector="OR",
                        ),
                        name="export_report_known",
                    ),
                ),
            ],
        )
    ]

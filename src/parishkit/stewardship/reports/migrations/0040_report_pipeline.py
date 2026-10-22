"""Forward migration: the report email pipeline (#1786, delivery plan item 3).

Its SQL is the frozen file ``schema/migrations/0156_report_pipeline.sql``
(prefix 0156 in the repository-wide file sequence), read whole and never
parsed. It creates the report email manual request, preparation, snapshot
and recipient tables with
their guards, write admissions, slot evaluation, page proof, reads and
completion checks, and adds ``report_email_prepare`` to the two task-type
maps. It ends with a DO block that refuses to commit unless all of it is
installed as declared. The state operations record the four models in
Django's model state, so ``makemigrations --check`` stays clean. Reversing
needs its own forward migration.

It follows the users swap (#952), so its tables are granted only once it is
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
    / "0156_report_pipeline.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration, which also installs the
        # previous frozen file (0155, #1705), so the app keeps one leaf and
        # the files apply in prefix order. It follows the users swap, so
        # `migrate --users-upgrade-base` never applies it.
        ("stewardship_reports", "0039_reviewed_pledge_resubmission"),
        ("stewardship_campaigns", "0014_digest_skip_dates"),
        ("stewardship_jobs", "0035_leader_emails"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="ReportEmailManualRequest",
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
                        ("definition_id", models.UUIDField()),
                        ("revision_id", models.UUIDField()),
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
                        "db_table": "stewardship_report_email_manual_request",
                    },
                ),
                migrations.CreateModel(
                    name="ReportEmailPreparation",
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
                        ("campaign_id", models.UUIDField()),
                        ("definition_id", models.UUIDField()),
                        ("revision_id", models.UUIDField()),
                        ("campaign_configuration_id", models.UUIDField()),
                        ("task_id", models.UUIDField(unique=True)),
                        ("mode", models.CharField(max_length=16)),
                        ("rehearsal_epoch_id", models.UUIDField(null=True)),
                        ("cutoff", parishkit.stewardship.storage.UTCDateTimeField()),
                        ("cursor", models.DateField(null=True)),
                        ("phase", models.CharField(default="dates", max_length=12)),
                        ("occurrence_id", models.UUIDField(null=True, unique=True)),
                        ("run_id", models.UUIDField(null=True)),
                        ("task_fence", models.PositiveBigIntegerField(null=True)),
                        ("worker_id", models.UUIDField(null=True)),
                        ("covered_slots", models.JSONField(null=True)),
                        ("as_of_kind", models.CharField(max_length=16, null=True)),
                        (
                            "as_of",
                            parishkit.stewardship.storage.UTCDateTimeField(null=True),
                        ),
                        (
                            "captured_at",
                            parishkit.stewardship.storage.UTCDateTimeField(null=True),
                        ),
                        ("report_day", models.DateField(null=True)),
                        ("source_id", models.UUIDField(null=True)),
                        (
                            "submission_watermark",
                            models.PositiveBigIntegerField(null=True),
                        ),
                        ("timezone_configuration_id", models.UUIDField(null=True)),
                        ("through_date", models.DateField(null=True)),
                    ],
                    options={
                        "db_table": "stewardship_report_email_preparation",
                        "abstract": False,
                        "indexes": [
                            models.Index(
                                fields=["definition_id", "mode"],
                                name="report_email_definition",
                            )
                        ],
                        "constraints": [
                            models.CheckConstraint(
                                condition=models.Q(("version__gte", 1)),
                                name="stewardship_reports_reportemailpreparation_positive_version",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    (
                                        "phase__in",
                                        (
                                            "dates",
                                            "cover",
                                            "inputs",
                                            "capture",
                                            "fanout",
                                            "complete",
                                            "cancelled",
                                        ),
                                    )
                                ),
                                name="report_email_phase",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    models.Q(
                                        ("mode", "production"),
                                        ("rehearsal_epoch_id__isnull", True),
                                    ),
                                    models.Q(
                                        ("mode", "testing"),
                                        ("rehearsal_epoch_id__isnull", False),
                                    ),
                                    _connector="OR",
                                ),
                                name="report_email_namespace",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    models.Q(
                                        ("as_of__isnull", True),
                                        ("as_of_kind__isnull", True),
                                        ("captured_at__isnull", True),
                                        ("covered_slots__isnull", True),
                                        ("report_day__isnull", True),
                                        ("source_id__isnull", True),
                                        ("submission_watermark__isnull", True),
                                        ("through_date__isnull", True),
                                        ("timezone_configuration_id__isnull", True),
                                    ),
                                    models.Q(
                                        ("as_of__isnull", False),
                                        (
                                            "as_of_kind__in",
                                            ("previous_day_end", "send_time"),
                                        ),
                                        ("captured_at__isnull", False),
                                        ("covered_slots__isnull", False),
                                        ("source_id__isnull", False),
                                        ("submission_watermark__isnull", False),
                                        ("timezone_configuration_id__isnull", False),
                                    ),
                                    _connector="OR",
                                ),
                                name="report_email_inputs",
                            ),
                        ],
                    },
                ),
                migrations.CreateModel(
                    name="ReportEmailSnapshot",
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
                        ("values", models.JSONField()),
                        ("blocks", models.JSONField()),
                        ("has_content", models.BooleanField()),
                        ("recipients", models.JSONField()),
                        ("subject", models.CharField(max_length=254)),
                        ("html", models.TextField()),
                        ("text", models.TextField()),
                        ("images", models.JSONField()),
                        ("fence", models.PositiveBigIntegerField()),
                        ("worker_id", models.UUIDField()),
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
                            "fact_set",
                            models.ForeignKey(
                                null=True,
                                on_delete=django.db.models.deletion.PROTECT,
                                to="stewardship_reports.campaigndailyfactset",
                            ),
                        ),
                        (
                            "preparation",
                            models.OneToOneField(
                                on_delete=django.db.models.deletion.PROTECT,
                                to="stewardship_reports.reportemailpreparation",
                            ),
                        ),
                        (
                            "run",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                to="stewardship_jobs.taskrun",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_report_email_snapshot",
                    },
                ),
                migrations.CreateModel(
                    name="ReportEmailRecipient",
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
                        ("address", models.CharField(max_length=254)),
                        ("kind", models.CharField(max_length=8)),
                        ("ref", models.CharField(max_length=64, null=True)),
                        ("disposition", models.CharField(max_length=8)),
                        (
                            "outbox",
                            models.OneToOneField(
                                null=True,
                                on_delete=django.db.models.deletion.PROTECT,
                                to="stewardship_jobs.outboxmessage",
                            ),
                        ),
                        (
                            "snapshot",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                to="stewardship_reports.reportemailsnapshot",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_report_email_recipient",
                        "constraints": [
                            models.UniqueConstraint(
                                fields=("snapshot", "address"),
                                name="report_email_recipient_once",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("kind__in", ("user", "group", "outside"))
                                ),
                                name="report_email_recipient_kind",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    models.Q(
                                        ("disposition", "queued"),
                                        ("outbox__isnull", False),
                                    ),
                                    models.Q(
                                        ("disposition", "revoked"),
                                        ("outbox__isnull", True),
                                    ),
                                    _connector="OR",
                                ),
                                name="report_email_recipient_disposition",
                            ),
                        ],
                    },
                ),
            ],
        ),
    ]

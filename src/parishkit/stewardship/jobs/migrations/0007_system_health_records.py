"""Forward migration: the System health page's records (ADM-13 PR 1, #530).

Its SQL is the frozen file ``schema/migrations/0007_system_health_records.sql``
(the next prefix in the repository-wide file sequence after this app's own
``0006_due_work_context.sql``, which is also its previous migration), read
whole and never parsed. It creates two tables with their guards:

- ``stewardship_service_status``: what each online process last reported
  about itself (version, debug logging and a mail consumer's sender state).
  Each service's login writes only its own rows, every time comes from the
  database clock, and only the worker's housekeeping deletes a row, a day
  after its last report.
- ``stewardship_source_drop_count``: every count a refused ParishSoft load
  was checked on, written by the worker with the refusal; append-only.

It also drops and re-adds ``operational_event_safe`` on the operational log
with ``service_status_failed`` added, copied from the fresh-install
``tables.sql`` (the constraint is SQL-only, so no model state changes). It
replaces no function.

It ends with a DO block that refuses to commit unless every table,
constraint, trigger and function is installed. The state operations below
describe the service status model; ``stewardship_source.0003_source_drop_counts``
records the drop count model's state. A digest test pins the frozen file. A
fresh install runs the baseline and then every forward migration, ending in
the same catalog as an upgraded database. Like the baseline, it has no
reverse operation.
"""

from pathlib import Path

from django.db import migrations, models

import parishkit.stewardship.storage

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0007_system_health_records.sql"
)

SERVICES = (
    "web",
    "worker",
    "scheduler",
    "mail-dispatch",
    "config-installer",
    "credential-installer",
)
SENDER_STATES = ("running", "outage_paused", "gmail_held", "daily_limit", "halted")


class Migration(migrations.Migration):
    dependencies = [
        # This app's latest migration, which also installed the previous
        # frozen file (0006_due_work_context.sql), so the files apply in
        # prefix order.
        ("stewardship_jobs", "0006_due_work_context"),
    ]

    operations = [
        migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8")),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="ServiceStatus",
                    fields=[
                        (
                            "id",
                            models.UUIDField(
                                editable=False, primary_key=True, serialize=False
                            ),
                        ),
                        ("service", models.CharField(max_length=24)),
                        ("process", models.CharField(max_length=8)),
                        (
                            "target",
                            models.CharField(blank=True, max_length=32, null=True),
                        ),
                        (
                            "started_at",
                            parishkit.stewardship.storage.UTCDateTimeField(),
                        ),
                        (
                            "reported_at",
                            parishkit.stewardship.storage.UTCDateTimeField(),
                        ),
                        ("application_version", models.CharField(max_length=40)),
                        ("debug_logging", models.BooleanField()),
                        (
                            "sender_state",
                            models.CharField(blank=True, max_length=16, null=True),
                        ),
                        (
                            "sender_since",
                            parishkit.stewardship.storage.UTCDateTimeField(
                                blank=True, null=True
                            ),
                        ),
                        (
                            "sender_until",
                            parishkit.stewardship.storage.UTCDateTimeField(
                                blank=True, null=True
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_service_status",
                        "indexes": [
                            models.Index(
                                fields=["reported_at"], name="service_status_reported"
                            )
                        ],
                        "constraints": [
                            models.CheckConstraint(
                                condition=models.Q(service__in=SERVICES)
                                & models.Q(process__in=("main", "source", "mail")),
                                name="service_status_names",
                            ),
                            models.CheckConstraint(
                                condition=(
                                    models.Q(process="main")
                                    | models.Q(process="source", service="worker")
                                    | models.Q(process="mail", service="mail-dispatch")
                                )
                                & (
                                    models.Q(
                                        service="credential-installer",
                                        target__isnull=False,
                                    )
                                    | (
                                        ~models.Q(service="credential-installer")
                                        & models.Q(target__isnull=True)
                                    )
                                ),
                                name="service_status_process",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(target__isnull=True)
                                | models.Q(target__regex=r"^[a-z][a-z0-9_]{0,31}$"),
                                name="service_status_target",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    application_version__regex=r"^[0-9A-Za-z.+-]{1,40}$"
                                ),
                                name="service_status_version",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    started_at__lte=models.F("reported_at")
                                ),
                                name="service_status_times",
                            ),
                            models.CheckConstraint(
                                condition=(
                                    models.Q(
                                        service="mail-dispatch",
                                        sender_state__in=SENDER_STATES,
                                        sender_since__isnull=False,
                                    )
                                    | (
                                        ~models.Q(service="mail-dispatch")
                                        & models.Q(
                                            sender_state__isnull=True,
                                            sender_since__isnull=True,
                                        )
                                    )
                                )
                                & (
                                    models.Q(sender_until__isnull=True)
                                    | models.Q(
                                        sender_state__in=("outage_paused", "gmail_held")
                                    )
                                ),
                                name="service_status_sender",
                            ),
                        ],
                    },
                ),
            ]
        ),
    ]

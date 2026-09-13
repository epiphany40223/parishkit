"""Retain exact initial source/configuration/population completion provenance."""

import uuid

import django.db.models.deletion
import django.db.models.functions.datetime
from django.db import migrations, models

import parishkit.stewardship.observability
import parishkit.stewardship.storage


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0087_setup_prepared_guards"),
        ("stewardship_jobs", "0005_scheduler_cancellation_guard"),
        ("stewardship_source", "0022_setup_final_source_cleanup"),
    ]

    operations = [
        migrations.CreateModel(
            name="SetupCompletion",
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
                ("actor_id", models.UUIDField(blank=True, editable=False, null=True)),
                (
                    "correlation_id",
                    models.UUIDField(
                        db_index=True,
                        default=parishkit.stewardship.observability.current_correlation,
                        editable=False,
                    ),
                ),
                ("task_fence", models.PositiveBigIntegerField()),
                ("source_fence", models.PositiveBigIntegerField()),
                (
                    "activation",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_accounts.configurationactivation",
                    ),
                ),
                (
                    "preparation",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_accounts.setuppreparationreceipt",
                    ),
                ),
                (
                    "snapshot",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_source.sourcesnapshot",
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
                "db_table": "stewardship_setup_completion",
                "constraints": [
                    models.UniqueConstraint(
                        models.Value(1), name="setup_one_completion"
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("source_fence__gt", 0), ("task_fence__gt", 0)
                        ),
                        name="setup_completion_fences",
                    ),
                ],
            },
        ),
    ]

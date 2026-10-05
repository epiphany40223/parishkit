"""Model state for every count a refused ParishSoft load was checked on (ADM-13).

The table, its constraints and its guards are created by
``stewardship_jobs.0007_system_health_records``, whose frozen SQL file
``schema/migrations/0007_system_health_records.sql`` installs both System
health tables. CI runs ``makemigrations --check``, so this migration records
the ``SourceDropCount`` model in Django's state only; it runs no SQL and
therefore depends on the migration that does.
"""

import uuid

import django.db.models.deletion
import django.db.models.functions.datetime
from django.db import migrations, models

import parishkit.stewardship.observability
import parishkit.stewardship.storage

MEASURES = (
    "family",
    "member",
    "ministry",
    "roster",
    "fund",
    "portal_eligible_families",
    "email_eligible_families",
    "active_head_families",
    "valid_email_contacts",
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_source", "0002_refresh_tick_times"),
        ("stewardship_jobs", "0007_system_health_records"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="SourceDropCount",
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
                        ("measure", models.CharField(max_length=32)),
                        (
                            "before",
                            models.PositiveBigIntegerField(blank=True, null=True),
                        ),
                        ("after", models.PositiveBigIntegerField()),
                        ("limit_percent", models.PositiveSmallIntegerField()),
                        ("failed", models.BooleanField()),
                        (
                            "attempt",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="drop_counts",
                                to="stewardship_source.sourcerefreshattempt",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_source_drop_count",
                        "constraints": [
                            models.UniqueConstraint(
                                fields=("attempt", "measure"),
                                name="source_drop_count_identity",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(measure__in=MEASURES),
                                name="source_drop_count_measure",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(limit_percent__lte=100),
                                name="source_drop_count_limit",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(failed=False)
                                | models.Q(before__gt=0, limit_percent__lt=100),
                                name="source_drop_count_failure",
                            ),
                        ],
                    },
                ),
            ]
        ),
    ]

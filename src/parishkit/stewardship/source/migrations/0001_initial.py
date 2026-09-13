import uuid

import django.db.models.deletion
import django.db.models.functions.datetime
from django.db import migrations, models

import parishkit.stewardship.observability
import parishkit.stewardship.storage


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("stewardship_jobs", "0002_taskrun_guards"),
    ]

    operations = [
        migrations.CreateModel(
            name="SourceMutationLease",
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
                (
                    "updated_at",
                    parishkit.stewardship.storage.UTCDateTimeField(
                        db_default=django.db.models.functions.datetime.Now(),
                        editable=False,
                    ),
                ),
                ("version", models.PositiveBigIntegerField(default=1, editable=False)),
                (
                    "singleton",
                    models.BooleanField(default=True, editable=False, unique=True),
                ),
                ("task_fence", models.PositiveBigIntegerField(default=0)),
                ("worker_id", models.UUIDField(blank=True, null=True)),
                ("fence", models.PositiveBigIntegerField(default=0)),
                ("phase", models.CharField(default="idle", max_length=16)),
                (
                    "acquired_at",
                    parishkit.stewardship.storage.UTCDateTimeField(
                        blank=True, null=True
                    ),
                ),
                (
                    "heartbeat_at",
                    parishkit.stewardship.storage.UTCDateTimeField(
                        blank=True, null=True
                    ),
                ),
                (
                    "expires_at",
                    parishkit.stewardship.storage.UTCDateTimeField(
                        blank=True, null=True
                    ),
                ),
                (
                    "external_deadline",
                    parishkit.stewardship.storage.UTCDateTimeField(
                        blank=True, null=True
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_jobs.taskrun",
                    ),
                ),
            ],
            options={
                "db_table": "stewardship_source_lease",
                "abstract": False,
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("version__gte", 1)),
                        name="stewardship_source_sourcemutationlease_positive_version",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("singleton", True)),
                        name="source_lease_singleton",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("expires_at__isnull", True),
                                ("owner__isnull", True),
                                ("phase", "idle"),
                                ("worker_id__isnull", True),
                            ),
                            models.Q(
                                ("acquired_at__isnull", False),
                                ("expires_at__gt", models.F("heartbeat_at")),
                                ("expires_at__isnull", False),
                                ("fence__gt", 0),
                                ("heartbeat_at__gte", models.F("acquired_at")),
                                ("heartbeat_at__isnull", False),
                                ("owner__isnull", False),
                                (
                                    "phase__in",
                                    ("full", "delta", "publication", "compaction"),
                                ),
                                ("task_fence__gt", 0),
                                ("worker_id__isnull", False),
                            ),
                            _connector="OR",
                        ),
                        name="source_lease_owner_shape",
                    ),
                ],
            },
        ),
    ]

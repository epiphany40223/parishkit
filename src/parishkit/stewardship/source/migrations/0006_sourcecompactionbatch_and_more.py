import uuid

import django.db.models.deletion
import django.db.models.functions.datetime
from django.db import migrations, models

import parishkit.stewardship.observability
import parishkit.stewardship.storage


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_jobs", "0002_taskrun_guards"),
        ("stewardship_source", "0005_source_snapshot_guards"),
    ]

    operations = [
        migrations.CreateModel(
            name="SourceCompactionBatch",
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
                ("source_fence", models.PositiveBigIntegerField()),
                ("cutoff_at", parishkit.stewardship.storage.UTCDateTimeField()),
                ("recent_cutoff", parishkit.stewardship.storage.UTCDateTimeField()),
                ("yearly_cutoff", parishkit.stewardship.storage.UTCDateTimeField()),
                ("snapshot_count", models.PositiveBigIntegerField()),
                ("membership_count", models.PositiveBigIntegerField()),
                ("payload_count", models.PositiveBigIntegerField()),
            ],
            options={
                "db_table": "stewardship_source_compaction",
            },
        ),
        migrations.RemoveConstraint(
            model_name="sourcecurrent",
            name="source_current_shape",
        ),
        migrations.RemoveConstraint(
            model_name="sourcesnapshot",
            name="source_snapshot_promotion_shape",
        ),
        migrations.RemoveConstraint(
            model_name="sourcesnapshotpin",
            name="source_pin_expiry_owner",
        ),
        migrations.AddConstraint(
            model_name="sourcecurrent",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("generation", 0),
                        ("organization_id__isnull", True),
                        ("snapshot__isnull", True),
                    ),
                    models.Q(
                        ("generation__gt", 0),
                        ("organization_id__gt", 0),
                        ("organization_id__isnull", False),
                        ("snapshot__isnull", False),
                    ),
                    _connector="OR",
                ),
                name="source_current_shape",
            ),
        ),
        migrations.AddConstraint(
            model_name="sourcesnapshot",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("generation__gt", 0),
                        ("generation__isnull", False),
                        ("promoted_at__isnull", False),
                        ("state", "promoted"),
                    ),
                    models.Q(
                        models.Q(("state", "promoted"), _negated=True),
                        ("generation__isnull", True),
                        ("promoted_at__isnull", True),
                    ),
                    _connector="OR",
                ),
                name="source_snapshot_promotion_shape",
            ),
        ),
        migrations.AddConstraint(
            model_name="sourcesnapshotpin",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("expires_at__isnull", True),
                        models.Q(("parent_kind", "form_baseline"), _negated=True),
                    ),
                    models.Q(
                        ("expires_at__isnull", False), ("parent_kind", "form_baseline")
                    ),
                    _connector="OR",
                ),
                name="source_pin_expiry_owner",
            ),
        ),
        migrations.AddField(
            model_name="sourcecompactionbatch",
            name="task",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                to="stewardship_jobs.taskrun",
            ),
        ),
        migrations.AddConstraint(
            model_name="sourcecompactionbatch",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("source_fence__gt", 0),
                    ("yearly_cutoff__lt", models.F("recent_cutoff")),
                    ("recent_cutoff__lt", models.F("cutoff_at")),
                ),
                name="source_compaction_cutoffs",
            ),
        ),
    ]

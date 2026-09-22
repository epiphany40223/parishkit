import uuid

import django.db.models.functions.datetime
from django.db import migrations, models

import parishkit.stewardship.storage


class Migration(migrations.Migration):
    # Baseline SQL owns creation; this step only resolves the model state.

    dependencies = [
        ("stewardship_jobs", "0002_initial_delivery"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="BackupRun",
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
                            "started_at",
                            parishkit.stewardship.storage.UTCDateTimeField(),
                        ),
                        (
                            "completed_at",
                            parishkit.stewardship.storage.UTCDateTimeField(
                                db_default=django.db.models.functions.datetime.Now()
                            ),
                        ),
                        ("database_bytes", models.BigIntegerField()),
                        ("files_bytes", models.BigIntegerField()),
                        ("manifest_digest", models.CharField(max_length=64)),
                        ("recipient_fingerprint", models.CharField(max_length=16)),
                        ("application_version", models.CharField(max_length=40)),
                    ],
                    options={
                        "db_table": "stewardship_backup_run",
                    },
                ),
                migrations.AddConstraint(
                    model_name="backuprun",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            ("started_at__lte", models.F("completed_at"))
                        ),
                        name="backup_run_times",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="backuprun",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            ("database_bytes__gte", 0), ("files_bytes__gte", 0)
                        ),
                        name="backup_run_sizes",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="backuprun",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            ("manifest_digest__regex", "^[0-9a-f]{64}$"),
                            ("recipient_fingerprint__regex", "^[0-9a-f]{16}$"),
                        ),
                        name="backup_run_digests",
                    ),
                ),
            ]
        )
    ]

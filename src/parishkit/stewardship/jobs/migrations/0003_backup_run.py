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
                migrations.CreateModel(
                    name="BackupDriveProbe",
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
                                db_default=django.db.models.functions.datetime.Now()
                            ),
                        ),
                        ("requested_by_id", models.UUIDField()),
                        ("folder_id", models.CharField(max_length=200)),
                        ("subject", models.CharField(max_length=254)),
                        (
                            "state",
                            models.CharField(db_default="pending", max_length=16),
                        ),
                        ("failure_kind", models.CharField(max_length=32, null=True)),
                        (
                            "completed_at",
                            parishkit.stewardship.storage.UTCDateTimeField(null=True),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_backup_drive_probe",
                        "indexes": [
                            models.Index(
                                condition=models.Q(("state", "pending")),
                                fields=["created_at"],
                                name="backup_probe_pending",
                            )
                        ],
                        "constraints": [
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("state__in", ["pending", "succeeded", "failed"])
                                ),
                                name="backup_probe_state",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("folder_id__regex", "^[A-Za-z0-9_-]{10,200}$")
                                ),
                                name="backup_probe_folder",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    models.Q(
                                        ("completed_at__isnull", True),
                                        ("failure_kind__isnull", True),
                                        ("state", "pending"),
                                    ),
                                    models.Q(
                                        ("completed_at__isnull", False),
                                        ("failure_kind__isnull", True),
                                        ("state", "succeeded"),
                                    ),
                                    models.Q(
                                        ("completed_at__isnull", False),
                                        ("failure_kind__isnull", False),
                                        ("state", "failed"),
                                    ),
                                    _connector="OR",
                                ),
                                name="backup_probe_shape",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("failure_kind__isnull", True),
                                    (
                                        "failure_kind__in",
                                        (
                                            "authorization",
                                            "api_disabled",
                                            "not_found",
                                            "permission",
                                            "not_folder",
                                            "credential",
                                            "verification",
                                            "unavailable",
                                            "unexpected",
                                            "unanswered",
                                        ),
                                    ),
                                    _connector="OR",
                                ),
                                name="backup_probe_failure_kind",
                            ),
                        ],
                    },
                ),
                migrations.CreateModel(
                    name="BackupUpload",
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
                                db_default=django.db.models.functions.datetime.Now()
                            ),
                        ),
                        ("state", models.CharField(max_length=16)),
                        ("set_name", models.CharField(max_length=16, null=True)),
                        ("manifest_digest", models.CharField(max_length=64, null=True)),
                        ("folder_id", models.CharField(max_length=200, null=True)),
                        ("failure_kind", models.CharField(max_length=32, null=True)),
                    ],
                    options={
                        "db_table": "stewardship_backup_upload",
                        "indexes": [
                            models.Index(
                                models.OrderBy(models.F("created_at"), descending=True),
                                name="backup_upload_newest",
                            )
                        ],
                        "constraints": [
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("state__in", ["uploaded", "failed", "disabled"])
                                ),
                                name="backup_upload_state",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("failure_kind__isnull", True),
                                    (
                                        "failure_kind__in",
                                        (
                                            "authorization",
                                            "api_disabled",
                                            "not_found",
                                            "permission",
                                            "not_folder",
                                            "credential",
                                            "verification",
                                            "unavailable",
                                            "unexpected",
                                            "unanswered",
                                        ),
                                    ),
                                    _connector="OR",
                                ),
                                name="backup_upload_failure_kind",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    models.Q(
                                        ("failure_kind__isnull", True),
                                        ("folder_id__isnull", False),
                                        ("manifest_digest__isnull", False),
                                        ("set_name__isnull", False),
                                        ("state", "uploaded"),
                                    ),
                                    models.Q(
                                        ("failure_kind__isnull", False),
                                        ("folder_id__isnull", False),
                                        ("state", "failed"),
                                    ),
                                    models.Q(
                                        ("failure_kind__isnull", True),
                                        ("folder_id__isnull", True),
                                        ("manifest_digest__isnull", True),
                                        ("set_name__isnull", True),
                                        ("state", "disabled"),
                                    ),
                                    _connector="OR",
                                ),
                                name="backup_upload_shape",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    models.Q(
                                        ("set_name__isnull", True),
                                        ("set_name__regex", "^[0-9]{8}T[0-9]{6}Z$"),
                                        _connector="OR",
                                    ),
                                    models.Q(
                                        ("manifest_digest__isnull", True),
                                        ("manifest_digest__regex", "^[0-9a-f]{64}$"),
                                        _connector="OR",
                                    ),
                                    models.Q(
                                        ("folder_id__isnull", True),
                                        ("folder_id__regex", "^[A-Za-z0-9_-]{10,200}$"),
                                        _connector="OR",
                                    ),
                                ),
                                name="backup_upload_values",
                            ),
                        ],
                    },
                ),
            ]
        )
    ]

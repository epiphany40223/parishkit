"""Forward migration: Take a backup now's request record (ADM-13 PR 3, #530).

Its SQL is the frozen file ``schema/migrations/0016_backup_request.sql``, read
whole and never parsed. It creates ``stewardship_backup_request`` with its
checks, indexes and run key, and the guard trigger that admits a request
only from the web login for a freshly signed-in Administrator, one at a
time and never during a restore review, and lets only the backup login
settle it. It ends with a DO block that refuses to commit unless all of it
is installed. The state operation records the model in Django's model
state, so ``makemigrations --check`` stays clean. Reversing needs its own
forward migration, so, like the baseline, it has no reverse operation.
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
    / "0016_backup_request.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The jobs app's previous migration (#684, frozen file 0012).
        ("stewardship_jobs", "0010_daily_send_count"),
        # The migration that installed the previous frozen file (0015,
        # #709), so the files apply in prefix order.
        ("stewardship_reports", "0002_directory_member_search"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="BackupRequest",
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
                        ("actor_id", models.UUIDField()),
                        ("session_id", models.UUIDField()),
                        (
                            "authenticated_at",
                            parishkit.stewardship.storage.UTCDateTimeField(),
                        ),
                        (
                            "state",
                            models.CharField(db_default="waiting", max_length=16),
                        ),
                        (
                            "held_at",
                            parishkit.stewardship.storage.UTCDateTimeField(null=True),
                        ),
                        (
                            "claimed_at",
                            parishkit.stewardship.storage.UTCDateTimeField(null=True),
                        ),
                        (
                            "finished_at",
                            parishkit.stewardship.storage.UTCDateTimeField(null=True),
                        ),
                        ("failure_kind", models.CharField(max_length=48, null=True)),
                        (
                            "backup_run",
                            models.ForeignKey(
                                null=True,
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="requests",
                                to="stewardship_jobs.backuprun",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_backup_request",
                        "indexes": [
                            models.Index(
                                models.OrderBy(models.F("created_at"), descending=True),
                                name="backup_request_newest",
                            )
                        ],
                        "constraints": [
                            models.CheckConstraint(
                                condition=models.Q(
                                    state__in=(
                                        "waiting",
                                        "running",
                                        "finished",
                                        "failed",
                                        "expired",
                                    )
                                ),
                                name="backup_request_state",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(failure_kind__isnull=True)
                                | models.Q(
                                    failure_kind__regex="^[a-z][a-z0-9_]{0,47}$"
                                ),
                                name="backup_request_failure_kind",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    backup_run__isnull=True,
                                    claimed_at__isnull=True,
                                    failure_kind__isnull=True,
                                    finished_at__isnull=True,
                                    state="waiting",
                                )
                                | models.Q(
                                    backup_run__isnull=True,
                                    claimed_at__isnull=False,
                                    failure_kind__isnull=True,
                                    finished_at__isnull=True,
                                    state="running",
                                )
                                | models.Q(
                                    backup_run__isnull=False,
                                    claimed_at__isnull=False,
                                    failure_kind__isnull=True,
                                    finished_at__isnull=False,
                                    state="finished",
                                )
                                | models.Q(
                                    backup_run__isnull=True,
                                    claimed_at__isnull=False,
                                    failure_kind__isnull=False,
                                    finished_at__isnull=False,
                                    state="failed",
                                )
                                | models.Q(
                                    backup_run__isnull=True,
                                    claimed_at__isnull=True,
                                    failure_kind__isnull=True,
                                    finished_at__isnull=False,
                                    state="expired",
                                ),
                                name="backup_request_shape",
                            ),
                        ],
                    },
                ),
            ],
        ),
    ]

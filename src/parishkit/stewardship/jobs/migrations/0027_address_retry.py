"""Forward migration: a Family email's follow-up to addresses refused for now (#806).

Its SQL is the frozen file ``schema/migrations/0077_address_retry.sql`` (prefix
0077 in the repository-wide file sequence), read whole and never parsed. It
creates ``stewardship_outbox_address_retry`` with its checks and guard
trigger, the mail-dispatch-only definers that open a follow-up (and its
``family_address_retry`` task) and read its link token, the web-only definer
that reads what each follow-up did, and adds the task type to the execution
and creator maps. It ends with a DO block that refuses to commit unless all of
it is installed as declared. The state operation records the model in
Django's model state, so ``makemigrations --check`` stays clean. Reversing
needs its own forward migration.

It follows the users swap (#952), so its table and functions are granted only
once it is applied (runtime_grants.CLOSURE_TABLES and CLOSURE_FUNCTIONS).
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
    / "0077_address_retry.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The jobs app's previous migration, which also installs the
        # previous frozen file (0076, #530), so the files apply in prefix
        # order. It follows the users swap, so `migrate --users-upgrade-base`
        # never applies this file.
        ("stewardship_jobs", "0026_daily_send_resume"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="AddressRetry",
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
                        ("positions", models.JSONField()),
                        ("remaining", models.JSONField()),
                        ("refused", models.JSONField()),
                        ("outcome", models.JSONField(null=True)),
                        (
                            "state",
                            models.CharField(
                                db_default="waiting", default="waiting", max_length=16
                            ),
                        ),
                        (
                            "attempt",
                            models.PositiveIntegerField(db_default=0, default=0),
                        ),
                        (
                            "spared",
                            models.PositiveIntegerField(db_default=0, default=0),
                        ),
                        (
                            "not_before",
                            parishkit.stewardship.storage.UTCDateTimeField(),
                        ),
                        (
                            "expires_at",
                            parishkit.stewardship.storage.UTCDateTimeField(),
                        ),
                        ("run_id", models.UUIDField(null=True)),
                        ("task_fence", models.PositiveBigIntegerField(null=True)),
                        ("worker_id", models.UUIDField(null=True)),
                        (
                            "submitted_at",
                            parishkit.stewardship.storage.UTCDateTimeField(null=True),
                        ),
                        (
                            "provider_deadline",
                            parishkit.stewardship.storage.UTCDateTimeField(null=True),
                        ),
                        (
                            "finished_at",
                            parishkit.stewardship.storage.UTCDateTimeField(null=True),
                        ),
                        (
                            "reason",
                            models.CharField(
                                blank=True, db_default="", default="", max_length=32
                            ),
                        ),
                        ("sealed_substitutions", models.TextField(null=True)),
                        ("sealed_key_id", models.CharField(max_length=48, null=True)),
                        ("token_generation_id", models.UUIDField()),
                        ("credential_epoch_id", models.UUIDField()),
                        (
                            "message",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="address_retries",
                                to="stewardship_jobs.outboxmessage",
                            ),
                        ),
                        (
                            "render",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="+",
                                to="stewardship_jobs.outboxrender",
                            ),
                        ),
                        (
                            "source_event",
                            models.OneToOneField(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="address_retry",
                                to="stewardship_jobs.outboxevent",
                            ),
                        ),
                        (
                            "task",
                            models.OneToOneField(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="address_retry",
                                to="stewardship_jobs.taskrun",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_outbox_address_retry",
                        "abstract": False,
                        "constraints": [
                            models.CheckConstraint(
                                condition=models.Q(("version__gte", 1)),
                                name="stewardship_jobs_addressretry_positive_version",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    (
                                        "state__in",
                                        (
                                            "waiting",
                                            "submitting",
                                            "finished",
                                            "unknown",
                                            "failed",
                                            "cancelled",
                                        ),
                                    )
                                ),
                                name="address_retry_state",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("reason", ""),
                                    ("reason__regex", "^[a-z][a-z0-9_]{0,31}$"),
                                    _connector="OR",
                                ),
                                name="address_retry_reason",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    models.Q(
                                        ("finished_at__isnull", False),
                                        ("sealed_key_id", None),
                                        ("sealed_substitutions", None),
                                        (
                                            "state__in",
                                            (
                                                "finished",
                                                "unknown",
                                                "failed",
                                                "cancelled",
                                            ),
                                        ),
                                    ),
                                    models.Q(
                                        models.Q(
                                            (
                                                "state__in",
                                                (
                                                    "finished",
                                                    "unknown",
                                                    "failed",
                                                    "cancelled",
                                                ),
                                            ),
                                            _negated=True,
                                        ),
                                        ("finished_at", None),
                                        ("sealed_key_id__isnull", False),
                                        ("sealed_substitutions__isnull", False),
                                    ),
                                    _connector="OR",
                                ),
                                name="address_retry_scrub",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    models.Q(
                                        ("attempt", 0),
                                        ("provider_deadline", None),
                                        ("run_id", None),
                                        ("spared", 0),
                                        ("submitted_at", None),
                                        ("task_fence", None),
                                        ("worker_id", None),
                                    ),
                                    models.Q(
                                        ("attempt__gte", 1),
                                        (
                                            "provider_deadline__gte",
                                            models.F("submitted_at"),
                                        ),
                                        ("provider_deadline__isnull", False),
                                        ("run_id__isnull", False),
                                        ("spared__lte", models.F("attempt")),
                                        ("submitted_at__isnull", False),
                                        ("task_fence__gte", 1),
                                        ("task_fence__isnull", False),
                                        ("worker_id__isnull", False),
                                    ),
                                    _connector="OR",
                                ),
                                name="address_retry_attempt_shape",
                            ),
                        ],
                    },
                ),
            ],
        )
    ]

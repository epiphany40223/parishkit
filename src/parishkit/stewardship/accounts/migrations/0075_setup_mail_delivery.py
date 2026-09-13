"""Retain exact setup test intent and a non-retryable submission boundary."""

import uuid

import django.db.models.deletion
import django.db.models.functions.datetime
from django.db import migrations, models

import parishkit.stewardship.observability
import parishkit.stewardship.storage


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0074_setup_schedule_drafts"),
        ("stewardship_jobs", "0005_scheduler_cancellation_guard"),
    ]

    operations = [
        migrations.CreateModel(
            name="SetupMailDelivery",
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
                ("attempt_version", models.PositiveBigIntegerField()),
                ("request_key", models.UUIDField()),
                ("candidate_digest", models.CharField(max_length=64)),
                ("credential_version", models.PositiveBigIntegerField()),
                ("fingerprint", models.CharField(max_length=64)),
                ("mail", models.JSONField()),
                ("state", models.CharField(default="queued", max_length=24)),
                ("task_fence", models.PositiveBigIntegerField(null=True)),
                ("worker_id", models.UUIDField(null=True)),
                (
                    "submitted_at",
                    parishkit.stewardship.storage.UTCDateTimeField(null=True),
                ),
                (
                    "deadline_at",
                    parishkit.stewardship.storage.UTCDateTimeField(null=True),
                ),
                (
                    "finished_at",
                    parishkit.stewardship.storage.UTCDateTimeField(null=True),
                ),
                (
                    "scrubbed_at",
                    parishkit.stewardship.storage.UTCDateTimeField(null=True),
                ),
                (
                    "attempt",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_accounts.setupattempt",
                    ),
                ),
                (
                    "credential",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_accounts.setupsealedcredential",
                    ),
                ),
                (
                    "run",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="setup_mail_submissions",
                        to="stewardship_jobs.taskrun",
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
                "db_table": "stewardship_setup_mail_delivery",
                "abstract": False,
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("version__gte", 1)),
                        name="stewardship_accounts_setupmaildelivery_positive_version",
                    ),
                    models.UniqueConstraint(
                        fields=("attempt", "request_key"),
                        name="setup_mail_explicit_request",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(("state__in", ["queued", "submitting"])),
                        fields=("attempt",),
                        name="setup_mail_one_pending",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("attempt_version__gte", 1), ("credential_version__gte", 1)
                        ),
                        name="setup_mail_positive_bindings",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("candidate_digest__regex", "^[0-9a-f]{64}$"),
                            ("fingerprint__regex", "^[0-9a-f]{64}$"),
                        ),
                        name="setup_mail_digest_shape",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "state__in",
                                [
                                    "queued",
                                    "submitting",
                                    "accepted",
                                    "not_sent",
                                    "delivery_unknown",
                                    "cancelled",
                                ],
                            )
                        ),
                        name="setup_mail_known_state",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("deadline_at", None),
                                ("run", None),
                                ("state__in", ["queued", "cancelled"]),
                                ("submitted_at", None),
                                ("task_fence", None),
                                ("worker_id", None),
                            ),
                            models.Q(
                                ("deadline_at__isnull", False),
                                ("run__isnull", False),
                                (
                                    "state__in",
                                    [
                                        "submitting",
                                        "accepted",
                                        "not_sent",
                                        "delivery_unknown",
                                    ],
                                ),
                                ("submitted_at__isnull", False),
                                ("task_fence__gte", 1),
                                ("worker_id__isnull", False),
                            ),
                            _connector="OR",
                        ),
                        name="setup_mail_submission_shape",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("finished_at", None),
                                ("state__in", ["queued", "submitting"]),
                            ),
                            models.Q(
                                ("finished_at__isnull", False),
                                models.Q(
                                    ("state__in", ["queued", "submitting"]),
                                    _negated=True,
                                ),
                            ),
                            _connector="OR",
                        ),
                        name="setup_mail_terminal_time",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("scrubbed_at", None), ("mail", {}), _connector="OR"
                        ),
                        name="setup_mail_scrubbed_payload",
                    ),
                ],
            },
        ),
    ]

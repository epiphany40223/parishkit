"""Retain exact Slack setup-test intent and closed delivery outcome metadata."""

import uuid

import django.db.models.deletion
import django.db.models.functions.datetime
from django.db import migrations, models

import parishkit.stewardship.observability
import parishkit.stewardship.storage


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0079_setup_mail_unsent_failure"),
    ]

    operations = [
        migrations.CreateModel(
            name="SetupSlackDelivery",
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
                ("state", models.CharField(default="queued", max_length=24)),
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
            ],
            options={
                "db_table": "stewardship_setup_slack_delivery",
                "abstract": False,
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("version__gte", 1)),
                        name="stewardship_accounts_setupslackdelivery_positive_version",
                    ),
                    models.UniqueConstraint(
                        fields=("attempt", "request_key"),
                        name="setup_slack_explicit_request",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(("state__in", ["queued", "submitting"])),
                        fields=("attempt",),
                        name="setup_slack_one_pending",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("attempt_version__gte", 1), ("credential_version__gte", 1)
                        ),
                        name="setup_slack_positive_bindings",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("candidate_digest__regex", "^[0-9a-f]{64}$"),
                            ("fingerprint__regex", "^[0-9a-f]{64}$"),
                        ),
                        name="setup_slack_digest_shape",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "state__in",
                                [
                                    "queued",
                                    "cancelled",
                                    "submitting",
                                    "accepted",
                                    "not_sent",
                                    "delivery_unknown",
                                ],
                            )
                        ),
                        name="setup_slack_known_state",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("deadline_at", None),
                                ("state__in", ["queued", "cancelled"]),
                                ("submitted_at", None),
                                ("worker_id", None),
                            ),
                            models.Q(
                                ("deadline_at__isnull", False),
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
                                ("worker_id__isnull", False),
                            ),
                            _connector="OR",
                        ),
                        name="setup_slack_submission_shape",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("finished_at", None),
                                ("state__in", ["queued", "submitting"]),
                            ),
                            models.Q(
                                models.Q(
                                    ("state__in", ["queued", "submitting"]),
                                    _negated=True,
                                ),
                                ("finished_at__isnull", False),
                            ),
                            _connector="OR",
                        ),
                        name="setup_slack_terminal_shape",
                    ),
                ],
            },
        ),
    ]

"""Keep explicit campaign test mail independent of scheduled delivery records."""

import uuid

import django.db.models.deletion
from django.db import migrations, models
from django.db.models.functions import Now

import parishkit.stewardship.observability
import parishkit.stewardship.storage


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0089_setup_completion_guards"),
        ("stewardship_campaigns", "0036_family_presence_idle"),
        ("stewardship_jobs", "0005_scheduler_cancellation_guard"),
    ]
    operations = [
        migrations.CreateModel(
            name="CampaignMailTest",
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
                        db_default=Now(), editable=False
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
                        db_default=Now(), editable=False
                    ),
                ),
                ("version", models.PositiveBigIntegerField(default=1, editable=False)),
                ("requested_by_id", models.UUIDField()),
                ("request_key", models.UUIDField()),
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
                    "campaign",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_campaigns.campaign",
                    ),
                ),
                (
                    "configuration",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_accounts.appliedconfigurationversion",
                    ),
                ),
                (
                    "run",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="campaign_mail_tests",
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
                (
                    "template",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_accounts.contentversion",
                    ),
                ),
            ],
            options={
                "db_table": "stewardship_campaign_mail_test",
                "abstract": False,
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(version__gte=1),
                        name="stewardship_accounts_campaignmailtest_positive_version",
                    ),
                    models.UniqueConstraint(
                        fields=("requested_by_id", "request_key"),
                        name="campaign_mail_explicit_request",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(state__in=["queued", "submitting"]),
                        fields=("campaign",),
                        name="campaign_mail_one_pending",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(fingerprint__regex="^[0-9a-f]{64}$"),
                        name="campaign_mail_fingerprint",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            state__in=[
                                "queued",
                                "submitting",
                                "accepted",
                                "not_sent",
                                "delivery_unknown",
                                "cancelled",
                            ]
                        ),
                        name="campaign_mail_known_state",
                    ),
                    models.CheckConstraint(
                        condition=(
                            models.Q(
                                state__in=["queued", "cancelled"],
                                submitted_at=None,
                                deadline_at=None,
                                run=None,
                                task_fence=None,
                                worker_id=None,
                            )
                            | models.Q(
                                state__in=[
                                    "submitting",
                                    "accepted",
                                    "not_sent",
                                    "delivery_unknown",
                                ],
                                submitted_at__isnull=False,
                                deadline_at__isnull=False,
                                run__isnull=False,
                                task_fence__gte=1,
                                worker_id__isnull=False,
                            )
                        ),
                        name="campaign_mail_submission_shape",
                    ),
                    models.CheckConstraint(
                        condition=(
                            models.Q(
                                state__in=["queued", "submitting"], finished_at=None
                            )
                            | (
                                models.Q(finished_at__isnull=False, mail={})
                                & ~models.Q(state__in=["queued", "submitting"])
                            )
                        ),
                        name="campaign_mail_terminal_scrub",
                    ),
                ],
            },
        ),
    ]

"""Keep one ephemeral public recipient for each original setup mail claim."""

import uuid

import django.db.models.deletion
import django.db.models.functions.datetime
from django.db import migrations, models

import parishkit.stewardship.observability
import parishkit.stewardship.storage


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0076_setup_mail_guards"),
        ("stewardship_jobs", "0005_scheduler_cancellation_guard"),
    ]

    operations = [
        migrations.CreateModel(
            name="SetupMailExchange",
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
                ("task_fence", models.PositiveBigIntegerField()),
                ("worker_id", models.UUIDField()),
                ("public_key", models.BinaryField(max_length=32)),
                ("ciphertext", models.TextField(null=True)),
                (
                    "replied_at",
                    parishkit.stewardship.storage.UTCDateTimeField(null=True),
                ),
                (
                    "scrubbed_at",
                    parishkit.stewardship.storage.UTCDateTimeField(null=True),
                ),
                (
                    "delivery",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_accounts.setupmaildelivery",
                    ),
                ),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_jobs.taskrun",
                    ),
                ),
            ],
            options={
                "db_table": "stewardship_setup_mail_exchange",
                "abstract": False,
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("version__gte", 1)),
                        name="stewardship_accounts_setupmailexchange_positive_version",
                    ),
                    models.UniqueConstraint(
                        fields=("run", "task_fence"),
                        name="setup_mail_exchange_one_recipient",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("task_fence__gte", 1)),
                        name="setup_mail_exchange_positive_fence",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(("ciphertext", None), ("replied_at", None)),
                            models.Q(
                                ("ciphertext__isnull", False),
                                ("replied_at__isnull", False),
                                ("scrubbed_at", None),
                            ),
                            models.Q(
                                ("ciphertext", None), ("scrubbed_at__isnull", False)
                            ),
                            _connector="OR",
                        ),
                        name="setup_mail_exchange_reply_shape",
                    ),
                ],
            },
        ),
    ]

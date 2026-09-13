"""Bind one frozen initial configuration to exact source and delivery receipts."""

import uuid

import django.db.models.deletion
import django.db.models.functions.datetime
from django.db import migrations, models

import parishkit.stewardship.observability
import parishkit.stewardship.storage


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0081_setup_slack_guards"),
    ]

    operations = [
        migrations.CreateModel(
            name="SetupReadinessBinding",
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
                ("testing_recipient", models.EmailField(max_length=254)),
                (
                    "intent",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_accounts.setupconfigurationintent",
                    ),
                ),
                (
                    "mail_delivery",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_accounts.setupmaildelivery",
                    ),
                ),
                (
                    "slack_delivery",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_accounts.setupslackdelivery",
                    ),
                ),
                (
                    "source_result",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_accounts.setupsourceresult",
                    ),
                ),
            ],
            options={
                "db_table": "stewardship_setup_readiness_binding",
            },
        ),
    ]

"""Retain the configuration installer's safe initial preparation proof."""

import uuid

import django.db.models.deletion
import django.db.models.functions.datetime
from django.db import migrations, models

import parishkit.stewardship.observability
import parishkit.stewardship.storage


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0085_setup_credential_guards"),
    ]

    operations = [
        migrations.CreateModel(
            name="SetupPreparationReceipt",
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
                    "configuration",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_accounts.appliedconfigurationversion",
                    ),
                ),
                (
                    "readiness",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_accounts.setupreadinessbinding",
                    ),
                ),
            ],
            options={
                "db_table": "stewardship_setup_prepared",
            },
        ),
    ]

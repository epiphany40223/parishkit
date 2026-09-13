import uuid

import django.db.models.deletion
import django.db.models.functions.datetime
from django.db import migrations, models

import parishkit.stewardship.observability
import parishkit.stewardship.storage


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0083_setup_readiness_guards"),
    ]

    operations = [
        migrations.CreateModel(
            name="SetupCredentialInstallation",
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
                ("target", models.CharField(max_length=32)),
                ("credential_version", models.PositiveBigIntegerField()),
                ("fingerprint", models.CharField(max_length=64)),
                (
                    "credential",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_accounts.setupsealedcredential",
                    ),
                ),
                (
                    "readiness",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_accounts.setupreadinessbinding",
                    ),
                ),
                (
                    "request",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_accounts.secretreplacementrequest",
                    ),
                ),
            ],
            options={
                "db_table": "stewardship_setup_credential_install",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("readiness", "target"), name="setup_install_one_target"
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("target__in", ["parishsoft", "google_workspace", "slack"])
                        ),
                        name="setup_install_known_target",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("credential_version__gte", 1),
                            ("fingerprint__regex", "^[0-9a-f]{64}$"),
                        ),
                        name="setup_install_bound_version",
                    ),
                ],
            },
        ),
    ]

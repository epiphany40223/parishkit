"""Keep wizard ciphertext separate from immediately executable replacements."""

from importlib import import_module

from django.db import migrations, models
from django.db.models.functions import Now

from parishkit.stewardship.storage import UTCDateTimeField

_fields = import_module(
    "parishkit.stewardship.accounts.migrations.0063_setup_configuration_journal"
)._fields


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0065_aborted_setup_branding")]
    operations = [
        migrations.CreateModel(
            name="SetupSealedCredential",
            fields=_fields()
            + [
                ("updated_at", UTCDateTimeField(db_default=Now(), editable=False)),
                ("version", models.PositiveBigIntegerField(default=1, editable=False)),
                ("target", models.CharField(max_length=32)),
                ("ciphertext", models.TextField(null=True)),
                ("fingerprint", models.CharField(max_length=64)),
                ("settings", models.JSONField()),
                ("scrubbed_at", UTCDateTimeField(null=True)),
                (
                    "attempt",
                    models.ForeignKey(
                        on_delete=models.PROTECT, to="stewardship_accounts.setupattempt"
                    ),
                ),
            ],
            options={
                "db_table": "stewardship_setup_sealed_credential",
                "abstract": False,
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(version__gte=1),
                        name="stewardship_accounts_setupsealedcredential_positive_version",
                    ),
                    models.UniqueConstraint(
                        fields=["attempt", "target"],
                        name="setup_secret_attempt_target",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            target__in=["parishsoft", "google_workspace", "slack"]
                        ),
                        name="setup_secret_target",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(fingerprint__regex=r"^[0-9a-f]{64}$"),
                        name="setup_secret_fingerprint",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ciphertext__isnull=False, scrubbed_at__isnull=True
                        )
                        | models.Q(ciphertext__isnull=True, scrubbed_at__isnull=False),
                        name="setup_secret_scrub_shape",
                    ),
                ],
            },
        ),
    ]

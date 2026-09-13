"""Keep setup source credentials encrypted to one ephemeral worker recipient."""

from importlib import import_module

from django.db import migrations, models
from django.db.models.functions import Now

from parishkit.stewardship.storage import UTCDateTimeField

_fields = import_module(
    "parishkit.stewardship.accounts.migrations.0063_setup_configuration_journal"
)._fields


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0067_setup_sealed_guards")]
    operations = [
        migrations.CreateModel(
            name="SetupSourceExchange",
            fields=_fields()
            + [
                ("updated_at", UTCDateTimeField(db_default=Now(), editable=False)),
                ("version", models.PositiveBigIntegerField(default=1, editable=False)),
                ("credential_version", models.PositiveBigIntegerField()),
                ("fingerprint", models.CharField(max_length=64)),
                ("task_fence", models.PositiveBigIntegerField()),
                ("worker_id", models.UUIDField()),
                ("source_fence", models.PositiveBigIntegerField()),
                ("public_key", models.BinaryField(max_length=32)),
                ("ciphertext", models.TextField(null=True)),
                ("replied_at", UTCDateTimeField(null=True)),
                ("scrubbed_at", UTCDateTimeField(null=True)),
                (
                    "attempt",
                    models.ForeignKey(
                        on_delete=models.PROTECT, to="stewardship_accounts.setupattempt"
                    ),
                ),
                (
                    "credential",
                    models.ForeignKey(
                        on_delete=models.PROTECT,
                        to="stewardship_accounts.setupsealedcredential",
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=models.PROTECT, to="stewardship_jobs.taskrun"
                    ),
                ),
            ],
            options={
                "db_table": "stewardship_setup_source_exchange",
                "abstract": False,
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(version__gte=1),
                        name="stewardship_accounts_setupsourceexchange_positive_version",
                    ),
                    models.UniqueConstraint(
                        fields=["task", "task_fence", "source_fence"],
                        name="setup_exchange_one_recipient_per_claim",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            credential_version__gte=1,
                            task_fence__gte=1,
                            source_fence__gte=1,
                        ),
                        name="setup_exchange_positive_fences",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(fingerprint__regex=r"^[0-9a-f]{64}$"),
                        name="setup_exchange_fingerprint",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ciphertext__isnull=True,
                            replied_at__isnull=True,
                        )
                        | models.Q(
                            ciphertext__isnull=False,
                            replied_at__isnull=False,
                            scrubbed_at__isnull=True,
                        )
                        | models.Q(ciphertext__isnull=True, scrubbed_at__isnull=False),
                        name="setup_exchange_reply_shape",
                    ),
                ],
            },
        )
    ]

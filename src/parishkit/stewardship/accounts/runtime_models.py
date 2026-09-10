"""Runtime configuration and immutable activation evidence, separate from YAML.

This first runtime schema admits Testing only. Campaign/mode/restore workflows
must extend its guards explicitly before those capabilities can be enabled.
"""

from django.db import models

from parishkit.stewardship.storage import ImmutableRecord, MutableRecord


class SystemConfiguration(MutableRecord):
    """One deployment runtime row; only the activation ledger advances its pointer."""

    immutable_fields = MutableRecord.immutable_fields + (
        "mode",
        "testing_recipient",
        "restore_review_required",
    )
    mode = models.CharField(max_length=16, default="testing")
    testing_recipient = models.EmailField()
    restore_review_required = models.BooleanField(default=False)
    active_configuration = models.ForeignKey(
        "AppliedConfigurationVersion", null=True, blank=True, on_delete=models.PROTECT
    )
    current_campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_system_configuration"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                models.Value(1), name="system_configuration_singleton"
            ),
            models.CheckConstraint(
                condition=models.Q(mode="testing"), name="system_testing_only"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    testing_recipient__regex=r"^[^\s@]+@[^\s@]+\.[^\s@]+$"
                ),
                name="system_testing_recipient",
            ),
        ]


class ConfigurationActivation(ImmutableRecord):
    """Each activation atomically advances runtime, request, and safe audit history."""

    configuration = models.OneToOneField(
        "AppliedConfigurationVersion", on_delete=models.PROTECT
    )
    predecessor = models.ForeignKey(
        "AppliedConfigurationVersion",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="activation_successors",
    )
    request = models.OneToOneField(
        "ConfigurationChangeRequest",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="activation",
    )
    sequence = models.PositiveBigIntegerField(unique=True)

    class Meta:
        db_table = "stewardship_config_activation"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(sequence__gte=1), name="activation_positive_sequence"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(predecessor__isnull=True, request__isnull=True, sequence=1)
                    | models.Q(
                        predecessor__isnull=False, request__isnull=False, sequence__gt=1
                    )
                ),
                name="activation_bootstrap_or_request",
            ),
        ]

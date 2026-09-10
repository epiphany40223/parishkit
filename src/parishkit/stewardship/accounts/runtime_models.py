"""Runtime configuration and immutable activation evidence, separate from YAML.

Campaign commands and configuration activations have independent version
sequences. Restore metadata is reserved for its later guarded operational owner.
"""

from django.db import models

from parishkit.stewardship.storage import (
    ImmutableRecord,
    MutableRecord,
    UTCDateTimeField,
)


class SystemConfiguration(MutableRecord):
    """One runtime row; immutable activation and transition ledgers own its pointers."""

    immutable_fields = MutableRecord.immutable_fields + ("testing_recipient",)
    mode = models.CharField(max_length=16, default="testing")
    testing_recipient = models.EmailField()
    restore_review_required = models.BooleanField(default=False)
    configuration_sequence = models.PositiveBigIntegerField(default=1, db_default=1)
    restore_id = models.UUIDField(null=True)
    restore_backup_at = UTCDateTimeField(null=True)
    restore_activated_at = UTCDateTimeField(null=True)
    restore_released_at = UTCDateTimeField(null=True)
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
                condition=models.Q(mode__in=["testing", "production"]),
                name="system_known_mode",
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

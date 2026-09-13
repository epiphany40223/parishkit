"""Immutable, non-secret scope bound to a sealed credential request."""

from django.db import models

from parishkit.stewardship.storage import ImmutableRecord

from .secret_models import SecretReplacementRequest


class ProviderValidationContext(ImmutableRecord):
    """Tell one isolated installer which tenant/mailbox/channel it may test.

    This is input evidence, not a successful provider check or readiness proof.
    The target installer cannot read the complete application configuration.
    """

    request = models.OneToOneField(SecretReplacementRequest, on_delete=models.PROTECT)
    target = models.CharField(max_length=32)
    settings = models.JSONField()

    class Meta:
        db_table = "stewardship_provider_context"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    target__in=["parishsoft", "google_workspace", "slack"]
                ),
                name="provider_context_known_target",
            )
        ]

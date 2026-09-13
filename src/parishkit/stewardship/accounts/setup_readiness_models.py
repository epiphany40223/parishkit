"""Immutable initial-setup readiness selected by one final confirmation."""

from django.db import models

from parishkit.stewardship.storage import ImmutableRecord


class SetupReadinessBinding(ImmutableRecord):
    """Pin reviewed source and successful tests without declaring setup configured.

    Installation and final source/Family activation must consume this exact
    proof. The original source result is catalog evidence, not selected financial
    coverage; that additional load remains a separate finalization requirement.
    """

    intent = models.OneToOneField("SetupConfigurationIntent", on_delete=models.PROTECT)
    source_result = models.ForeignKey("SetupSourceResult", on_delete=models.PROTECT)
    mail_delivery = models.ForeignKey("SetupMailDelivery", on_delete=models.PROTECT)
    slack_delivery = models.ForeignKey(
        "SetupSlackDelivery", on_delete=models.PROTECT, null=True
    )
    testing_recipient = models.EmailField(max_length=254)

    class Meta:
        db_table = "stewardship_setup_readiness_binding"

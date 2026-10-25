"""Immutable initial-setup readiness selected by one final confirmation."""

from django.db import models

from parishkit.stewardship.storage import ImmutableRecord


class SetupReadinessBinding(ImmutableRecord):
    """Pin the checked source and successful tests without declaring setup configured.

    Installation and final source/Family activation must consume this exact
    proof. Since #2201 the source is the setup attempt's newest ready parish
    data check (``source_check``) of the staged ParishSoft key; the final
    load at Finish setup is the only full load. ``source_result``, the staged
    load an earlier release bound, stays only on bindings made before that
    change: SQL requires exactly one of the two, and new bindings the check.
    """

    intent = models.OneToOneField("SetupConfigurationIntent", on_delete=models.PROTECT)
    source_result = models.ForeignKey(
        "SetupSourceResult", on_delete=models.PROTECT, null=True
    )
    source_check = models.ForeignKey(
        "SetupSourceCheck", on_delete=models.PROTECT, null=True
    )
    mail_delivery = models.ForeignKey("SetupMailDelivery", on_delete=models.PROTECT)
    slack_delivery = models.ForeignKey(
        "SetupSlackDelivery", on_delete=models.PROTECT, null=True
    )
    testing_recipient = models.EmailField(max_length=254)

    class Meta:
        db_table = "stewardship_setup_readiness_binding"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    source_result__isnull=True, source_check__isnull=False
                )
                | models.Q(source_result__isnull=False, source_check__isnull=True),
                name="setup_readiness_one_source",
            )
        ]

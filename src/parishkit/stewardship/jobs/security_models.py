"""Frozen security-event recipient cohorts and per-Administrator delivery intents."""

from django.db import models

from parishkit.stewardship.storage import ImmutableRecord


class SecurityCohort(ImmutableRecord):
    """One frozen cohort per event, the recipients the trigger recorded, by its worker.

    The event's recipients are the Administrators who existed before the
    expansion, so the cohort copies them rather than reading current grants;
    an address that stopped being an Administrator since is still told.
    """

    event = models.OneToOneField(
        "stewardship_accounts.PolicySecurityEvent", on_delete=models.PROTECT
    )
    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion", on_delete=models.PROTECT
    )
    parish = models.ForeignKey("stewardship_accounts.Parish", on_delete=models.PROTECT)
    mode = models.CharField(max_length=16)
    addresses = models.JSONField()
    recipient_count = models.PositiveIntegerField()
    run = models.ForeignKey("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    fence = models.PositiveBigIntegerField()
    worker_id = models.UUIDField()

    class Meta:
        db_table = "stewardship_security_cohort"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(mode__in=("testing", "production")),
                name="security_cohort_mode",
            ),
            models.CheckConstraint(
                condition=models.Q(recipient_count__gte=1, fence__gte=1),
                name="security_cohort_counts",
            ),
        ]


class SecurityRecipient(ImmutableRecord):
    """Atomic one-Administrator email intent for one security event."""

    cohort = models.ForeignKey(SecurityCohort, on_delete=models.PROTECT)
    address = models.EmailField()
    outbox = models.OneToOneField(
        "stewardship_jobs.OutboxMessage", on_delete=models.PROTECT
    )

    class Meta:
        db_table = "stewardship_security_recipient"
        constraints = [
            models.UniqueConstraint(
                fields=["cohort", "address"], name="security_recipient_address"
            ),
        ]

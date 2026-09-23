"""Opaque preparation tickets bind queued hints to their original mode/epoch."""

from django.db import models

from parishkit.stewardship.storage import (
    ImmutableRecord,
    MutableRecord,
    UTCDateTimeField,
)

FAMILY_TEST_STATES = ("queued", "prepared", "cancelled", "failed")
# One Admin request may name at most this many Families, and a campaign may
# have at most this many chosen-Family tests unsettled at once.
FAMILY_TEST_LIMIT = 10


class FamilyMailPreparation(ImmutableRecord):
    """No recipients, content or secrets; execution outcomes live in the outbox.

    UUID references deliberately survive deletion of Testing work. They provide
    stale-hint cancellation evidence without retaining a credential or preventing
    the existing epoch/occurrence cleanup owner from removing its records.
    """

    occurrence_id = models.UUIDField()
    task_id = models.UUIDField(unique=True)
    mode = models.CharField(max_length=16)
    rehearsal_epoch_id = models.UUIDField(null=True)

    class Meta:
        db_table = "stewardship_family_mail_preparation"
        constraints = [
            models.UniqueConstraint(
                fields=["occurrence_id", "rehearsal_epoch_id"],
                nulls_distinct=False,
                name="family_mail_preparation_scope",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(mode="production", rehearsal_epoch_id__isnull=True)
                    | models.Q(mode="testing", rehearsal_epoch_id__isnull=False)
                ),
                name="family_mail_preparation_namespace",
            ),
        ]


class FamilyMailTest(MutableRecord):
    """One Admin-chosen Family's Testing send, bound to its epoch and template.

    The ticket is the outbox message's semantic key, so a test can never satisfy
    a scheduled occurrence. Only a queued ticket names its Family: preparation
    and every cancellation scrub the Family link, leaving the Family only on the
    outbox message that Testing cleanup deletes. Rendered content, codes and
    links never enter this table.
    """

    immutable_fields = MutableRecord.immutable_fields + (
        "campaign_id",
        "configuration_id",
        "template_id",
        "requested_by_id",
        "request_key",
        "sequence",
        "reauthenticated_at",
        "rehearsal_epoch_id",
        "task_id",
    )
    write_once_fields = ("outbox_id",)
    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion", on_delete=models.PROTECT
    )
    template = models.ForeignKey(
        "stewardship_accounts.ContentVersion", on_delete=models.PROTECT
    )
    requested_by_id = models.UUIDField()
    request_key = models.UUIDField()
    sequence = models.PositiveBigIntegerField()
    reauthenticated_at = UTCDateTimeField()
    rehearsal_epoch_id = models.UUIDField()
    task = models.OneToOneField("TaskRun", on_delete=models.PROTECT)
    family_id = models.UUIDField(null=True)
    outbox_id = models.UUIDField(null=True)
    state = models.CharField(max_length=16, default="queued", db_default="queued")

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_family_mail_test"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=["requested_by_id", "request_key", "sequence"],
                name="family_mail_test_request",
            ),
            models.CheckConstraint(
                condition=models.Q(sequence__gte=1, sequence__lte=FAMILY_TEST_LIMIT),
                name="family_mail_test_sequence",
            ),
            models.CheckConstraint(
                condition=models.Q(state__in=FAMILY_TEST_STATES),
                name="family_mail_test_known_state",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(state="queued", family_id__isnull=False, outbox_id=None)
                    | models.Q(
                        state="prepared", family_id=None, outbox_id__isnull=False
                    )
                    | models.Q(
                        state__in=["cancelled", "failed"],
                        family_id=None,
                        outbox_id=None,
                    )
                ),
                name="family_mail_test_family_scrub",
            ),
        ]

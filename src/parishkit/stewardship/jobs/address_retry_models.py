"""A Family email's follow-up to the addresses refused only for now (#806).

One email goes to all of a Family's addresses at once, and the provider can
accept it for some and refuse others temporarily. The email itself is then
delivered and never sent again; this record sends the same email (its
original rendering, code and link) to just the refused addresses, retried on
its own schedule, so no address that accepted it ever receives it twice.

Positions index the original rendering's routed recipients. ``positions`` is
fixed at creation (the accepted attempt's refused-for-now positions);
``remaining`` (still to send) only shrinks and ``refused`` (refused
permanently) only grows, inside ``positions``. The SQL guard in frozen
migration 0077 enforces every move; this model only declares the storage.
"""

from django.db import models

from parishkit.stewardship.storage import MutableRecord, UTCDateTimeField

ADDRESS_RETRY_STATES = (
    "waiting",
    "submitting",
    "finished",
    "unknown",
    "failed",
    "cancelled",
)
# States after which the follow-up never sends again.
ADDRESS_RETRY_TERMINAL = ("finished", "unknown", "failed", "cancelled")


class AddressRetry(MutableRecord):
    """One accepted attempt's follow-up to its addresses refused for now.

    The sealed reference is a copy of the parent message's, taken in the
    transaction that recorded the acceptance (the parent's own copy is
    scrubbed then); it is opened against the parent's identity and original
    rendering, never re-sealed, and scrubbed when the follow-up ends.
    """

    immutable_fields = MutableRecord.immutable_fields + (
        "message_id",
        "source_event_id",
        "render_id",
        "task_id",
        "positions",
        "expires_at",
        "token_generation_id",
        "credential_epoch_id",
    )
    message = models.ForeignKey(
        "OutboxMessage", on_delete=models.PROTECT, related_name="address_retries"
    )
    source_event = models.OneToOneField(
        "OutboxEvent", on_delete=models.PROTECT, related_name="address_retry"
    )
    render = models.ForeignKey(
        "OutboxRender", on_delete=models.PROTECT, related_name="+"
    )
    task = models.OneToOneField(
        "TaskRun", on_delete=models.PROTECT, related_name="address_retry"
    )
    positions = models.JSONField()
    remaining = models.JSONField()
    refused = models.JSONField()
    # The latest finished follow-up attempt: its status and the positions it
    # refused permanently and for now (no address, provider text or secret).
    outcome = models.JSONField(null=True)
    state = models.CharField(max_length=16, default="waiting", db_default="waiting")
    attempt = models.PositiveIntegerField(default=0, db_default=0)
    # Attempts a sending limit or provider outage answered: they do not
    # count against the follow-up's attempt budget.
    spared = models.PositiveIntegerField(default=0, db_default=0)
    not_before = UTCDateTimeField()
    expires_at = UTCDateTimeField()
    run_id = models.UUIDField(null=True)
    task_fence = models.PositiveBigIntegerField(null=True)
    worker_id = models.UUIDField(null=True)
    submitted_at = UTCDateTimeField(null=True)
    provider_deadline = UTCDateTimeField(null=True)
    finished_at = UTCDateTimeField(null=True)
    reason = models.CharField(max_length=32, blank=True, default="", db_default="")
    sealed_substitutions = models.TextField(null=True)
    sealed_key_id = models.CharField(max_length=48, null=True)
    token_generation_id = models.UUIDField()
    credential_epoch_id = models.UUIDField()

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_outbox_address_retry"
        constraints = MutableRecord.Meta.constraints + [
            models.CheckConstraint(
                condition=models.Q(state__in=ADDRESS_RETRY_STATES),
                name="address_retry_state",
            ),
            models.CheckConstraint(
                condition=models.Q(reason="")
                | models.Q(reason__regex=r"^[a-z][a-z0-9_]{0,31}$"),
                name="address_retry_reason",
            ),
            # Ended: finished at a time, sealed reference scrubbed. Open:
            # not finished, sealed reference kept.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        state__in=ADDRESS_RETRY_TERMINAL,
                        finished_at__isnull=False,
                        sealed_substitutions=None,
                        sealed_key_id=None,
                    )
                    | (
                        ~models.Q(state__in=ADDRESS_RETRY_TERMINAL)
                        & models.Q(
                            finished_at=None,
                            sealed_substitutions__isnull=False,
                            sealed_key_id__isnull=False,
                        )
                    )
                ),
                name="address_retry_scrub",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        attempt=0,
                        spared=0,
                        run_id=None,
                        task_fence=None,
                        worker_id=None,
                        submitted_at=None,
                        provider_deadline=None,
                    )
                    | models.Q(
                        attempt__gte=1,
                        spared__lte=models.F("attempt"),
                        run_id__isnull=False,
                        task_fence__gte=1,
                        task_fence__isnull=False,
                        worker_id__isnull=False,
                        submitted_at__isnull=False,
                        provider_deadline__gte=models.F("submitted_at"),
                        provider_deadline__isnull=False,
                    )
                ),
                name="address_retry_attempt_shape",
            ),
        ]

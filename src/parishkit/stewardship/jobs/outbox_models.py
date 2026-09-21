"""Durable delivery identity and redacted, numbered attempt history.

The mutable selector contains the only sealed substitution; history never copies
ciphertext. Immutable render versions preserve the exact redacted content and
recipient selection used by each attempt even when later unsent work is prepared
again. Runtime exposure belongs to the compiled delivery owners, not these ORM
declarations or possession of a message UUID.
"""

from django.db import models
from django.db.models.functions import Now

from parishkit.stewardship.storage import (
    ImmutableRecord,
    MutableRecord,
    UTCDateTimeField,
)

from .delivery_states import TERMINAL_DELIVERY_STATES, DeliveryAction, DeliveryState

TERMINAL_VALUES = tuple(
    state.value for state in DeliveryState if state in TERMINAL_DELIVERY_STATES
)
DELIVERY_PURPOSES = (
    "initial",
    "reminder",
    "receipt",
    "daily_digest",
    "weekly_digest",
    "operational",
    "security_event",
)
# Purposes routed to Administrators as operational mail: no campaign, no
# Family, no credential namespace and no Testing redirection.
ADMINISTRATOR_PURPOSES = ("operational", "security_event")
DELIVERY_ACTIONS = ("created", "prepared", "hold", "release_hold") + tuple(
    action.value for action in DeliveryAction
)


class DeliveryCommand(models.Model):
    """Current command evidence is copied atomically into immutable history.

    Notes are private reconciliation records, not operational log messages. Raw
    provider identifiers and error responses must never be placed in them.
    """

    command_id = models.UUIDField()
    command_digest = models.CharField(max_length=64)
    action = models.CharField(max_length=24)
    provider_key_digest = models.CharField(max_length=64, blank=True)
    provider_message_digest = models.CharField(max_length=64, blank=True)
    evidence_digest = models.CharField(max_length=64, blank=True)
    evidence_note = models.CharField(max_length=2000, blank=True)
    reason = models.CharField(max_length=64, blank=True)

    class Meta:
        abstract = True


class DeliveryPauseHold(ImmutableRecord):
    """One campaign pause version can hold scheduled mail and direct receipts."""

    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    pause_version = models.PositiveBigIntegerField()

    class Meta:
        db_table = "stewardship_delivery_pause_hold"
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "pause_version"], name="delivery_pause_identity"
            ),
            models.CheckConstraint(
                condition=models.Q(pause_version__gte=1), name="delivery_pause_positive"
            ),
        ]


class OutboxMessage(MutableRecord, DeliveryCommand):
    """One scoped semantic delivery across automatic and explicit attempts.

    Rendering and sealed-value replacement require separate journaled preparation
    admission; they are not ordinary model edits. A terminal failed message may
    be explicitly retried, but a delivered or cancelled identity never reopens.
    Pause is orthogonal to state and does not manufacture a delivery outcome.
    """

    immutable_fields = MutableRecord.immutable_fields + (
        "scope_id",
        "semantic_key",
        "campaign_id",
        "family_id",
        "mode",
        "routing",
        "purpose",
        "task_id",
        "credential_namespace",
        "rehearsal_epoch_id",
    )
    scope_id = models.UUIDField()
    semantic_key = models.UUIDField()
    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", null=True, on_delete=models.PROTECT
    )
    family = models.ForeignKey(
        "stewardship_campaigns.FamilyCampaign", null=True, on_delete=models.PROTECT
    )
    mode = models.CharField(max_length=16)
    routing = models.CharField(max_length=24)
    purpose = models.CharField(max_length=24)
    task = models.OneToOneField("TaskRun", on_delete=models.PROTECT)
    render = models.ForeignKey(
        "OutboxRender", on_delete=models.PROTECT, related_name="+"
    )
    credential_namespace = models.CharField(max_length=16, default="none")
    rehearsal_epoch_id = models.UUIDField(null=True)
    token_generation_id = models.UUIDField(null=True)
    credential_epoch_id = models.UUIDField(null=True)
    sealed_substitutions = models.TextField(null=True)
    sealed_key_id = models.CharField(max_length=48, null=True)
    state = models.CharField(max_length=24, default="pending", db_default="pending")
    attempt = models.PositiveBigIntegerField(default=0, db_default=0)
    not_before = UTCDateTimeField(db_default=Now())
    run = models.ForeignKey(
        "TaskRun", null=True, on_delete=models.PROTECT, related_name="outbox_attempts"
    )
    task_fence = models.PositiveBigIntegerField(null=True)
    worker_id = models.UUIDField(null=True)
    submitted_at = UTCDateTimeField(null=True)
    provider_deadline = UTCDateTimeField(null=True)
    finished_at = UTCDateTimeField(null=True)
    pause_hold = models.ForeignKey(
        DeliveryPauseHold, null=True, on_delete=models.PROTECT
    )
    pause_version = models.PositiveBigIntegerField(null=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_outbox_message"
        indexes = [
            models.Index(fields=["state", "not_before"], name="outbox_due"),
            models.Index(fields=["campaign", "state"], name="outbox_campaign_state"),
        ]
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=["scope_id", "mode", "semantic_key"], name="outbox_semantic_key"
            ),
            models.CheckConstraint(
                condition=models.Q(state__in=[state.value for state in DeliveryState]),
                name="outbox_known_state",
            ),
            models.CheckConstraint(
                condition=models.Q(action__in=DELIVERY_ACTIONS), name="outbox_action"
            ),
            models.CheckConstraint(
                condition=models.Q(purpose__in=DELIVERY_PURPOSES), name="outbox_purpose"
            ),
            models.CheckConstraint(
                condition=models.Q(mode__in=["testing", "production"]),
                name="outbox_mode",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        routing="operational",
                        purpose__in=ADMINISTRATOR_PURPOSES,
                        family=None,
                    )
                    | (
                        ~models.Q(purpose__in=ADMINISTRATOR_PURPOSES)
                        & models.Q(campaign__isnull=False)
                        & (
                            models.Q(routing="testing_override", mode="testing")
                            | models.Q(routing="production", mode="production")
                        )
                    )
                ),
                name="outbox_routing",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        purpose__in=["initial", "reminder", "receipt"],
                        family__isnull=False,
                    )
                    | models.Q(
                        purpose__in=[
                            "daily_digest",
                            "weekly_digest",
                            "operational",
                            "security_event",
                        ],
                        family=None,
                    )
                ),
                name="outbox_family_purpose",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        credential_namespace="none",
                        rehearsal_epoch_id=None,
                        token_generation_id=None,
                        credential_epoch_id=None,
                        sealed_substitutions=None,
                        sealed_key_id=None,
                    )
                    | (
                        models.Q(family__isnull=False)
                        & (
                            models.Q(
                                credential_namespace="production",
                                mode="production",
                                rehearsal_epoch_id=None,
                                token_generation_id__isnull=False,
                                credential_epoch_id__isnull=False,
                            )
                            | models.Q(
                                credential_namespace="rehearsal",
                                mode="testing",
                                rehearsal_epoch_id__isnull=False,
                                token_generation_id=None,
                                credential_epoch_id=None,
                            )
                        )
                    )
                ),
                name="outbox_credential_namespace",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        state__in=TERMINAL_VALUES,
                        finished_at__isnull=False,
                        sealed_substitutions=None,
                        sealed_key_id=None,
                    )
                    | (
                        ~models.Q(state__in=TERMINAL_VALUES)
                        & models.Q(finished_at=None)
                        & (
                            models.Q(credential_namespace="none")
                            | models.Q(
                                sealed_substitutions__isnull=False,
                                sealed_key_id__isnull=False,
                            )
                        )
                    )
                ),
                name="outbox_terminal_scrub",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        attempt=0,
                        run=None,
                        task_fence=None,
                        worker_id=None,
                        submitted_at=None,
                        provider_deadline=None,
                    )
                    | models.Q(
                        attempt__gte=1,
                        run__isnull=False,
                        task_fence__gte=1,
                        task_fence__isnull=False,
                        worker_id__isnull=False,
                        submitted_at__isnull=False,
                        provider_deadline__gte=models.F("submitted_at"),
                        provider_deadline__isnull=False,
                    )
                ),
                name="outbox_attempt_shape",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(
                        state__in=[
                            "submitting",
                            "delivery_unknown",
                            "delivered",
                            "retry_wait",
                        ]
                    )
                    | models.Q(attempt__gte=1)
                ),
                name="outbox_submitted_outcome",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(pause_hold=None)
                    | models.Q(
                        routing="production",
                        campaign__isnull=False,
                        pause_version__gte=1,
                        pause_version__isnull=False,
                        state__in=["pending", "retry_wait"],
                    )
                ),
                name="outbox_pause_shape",
            ),
        ]


class OutboxRender(ImmutableRecord):
    """Credential-free rendering selected before any associated provider attempt."""

    message = models.ForeignKey(
        OutboxMessage, on_delete=models.PROTECT, related_name="renders"
    )
    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion", on_delete=models.PROTECT
    )
    template = models.ForeignKey(
        "stewardship_accounts.ContentVersion", null=True, on_delete=models.PROTECT
    )
    sender = models.EmailField()
    reply_to = models.EmailField()
    intended_recipients = models.JSONField()
    routed_recipients = models.JSONField()
    subject = models.CharField(max_length=254)
    html = models.TextField()
    text = models.TextField()
    payload_digest = models.CharField(max_length=64)

    class Meta:
        db_table = "stewardship_outbox_render"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(payload_digest__regex=r"^[0-9a-f]{64}$"),
                name="outbox_render_digest",
            ),
            models.CheckConstraint(
                condition=~models.Q(subject=""),
                name="outbox_render_subject",
            ),
        ]


class OutboxEvent(ImmutableRecord, DeliveryCommand):
    """Versioned command/outcome history never overwrites an unknown attempt.

    Each provider submission starts a numbered attempt. Later resolution and
    explicit resend append history under the same message, without copying any
    sealed substitutions, provider error payload, or raw provider identifier.
    """

    message = models.ForeignKey(
        OutboxMessage, on_delete=models.PROTECT, related_name="events"
    )
    version = models.PositiveBigIntegerField()
    previous_state = models.CharField(max_length=24, blank=True)
    state = models.CharField(max_length=24)
    attempt = models.PositiveBigIntegerField()
    render = models.ForeignKey(OutboxRender, on_delete=models.PROTECT)
    run = models.ForeignKey("TaskRun", null=True, on_delete=models.PROTECT)
    task_fence = models.PositiveBigIntegerField(null=True)
    worker_id = models.UUIDField(null=True)
    not_before = UTCDateTimeField()
    submitted_at = UTCDateTimeField(null=True)
    provider_deadline = UTCDateTimeField(null=True)
    finished_at = UTCDateTimeField(null=True)
    provider_identity = models.CharField(
        max_length=64, blank=True, default="", db_default="", editable=False
    )

    class Meta:
        db_table = "stewardship_outbox_event"
        indexes = [
            models.Index(fields=["message", "attempt"], name="outbox_attempt_history"),
            models.Index(
                fields=["provider_identity", "-created_at", "-id"],
                name="outbox_provider_history",
                condition=models.Q(
                    previous_state="submitting",
                    submitted_at__isnull=False,
                    reason__in=(
                        "smtp_accepted",
                        "smtp_transient",
                        "smtp_unavailable",
                        "smtp_permanent",
                        "smtp_delivery_unknown",
                        "smtp_systemic",
                    ),
                )
                & (
                    models.Q(evidence_note__startswith='{"health":"healthy",')
                    | models.Q(evidence_note__startswith='{"health":"unavailable",')
                    | models.Q(evidence_note__startswith='{"health":"systemic",')
                ),
            ),
            models.Index(
                fields=["provider_identity", "-created_at", "-id"],
                name="outbox_provider_healthy",
                condition=models.Q(evidence_note__startswith='{"health":"healthy",'),
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["message", "version"], name="outbox_event_version"
            ),
            models.UniqueConstraint(
                fields=["message", "command_id"], name="outbox_command_once"
            ),
            models.CheckConstraint(
                condition=models.Q(version__gte=1), name="outbox_event_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(command_digest__regex=r"^[0-9a-f]{64}$"),
                name="outbox_command_digest",
            ),
            models.CheckConstraint(
                condition=models.Q(action__in=DELIVERY_ACTIONS),
                name="outbox_event_action",
            ),
            models.CheckConstraint(
                condition=models.Q(state__in=[state.value for state in DeliveryState]),
                name="outbox_event_state",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    previous_state__in=[""] + [state.value for state in DeliveryState]
                ),
                name="outbox_event_previous_state",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(provider_key_digest="")
                    | models.Q(provider_key_digest__regex=r"^[0-9a-f]{64}$")
                )
                & (
                    models.Q(provider_message_digest="")
                    | models.Q(provider_message_digest__regex=r"^[0-9a-f]{64}$")
                )
                & (
                    models.Q(evidence_digest="")
                    | models.Q(evidence_digest__regex=r"^[0-9a-f]{64}$")
                ),
                name="outbox_event_digests",
            ),
            models.CheckConstraint(
                condition=models.Q(reason="")
                | models.Q(reason__regex=r"^[a-z][a-z0-9_]{0,63}$"),
                name="outbox_event_reason",
            ),
        ]

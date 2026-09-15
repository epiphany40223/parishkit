"""Durable go-live intent and cleanup checkpoints, not Production activation."""

from django.db import models

from parishkit.stewardship.storage import (
    ImmutableRecord,
    MutableRecord,
    UTCDateTimeField,
)

from .production_states import (
    GATE_OWNING_PRODUCTION_STATES,
    ProductionAction,
    ProductionState,
)

GATE_STATES = tuple(
    state.value for state in ProductionState if state in GATE_OWNING_PRODUCTION_STATES
)
PRODUCTION_ACTIONS = (
    ("created",)
    + (ProductionAction.START.value, ProductionAction.RECOVER.value, "checkpoint")
    + tuple(
        action.value
        for action in ProductionAction
        if action not in (ProductionAction.START, ProductionAction.RECOVER)
    )
)


class TestingAggregate(ImmutableRecord):
    """Non-sensitive pre-cleanup evidence survives removal of Testing detail."""

    campaign = models.ForeignKey("Campaign", on_delete=models.PROTECT)
    inventory_digest = models.CharField(max_length=64)
    readiness_digest = models.CharField(max_length=64)
    submissions = models.PositiveBigIntegerField()
    families = models.PositiveBigIntegerField()
    messages = models.PositiveBigIntegerField()
    delivered = models.PositiveBigIntegerField()
    failed = models.PositiveBigIntegerField()
    cancelled = models.PositiveBigIntegerField()

    class Meta:
        db_table = "stewardship_testing_aggregate"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(inventory_digest__regex=r"^[0-9a-f]{64}$")
                & models.Q(readiness_digest__regex=r"^[0-9a-f]{64}$"),
                name="testing_aggregate_digests",
            ),
            models.CheckConstraint(
                condition=models.Q(families__lte=models.F("submissions")),
                name="testing_aggregate_families",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    messages=models.F("delivered")
                    + models.F("failed")
                    + models.F("cancelled")
                ),
                name="testing_aggregate_terminal",
            ),
        ]


class ProductionTransitionRequest(MutableRecord):
    """One irreversible cleanup request owns the gate even after exhausted retry.

    Inventory creation, readiness verification and deletion belong to compiled
    owning services. Their UUID/digest references here do not confer permission.
    Activation fields may be filled only by the later final-transition owner.
    """

    immutable_fields = MutableRecord.immutable_fields + (
        "campaign_id",
        "initiated_by_id",
        "request_key",
        "configuration_id",
        "aggregate_id",
        "inventory_digest",
        "inventory_counts",
        "inventory_total",
        "gate_version",
        "invalidated_epoch_id",
        "task_id",
        "acknowledged_at",
        "reauthenticated_at",
    )
    campaign = models.ForeignKey("Campaign", on_delete=models.PROTECT)
    initiated_by_id = models.UUIDField()
    request_key = models.UUIDField()
    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion", on_delete=models.PROTECT
    )
    aggregate = models.OneToOneField(TestingAggregate, on_delete=models.PROTECT)
    inventory_digest = models.CharField(max_length=64)
    inventory_counts = models.JSONField()
    inventory_total = models.PositiveBigIntegerField()
    gate_version = models.PositiveBigIntegerField()
    invalidated_epoch_id = models.UUIDField(null=True)
    task = models.OneToOneField("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    acknowledged_at = UTCDateTimeField()
    reauthenticated_at = UTCDateTimeField()
    state = models.CharField(
        max_length=24, default="cleanup_queued", db_default="cleanup_queued"
    )
    action = models.CharField(max_length=24, default="created", db_default="created")
    command_id = models.UUIDField()
    processed_count = models.PositiveBigIntegerField(default=0, db_default=0)
    checkpoint_sequence = models.PositiveBigIntegerField(default=0, db_default=0)
    run = models.ForeignKey(
        "stewardship_jobs.TaskRun",
        null=True,
        on_delete=models.PROTECT,
        related_name="production_attempts",
    )
    task_fence = models.PositiveBigIntegerField(null=True)
    worker_id = models.UUIDField(null=True)
    failure_reason = models.CharField(max_length=64, blank=True)
    activated_at = UTCDateTimeField(null=True)
    activation_digest = models.CharField(max_length=64, blank=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_production_request"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=["campaign", "request_key"], name="production_request_key"
            ),
            models.UniqueConstraint(
                fields=["campaign"],
                condition=models.Q(state__in=GATE_STATES),
                name="production_one_gate_owner",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    state__in=[state.value for state in ProductionState]
                ),
                name="production_known_state",
            ),
            models.CheckConstraint(
                condition=models.Q(action__in=list(PRODUCTION_ACTIONS)),
                name="production_known_action",
            ),
            models.CheckConstraint(
                condition=models.Q(gate_version__gte=1)
                & models.Q(processed_count__lte=models.F("inventory_total")),
                name="production_count_bounds",
            ),
            models.CheckConstraint(
                condition=models.Q(inventory_digest__regex=r"^[0-9a-f]{64}$"),
                name="production_inventory_digest",
            ),
            models.CheckConstraint(
                condition=models.Q(failure_reason="")
                | models.Q(failure_reason__regex=r"^[a-z][a-z0-9_]{0,63}$"),
                name="production_safe_failure",
            ),
            models.CheckConstraint(
                condition=models.Q(run=None, task_fence=None, worker_id=None)
                | models.Q(
                    run__isnull=False,
                    task_fence__isnull=False,
                    task_fence__gte=1,
                    worker_id__isnull=False,
                ),
                name="production_attempt_binding",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    state="activated",
                    activated_at__isnull=False,
                    activation_digest__regex=r"^[0-9a-f]{64}$",
                )
                | (
                    ~models.Q(state="activated")
                    & models.Q(activated_at=None, activation_digest="")
                ),
                name="production_activation_shape",
            ),
        ]


class ProductionCleanupCheckpoint(ImmutableRecord):
    """One exact batch commits alongside its owner-verified Testing deletions."""

    request = models.ForeignKey(
        ProductionTransitionRequest,
        on_delete=models.PROTECT,
        related_name="checkpoints",
    )
    command_id = models.UUIDField()
    sequence = models.PositiveBigIntegerField()
    counts = models.JSONField()
    deleted_count = models.PositiveBigIntegerField()
    batch_digest = models.CharField(max_length=64)
    run = models.ForeignKey("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    task_fence = models.PositiveBigIntegerField()
    worker_id = models.UUIDField()

    class Meta:
        db_table = "stewardship_production_checkpoint"
        constraints = [
            models.UniqueConstraint(
                fields=["request", "sequence"], name="production_checkpoint_order"
            ),
            models.UniqueConstraint(
                fields=["request", "command_id"], name="production_checkpoint_command"
            ),
            models.UniqueConstraint(
                fields=["request", "batch_digest"], name="production_checkpoint_batch"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    sequence__gte=1, deleted_count__gte=1, task_fence__gte=1
                ),
                name="production_checkpoint_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(batch_digest__regex=r"^[0-9a-f]{64}$"),
                name="production_batch_digest",
            ),
        ]


class ProductionTransitionEvent(ImmutableRecord):
    """Append-only non-sensitive state snapshot, generated with the request write."""

    request = models.ForeignKey(
        ProductionTransitionRequest, on_delete=models.PROTECT, related_name="events"
    )
    command_id = models.UUIDField()
    version = models.PositiveBigIntegerField()
    previous_state = models.CharField(max_length=24, blank=True)
    state = models.CharField(max_length=24)
    action = models.CharField(max_length=24)
    snapshot = models.JSONField()

    class Meta:
        db_table = "stewardship_production_event"
        constraints = [
            models.UniqueConstraint(
                fields=["request", "version"], name="production_event_version"
            ),
            models.UniqueConstraint(
                fields=["request", "command_id"], name="production_event_command"
            ),
            models.CheckConstraint(
                condition=models.Q(version__gte=1), name="production_event_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    state__in=[state.value for state in ProductionState]
                ),
                name="production_event_state",
            ),
        ]

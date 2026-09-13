"""Durable task identity and fenced history; no scheduler or provider execution.

The initial run is its own retry root and logical-operation identity. Later runs
retain that root and domain request, without reopening failed historical rows.
Only internal, non-secret identifiers and coded reasons belong in these records.
"""

from django.db import models
from django.db.models.functions import Now

from parishkit.stewardship.storage import (
    ImmutableRecord,
    MutableRecord,
    UTCDateTimeField,
)

from .phases import PHASE_VALUES

TASK_STATES = (
    "queued",
    "running",
    "retry_wait",
    "abandoned",
    "succeeded",
    "failed",
    "cancelled",
)
NONTERMINAL_STATES = TASK_STATES[:4]
TASK_ACTIONS = (
    "created",
    "explicit_retry",
    "claim",
    "heartbeat",
    "progress",
    "complete",
    "retryable_failure",
    "permanent_failure",
    "safe_cancel",
    "lease_expired",
    "recovery_retry",
    "recovery_complete",
    "recovery_fail",
    "recovery_cancel",
)


class TaskRun(MutableRecord):
    """One execution in a retry chain, with a versioned, expiring worker claim.

    The root UUID is also the logical-operation UUID. Execution keys are UUID text
    for initial runs and database-derived ``retry:<root>:<sequence>`` for retries.
    Task type is a developer-defined identifier, not a user-entered description.
    No task arguments, report values, provider errors, or credentials are accepted.
    """

    immutable_fields = MutableRecord.immutable_fields + (
        "task_type",
        "idempotency_key",
        "root_id",
        "parent_id",
        "retry_sequence",
        "retry_command_id",
        "domain_request_id",
        "initiated_by_id",
    )
    task_type = models.CharField(max_length=64)
    idempotency_key = models.CharField(max_length=80, null=True, blank=True)
    root = models.ForeignKey(
        "self", on_delete=models.PROTECT, related_name="chain_runs"
    )
    parent = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="retries"
    )
    retry_sequence = models.PositiveBigIntegerField(default=0)
    retry_command_id = models.UUIDField(null=True, blank=True)
    domain_request_id = models.UUIDField(null=True, blank=True)
    initiated_by_id = models.UUIDField(null=True, blank=True)
    state = models.CharField(max_length=16, default="queued", db_default="queued")
    action = models.CharField(max_length=24, default="created", db_default="created")
    attempt = models.PositiveBigIntegerField(default=0, db_default=0)
    fence = models.PositiveBigIntegerField(default=0, db_default=0)
    worker_id = models.UUIDField(null=True, blank=True)
    heartbeat_at = UTCDateTimeField(null=True, blank=True)
    lease_expires_at = UTCDateTimeField(null=True, blank=True)
    not_before = UTCDateTimeField(db_default=Now())
    progress_current = models.PositiveBigIntegerField(default=0, db_default=0)
    progress_total = models.PositiveBigIntegerField(default=0, db_default=0)
    phase = models.CharField(
        max_length=32, default="unspecified", db_default="unspecified"
    )

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_task_run"
        indexes = [
            models.Index(fields=["state", "not_before"], name="task_due"),
            models.Index(fields=["state", "lease_expires_at"], name="task_lease"),
        ]
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=["task_type", "idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="task_execution_key",
            ),
            models.UniqueConstraint(
                fields=["root", "retry_sequence"], name="task_retry_sequence"
            ),
            models.UniqueConstraint(
                fields=["root"],
                condition=models.Q(state__in=NONTERMINAL_STATES),
                name="task_one_nonterminal",
            ),
            models.UniqueConstraint(
                fields=["root", "retry_command_id"],
                condition=models.Q(retry_command_id__isnull=False),
                name="task_retry_command",
            ),
            models.CheckConstraint(
                condition=models.Q(state__in=TASK_STATES), name="task_known_state"
            ),
            models.CheckConstraint(
                condition=models.Q(action__in=TASK_ACTIONS), name="task_known_action"
            ),
            models.CheckConstraint(
                condition=models.Q(task_type__regex=r"^[a-z][a-z0-9_]{0,63}$"),
                name="task_type_identifier",
            ),
            models.CheckConstraint(
                condition=models.Q(progress_current__lte=models.F("progress_total")),
                name="task_progress_bounds",
            ),
            models.CheckConstraint(
                condition=models.Q(phase__in=PHASE_VALUES), name="task_phase_known"
            ),
            models.CheckConstraint(
                condition=models.Q(fence__gte=models.F("attempt")),
                name="task_fence_bounds",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    state="running",
                    worker_id__isnull=False,
                    heartbeat_at__isnull=False,
                    lease_expires_at__isnull=False,
                )
                | (
                    ~models.Q(state="running") & models.Q(lease_expires_at__isnull=True)
                ),
                name="task_claim_shape",
            ),
        ]


class TaskRunEvent(ImmutableRecord):
    """One immutable snapshot per version, including each numbered attempt.

    Claim events begin an attempt; failure/completion/expiry events retain its
    outcome. Heartbeat/progress events retain their own version and fencing evidence.
    History is generated by SQL with the task update, not best-effort application logs.
    """

    run = models.ForeignKey(TaskRun, on_delete=models.PROTECT, related_name="events")
    version = models.PositiveBigIntegerField()
    previous_state = models.CharField(max_length=16, blank=True)
    state = models.CharField(max_length=16)
    action = models.CharField(max_length=24)
    attempt = models.PositiveBigIntegerField()
    fence = models.PositiveBigIntegerField()
    worker_id = models.UUIDField(null=True, blank=True)
    heartbeat_at = UTCDateTimeField(null=True, blank=True)
    lease_expires_at = UTCDateTimeField(null=True, blank=True)
    not_before = UTCDateTimeField()
    progress_current = models.PositiveBigIntegerField()
    progress_total = models.PositiveBigIntegerField()
    phase = models.CharField(
        max_length=32, default="unspecified", db_default="unspecified"
    )

    class Meta:
        db_table = "stewardship_task_event"
        constraints = [
            models.UniqueConstraint(
                fields=["run", "version"], name="task_event_version"
            ),
            models.CheckConstraint(
                condition=models.Q(version__gte=1), name="task_event_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(phase__in=PHASE_VALUES),
                name="task_event_phase_known",
            ),
        ]

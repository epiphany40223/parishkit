"""Campaign runtime history and resumable work, separate from immutable YAML.

Soft request/token/source identifiers are binding references for later owning
services, never assertions that their readiness or provider effects occurred.
"""

from django.db import models

from parishkit.stewardship.storage import (
    ImmutableRecord,
    MutableRecord,
    UTCDateTimeField,
)


class CampaignWorkGate(MutableRecord):
    """Shared purge admission reservation, owned by DAT-09's eventual request.

    This is the gate substrate, not a second purge state machine. The owning
    PurgeRequest must reference this unique request identity and transition it
    atomically; no purge executor or deletion is exposed by Phase 1A.
    """

    immutable_fields = MutableRecord.immutable_fields + ("campaign_id", "request_id")
    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    request_id = models.UUIDField(unique=True)
    state = models.CharField(max_length=16, default="preparing")

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_campaign_work_gate"
        constraints = MutableRecord.Meta.constraints + [
            models.CheckConstraint(
                condition=models.Q(
                    state__in=["preparing", "running", "released", "tombstone"]
                ),
                name="campaign_work_gate_state",
            ),
            models.UniqueConstraint(
                fields=["campaign"],
                condition=models.Q(state__in=["preparing", "running"]),
                name="campaign_one_work_gate",
            ),
        ]


class CampaignTransition(ImmutableRecord):
    """An idempotent, attributed lifecycle command and its atomic before/after facts."""

    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    request_id = models.UUIDField(unique=True)
    action = models.CharField(max_length=24)
    expected_version = models.PositiveBigIntegerField()
    expected_runtime_version = models.PositiveBigIntegerField()
    before_state = models.CharField(max_length=24)
    after_state = models.CharField(max_length=24)
    before_mode = models.CharField(max_length=16)
    after_mode = models.CharField(max_length=16)
    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion", on_delete=models.PROTECT
    )
    prior_projection = models.ForeignKey(
        "stewardship_campaigns.CampaignConfiguration",
        null=True,
        on_delete=models.PROTECT,
    )
    boundary_id = models.UUIDField(null=True)
    task_fence = models.PositiveBigIntegerField(null=True)
    token_generation_id = models.UUIDField(null=True)
    reason = models.CharField(max_length=1024, default="")

    class Meta:
        db_table = "stewardship_campaign_transition"
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "expected_version"],
                name="campaign_transition_version",
            )
        ]


class RuntimeTransition(ImmutableRecord):
    """Global mode/pointer/restore evidence; campaign archive history is unchanged."""

    request_id = models.UUIDField(unique=True)
    expected_version = models.PositiveBigIntegerField(unique=True)
    action = models.CharField(max_length=24)
    before_mode = models.CharField(max_length=16)
    after_mode = models.CharField(max_length=16)
    before_campaign_id = models.UUIDField(null=True)
    after_campaign_id = models.UUIDField(null=True)
    campaign_transition = models.OneToOneField(
        CampaignTransition, null=True, on_delete=models.PROTECT
    )
    restore_id = models.UUIDField(null=True)
    backup_at = UTCDateTimeField(null=True)
    reason = models.CharField(max_length=1024, default="")

    class Meta:
        db_table = "stewardship_runtime_transition"


class CampaignConfigurationIntent(ImmutableRecord):
    """Bind an exceptional end edit to one exact YAML request and runtime version.

    This record is not authorization: its installer must rerun the owning
    verifier at preparation and final activation, including interrupted recovery.
    """

    request = models.OneToOneField(
        "stewardship_accounts.ConfigurationChangeRequest", on_delete=models.PROTECT
    )
    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    prior_projection = models.ForeignKey(
        "stewardship_campaigns.CampaignConfiguration", on_delete=models.PROTECT
    )
    action = models.CharField(max_length=16)
    expected_version = models.PositiveBigIntegerField()
    expected_runtime_version = models.PositiveBigIntegerField()
    token_generation_id = models.UUIDField(null=True)

    class Meta:
        db_table = "stewardship_campaign_config_intent"


class CampaignControlChange(ImmutableRecord):
    """Orthogonal pause/live-effect history never changes lifecycle or routing."""

    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    request_id = models.UUIDField(unique=True)
    expected_version = models.PositiveBigIntegerField()
    expected_runtime_version = models.PositiveBigIntegerField()
    action = models.CharField(max_length=24)
    reason = models.CharField(max_length=1024, default="")
    evidence_id = models.UUIDField(null=True)
    occurred_at = UTCDateTimeField()

    class Meta:
        db_table = "stewardship_campaign_control"
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "expected_version"], name="campaign_control_version"
            )
        ]


class CampaignConfigurationAbort(ImmutableRecord):
    """Durable cancellation of an exceptional candidate that never became applied.

    The file installer restores the still-active predecessor only after this
    decision commits. Recovery repeats that exact selection; applied history
    is never rolled back or removed.
    """

    intent = models.OneToOneField(CampaignConfigurationIntent, on_delete=models.PROTECT)
    reason = models.CharField(max_length=1024)

    class Meta:
        db_table = "stewardship_campaign_config_abort"


class CampaignBoundaryOccurrence(MutableRecord):
    """One resolved boundary, with task-owned attempts and immutable transition link."""

    immutable_fields = MutableRecord.immutable_fields + (
        "campaign_id",
        "kind",
        "due_at",
    )
    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    kind = models.CharField(max_length=8)
    due_at = UTCDateTimeField()
    state = models.CharField(max_length=16, default="pending")
    task = models.ForeignKey(
        "stewardship_jobs.TaskRun", null=True, on_delete=models.PROTECT
    )
    transition = models.OneToOneField(
        CampaignTransition, null=True, on_delete=models.PROTECT
    )
    completed_at = UTCDateTimeField(null=True)
    reason = models.CharField(max_length=64, default="")

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_campaign_boundary"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=["campaign", "kind", "due_at"], name="campaign_boundary_identity"
            ),
            models.CheckConstraint(
                condition=models.Q(kind__in=["start", "close"]),
                name="campaign_boundary_kind",
            ),
            models.CheckConstraint(
                condition=models.Q(state__in=["pending", "succeeded", "skipped"]),
                name="campaign_boundary_state",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        state="pending",
                        completed_at__isnull=True,
                        transition__isnull=True,
                    )
                    | models.Q(
                        state="succeeded",
                        completed_at__isnull=False,
                        transition__isnull=False,
                    )
                    | models.Q(
                        state="skipped",
                        completed_at__isnull=False,
                        transition__isnull=True,
                    )
                ),
                name="campaign_boundary_result",
            ),
        ]
        indexes = [
            models.Index(fields=["state", "due_at"], name="campaign_boundary_due")
        ]


class ActivationCatchUpDemand(MutableRecord):
    """Unfinished demand, not TaskRun status, supplies the live schedule hold."""

    immutable_fields = MutableRecord.immutable_fields + (
        "campaign_id",
        "activation_id",
        "cutoff",
        "configuration_id",
    )
    write_once_fields = ("source_snapshot_id", "task_root_id")
    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    activation = models.OneToOneField(CampaignTransition, on_delete=models.PROTECT)
    cutoff = UTCDateTimeField()
    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion", on_delete=models.PROTECT
    )
    source_snapshot_id = models.UUIDField(null=True)
    task_root = models.ForeignKey(
        "stewardship_jobs.TaskRun", null=True, on_delete=models.PROTECT
    )
    phase = models.CharField(max_length=32, default="pending")
    cursor = models.CharField(max_length=128, default="")
    groups_completed = models.PositiveBigIntegerField(default=0)
    items_completed = models.PositiveBigIntegerField(default=0)
    completed_at = UTCDateTimeField(null=True)
    failure_code = models.CharField(max_length=64, default="")

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_activation_catchup"
        indexes = [
            models.Index(
                fields=["campaign", "completed_at"], name="campaign_catchup_hold"
            )
        ]


class CatchUpCheckpoint(ImmutableRecord):
    """Committed group cursor and counts permit bounded idempotent recovery."""

    demand = models.ForeignKey(ActivationCatchUpDemand, on_delete=models.PROTECT)
    sequence = models.PositiveBigIntegerField()
    group_key = models.CharField(max_length=128)
    cursor = models.CharField(max_length=128)
    items = models.PositiveBigIntegerField()
    task = models.ForeignKey("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    fence = models.PositiveBigIntegerField()
    phase = models.CharField(max_length=32, default="enumerating")
    complete = models.BooleanField(default=False)

    class Meta:
        db_table = "stewardship_catchup_checkpoint"
        constraints = [
            models.UniqueConstraint(
                fields=["demand", "sequence"], name="catchup_checkpoint_sequence"
            ),
            models.UniqueConstraint(
                fields=["demand", "group_key"], name="catchup_checkpoint_group"
            ),
        ]

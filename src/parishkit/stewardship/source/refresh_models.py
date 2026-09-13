"""Immutable refresh scope and every command coalesced into its execution."""

from django.db import models

from parishkit.stewardship.storage import ImmutableRecord, UTCDateTimeField

REFRESH_KINDS = ("full", "delta")
REFRESH_CAUSES = ("manual", "nightly", "initial", "delta", "fallback")


class SourceRefreshRequest(ImmutableRecord):
    """A retry chain cannot silently change tenant or its campaign giving window.

    Configuration identity records provenance, not admission: unrelated content
    edits do not invalidate this request. Current tenant and window must still
    match at every effect. The task root binds all automatic/explicit retries.
    """

    task_root = models.OneToOneField(
        "stewardship_jobs.TaskRun", on_delete=models.PROTECT
    )
    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion", on_delete=models.PROTECT
    )
    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    organization_id = models.PositiveBigIntegerField()
    kind = models.CharField(max_length=8)
    window_canonical = models.TextField()
    window_digest = models.CharField(max_length=64)

    class Meta:
        db_table = "stewardship_source_refresh_request"
        indexes = [
            models.Index(
                fields=["window_digest", "kind"], name="source_refresh_pending_scope"
            )
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    organization_id__gt=0, organization_id__lte=2**31 - 1
                ),
                name="source_refresh_organization",
            ),
            models.CheckConstraint(
                condition=models.Q(kind__in=REFRESH_KINDS), name="source_refresh_kind"
            ),
            models.CheckConstraint(
                condition=models.Q(window_digest__regex=r"^[0-9a-f]{64}$"),
                name="source_refresh_window_digest",
            ),
        ]


class SourceRefreshCommand(ImmutableRecord):
    """One idempotent caller command, including additional Admin requesters.

    Coalescing records a dependency, never a fabricated success. The linked
    request's actual task chain and promoted manifest determine its outcome.
    """

    request = models.ForeignKey(
        SourceRefreshRequest, on_delete=models.PROTECT, related_name="commands"
    )
    kind = models.CharField(max_length=8)
    cause = models.CharField(max_length=16)

    class Meta:
        db_table = "stewardship_source_refresh_command"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(kind__in=REFRESH_KINDS),
                name="source_refresh_command_kind",
            ),
            models.CheckConstraint(
                condition=models.Q(cause__in=REFRESH_CAUSES),
                name="source_refresh_command_cause",
            ),
            models.CheckConstraint(
                condition=models.Q(cause="delta", kind="delta")
                | (~models.Q(cause="delta") & models.Q(kind="full")),
                name="source_refresh_command_cause_kind",
            ),
            models.CheckConstraint(
                condition=~models.Q(cause="manual") | models.Q(actor_id__isnull=False),
                name="source_refresh_manual_actor",
            ),
        ]


class SourceRefreshTick(ImmutableRecord):
    """Retain scheduled command identity and the exact applied cadence inputs."""

    command = models.OneToOneField(SourceRefreshCommand, on_delete=models.PROTECT)
    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion", on_delete=models.PROTECT
    )
    due_at = UTCDateTimeField()
    timezone = models.CharField(max_length=128)
    nightly_time = models.CharField(max_length=5)
    slot_key = models.CharField(max_length=64, unique=True)

    class Meta:
        db_table = "stewardship_source_refresh_tick"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(slot_key__regex=r"^[0-9a-f]{64}$"),
                name="source_tick_key",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    nightly_time__regex=r"^([01][0-9]|2[0-3]):[0-5][0-9]$"
                ),
                name="source_tick_time",
            ),
        ]


class SourceRefreshAttempt(ImmutableRecord):
    """One concrete claim binds its manifest, applied config and loaded credential."""

    request = models.ForeignKey(SourceRefreshRequest, on_delete=models.PROTECT)
    snapshot = models.OneToOneField(
        "SourceSnapshot", on_delete=models.PROTECT, related_name="refresh_attempt"
    )
    task = models.ForeignKey("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    task_fence = models.PositiveBigIntegerField()
    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion", on_delete=models.PROTECT
    )
    credential_fingerprint = models.CharField(max_length=64)

    class Meta:
        db_table = "stewardship_source_refresh_attempt"
        constraints = [
            models.UniqueConstraint(
                fields=["task", "task_fence"], name="source_attempt_claim"
            ),
            models.CheckConstraint(
                condition=models.Q(task_fence__gt=0), name="source_attempt_fence"
            ),
            models.CheckConstraint(
                condition=models.Q(credential_fingerprint__regex=r"^[0-9a-f]{64}$"),
                name="source_attempt_credential",
            ),
        ]


class SourceRefreshFallback(ImmutableRecord):
    """A delta depends on one requested full refresh, not an invented success.

    The linked command retains normal full-request coalescing and outcome
    provenance. Waiting for this dependency must not consume provider attempts
    or repeat the incomplete delta. The creating worker claim remains auditable.
    """

    request = models.OneToOneField(
        SourceRefreshRequest, on_delete=models.PROTECT, related_name="full_fallback"
    )
    command = models.OneToOneField(SourceRefreshCommand, on_delete=models.PROTECT)
    task = models.ForeignKey("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    task_fence = models.PositiveBigIntegerField()
    attempt = models.ForeignKey(
        SourceRefreshAttempt,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    reason = models.CharField(max_length=24)

    class Meta:
        db_table = "stewardship_source_refresh_fallback"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(task_fence__gt=0), name="source_fallback_fence"
            ),
            models.CheckConstraint(
                condition=models.Q(reason="no_base", attempt__isnull=True)
                | models.Q(reason="incomplete_delta", attempt__isnull=False),
                name="source_fallback_reason",
            ),
        ]

"""Immutable submitted intents and append-only installer checkpoints."""

from django.contrib.postgres.indexes import GinIndex
from django.db import models

from parishkit.stewardship.storage import ImmutableRecord


class ConfigurationChangeRequest(ImmutableRecord):
    """An actor/key names one immutable base/patch, independent of browser retry."""

    request_key = models.UUIDField()
    request_schema = models.CharField(max_length=64)
    base = models.ForeignKey("AppliedConfigurationVersion", on_delete=models.PROTECT)
    patch = models.JSONField()
    payload_fingerprint = models.CharField(max_length=64)
    candidate_version_id = models.UUIDField(unique=True)
    candidate_digest = models.CharField(max_length=64, unique=True)
    authority = models.CharField(max_length=24, default="admin", db_default="admin")
    operator_name = models.CharField(max_length=254, null=True)
    operator_reason = models.CharField(max_length=1024, null=True)
    confirmed_deployment_id = models.UUIDField(null=True)
    recovery_target = models.EmailField(null=True)

    class Meta:
        db_table = "stewardship_config_request"
        indexes = [
            GinIndex(
                fields=["patch"],
                name="config_request_patch_lookup",
                opclasses=["jsonb_path_ops"],
            )
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    request_schema__in=[
                        "parish-integrations-patch-v1",
                        "foundation-policy-patch-v2",
                        "operator-recovery-patch-v1",
                        "operator-recovery-patch-v2",
                        "operator-recovery-bootstrap-v1",
                        "campaign-foundation-patch-v3",
                        "ministry-activity-patch-v4",
                        "operator-recovery-ministry-v4",
                        "campaign-content-patch-v5",
                        "operator-recovery-content-v5",
                        "integration-credential-patch-v6",
                        "initial-setup-patch-v7",
                        "source-cadence-patch-v8",
                        "operator-recovery-cadence-v8",
                        "integration-credential-cadence-v8",
                    ]
                ),
                name="config_request_schema",
            ),
            models.UniqueConstraint(
                fields=["actor_id", "request_key"], name="config_request_actor_key"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        authority="admin",
                        actor_id__isnull=False,
                        operator_name__isnull=True,
                        operator_reason__isnull=True,
                        confirmed_deployment_id__isnull=True,
                        recovery_target__isnull=True,
                    )
                    & ~models.Q(
                        request_schema__in=[
                            "operator-recovery-patch-v1",
                            "operator-recovery-patch-v2",
                            "operator-recovery-bootstrap-v1",
                            "operator-recovery-ministry-v4",
                            "operator-recovery-content-v5",
                            "operator-recovery-cadence-v8",
                        ]
                    )
                    | models.Q(
                        authority="operator_recovery",
                        actor_id__isnull=True,
                        operator_name__isnull=False,
                        operator_reason__isnull=False,
                        confirmed_deployment_id__isnull=False,
                        recovery_target__isnull=False,
                        request_schema__in=[
                            "operator-recovery-patch-v1",
                            "operator-recovery-patch-v2",
                            "operator-recovery-bootstrap-v1",
                            "operator-recovery-ministry-v4",
                            "operator-recovery-content-v5",
                            "operator-recovery-cadence-v8",
                        ],
                    )
                ),
                name="config_request_actor",
            ),
            models.UniqueConstraint(
                fields=["request_key"],
                condition=models.Q(authority="operator_recovery"),
                name="operator_recovery_operation",
            ),
            models.CheckConstraint(
                condition=models.Q(payload_fingerprint__regex=r"^[0-9a-f]{64}$"),
                name="config_request_payload_digest",
            ),
            models.CheckConstraint(
                condition=models.Q(candidate_digest__regex=r"^[0-9a-f]{64}$"),
                name="config_request_candidate_digest",
            ),
        ]


class ConfigurationRequestCheckpoint(ImmutableRecord):
    """The latest sequence is the state; no second mutable status can diverge."""

    request = models.ForeignKey(
        ConfigurationChangeRequest, on_delete=models.PROTECT, related_name="checkpoints"
    )
    sequence = models.PositiveIntegerField()
    state = models.CharField(max_length=16)
    failure_code = models.CharField(max_length=32, default="", db_default="")

    class Meta:
        db_table = "stewardship_config_checkpoint"
        constraints = [
            models.UniqueConstraint(
                fields=["request", "sequence"], name="config_checkpoint_sequence"
            ),
            models.CheckConstraint(
                condition=models.Q(sequence__gte=1), name="config_checkpoint_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    state__in=[
                        "staged",
                        "validating",
                        "prepared",
                        "yaml_activated",
                        "applied",
                        "failed",
                        "cancelled",
                    ]
                ),
                name="config_checkpoint_installer_states",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        state="failed",
                        failure_code__in=[
                            "stale_base",
                            "invalid_candidate",
                            "actor_unauthorized",
                        ],
                    )
                    | (~models.Q(state="failed") & models.Q(failure_code=""))
                ),
                name="config_checkpoint_failure_code",
            ),
        ]

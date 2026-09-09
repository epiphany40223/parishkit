"""Immutable submitted intents and append-only intake checkpoints.

Installer checkpoints will extend this admitted state machine in a later
migration. There is deliberately no way to represent Applied before the actual
YAML/database activation service and its atomic effects are implemented.
"""

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

    class Meta:
        db_table = "stewardship_config_request"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(request_schema__in=["parish-integrations-patch-v1"]),
                name="config_request_schema",
            ),
            models.UniqueConstraint(
                fields=["actor_id", "request_key"], name="config_request_actor_key"
            ),
            models.CheckConstraint(
                condition=models.Q(actor_id__isnull=False), name="config_request_actor"
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
                condition=models.Q(state__in=["staged", "cancelled"]),
                name="config_checkpoint_intake_states",
            ),
        ]

"""Metadata guards for short, exact Production confirmation."""

from django.db import models

from parishkit.stewardship.storage import ImmutableRecord, UTCDateTimeField


class ActivationImpactRevision(models.Model):
    """SQL-maintained change clock; no Family identities or message payloads.

    Relevant statement triggers create the singleton lazily and advance it in
    the writer's transaction. Runtime roles can read but cannot reset it. A
    global clock conservatively invalidates previews for historical mail changes
    too, avoiding missing cross-campaign acceptance/restore dependencies.
    """

    singleton = models.BooleanField(primary_key=True, default=True, editable=False)
    version = models.PositiveBigIntegerField(default=1, db_default=1)

    class Meta:
        db_table = "stewardship_activation_impact"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(singleton=True), name="activation_impact_singleton"
            ),
            models.CheckConstraint(
                condition=models.Q(version__gte=1), name="activation_impact_positive"
            ),
        ]


class ProductionConfirmation(ImmutableRecord):
    """One current-Admin intent and its atomic lifecycle effect, never a toggle.

    The compiled web owner verifies the signed preview and all current readiness
    before insertion. Private SQL effects enforce current scope, exact versions,
    fresh session, impact revision and completed prepared links again, then
    commit the campaign/request/runtime change and catch-up allocation together.
    Session identity is opaque history, not a retention-blocking foreign key.
    """

    request = models.OneToOneField(
        "ProductionTransitionRequest", on_delete=models.PROTECT
    )
    preparation = models.OneToOneField(
        "ProductionTokenPreparation", on_delete=models.PROTECT
    )
    activation = models.OneToOneField("CampaignTransition", on_delete=models.PROTECT)
    generation = models.OneToOneField(
        "FamilyAccessTokenGeneration", on_delete=models.PROTECT
    )
    request_key = models.UUIDField(unique=True)
    session_id = models.UUIDField()
    authenticated_at = UTCDateTimeField()
    expires_at = UTCDateTimeField()
    preview_at = UTCDateTimeField()
    preview_counts = models.JSONField()
    expected_request_version = models.PositiveBigIntegerField()
    expected_campaign_version = models.PositiveBigIntegerField()
    expected_runtime_version = models.PositiveBigIntegerField()
    impact_revision = models.PositiveBigIntegerField()
    readiness_digest = models.CharField(max_length=64)
    target_state = models.CharField(max_length=9)

    class Meta:
        db_table = "stewardship_production_confirmation"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(expected_request_version__gte=1)
                & models.Q(expected_campaign_version__gte=1)
                & models.Q(expected_runtime_version__gte=1)
                & models.Q(impact_revision__gte=1),
                name="production_confirmation_versions",
            ),
            models.CheckConstraint(
                condition=models.Q(readiness_digest__regex=r"^[0-9a-f]{64}$"),
                name="production_confirmation_digest",
            ),
            models.CheckConstraint(
                condition=models.Q(target_state__in=("scheduled", "active")),
                name="production_confirmation_target",
            ),
        ]

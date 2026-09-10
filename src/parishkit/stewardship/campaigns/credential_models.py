"""Campaign identity, immutable cohort provenance and disjoint credential scopes."""

from uuid import uuid4

from django.db import models

from parishkit.stewardship.storage import (
    ImmutableRecord,
    MutableRecord,
    UTCDateTimeField,
)


class DeploymentCredentialState(MutableRecord):
    """Restore rotates the global link fence before any Family route resumes."""

    family_link_epoch = models.UUIDField(default=uuid4)
    restore_id = models.UUIDField(null=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_credential_deployment"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                models.Value(1), name="credential_deployment_singleton"
            ),
        ]


class CredentialKeyState(MutableRecord):
    """Public inventory digest fences stale keyring holders during rotation."""

    kind = models.CharField(max_length=32, unique=True)
    inventory_digest = models.CharField(max_length=64)
    inventory = models.JSONField()

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_credential_key_state"


class FamilyCampaign(MutableRecord):
    """One stable DUID/code per campaign, even across repeated eligibility changes."""

    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    family_duid = models.PositiveBigIntegerField()
    active = models.BooleanField()
    portal_eligible = models.BooleanField()
    email_eligible = models.BooleanField()
    email_deliverable = models.BooleanField()
    status_reason = models.CharField(max_length=48)
    deliverability_reason = models.CharField(max_length=48)
    first_eligible_at = UTCDateTimeField(null=True)
    first_eligible_source_generation = models.PositiveBigIntegerField(null=True)
    eligibility_changed_at = UTCDateTimeField()
    source_generation = models.PositiveBigIntegerField()
    code_ciphertext = models.TextField(null=True)
    initial_invitation_state = models.CharField(max_length=24, default="not_sent")
    first_live_submission_id = models.UUIDField(null=True)
    effective_submission_id = models.UUIDField(null=True)
    last_activity_at = UTCDateTimeField(null=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_family_campaign"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=["campaign", "family_duid"], name="family_campaign_duid"
            ),
            models.CheckConstraint(
                condition=models.Q(family_duid__gt=0),
                name="family_campaign_positive_duid",
            ),
            models.CheckConstraint(
                condition=models.Q(portal_eligible=False)
                | models.Q(
                    active=True,
                    code_ciphertext__isnull=False,
                    first_eligible_at__isnull=False,
                    first_eligible_source_generation__isnull=False,
                ),
                name="family_eligible_has_code_cohort",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    first_eligible_at__isnull=True,
                    first_eligible_source_generation__isnull=True,
                )
                | models.Q(
                    first_eligible_at__isnull=False,
                    first_eligible_source_generation__isnull=False,
                ),
                name="family_cohort_pair",
            ),
            models.CheckConstraint(
                condition=models.Q(email_deliverable=False)
                | models.Q(email_eligible=True),
                name="family_deliverable_is_eligible",
            ),
        ]


class FamilyEligibilityChange(ImmutableRecord):
    """Safe status/cohort history references source generation, not a mutable copy."""

    family = models.ForeignKey(FamilyCampaign, on_delete=models.PROTECT)
    source_generation = models.PositiveBigIntegerField()
    family_version = models.PositiveBigIntegerField()
    active = models.BooleanField()
    portal_eligible = models.BooleanField()
    email_eligible = models.BooleanField()
    email_deliverable = models.BooleanField()
    status_reason = models.CharField(max_length=48)
    deliverability_reason = models.CharField(max_length=48)

    class Meta:
        db_table = "stewardship_family_eligibility"
        constraints = [
            models.UniqueConstraint(
                fields=["family", "family_version"],
                name="family_eligibility_version",
            )
        ]


class FamilyCodeFingerprint(ImmutableRecord):
    """Indexed MAC lookup; uniqueness cannot depend on reversible-code scans."""

    family = models.ForeignKey(FamilyCampaign, on_delete=models.PROTECT)
    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    key_id = models.CharField(max_length=48)
    algorithm = models.CharField(max_length=24, default="hmac-sha256-v1")
    digest = models.CharField(max_length=64)

    class Meta:
        db_table = "stewardship_family_code_mac"
        constraints = [
            models.UniqueConstraint(
                fields=["family", "key_id"], name="family_code_key_once"
            ),
            models.UniqueConstraint(
                fields=["campaign", "key_id", "digest"], name="campaign_code_key_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(algorithm="hmac-sha256-v1")
                & models.Q(digest__regex=r"^[0-9a-f]{64}$"),
                name="family_code_mac_format",
            ),
        ]


class FamilyAccessTokenGeneration(MutableRecord):
    """Inactive preparation is not authority; only the Campaign pointer grants use."""

    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    operation_id = models.UUIDField(unique=True)
    task_id = models.UUIDField(null=True)
    credential_epoch = models.UUIDField()
    restore_id = models.UUIDField(null=True)
    preparation_revision = models.PositiveBigIntegerField(default=1)
    source_snapshot_id = models.UUIDField()
    source_generation = models.PositiveBigIntegerField()
    configuration = models.ForeignKey(
        "stewardship_campaigns.CampaignConfiguration", on_delete=models.PROTECT
    )
    configuration_request = models.ForeignKey(
        "stewardship_accounts.ConfigurationChangeRequest",
        null=True,
        on_delete=models.PROTECT,
    )
    key_id = models.CharField(max_length=48)
    key_inventory_digest = models.CharField(max_length=64)
    coverage_digest = models.CharField(max_length=64, default="")
    coverage_count = models.PositiveBigIntegerField(default=0)
    checkpoint = models.PositiveBigIntegerField(default=0)
    completed_at = UTCDateTimeField(null=True)
    state = models.CharField(max_length=16, default="building")

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_family_token_generation"
        constraints = MutableRecord.Meta.constraints + [
            models.CheckConstraint(
                condition=models.Q(
                    state__in=[
                        "building",
                        "ready",
                        "active",
                        "failed",
                        "cancelled",
                        "superseded",
                    ]
                ),
                name="family_token_generation_state",
            ),
            models.CheckConstraint(
                condition=~models.Q(state__in=["ready", "active"])
                | models.Q(
                    completed_at__isnull=False, coverage_digest__regex=r"^[0-9a-f]{64}$"
                ),
                name="family_token_ready_manifest",
            ),
        ]


class FamilyAccessToken(MutableRecord):
    """A reusable token has no recoverable plaintext outside the sealed envelope."""

    family = models.ForeignKey(FamilyCampaign, on_delete=models.PROTECT)
    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    generation = models.ForeignKey(
        FamilyAccessTokenGeneration, on_delete=models.PROTECT
    )
    ciphertext = models.TextField(null=True)
    digest = models.CharField(max_length=64, null=True)
    destroyed_at = UTCDateTimeField(null=True)
    revoked_at = UTCDateTimeField(null=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_family_token"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=["family", "generation"], name="family_token_generation_once"
            ),
            models.UniqueConstraint(
                fields=["campaign", "digest"], name="family_token_campaign_digest"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    ciphertext__isnull=False,
                    digest__isnull=False,
                    destroyed_at__isnull=True,
                )
                | models.Q(
                    ciphertext__isnull=True,
                    digest__isnull=True,
                    destroyed_at__isnull=False,
                ),
                name="family_token_destroyed_pair",
            ),
        ]


class RehearsalEpoch(MutableRecord):
    """Epoch invalidation and pointer removal precede asynchronous secret cleanup."""

    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    invalidated_at = UTCDateTimeField(null=True)
    state = models.CharField(max_length=16, default="active")

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_rehearsal_epoch"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=["campaign"],
                condition=models.Q(state="active"),
                name="campaign_one_rehearsal_epoch",
            ),
            models.CheckConstraint(
                condition=models.Q(state="active", invalidated_at__isnull=True)
                | models.Q(state="invalidated", invalidated_at__isnull=False),
                name="rehearsal_epoch_state",
            ),
        ]


class CampaignCredentialState(MutableRecord):
    """One campaign-owned credential lock row and its current rehearsal pointer."""

    campaign = models.OneToOneField(
        "stewardship_campaigns.Campaign",
        on_delete=models.PROTECT,
        related_name="credential_state",
    )
    rehearsal_epoch = models.ForeignKey(
        RehearsalEpoch, on_delete=models.PROTECT, null=True
    )
    go_live_gate = models.BooleanField(default=False)
    source_snapshot_id = models.UUIDField(null=True)
    source_generation = models.PositiveBigIntegerField(null=True)
    eligibility_digest = models.CharField(max_length=64, default="")
    eligible_count = models.PositiveBigIntegerField(default=0)
    population_dirty = models.BooleanField(default=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_campaign_credentials"


class RehearsalCredential(MutableRecord):
    """Disjoint testing code and token, never substituted into production reports."""

    epoch = models.ForeignKey(RehearsalEpoch, on_delete=models.PROTECT)
    family = models.ForeignKey(FamilyCampaign, on_delete=models.PROTECT)
    code_ciphertext = models.TextField()
    token_ciphertext = models.TextField()
    token_digest = models.CharField(max_length=64)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_rehearsal_credential"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=["epoch", "family"], name="rehearsal_epoch_family_once"
            ),
            models.UniqueConstraint(
                fields=["epoch", "token_digest"], name="rehearsal_epoch_token_unique"
            ),
        ]


class RehearsalCodeFingerprint(ImmutableRecord):
    """Testing lookup is epoch-scoped and excludes anonymous retired reservations."""

    credential = models.ForeignKey(RehearsalCredential, on_delete=models.PROTECT)
    epoch = models.ForeignKey(RehearsalEpoch, on_delete=models.PROTECT)
    key_id = models.CharField(max_length=48)
    algorithm = models.CharField(max_length=24, default="hmac-sha256-v1")
    digest = models.CharField(max_length=64)

    class Meta:
        db_table = "stewardship_rehearsal_code_mac"
        constraints = [
            models.UniqueConstraint(
                fields=["credential", "key_id"], name="rehearsal_code_key_once"
            ),
            models.UniqueConstraint(
                fields=["epoch", "key_id", "digest"],
                name="rehearsal_epoch_code_key_unique",
            ),
        ]


class RehearsalCodeReservation(models.Model):
    """No Family, credential or epoch link and no recoverable historical code."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    key_id = models.CharField(max_length=48)
    algorithm = models.CharField(max_length=24, default="hmac-sha256-v1")
    digest = models.CharField(max_length=64)

    class Meta:
        db_table = "stewardship_rehearsal_reservation"
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "key_id", "algorithm", "digest"],
                name="rehearsal_reservation_unique",
            ),
        ]


class FamilySession(MutableRecord):
    """Separate PostgreSQL authority with fixed mode/epoch and absolute deadline."""

    session = models.OneToOneField("sessions.Session", on_delete=models.PROTECT)
    family = models.ForeignKey(FamilyCampaign, on_delete=models.PROTECT)
    mode = models.CharField(max_length=16)
    credential_epoch = models.UUIDField(default=uuid4)
    rehearsal_epoch = models.ForeignKey(
        RehearsalEpoch, on_delete=models.PROTECT, null=True
    )
    authenticated_at = UTCDateTimeField()
    last_activity_at = UTCDateTimeField()
    last_keepalive_at = UTCDateTimeField(null=True)
    expires_at = UTCDateTimeField()
    revoked_at = UTCDateTimeField(null=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_family_session"
        constraints = MutableRecord.Meta.constraints + [
            models.CheckConstraint(
                condition=models.Q(mode="production", rehearsal_epoch__isnull=True)
                | models.Q(mode="testing", rehearsal_epoch__isnull=False),
                name="family_session_mode_epoch",
            ),
            models.CheckConstraint(
                condition=models.Q(last_activity_at__gte=models.F("authenticated_at"))
                & models.Q(expires_at__gt=models.F("last_activity_at")),
                name="family_session_chronology",
            ),
        ]

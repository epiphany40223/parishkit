"""Immutable prepared YAML snapshots and their protected normalized projections.

Preparation is not activation. The request/installer increment will add the
runtime active pointer and atomic activation effects; no active flag on these
historical rows can become an independent configuration authority.
"""

from django.db import models

from parishkit.stewardship.storage import ImmutableRecord


class AppliedConfigurationVersion(ImmutableRecord):
    """A canonical candidate, prepared atomically with all its projections.

    The primary key is the authoritative YAML version UUID. ``record_id`` on a
    projection is instead that YAML record's stable identity across versions.
    Validation evidence names the concrete schema and exact normalized digest,
    never raw validation errors or secret-bearing input.
    """

    digest = models.CharField(max_length=64, unique=True)
    schema_version = models.PositiveSmallIntegerField()
    predecessor = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="successors",
    )
    canonical_document = models.JSONField()
    normalized_digest = models.CharField(max_length=64)
    validation_schema = models.CharField(max_length=64)

    class Meta:
        db_table = "stewardship_configuration_version"
        constraints = [
            models.UniqueConstraint(
                models.Value(1),
                condition=models.Q(predecessor__isnull=True),
                name="configuration_single_root",
            ),
            models.CheckConstraint(
                condition=models.Q(schema_version=1), name="configuration_schema_v1"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    validation_schema__in=[
                        "parish-integrations-v1",
                        "foundation-policy-v2",
                        "campaign-foundation-v3",
                        "bootstrap-policy-v1",
                    ]
                ),
                name="configuration_validation_schema",
            ),
            models.CheckConstraint(
                condition=models.Q(digest__regex=r"^[0-9a-f]{64}$")
                & models.Q(normalized_digest__regex=r"^[0-9a-f]{64}$"),
                name="configuration_valid_digests",
            ),
            models.CheckConstraint(
                condition=~models.Q(predecessor_id=models.F("id")),
                name="configuration_not_own_predecessor",
            ),
        ]


class Parish(ImmutableRecord):
    """One immutable parish profile for each prepared YAML version, never a tenant.

    Campaigns and audits may protect a specific profile version without making
    its display name/timezone mutable or cascading parish history into a campaign.
    The preparation service preserves record_id through the predecessor chain.
    """

    configuration = models.OneToOneField(
        AppliedConfigurationVersion, on_delete=models.PROTECT, related_name="parish"
    )
    record_id = models.UUIDField(db_index=True)
    name = models.CharField(max_length=254)
    website = models.URLField(max_length=2048)
    timezone = models.CharField(max_length=254)
    phone = models.CharField(max_length=12)
    large_logo_id = models.UUIDField()
    menu_logo_id = models.UUIDField()
    icon_logo_id = models.UUIDField()
    favicon_id = models.UUIDField()

    class Meta:
        db_table = "stewardship_parish"
        # Simple structural checks complement strict service validation. IANA
        # membership uses frozen application schema data, not a drifting server
        # timezone catalog; URL semantics likewise remain in the strict schema.
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(name="") & ~models.Q(timezone=""),
                name="parish_nonempty_identity",
            ),
            models.CheckConstraint(
                condition=models.Q(phone__regex=r"^\+1[2-9][0-9]{2}[2-9][0-9]{6}$"),
                name="parish_us_phone",
            ),
            models.CheckConstraint(
                condition=models.Q(website__iregex=r"^https?://"),
                name="parish_website_scheme",
            ),
        ]


class AppliedIntegration(ImmutableRecord):
    """Explicitly allowlisted non-secret settings and credential fingerprints."""

    configuration = models.ForeignKey(
        AppliedConfigurationVersion,
        on_delete=models.PROTECT,
        related_name="integrations",
    )
    record_id = models.UUIDField()
    kind = models.CharField(max_length=32)
    settings = models.JSONField()
    credential_fingerprint = models.CharField(max_length=64, null=True, blank=True)

    class Meta:
        db_table = "stewardship_applied_integration"
        constraints = [
            models.UniqueConstraint(
                fields=["configuration", "kind"], name="configuration_integration_kind"
            ),
            models.UniqueConstraint(
                fields=["configuration", "record_id"],
                name="configuration_integration_id",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    kind__in=[
                        "parishsoft",
                        "google_oauth",
                        "google_workspace",
                        "email",
                        "slack",
                        "backup",
                    ]
                ),
                name="integration_known_kind",
            ),
            models.CheckConstraint(
                condition=models.Q(credential_fingerprint__isnull=True)
                | models.Q(credential_fingerprint__regex=r"^[0-9a-f]{64}$"),
                name="integration_safe_fingerprint",
            ),
        ]

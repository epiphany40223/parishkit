"""Opaque upload ownership and immutable normalized-media receipts."""

from django.db import models

from parishkit.stewardship.storage import (
    ImmutableRecord,
    MutableRecord,
    UTCDateTimeField,
)


class BrandingBundle(MutableRecord):
    """Fence one upload before filesystem work; publication comes only from YAML.

    Ready means all normalized files have durable receipts, not that this branding
    is selected. Retained Parish projections pin files against staging cleanup.
    Session and setup identifiers are soft references so cleanup can outlive them.
    """

    immutable_fields = MutableRecord.immutable_fields + (
        "owner_id",
        "session_id",
        "setup_attempt_id",
        "base_id",
        "expires_at",
    )
    owner_id = models.UUIDField()
    session_id = models.UUIDField()
    setup_attempt_id = models.UUIDField(null=True)
    base = models.ForeignKey("AppliedConfigurationVersion", on_delete=models.PROTECT)
    expires_at = UTCDateTimeField()
    state = models.CharField(max_length=16, default="writing", db_default="writing")
    # How a logo bundle's four sizes were made (#1062, schema migration
    # 0082): "basic", one image resized for every size, or "advanced", the
    # sizes uploaded separately. Parish logos opens in the mode that saved
    # the current logos. Empty for campaign artwork and for bundles staged
    # before the mode was recorded (all of them made from one image).
    mode = models.CharField(max_length=8, null=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_branding_bundle"
        constraints = MutableRecord.Meta.constraints + [
            models.CheckConstraint(
                condition=models.Q(
                    state__in=["writing", "ready", "cleanup_pending", "scrubbed"]
                ),
                name="branding_bundle_state",
            ),
            models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F("created_at")),
                name="branding_bundle_interval",
            ),
            models.CheckConstraint(
                condition=models.Q(mode__isnull=True)
                | models.Q(mode__in=["basic", "advanced"]),
                name="branding_bundle_mode",
            ),
        ]


class BrandingAsset(ImmutableRecord):
    """A fixed UUID identifies one re-encoded file, not an original uploaded name."""

    bundle = models.ForeignKey(
        BrandingBundle, on_delete=models.PROTECT, related_name="assets"
    )
    label = models.CharField(max_length=8)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)

    class Meta:
        db_table = "stewardship_branding_asset"
        constraints = [
            models.UniqueConstraint(
                fields=["bundle", "label"], name="branding_asset_variant"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    label__in=["large", "menu", "icon", "favicon", "banner", "section"]
                ),
                name="branding_asset_label",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    width__gte=1,
                    width__lte=1024,
                    height__gte=1,
                    height__lte=1024,
                    size__gte=1,
                    size__lte=5 * 1024 * 1024,
                ),
                name="branding_asset_bounds",
            ),
            models.CheckConstraint(
                condition=models.Q(sha256__regex=r"^[0-9a-f]{64}$"),
                name="branding_asset_digest",
            ),
        ]

"""Validate new branding references before configuration files can be selected."""

from uuid import UUID

from django.db import connection

from parishkit.config import ConfigError

from .branding_models import BrandingAsset
from .configuration_errors import ConfigurationReadinessUnavailable
from .configuration_models import AppliedConfigurationVersion, Parish
from .runtime_models import ConfigurationActivation
from .sessions import database_now


def validate_installation(document, *, actor_id):
    """Keep legacy roots unchanged; new branding requires an owned complete bundle."""
    parishes = document["sections"].get("parish", [])
    if not parishes or document["predecessor_digest"] is None:
        return
    predecessor = AppliedConfigurationVersion.objects.get(
        digest=document["predecessor_digest"]
    )
    _validate_campaign_artwork(document, predecessor, actor_id=actor_id)
    values = parishes[0]["values"]["branding"]
    previous = predecessor.canonical_document["sections"].get("parish", [])
    if previous and previous[0]["values"]["branding"] == values:
        return
    assets = list(
        BrandingAsset.objects.select_related("bundle").filter(pk__in=values.values())
    )
    if (
        len(assets) != 4
        or len({row.bundle_id for row in assets}) != 1
        or any(row.pk != UUID(values[row.label]) for row in assets)
    ):
        raise ConfigError("Branding requires a complete normalized bundle.")
    bundle = assets[0].bundle
    if bundle.state != "ready":
        raise ConfigurationReadinessUnavailable("Branding is not ready.")
    retained = Parish.objects.filter(
        configuration_id__in=ConfigurationActivation.objects.values("configuration_id"),
        large_logo_id=values["large"],
    ).exists()
    if not retained and (
        bundle.expires_at <= database_now()
        or bundle.base_id != predecessor.pk
        or bundle.owner_id != actor_id
    ):
        raise ConfigError("Branding ownership or configuration changed.")


def campaign_artwork_retained(asset_id):
    """Whether an activated campaign configuration names this artwork image."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM public.stewardship_campaign_configuration c "
            "JOIN public.stewardship_config_activation v "
            "ON v.configuration_id=c.configuration_id "
            "CROSS JOIN LATERAL jsonb_each_text("
            "coalesce(c.values->'artwork'->'images','{}'::jsonb)) image "
            "WHERE image.value=%s)",
            [str(asset_id)],
        )
        return cursor.fetchone()[0]


def _validate_campaign_artwork(document, predecessor, *, actor_id):
    """Each newly selected campaign image is one owned, ready image of its kind.

    Mirrors ``stewardship_campaign_artwork_v1``: a banner slot takes a banner
    image and every page slot a section icon, each alone in a ready bundle.
    A new selection must be this Admin's unexpired upload on this base,
    unless an activated campaign configuration already retains that image.
    """
    before = {
        row["id"]: row["values"].get("artwork", {}).get("images", {})
        for row in predecessor.canonical_document["sections"].get("campaigns", [])
    }
    for row in document["sections"].get("campaigns", []):
        previous = before.get(row["id"], {})
        for slot, reference in (
            row["values"].get("artwork", {}).get("images", {}).items()
        ):
            if previous.get(slot) == reference:
                continue
            asset = (
                BrandingAsset.objects.select_related("bundle")
                .filter(pk=reference, label="banner" if slot == "banner" else "section")
                .first()
            )
            if asset is None or asset.bundle.assets.count() != 1:
                raise ConfigError("Campaign artwork requires one normalized image.")
            if asset.bundle.state != "ready":
                raise ConfigurationReadinessUnavailable(
                    "Campaign artwork is not ready."
                )
            if not campaign_artwork_retained(asset.pk) and (
                asset.bundle.expires_at <= database_now()
                or asset.bundle.base_id != predecessor.pk
                or asset.bundle.owner_id != actor_id
            ):
                raise ConfigError(
                    "Campaign artwork ownership or configuration changed."
                )

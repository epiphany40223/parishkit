"""Validate new branding references before configuration files can be selected."""

from uuid import UUID

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

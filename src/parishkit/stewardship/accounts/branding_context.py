"""Public parish chrome references only selected normalized media, never staging."""

from django.db import DatabaseError
from django.urls import reverse

from parishkit.config import ConfigError

from .authentication import runtime
from .branding_models import BrandingAsset
from .configuration_installation import coherent_configuration
from .limiting import LimiterUnavailable


def branding_for(parish):
    """Render one owner's pinned Parish projection, never silently the latest logo."""
    if parish is None:
        return {}
    found = set(
        BrandingAsset.objects.filter(
            pk__in=[parish.menu_logo_id, parish.favicon_id], bundle__state="ready"
        ).values_list("pk", flat=True)
    )
    return {
        "name": parish.name,
        "menu": reverse("public:branding_asset", args=[parish.menu_logo_id])
        if parish.menu_logo_id in found
        else None,
        "favicon": reverse("public:branding_asset", args=[parish.favicon_id])
        if parish.favicon_id in found
        else None,
    }


def parish_branding(request):
    """Keep unavailable/bootstrap/error pages functional without inventing logo URLs."""
    try:
        service = runtime()
        if not service.configured():
            return {}
        # An owning view may lend its already verified projection for this
        # render only. Never consume this presentation hint for authorization.
        root = getattr(request, "_stewardship_display_configuration", None)
        if root is None:
            root = coherent_configuration(service.store)
            request._stewardship_display_configuration = root
        if root.restore_review_required:
            return {}
        parish = getattr(root.active_configuration, "parish", None)
        if parish is None:
            return {}
        return {"parish_branding": branding_for(parish)}
    except (ConfigError, DatabaseError, LimiterUnavailable, OSError):
        return {}

"""Public parish chrome references only selected normalized media, never staging."""

from django.conf import settings
from django.db import DatabaseError
from django.db.models import Subquery
from django.urls import reverse

from parishkit.config import ConfigError
from parishkit.stewardship.deployment import DeploymentProfile
from parishkit.stewardship.web import dates

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


def campaign_artwork(values, *, origin=""):
    """The campaign's selected, ready artwork images (#248), keyed by slot.

    ``values`` are a campaign configuration's values; unset slots are absent.
    """
    return artwork_images(values.get("artwork", {}).get("images", {}), origin=origin)


def banner_for_email(values, slot, *, origin):
    """The campaign banner for one Family email, unless that email hides it."""
    if slot in values.get("artwork", {}).get("hide_banner", []):
        return None
    return campaign_artwork(values, origin=origin).get("banner")


def artwork_images(references, *, origin=""):
    """Resolve ``{slot: asset id}`` to ``{slot: {"url", "width", "height"}}``.

    Unset or unavailable slots are absent, so callers render nothing there.
    Pass ``origin`` for absolute URLs (emails).
    """
    # Read only the columns mail dispatch may SELECT (family_dispatch_grants):
    # dispatch re-renders each message and resolves the banner itself.
    assets = {
        str(pk): (width, height)
        for pk, width, height in BrandingAsset.objects.filter(
            pk__in=list(references.values()), bundle__state="ready"
        ).values_list("pk", "width", "height")
    }
    return {
        slot: {
            "url": origin + reverse("public:branding_asset", args=[reference]),
            "width": assets[str(reference)][0],
            "height": assets[str(reference)][1],
        }
        for slot, reference in references.items()
        if str(reference) in assets
    }


def display_parish(request):
    """The request's active Parish projection, or None on bootstrap/error pages.

    The lookup is cached on the request so branding, the date format and any
    owning view share one configuration read per request.
    """
    try:
        service = runtime()
        if not service.configured():
            return None
        # An owning view may lend its already verified projection for this
        # render only. Never consume this presentation hint for authorization.
        root = getattr(request, "_stewardship_display_configuration", None)
        if root is None:
            root = coherent_configuration(service.store)
            request._stewardship_display_configuration = root
        if root.restore_review_required:
            return None
        return getattr(root.active_configuration, "parish", None)
    except (ConfigError, DatabaseError, LimiterUnavailable, OSError):
        return None


def parish_branding(request):
    """Keep unavailable/bootstrap/error pages functional without inventing logo URLs."""
    parish = display_parish(request)
    if parish is None:
        return {}
    try:
        return {"parish_branding": branding_for(parish)}
    except (DatabaseError, OSError):
        return {}


def date_format(request):
    """Feed ``<body data-date-format>`` so date-format-v1.js matches the server."""
    return {"date_format": dates.current()}


def local_environment(request):
    """Flag the LOCAL deployment (#476) so base.html shows its standing banner.

    The banner tells a developer, on every Admin and Family page, that this is
    the local laptop environment with synthetic data. The flag is set only
    when the running profile is LOCAL (never for development, test or
    production), and emails are untouched, so what the mail catcher shows is
    exactly what a Family would receive. ``configure_web`` records the
    profile; a process without it (tests, management commands) shows nothing.
    """
    value = getattr(settings, "STEWARDSHIP_DEPLOYMENT_PROFILE", None)
    if value is not None and DeploymentProfile(value) is DeploymentProfile.LOCAL:
        return {"local_environment": True}
    return {}


def active_date_format():
    """The active configuration's parish date format, for request-less workers.

    Reads the canonical document's value directly: every consumer role may
    read configuration versions, while some may not read the Parish row.
    """
    from .configuration_models import AppliedConfigurationVersion
    from .runtime_models import SystemConfiguration

    return (
        AppliedConfigurationVersion.objects.filter(
            pk=Subquery(
                SystemConfiguration.objects.values("active_configuration_id")[:1]
            )
        )
        .values_list(
            "canonical_document__sections__parish__0__values__date_format", flat=True
        )
        .first()
    )


class DateFormatMiddleware:
    """Lend the request's parish date format to every server-side formatter.

    The resolver is lazy, so requests that never format a date (JSON, static
    redirects) never read the configuration for it.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        def resolve():
            parish = display_parish(request)
            return None if parish is None else parish.date_format

        token = dates.use(resolve)
        try:
            return self.get_response(request)
        finally:
            dates.reset(token)

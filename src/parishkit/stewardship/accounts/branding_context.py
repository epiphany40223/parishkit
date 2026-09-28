"""Public parish chrome references only selected normalized media, never staging."""

from django.db import DatabaseError
from django.db.models import Subquery
from django.urls import reverse

from parishkit.config import ConfigError
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

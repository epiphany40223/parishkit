"""The real URLconf plus one campaign-scoped legacy route, for tests only.

No campaign-scoped old address exists until the later URL slices (NAV-7 to
NAV-12) move campaign pages; this lets ``legacy(…, campaign=True)`` be
exercised through the real middleware, sessions and access gate now.
"""

from django.urls import include, path

from parishkit.stewardship import urls as base
from parishkit.stewardship.web.admin_routes import legacy

OLD = "campaign/<uuid:campaign_id>/test-old-logs"

admin_patterns = [
    *base.admin_patterns,
    path(OLD, legacy("logs", campaign=True), name="legacy_test_campaign_logs"),
]
urlpatterns = [
    path("admin/", include((admin_patterns, "admin")))
    if getattr(pattern, "namespace", None) == "admin"
    else pattern
    for pattern in base.urlpatterns
]

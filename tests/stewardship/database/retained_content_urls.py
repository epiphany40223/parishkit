"""The real URLconf plus a retained campaign's content history, for tests only.

Content history now opens only the current campaign (the URL scheme, #525):
an earlier campaign's pages are refused until the single-campaign change
(#145). The view still reads any retained campaign's content, so this
test-only route keeps that retention (old Parish name, logo and content
after later edits) proven for a campaign that is no longer current.
"""

from django.urls import include, path

from parishkit.stewardship import urls as base
from parishkit.stewardship.accounts import content_history

RETAINED = "/admin/test-retained/{campaign}/content/history/"

admin_patterns = [
    *base.admin_patterns,
    path(
        "test-retained/<uuid:campaign_id>/content/history/",
        content_history.content_history,
        name="test_retained_content_history",
    ),
    path(
        "test-retained/<uuid:campaign_id>/content/history/<uuid:revision_id>/",
        content_history.content_history,
        name="test_retained_content_history_revision",
    ),
]
urlpatterns = [
    path("admin/", include((admin_patterns, "admin")))
    if getattr(pattern, "namespace", None) == "admin"
    else pattern
    for pattern in base.urlpatterns
]

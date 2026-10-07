"""Campaign setup group URLs, part A: settings, content, images, schedules.

Pages end in ``/`` and name no campaign: each route hands its unchanged view
the current campaign's id (``current_campaign``). Form actions sit under
their page as nouns (``removal/``); an image upload posts to its slot. The
go-live chain, Production activation, Cancel go-live, the test email pages
and Campaign Ministries move in part B (NAV-10). Copy campaign keeps its
refusal (decision 18) at its new address.
"""

from django.urls import path

from ..accounts import (
    artwork_views,
    campaign_views,
    clone_views,
    content_history,
    content_views,
    schedule_views,
    share_views,
    talent_views,
)
from ..accounts.group_root_views import group_root
from ..web.admin_routes import current_campaign


def _page(route, view, name, *extra):
    """A Campaign setup route whose view takes the current campaign's id."""
    return path(route, current_campaign(view), *extra, name=name)


patterns = [
    # The group root opens the first entry the viewer may open now.
    path("campaign/", group_root("campaign"), name="campaign_root"),
    _page("campaign/settings/", campaign_views.campaign_settings, "campaign_settings"),
    _page("campaign/copy/", clone_views.campaign_clone, "campaign_clone"),
    _page("campaign/content/", content_views.content_settings, "content_catalog"),
    # History before the editor: "history/<revision>/" also has two segments.
    _page(
        "campaign/content/history/",
        content_history.content_history,
        "content_history",
    ),
    _page(
        "campaign/content/history/<uuid:revision_id>/",
        content_history.content_history,
        "content_history_revision",
    ),
    # NAV-10: this editor route already matches /admin/campaign/content/test/
    # <revision>/, so the new test email routes (content/test/<revision>/ and
    # its families/) must be listed above it, and a test must pin that order
    # as test_old_test_email_addresses_still_reach_their_pages pins the old.
    _page(
        "campaign/content/<str:kind>/<str:slot>/",
        content_views.content_settings,
        "content_edit",
    ),
    _page(
        "campaign/content/<str:kind>/<str:slot>/<uuid:revision_id>/",
        content_views.content_settings,
        "content_revision",
    ),
    _page("campaign/images/", artwork_views.artwork_settings, "artwork_settings"),
    _page(
        "campaign/images/<slug:slot>/",
        artwork_views.artwork_upload,
        "artwork_upload",
    ),
    _page(
        "campaign/images/<slug:slot>/removal/",
        artwork_views.artwork_remove,
        "artwork_remove",
    ),
    _page(
        "campaign/images/<slug:slot>/<uuid:bundle_id>/",
        artwork_views.artwork_preview,
        "artwork_preview",
    ),
    _page("campaign/schedules/", schedule_views.schedule_settings, "schedule_settings"),
    _page("campaign/share-options/", share_views.share_settings, "share_settings"),
    _page("campaign/talents/", talent_views.talent_settings, "talent_settings"),
]

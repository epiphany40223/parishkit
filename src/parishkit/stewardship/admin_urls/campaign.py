"""Campaign setup group URLs: settings, content, images, schedules, go-live.

Pages end in ``/`` and name no campaign: each route hands its unchanged view
the current campaign's id (``current_campaign``). Form actions sit under
their page as nouns (``removal/``, ``confirmation/``, ``cancellation/``); an
image upload posts to its slot. Part A (NAV-9) moved the settings, content,
image and schedule pages; part B (NAV-10) the test email pages, the go-live
chain, Production activation and Cancel go-live. Campaign Ministries moved
under Parish data's Ministries (``parish.py``). Copy campaign keeps its
refusal (decision 18) at its new address.
"""

from django.urls import path

from ..accounts import (
    activation_views,
    artwork_views,
    campaign_family_test_views,
    campaign_mail_views,
    campaign_views,
    clone_views,
    confirmation_views,
    content_history,
    content_views,
    go_live_views,
    schedule_views,
    share_views,
    talent_views,
    withdrawal_views,
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
    # The test email pages before the editor: "content/<kind>/<slot>/" also
    # matches content/test/<revision>/ (kind "test"). A unit test pins this.
    _page(
        "campaign/content/test/<uuid:revision_id>/",
        campaign_mail_views.campaign_mail,
        "campaign_mail",
    ),
    _page(
        "campaign/content/test/<uuid:revision_id>/families/",
        campaign_family_test_views.campaign_mail_families,
        "campaign_mail_families",
    ),
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
    # Going live: readiness, Testing cleanup, Family links, then Confirm
    # Production, each step under the one before it.
    _page("campaign/go-live/", go_live_views.readiness, "go_live"),
    _page(
        "campaign/go-live/families/",
        go_live_views.testing_families,
        "go_live_families",
    ),
    _page(
        "campaign/go-live/cleanup/<uuid:request_id>/",
        go_live_views.cleanup_status,
        "go_live_cleanup",
    ),
    _page(
        "campaign/go-live/cleanup/<uuid:request_id>/links/",
        activation_views.links,
        "go_live_links",
    ),
    _page(
        "campaign/go-live/cleanup/<uuid:request_id>/links/<uuid:preparation_id>/"
        "confirmation/",
        confirmation_views.confirmation,
        "production_confirmation",
    ),
    _page("campaign/production/", confirmation_views.progress, "production_progress"),
    _page(
        "campaign/production/cancellation/",
        withdrawal_views.withdrawal,
        "production_withdrawal",
    ),
]

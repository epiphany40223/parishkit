"""Campaign setup group URLs: settings, content, images, schedules, go-live.

Pages end in ``/`` and name no campaign: each route hands its unchanged view
the current campaign's id (``current_campaign``). Form actions sit under
their page as nouns (``removal/``, ``confirmation/``, ``cancellation/``); an
image upload posts to its slot. Part A (NAV-9) moved the settings, content,
image and schedule pages; part B (NAV-10) the test email pages, the go-live
chain, Production activation and Cancel go-live. Campaign Ministries joined
Campaign settings (#1119). Copy campaign keeps its
refusal (decision 18) at its new address.
"""

from django.urls import path

from parishkit.stewardship.campaigns.configuration import REMINDER_WORKGROUP_SLUG

from ..accounts import (
    artwork_views,
    campaign_create_views,
    campaign_family_test_views,
    campaign_mail_views,
    campaign_views,
    campaign_wizard_views,
    campaign_work_status,
    clone_views,
    confirmation_views,
    content_delete_views,
    content_history,
    content_views,
    go_live_confirm_views,
    go_live_prepare_views,
    go_live_views,
    reminder_workgroup_views,
    schedule_calendar_views,
    schedule_email_test,
    schedule_entry_views,
    schedule_views,
    share_views,
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
    # Its live end-date check (#944), a fragment the page reads.
    _page(
        "campaign/settings/end-date-check/",
        campaign_views.campaign_end_date_check,
        "campaign_end_date_check",
    ),
    # The passive campaign work status Campaign settings and Home poll
    # while campaign work holds their controls (#1619), a fragment.
    path(
        "campaign/work-status/",
        campaign_work_status.campaign_work_status,
        name="campaign_work_status",
    ),
    # The deployment's first campaign (#142); it names no campaign to wrap.
    path(
        "campaign/first/",
        campaign_create_views.campaign_create,
        name="campaign_create",
    ),
    # Its Families count preview (#1393), which the page posts to.
    path(
        "campaign/first/families/",
        campaign_create_views.campaign_create_families,
        name="campaign_create_families",
    ),
    # A Campaign setup step's Continue (#1404), a POST that records the step
    # kept as is and redirects to the next step.
    path(
        "campaign/setup/continuation/",
        campaign_wizard_views.step_continue,
        name="campaign_step_continue",
    ),
    _page("campaign/copy/", clone_views.campaign_clone, "campaign_clone"),
    _page("campaign/content/", content_views.content_settings, "content_catalog"),
    # Delete for the email template tables' confirmation dialog (#879),
    # POST only, like schedule deletion.
    _page(
        "campaign/content/deletion/",
        content_delete_views.content_delete,
        "content_delete",
    ),
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
    _page("campaign/schedules/", schedule_views.schedule_settings, "schedule_settings"),
    # New, Edit and Delete for the scheduled emails list (#878). "addition/" and
    # "deletion/" come before the schedule's own address, which takes a UUID.
    _page(
        "campaign/schedules/addition/",
        schedule_entry_views.schedule_new,
        "schedule_new",
    ),
    _page(
        "campaign/schedules/deletion/",
        schedule_entry_views.schedule_delete,
        "schedule_delete",
    ),
    # New and Edit scheduled email's test of an unsaved email edit (#446),
    # POST only, also before the UUID address.
    _page(
        "campaign/schedules/test-email/",
        schedule_email_test.schedule_email_test,
        "schedule_email_test",
    ),
    # The campaign wizard's Add these schedules and continue (#1404 PR 6),
    # POST only, also before the UUID address.
    _page(
        "campaign/schedules/suggestion/",
        schedule_entry_views.schedule_suggested,
        "schedule_suggested",
    ),
    # The read-only Campaign calendar (#469), also before the UUID address.
    _page(
        "campaign/schedules/calendar/",
        schedule_calendar_views.schedule_calendar,
        "schedule_calendar",
    ),
    _page(
        "campaign/schedules/<uuid:schedule_id>/",
        schedule_entry_views.schedule_edit,
        "schedule_edit",
    ),
    _page("campaign/share-options/", share_views.share_settings, "share_settings"),
    _page(
        f"campaign/{REMINDER_WORKGROUP_SLUG}/",
        reminder_workgroup_views.reminder_workgroup,
        "reminder_workgroup",
    ),
    # Going live (#1585): the Go live page's Check, Prepare and Confirm
    # Production steps, then Production activation. The Testing cleanup,
    # Prepare Family links, old Confirm Production and Cancel go-live pages
    # retired with no redirect (#864): their addresses answer as unknown ones.
    _page("campaign/go-live/", go_live_views.check, "go_live"),
    _page(
        "campaign/go-live/families/",
        go_live_views.testing_families,
        "go_live_families",
    ),
    _page(
        "campaign/go-live/preparing/",
        go_live_prepare_views.prepare,
        "go_live_prepare",
    ),
    _page(
        "campaign/go-live/confirmation/",
        go_live_confirm_views.confirmation,
        "go_live_confirm",
    ),
    # Production activation, the Activation step (#1585 slice 5), holds
    # Cancel go-live's review dialog; its old page retired with no redirect.
    _page("campaign/production/", confirmation_views.progress, "production_progress"),
]

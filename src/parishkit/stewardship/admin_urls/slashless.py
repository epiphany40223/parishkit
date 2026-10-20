"""Each Admin page's URL without its trailing slash, redirected to the page.

The URL scheme's one trailing-slash rule (admin-portal spec, "URL scheme"):
every Admin page URL ends in ``/`` and the other form redirects. Django's
``CommonMiddleware`` is not used, so the table lists each page's slashless
form. Form actions and status fragments are posted or polled at the
reversed URL only, so they have none. Route names are ``<page>_slashless``,
so the navigation registry can tell them from pages.

Old Admin addresses are not kept (#864): each answers 404.
"""

from django.urls import path

from parishkit.stewardship.campaigns.configuration import REMINDER_WORKGROUP_SLUG

from ..web.admin_routes import redirect_to

# (slashless route, page URL name).
SLASHLESS = (
    ("system", "system"),
    ("system/health", "system_health"),
    ("system/source-form", "source_form"),
    ("system/integrations", "integrations"),
    ("system/integrations/<str:target>", "integration_settings"),
    ("system/background", "background"),
    ("system/background/<uuid:task_id>", "background_task_page"),
    ("system/logs", "logs"),
    ("parish/settings", "parish_settings"),
    ("parish/logos", "branding_settings"),
    ("parish/ministries", "ministries"),
    ("parish/files", "hosted_files"),
    ("parish/files/deletion", "hosted_file_delete"),
    ("parish/files/<uuid:file_id>/name", "hosted_file_rename"),
    ("parish/past-campaigns", "past_campaigns"),
    ("parish/parishsoft-refresh", "source_refresh"),
    ("parish/data-to-fix", "parishsoft_fixes"),
    ("changes/<uuid:request_id>", "configuration_request"),
    # The group roots, like /admin/system above.
    ("campaign", "campaign_root"),
    ("mail", "mail_root"),
    ("parish", "parish_root"),
    ("campaign/settings", "campaign_settings"),
    ("campaign/first", "campaign_create"),
    ("campaign/copy", "campaign_clone"),
    ("campaign/content", "content_catalog"),
    ("campaign/content/history", "content_history"),
    ("campaign/content/history/<uuid:revision_id>", "content_history_revision"),
    # The test email pages before the editor, which would otherwise take
    # content/test/<revision> as kind "test" (a test pins it).
    ("campaign/content/test/<uuid:revision_id>", "campaign_mail"),
    ("campaign/content/test/<uuid:revision_id>/families", "campaign_mail_families"),
    ("campaign/content/<str:kind>/<str:slot>", "content_edit"),
    ("campaign/content/<str:kind>/<str:slot>/<uuid:revision_id>", "content_revision"),
    ("campaign/images", "artwork_settings"),
    ("campaign/images/<slug:slot>", "artwork_upload"),
    ("campaign/schedules", "schedule_settings"),
    ("campaign/schedules/addition", "schedule_new"),
    ("campaign/schedules/calendar", "schedule_calendar"),
    ("campaign/schedules/<uuid:schedule_id>", "schedule_edit"),
    ("campaign/share-options", "share_settings"),
    (f"campaign/{REMINDER_WORKGROUP_SLUG}", "reminder_workgroup"),
    ("campaign/go-live", "go_live"),
    ("campaign/go-live/families", "go_live_families"),
    ("campaign/go-live/preparing", "go_live_prepare"),
    ("campaign/go-live/confirmation", "go_live_confirm"),
    ("campaign/production", "production_progress"),
    ("reports", "reports"),
    ("reports/responses", "response_dashboard"),
    ("reports/comparison", "campaign_comparison"),
    ("reports/financial", "financial_report"),
    ("reports/limitations", "limitations_report"),
    ("reports/census", "census_changes"),
    ("reports/census/publication", "census_publication"),
    ("reports/information", "information_queue"),
    ("reports/information/<uuid:item_id>", "information_item"),
    ("reports/ministries", "ministry_report"),
    ("reports/ministries/follow-up/<uuid:request_id>", "ministry_followup_item"),
    ("reports/ministries/follow-up/roster", "ministry_roster"),
    ("reports/families", "family_directory"),
    ("reports/families/<uuid:family_id>", "timeline"),
    ("reports/exports/<uuid:request_id>", "report_export"),
    ("reports/emailed", "emailed_reports"),
    ("reports/emailed/weekly/new", "weekly_digest_manual"),
    ("reports/emailed/weekly/<uuid:snapshot_id>", "weekly_digest_snapshot"),
    (
        "reports/emailed/weekly/<uuid:snapshot_id>/items/<uuid:item_id>",
        "weekly_digest_item",
    ),
    ("reports/emailed/daily/<uuid:snapshot_id>", "daily_digest_snapshot"),
    ("mail/controls", "delivery_control"),
    ("mail/family-progress", "family_email_progress"),
    ("mail/family-history", "family_email_sends"),
    ("mail/sent", "deliveries"),
    ("mail/sent/<uuid:message_id>", "delivery"),
    ("mail/refusals", "delivery_refusals"),
    ("mail/refusals/<uuid:refusal_id>", "delivery_refusal"),
    ("mail/family-portal", "family_portal"),
    ("mail/presence", "presence"),
    ("mail/held-emails", "held_emails"),
    ("users", "users"),
    ("users/new", "user_new"),
    ("users/<uuid:user_id>/edit", "user_edit"),
    ("users/sessions", "signed_in_sessions"),
    ("users/automation", "automation_access"),
    ("users/automation/approval", "automation_approval"),
)

SUFFIX = "_slashless"

patterns = [
    path(old, redirect_to(new), name=f"{new}{SUFFIX}") for old, new in SLASHLESS
]

# {slashless route name: page URL name}.
TARGETS = {f"{new}{SUFFIX}": new for _old, new in SLASHLESS}

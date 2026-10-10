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

from ..web.admin_routes import redirect_to

# (slashless route, page URL name).
SLASHLESS = (
    ("system", "system"),
    ("system/health", "system_health"),
    ("system/source-form", "source_form"),
    ("system/integrations", "integrations"),
    ("system/integrations/<str:target>", "integration_settings"),
    ("system/key-changes/<uuid:request_id>", "credential_status"),
    ("system/key-changes/<uuid:request_id>/selection", "select_credential"),
    ("system/background", "background"),
    ("system/background/<uuid:task_id>", "background_task_page"),
    ("system/logs", "logs"),
    ("parish/settings", "parish_settings"),
    ("parish/logos", "branding_settings"),
    ("parish/logos/<uuid:bundle_id>", "branding_preview"),
    ("parish/ministries", "ministries"),
    ("parish/files", "hosted_files"),
    ("parish/files/deletion", "hosted_file_delete"),
    ("parish/files/<uuid:file_id>/name", "hosted_file_rename"),
    ("parish/parishsoft-refresh", "source_refresh"),
    ("changes/<uuid:request_id>", "configuration_request"),
    # The group roots, like /admin/system above.
    ("campaign", "campaign_root"),
    ("mail", "mail_root"),
    ("parish", "parish_root"),
    ("campaign/settings", "campaign_settings"),
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
    ("campaign/images/<slug:slot>/removal", "artwork_remove"),
    ("campaign/images/<slug:slot>/<uuid:bundle_id>", "artwork_preview"),
    ("campaign/schedules", "schedule_settings"),
    ("campaign/schedules/addition", "schedule_new"),
    ("campaign/schedules/<uuid:schedule_id>", "schedule_edit"),
    ("campaign/share-options", "share_settings"),
    ("campaign/talents", "talent_settings"),
    ("campaign/reminder-workgroup", "reminder_workgroup"),
    ("campaign/go-live", "go_live"),
    ("campaign/go-live/families", "go_live_families"),
    ("campaign/go-live/cleanup/<uuid:request_id>", "go_live_cleanup"),
    ("campaign/go-live/cleanup/<uuid:request_id>/links", "go_live_links"),
    (
        "campaign/go-live/cleanup/<uuid:request_id>/links/<uuid:preparation_id>"
        "/confirmation",
        "production_confirmation",
    ),
    ("campaign/production", "production_progress"),
    ("campaign/production/cancellation", "production_withdrawal"),
    ("parish/ministries/campaign", "campaign_ministries"),
    ("reports", "reports"),
    ("reports/responses", "response_dashboard"),
    ("reports/responses/<slug:key>", "response_list"),
    ("reports/participation", "participation"),
    ("reports/financial", "financial_report"),
    ("reports/talents", "talents_report"),
    ("reports/census", "census_changes"),
    ("reports/information", "information_queue"),
    ("reports/information/<uuid:item_id>", "information_item"),
    ("reports/ministries", "ministry_report"),
    ("reports/ministries/joining", "ministry_joiners"),
    ("reports/ministries/leaving", "ministry_leavers"),
    ("reports/ministries/follow-up", "ministry_followup"),
    ("reports/ministries/follow-up/<uuid:request_id>", "ministry_followup_item"),
    ("reports/families", "family_directory"),
    ("reports/families/<uuid:family_id>", "family_timeline"),
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
    ("mail/outgoing", "deliveries"),
    ("mail/outgoing/<uuid:message_id>", "delivery"),
    ("mail/refusals", "delivery_refusals"),
    ("mail/refusals/<uuid:refusal_id>", "delivery_refusal"),
    ("mail/family-portal", "family_portal"),
    ("mail/presence", "presence"),
    ("users/automation", "automation_access"),
    ("users/automation/approval", "automation_approval"),
)

SUFFIX = "_slashless"

patterns = [
    path(old, redirect_to(new), name=f"{new}{SUFFIX}") for old, new in SLASHLESS
]

# {slashless route name: page URL name}.
TARGETS = {f"{new}{SUFFIX}": new for _old, new in SLASHLESS}

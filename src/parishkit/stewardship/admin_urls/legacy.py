"""Every old Admin address, routed to a permanent redirect to its new page.

Each table row is ``(old route, new URL name)``; the old route keeps its own
converters so its arguments are passed to the new one unchanged. A row whose
old route names a campaign uses ``legacy(…, campaign=True)``. Route names are
``legacy_<new name>`` (``_slashless`` for the form without the trailing
slash, ``_chooser`` or ``_root`` for a retired address), so the navigation
registry can tell them from pages. The table only grows: an
old address is never dropped while bookmarks, sent digest emails or the
runbooks may still name it. The one exception is a page that is itself
removed (the Family codes page, #873): its old addresses go with it.
"""

from django.urls import path

from ..web.admin_routes import legacy

# (old route, new URL name), System group (NAV-6).
SYSTEM = (
    ("configuration/integrations", "integrations"),
    ("configuration/integrations/<str:target>", "integration_settings"),
    ("configuration/integrations/<str:target>/status", "integration_status"),
    ("configuration/integrations/<str:target>/dismiss", "dismiss_credential_result"),
    ("configuration/credentials/<uuid:request_id>", "credential_status"),
    ("configuration/credentials/<uuid:request_id>/select", "select_credential"),
    ("background", "background"),
    ("background/task/<uuid:task_id>", "background_task_page"),
    ("background/task/<uuid:task_id>/status", "background_task_status"),
    (
        "background/tasks/<uuid:task_id>/retry-family-preparation",
        "retry_family_preparation",
    ),
    ("background/tasks/<uuid:task_id>/retry-daily-digest", "retry_daily_digest"),
    ("background/tasks/<uuid:task_id>/retry-weekly-digest", "retry_weekly_digest"),
    ("background/tasks/<uuid:task_id>/retry-export-cleanup", "retry_export_cleanup"),
    ("logs", "logs"),
    ("logs/export", "logs_export"),
)

# (old route, new URL name), Parish data group and change status (NAV-7).
PARISH = (
    ("configuration/parish", "parish_settings"),
    ("configuration/branding", "branding_settings"),
    ("configuration/branding/<uuid:bundle_id>", "branding_preview"),
    ("configuration/branding/assets/<uuid:asset_id>.png", "branding_asset"),
    ("configuration/ministries", "ministries"),
    ("files/", "hosted_files"),
    ("files/upload", "hosted_file_upload"),
    ("files/delete", "hosted_file_delete"),
    ("files/<uuid:file_id>/name", "hosted_file_rename"),
    ("source/refresh", "source_refresh"),
    ("configuration/requests/<uuid:request_id>", "configuration_request"),
)

# (old route, new URL name), Mail and Family portal group (NAV-8). The old
# presence page address is not here: its polled JSON reads stay there, so
# ``mail.presence_reads`` redirects only its page reads.
MAIL = (
    ("deliveries", "deliveries"),
    ("deliveries/<uuid:message_id>", "delivery"),
    ("deliveries/<uuid:message_id>/resolve", "delivery_resolve"),
    ("deliveries/refusals", "delivery_refusals"),
    ("deliveries/refusals/<uuid:refusal_id>", "delivery_refusal"),
    ("deliveries/refusals/<uuid:refusal_id>/clear", "delivery_refusal_clear"),
    ("deliveries/family-progress", "family_email_progress"),
    ("deliveries/family-progress/status", "family_email_progress_status"),
    ("deliveries/family-sends", "family_email_sends"),
    ("family-portal", "family_portal"),
)

# (old route naming a campaign, new URL name): these redirect only for the
# current campaign and answer 410 for any other (``legacy(campaign=True)``).
# Each names its campaign as ``campaign_id``, under /admin/campaign/ or, for
# reports, /admin/reports/.
_C = "campaign/<uuid:campaign_id>"
CAMPAIGN = (
    (f"{_C}/delivery", "delivery_control"),
    # Campaign setup, part A (NAV-9).
    (f"{_C}/settings", "campaign_settings"),
    (f"{_C}/clone", "campaign_clone"),
    (f"{_C}/content", "content_catalog"),
    (f"{_C}/content/history", "content_history"),
    (f"{_C}/content/history/<uuid:revision_id>", "content_history_revision"),
    # The test email pages (NAV-10) before the editor row, which would
    # otherwise take content/test/<revision> as kind "test" (a test pins it).
    (f"{_C}/content/test/<uuid:revision_id>", "campaign_mail"),
    (f"{_C}/content/test/<uuid:revision_id>/families", "campaign_mail_families"),
    (f"{_C}/content/<str:kind>/<str:slot>", "content_edit"),
    (f"{_C}/content/<str:kind>/<str:slot>/<uuid:revision_id>", "content_revision"),
    (f"{_C}/images", "artwork_settings"),
    (f"{_C}/images/<slug:slot>", "artwork_upload"),
    (f"{_C}/images/<slug:slot>/remove", "artwork_remove"),
    (f"{_C}/images/<slug:slot>/<uuid:bundle_id>", "artwork_preview"),
    (f"{_C}/schedules", "schedule_settings"),
    (f"{_C}/share-options", "share_settings"),
    (f"{_C}/talents", "talent_settings"),
    # Campaign setup, part B (NAV-10): the go-live chain, Production
    # activation and Cancel go-live. A form left open on an old address is
    # re-posted (308) only for the current campaign, and its target page
    # still checks the CSRF token, a fresh sign-in and the reviewed token.
    (f"{_C}/go-live", "go_live"),
    (f"{_C}/go-live/families", "go_live_families"),
    (f"{_C}/go-live/cleanup/<uuid:request_id>", "go_live_cleanup"),
    (f"{_C}/go-live/cleanup/<uuid:request_id>/links", "go_live_links"),
    (
        f"{_C}/go-live/cleanup/<uuid:request_id>/links/<uuid:preparation_id>/confirm",
        "production_confirmation",
    ),
    (f"{_C}/production", "production_progress"),
    (f"{_C}/production/withdraw", "production_withdrawal"),
    # Campaign Ministries now sits under Parish data's Ministries.
    (f"{_C}/ministries", "campaign_ministries"),
    *(
        # Responses and reports (NAV-11). Each report's form actions move
        # with it, so a form left open on an old address is re-posted (308)
        # only for the current campaign, and the report still checks it.
        (f"reports/<uuid:campaign_id>/{old}", new)
        for old, new in (
            ("responses/", "response_dashboard"),
            ("responses/<slug:key>/", "response_list"),
            ("responses/<slug:key>/csv/", "response_list_export"),
            ("participation/", "participation"),
            ("participation/<uuid:fact_set_id>.png", "participation_chart"),
            ("participation/export", "report_export_create"),
            ("participation/exact-export", "report_exact_create"),
            ("financial/", "financial_report"),
            ("financial/export", "financial_export"),
            ("talents/", "talents_report"),
            ("talents/export", "talents_export"),
            ("information/", "information_queue"),
            ("information/export", "information_export"),
            ("information/<uuid:item_id>/", "information_item"),
            ("information/<uuid:item_id>/update", "information_update"),
            ("ministries/", "ministry_report"),
            ("ministries/join/", "ministry_joiners"),
            ("ministries/leave/", "ministry_leavers"),
            ("ministries/export/", "ministry_export"),
            ("ministries/packet/", "ministry_packet"),
            ("ministries/follow-up/", "ministry_followup"),
            ("ministries/follow-up/<uuid:request_id>/", "ministry_followup_item"),
            (
                "ministries/follow-up/<uuid:request_id>/update",
                "ministry_followup_update",
            ),
            ("families/", "family_directory"),
            ("families/export", "family_directory_export"),
            ("families/find", "find_family"),
            ("families/<uuid:family_id>/", "family_timeline"),
        )
    ),
)

# (old route, new URL name, name suffix): retired addresses that named no
# campaign (NAV-11). The two campaign choosers (decisions 10 and 19) and the
# old Ministry reports root open the report they stood for; the suffix keeps
# each route name distinct from the old campaign address of the same page.
RETIRED = (
    ("reports/campaigns/", "participation", "_chooser"),
    ("ministry-reports/", "ministry_report", "_root"),
    ("ministry-reports/campaigns/", "ministry_report", "_chooser"),
)

# Each page already in the scheme without its trailing slash (the one
# trailing-slash rule: "the other form redirects"). Form actions and status
# fragments are posted or polled at the reversed URL only, so they have none.
SLASHLESS = (
    ("system", "system"),
    ("system/health", "system_health"),
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
    # As in CAMPAIGN, the test email pages before the editor.
    ("campaign/content/test/<uuid:revision_id>", "campaign_mail"),
    ("campaign/content/test/<uuid:revision_id>/families", "campaign_mail_families"),
    ("campaign/content/<str:kind>/<str:slot>", "content_edit"),
    ("campaign/content/<str:kind>/<str:slot>/<uuid:revision_id>", "content_revision"),
    ("campaign/images", "artwork_settings"),
    ("campaign/images/<slug:slot>", "artwork_upload"),
    ("campaign/images/<slug:slot>/removal", "artwork_remove"),
    ("campaign/images/<slug:slot>/<uuid:bundle_id>", "artwork_preview"),
    ("campaign/schedules", "schedule_settings"),
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

# Every legacy route: (old route, new URL name, names a campaign, name suffix).
ROWS = (
    tuple((old, new, False, "") for old, new in SYSTEM + PARISH + MAIL)
    + tuple((old, new, True, "") for old, new in CAMPAIGN)
    + tuple((old, new, False, "_slashless") for old, new in SLASHLESS)
    + tuple((old, new, False, suffix) for old, new, suffix in RETIRED)
)

PREFIX = "legacy_"

patterns = [
    path(old, legacy(new, campaign=campaign), name=f"{PREFIX}{new}{suffix}")
    for old, new, campaign, suffix in ROWS
]

# {legacy route name: new URL name}, for code that resolves a stored old
# path (a change's remembered origin) to the page it now names.
TARGETS = {f"{PREFIX}{new}{suffix}": new for _old, new, _campaign, suffix in ROWS}

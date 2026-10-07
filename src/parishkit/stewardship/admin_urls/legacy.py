"""Every old Admin address, routed to a permanent redirect to its new page.

Each table row is ``(old route, new URL name)``; the old route keeps its own
converters so its arguments are passed to the new one unchanged. A row whose
old route names a campaign uses ``legacy(…, campaign=True)``. Route names are
``legacy_<new name>`` (``_slashless`` for the form without the trailing
slash), so
the navigation registry can tell them from pages. The table only grows: an
old address is never dropped while bookmarks, sent digest emails or the
runbooks may still name it.
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
)

PREFIX = "legacy_"

patterns = [
    path(old, legacy(new, campaign=campaign), name=f"{PREFIX}{new}{suffix}")
    for old, new, campaign, suffix in ROWS
]

# {legacy route name: new URL name}, for code that resolves a stored old
# path (a change's remembered origin) to the page it now names.
TARGETS = {f"{PREFIX}{new}{suffix}": new for _old, new, _campaign, suffix in ROWS}

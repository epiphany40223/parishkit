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
    ("users/automation", "automation_access"),
    ("users/automation/approval", "automation_approval"),
)

# Every legacy route: (old route, new URL name, names a campaign, name suffix).
ROWS = tuple((old, new, False, "") for old, new in SYSTEM + PARISH) + tuple(
    (old, new, False, "_slashless") for old, new in SLASHLESS
)

PREFIX = "legacy_"

patterns = [
    path(old, legacy(new, campaign=campaign), name=f"{PREFIX}{new}{suffix}")
    for old, new, campaign, suffix in ROWS
]

# {legacy route name: new URL name}, for code that resolves a stored old
# path (a change's remembered origin) to the page it now names.
TARGETS = {f"{PREFIX}{new}{suffix}": new for _old, new, _campaign, suffix in ROWS}

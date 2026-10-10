"""System group URLs: integrations, key changes, background work and logs.

Pages end in ``/``; a page's form actions and status fragment sit under it
as nouns. The JSON reads that scripts poll (``background/counts``,
``background/tasks`` and one task's ``background/tasks/<task>``) keep their
addresses in ``urls.py`` (decision 8), and System health's routes (ADM-13)
are already in this scheme there.
"""

from django.urls import path

from ..accounts import integration_selection_views, integration_views
from ..audit import log_views
from ..jobs import delivery_views
from ..jobs import views as job_views
from ..reports import export_views

patterns = [
    path(
        "system/integrations/",
        integration_views.integration_settings,
        name="integrations",
    ),
    path(
        "system/integrations/<str:target>/",
        integration_views.integration_settings,
        name="integration_settings",
    ),
    path(
        "system/integrations/<str:target>/status/",
        integration_views.integration_status,
        name="integration_status",
    ),
    path(
        "system/integrations/<str:target>/schedule-check/",
        integration_views.refresh_schedule_check,
        name="refresh_schedule_check",
    ),
    path(
        "system/integrations/<str:target>/dismissal/",
        integration_views.dismiss_credential_result,
        name="dismiss_credential_result",
    ),
    path(
        "system/key-changes/<uuid:request_id>/",
        integration_views.credential_status,
        name="credential_status",
    ),
    path(
        "system/key-changes/<uuid:request_id>/selection/",
        integration_selection_views.select_credential,
        name="select_credential",
    ),
    path("system/background/", job_views.background_page, name="background"),
    path(
        "system/background/<uuid:task_id>/",
        job_views.task_page,
        name="background_task_page",
    ),
    path(
        "system/background/<uuid:task_id>/status/",
        job_views.task_status,
        name="background_task_status",
    ),
    path(
        "system/background/<uuid:task_id>/family-preparation-retry/",
        delivery_views.preparation_retry,
        name="retry_family_preparation",
    ),
    path(
        "system/background/<uuid:task_id>/daily-digest-retry/",
        delivery_views.preparation_retry,
        {"daily": True},
        name="retry_daily_digest",
    ),
    path(
        "system/background/<uuid:task_id>/weekly-digest-retry/",
        delivery_views.preparation_retry,
        {"weekly": True},
        name="retry_weekly_digest",
    ),
    path(
        "system/background/<uuid:task_id>/export-cleanup-retry/",
        export_views.retry_cleanup_command,
        name="retry_export_cleanup",
    ),
    path("system/logs/", log_views.logs, name="logs"),
    path("system/logs/export/", log_views.export_logs, name="logs_export"),
]

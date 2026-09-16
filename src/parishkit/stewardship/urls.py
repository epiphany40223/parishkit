"""Namespaced scaffold; ingress isolation is additionally owned by ARC-03/OPS-01."""

from django.urls import include, path

from . import views
from .accounts import (
    access_gate,
    authentication,
    branding_views,
    campaign_mail_views,
    campaign_views,
    clone_views,
    code_reports,
    content_history,
    content_views,
    family_authentication,
    integration_selection_views,
    integration_views,
    ministry_views,
    parish_views,
    presence,
    schedule_views,
    setup_branding_views,
    setup_campaign_views,
    setup_cancellation_views,
    setup_confirmation_views,
    setup_content_views,
    setup_credential_views,
    setup_mail_views,
    setup_notification_views,
    setup_preview_views,
    setup_progress_views,
    setup_schedule_views,
    setup_share_views,
    setup_views,
    share_views,
)
from .jobs import delivery_views
from .jobs import views as job_views
from .reports import export_views
from .responses import views as response_views

public_patterns = [
    path(
        "branding/<uuid:asset_id>.png",
        branding_views.branding_asset,
        name="branding_asset",
    ),
    path("", family_authentication.entry, name="entry"),
    path("access/<str:token>", family_authentication.access, name="access"),
]
family_patterns = [
    path("form", response_views.start, name="form"),
    path("submit", response_views.submit, name="submit"),
    path("presence", presence.heartbeat, name="presence"),
    path("", family_authentication.portal, name="entry"),
    path("keepalive", family_authentication.keepalive, name="keepalive"),
    path("logout", family_authentication.logout, name="logout"),
]
admin_patterns = [
    path(
        "campaign/<uuid:campaign_id>/exports/participation",
        export_views.create,
        name="export_create",
    ),
    path("exports/download", export_views.download, name="export_download"),
    path("exports/<uuid:request_id>", export_views.status, name="export_status"),
    path("exports/<uuid:request_id>/cancel", export_views.cancel, name="export_cancel"),
    path(
        "exports/<uuid:request_id>/download-grant",
        export_views.download_grant,
        name="export_download_grant",
    ),
    path(
        "setup/slack-test",
        setup_notification_views.setup_notification,
        name="setup_notification",
    ),
    path(
        "setup/slack-test/status",
        setup_notification_views.setup_notification_status,
        name="setup_notification_status",
    ),
    path(
        "configuration/credentials/<uuid:request_id>/select",
        integration_selection_views.select_credential,
        name="select_credential",
    ),
    path(
        "configuration/branding",
        branding_views.branding_settings,
        name="branding_settings",
    ),
    path(
        "configuration/branding/<uuid:bundle_id>",
        branding_views.branding_preview,
        name="branding_preview",
    ),
    path(
        "configuration/branding/assets/<uuid:asset_id>.png",
        branding_views.branding_asset,
        {"private": True},
        name="branding_asset",
    ),
    path(
        "configuration/integrations",
        integration_views.integration_settings,
        name="integrations",
    ),
    path(
        "configuration/integrations/<str:target>",
        integration_views.integration_settings,
        name="integration_settings",
    ),
    path(
        "configuration/integrations/<str:target>/credential",
        integration_views.replace_credential,
        name="replace_credential",
    ),
    path(
        "configuration/credentials/<uuid:request_id>",
        integration_views.credential_status,
        name="credential_status",
    ),
    path(
        "campaign/<uuid:campaign_id>/clone",
        clone_views.campaign_clone,
        name="campaign_clone",
    ),
    path(
        "campaign/<uuid:campaign_id>/schedules",
        schedule_views.schedule_settings,
        name="schedule_settings",
    ),
    path(
        "campaign/<uuid:campaign_id>/content",
        content_views.content_settings,
        name="content_catalog",
    ),
    path(
        "campaign/<uuid:campaign_id>/content/history",
        content_history.content_history,
        name="content_history",
    ),
    path(
        "campaign/<uuid:campaign_id>/content/history/<uuid:revision_id>",
        content_history.content_history,
        name="content_history_revision",
    ),
    path(
        "campaign/<uuid:campaign_id>/content/test/<uuid:revision_id>",
        campaign_mail_views.campaign_mail,
        name="campaign_mail",
    ),
    path(
        "campaign/<uuid:campaign_id>/content/<str:kind>/<str:slot>",
        content_views.content_settings,
        name="content_edit",
    ),
    path(
        "campaign/<uuid:campaign_id>/content/<str:kind>/<str:slot>/<uuid:revision_id>",
        content_views.content_settings,
        name="content_revision",
    ),
    path("presence", presence.active_families, name="presence"),
    path(
        "campaign/<uuid:campaign_id>/share-options",
        share_views.share_settings,
        name="share_settings",
    ),
    path("campaign/new", campaign_views.campaign_settings, name="campaign_new"),
    path(
        "campaign/<uuid:campaign_id>/settings",
        campaign_views.campaign_settings,
        name="campaign_settings",
    ),
    path("configuration/parish", parish_views.parish_settings, name="parish_settings"),
    path(
        "configuration/ministries", ministry_views.ministry_activity, name="ministries"
    ),
    path(
        "configuration/requests/<uuid:request_id>",
        ministry_views.configuration_request,
        name="configuration_request",
    ),
    path("background", job_views.background_page, name="background"),
    path("deliveries", delivery_views.delivery_list, name="deliveries"),
    path("deliveries/refusals", delivery_views.refusal_list, name="delivery_refusals"),
    path(
        "deliveries/refusals/<uuid:refusal_id>",
        delivery_views.refusal_detail,
        name="delivery_refusal",
    ),
    path(
        "deliveries/refusals/<uuid:refusal_id>/clear",
        delivery_views.clear_refusal,
        name="delivery_refusal_clear",
    ),
    path(
        "deliveries/<uuid:message_id>", delivery_views.delivery_detail, name="delivery"
    ),
    path(
        "deliveries/<uuid:message_id>/resolve",
        delivery_views.resolution_command,
        name="delivery_resolve",
    ),
    path(
        "background/task/<uuid:task_id>",
        job_views.task_page,
        name="background_task_page",
    ),
    path("background/tasks", job_views.task_list, name="background_tasks"),
    path(
        "background/tasks/<uuid:task_id>/retry-family-preparation",
        delivery_views.preparation_retry,
        name="retry_family_preparation",
    ),
    path(
        "background/tasks/<uuid:task_id>/retry-export-cleanup",
        export_views.retry_cleanup_command,
        name="retry_export_cleanup",
    ),
    path("background/counts", job_views.task_counts, name="background_counts"),
    path(
        "background/tasks/<uuid:task_id>",
        job_views.task_detail,
        name="background_task",
    ),
    path("setup", setup_views.setup, name="setup"),
    path(
        "setup/cancel", setup_cancellation_views.setup_cancellation, name="setup_cancel"
    ),
    path(
        "setup/source/<uuid:task_id>",
        setup_progress_views.setup_source_progress,
        name="setup_source_progress",
    ),
    path("setup/branding", setup_branding_views.setup_branding, name="setup_branding"),
    path(
        "setup/credentials/<str:target>",
        setup_credential_views.setup_credential,
        name="setup_credential",
    ),
    path(
        "setup/branding/assets/<uuid:asset_id>.png",
        setup_branding_views.setup_branding_asset,
        name="setup_branding_asset",
    ),
    path("setup/campaign", setup_campaign_views.setup_campaign, name="setup_campaign"),
    path("setup/shares", setup_share_views.setup_shares, name="setup_shares"),
    path("setup/preview", setup_preview_views.setup_preview, name="setup_preview"),
    path(
        "setup/confirm",
        setup_confirmation_views.setup_confirmation,
        name="setup_confirmation",
    ),
    path("setup/mail-test", setup_mail_views.setup_mail, name="setup_mail"),
    path(
        "setup/mail-test/status",
        setup_mail_views.setup_mail_status,
        name="setup_mail_status",
    ),
    path(
        "setup/schedules", setup_schedule_views.setup_schedules, name="setup_schedules"
    ),
    path("setup/content", setup_content_views.setup_content, name="setup_content"),
    path(
        "setup/content/<str:kind>/<str:slot>",
        setup_content_views.setup_content_edit,
        name="setup_content_edit",
    ),
    path("setup/<str:step>", setup_views.setup_step, name="setup_step"),
    path("maintenance", access_gate.maintenance, name="maintenance"),
    path(
        "campaign/<uuid:campaign_id>/family-codes",
        code_reports.family_codes,
        name="family_codes",
    ),
    path("", authentication.index, name="index"),
    path("login", authentication.login, name="login"),
    path("logout", authentication.logout, name="logout"),
]
internal_patterns = [
    path("health/live", views.live, name="live"),
    path("health/ready", views.ready, name="ready"),
    path("metrics", views.metrics, name="metrics"),
]
urlpatterns = [
    path("admin/oauth/callback", authentication.callback, name="google_callback"),
    path("admin/oauth/start", authentication.login, name="google_login"),
    path("", include((public_patterns, "public"))),
    path("family/", include((family_patterns, "family"))),
    path("admin/", include((admin_patterns, "admin"))),
    path("", include((internal_patterns, "internal"))),
]

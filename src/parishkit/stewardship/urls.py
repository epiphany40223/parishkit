"""Namespaced scaffold; ingress isolation is additionally owned by ARC-03/OPS-01."""

from django.urls import include, path

from . import views
from .accounts import (
    access_gate,
    activation_views,
    assignment_views,
    authentication,
    branding_views,
    campaign_mail_views,
    campaign_views,
    chair_review_views,
    chair_views,
    clone_views,
    code_reports,
    confirmation_views,
    content_history,
    content_views,
    delivery_control_views,
    family_authentication,
    go_live_views,
    integration_selection_views,
    integration_views,
    ministry_views,
    parish_views,
    presence,
    rule_autosave_views,
    schedule_views,
    security_event_views,
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
    user_rule_views,
    user_views,
    withdrawal_views,
)
from .audit import log_views
from .jobs import delivery_views
from .jobs import views as job_views
from .reports import (
    campaign_picker,
    digest_views,
    directory_export_views,
    directory_views,
    exact_ui,
    exact_views,
    export_ui,
    export_views,
    financial_export_views,
    financial_views,
    information_export_views,
    information_views,
    ministry_export_views,
    ministry_followup_views,
    weekly_manual_views,
    weekly_views,
    workspace_views,
)
from .reports import ministry_views as ministry_report_views
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
    path("ministry-reports/", ministry_report_views.index, name="ministry_reports"),
    path(
        "reports/<uuid:campaign_id>/ministries/export/",
        ministry_export_views.create,
        name="ministry_export",
    ),
    path(
        "ministry-reports/campaigns/",
        ministry_report_views.picker,
        name="ministry_report_campaigns",
    ),
    path(
        "reports/<uuid:campaign_id>/ministries/",
        ministry_report_views.report,
        name="ministry_report",
    ),
    path(
        "reports/<uuid:campaign_id>/ministries/join/",
        ministry_report_views.report,
        {"action": "join"},
        name="ministry_joiners",
    ),
    path(
        "reports/<uuid:campaign_id>/ministries/leave/",
        ministry_report_views.report,
        {"action": "leave"},
        name="ministry_leavers",
    ),
    path(
        "reports/<uuid:campaign_id>/ministries/packet/",
        ministry_export_views.create_packet,
        name="ministry_packet",
    ),
    path(
        "reports/<uuid:campaign_id>/ministries/follow-up/",
        ministry_followup_views.queue,
        name="ministry_followup",
    ),
    path(
        "reports/<uuid:campaign_id>/ministries/follow-up/assign",
        ministry_followup_views.assign,
        name="ministry_followup_assign",
    ),
    path(
        "reports/<uuid:campaign_id>/ministries/follow-up/<uuid:request_id>/",
        ministry_followup_views.detail,
        name="ministry_followup_item",
    ),
    path(
        "reports/<uuid:campaign_id>/ministries/follow-up/<uuid:request_id>/update",
        ministry_followup_views.update,
        name="ministry_followup_update",
    ),
    path(
        "reports/<uuid:campaign_id>/financial/",
        financial_views.report,
        name="financial_report",
    ),
    path(
        "reports/<uuid:campaign_id>/financial/export",
        financial_export_views.create,
        name="financial_export",
    ),
    path(
        "reports/<uuid:campaign_id>/families/export",
        directory_export_views.create,
        name="family_directory_export",
    ),
    path(
        "reports/<uuid:campaign_id>/postal/export",
        directory_export_views.create,
        {"postal": True},
        name="postal_directory_export",
    ),
    path(
        "reports/<uuid:campaign_id>/families/",
        directory_views.directory,
        name="family_directory",
    ),
    path(
        "reports/<uuid:campaign_id>/postal/",
        directory_views.directory,
        {"postal": True},
        name="postal_directory",
    ),
    path(
        "reports/<uuid:campaign_id>/information/export",
        information_export_views.create,
        name="information_export",
    ),
    path(
        "reports/<uuid:campaign_id>/information/",
        information_views.queue,
        name="information_queue",
    ),
    path(
        "reports/<uuid:campaign_id>/information/<uuid:item_id>/",
        information_views.detail,
        name="information_item",
    ),
    path(
        "reports/<uuid:campaign_id>/information/<uuid:item_id>/update",
        information_views.update,
        name="information_update",
    ),
    path(
        "reports/<uuid:campaign_id>/participation/exact-export",
        exact_ui.create,
        name="report_exact_create",
    ),
    path(
        "reports/exact-exports/<uuid:request_id>/", exact_ui.detail, name="report_exact"
    ),
    path(
        "reports/exact-exports/<uuid:request_id>/cancel",
        exact_ui.command,
        {"action": "cancel"},
        name="report_exact_cancel",
    ),
    path(
        "reports/exact-exports/<uuid:request_id>/retry",
        exact_ui.command,
        {"action": "retry"},
        name="report_exact_retry",
    ),
    path(
        "reports/exports/<uuid:request_id>/regenerate",
        export_ui.command,
        {"action": "regenerate"},
        name="report_export_regenerate",
    ),
    path(
        "reports/<uuid:campaign_id>/participation/export",
        export_ui.create,
        name="report_export_create",
    ),
    path("reports/exports/<uuid:request_id>/", export_ui.detail, name="report_export"),
    path(
        "reports/exports/<uuid:request_id>/cancel",
        export_ui.command,
        {"action": "cancel"},
        name="report_export_cancel",
    ),
    path(
        "reports/exports/<uuid:request_id>/retry",
        export_ui.command,
        {"action": "retry"},
        name="report_export_retry",
    ),
    path(
        "reports/exports/<uuid:request_id>/download",
        export_ui.command,
        {"action": "download"},
        name="report_export_download",
    ),
    path("reports/", workspace_views.index, name="reports"),
    path("reports/campaigns/", campaign_picker.picker, name="report_campaigns"),
    path(
        "reports/<uuid:campaign_id>/participation/",
        workspace_views.participation,
        name="participation",
    ),
    path(
        "reports/<uuid:campaign_id>/participation/<uuid:fact_set_id>.png",
        workspace_views.participation,
        name="participation_chart",
    ),
    path(
        "campaign/<uuid:campaign_id>/exports/participation",
        export_views.create,
        name="export_create",
    ),
    path("exports/download", export_views.download, name="export_download"),
    path(
        "campaign/<uuid:campaign_id>/exports/exact-participation",
        exact_views.create,
        name="exact_export_create",
    ),
    path(
        "exact-exports/<uuid:request_id>",
        exact_views.status,
        name="exact_export_status",
    ),
    path(
        "exact-exports/<uuid:request_id>/cancel",
        exact_views.cancel,
        name="exact_export_cancel",
    ),
    path(
        "exact-exports/<uuid:request_id>/retry",
        exact_views.retry,
        name="exact_export_retry",
    ),
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
        "campaign/<uuid:campaign_id>/go-live",
        go_live_views.readiness,
        name="go_live",
    ),
    path(
        "campaign/<uuid:campaign_id>/go-live/families",
        go_live_views.testing_families,
        name="go_live_families",
    ),
    path(
        "campaign/<uuid:campaign_id>/go-live/cleanup/<uuid:request_id>",
        go_live_views.cleanup_status,
        name="go_live_cleanup",
    ),
    path(
        "campaign/<uuid:campaign_id>/go-live/cleanup/<uuid:request_id>/links",
        activation_views.links,
        name="go_live_links",
    ),
    path(
        "campaign/<uuid:campaign_id>/go-live/cleanup/<uuid:request_id>/links/<uuid:preparation_id>/confirm",
        confirmation_views.confirmation,
        name="production_confirmation",
    ),
    path(
        "campaign/<uuid:campaign_id>/production",
        confirmation_views.progress,
        name="production_progress",
    ),
    path(
        "campaign/<uuid:campaign_id>/production/withdraw",
        withdrawal_views.withdrawal,
        name="production_withdrawal",
    ),
    path(
        "campaign/<uuid:campaign_id>/delivery",
        delivery_control_views.control,
        name="delivery_control",
    ),
    path(
        "campaign/<uuid:campaign_id>/settings",
        campaign_views.campaign_settings,
        name="campaign_settings",
    ),
    path("configuration/parish", parish_views.parish_settings, name="parish_settings"),
    path("users", user_views.users, name="users"),
    path("users/rules", user_rule_views.user_rules, name="user_rules"),
    path("users/rules/apply", rule_autosave_views.rule_apply, name="rule_apply"),
    path("users/rules/base", rule_autosave_views.rule_base, name="rule_base"),
    path(
        "users/rules/requests/<uuid:request_id>",
        rule_autosave_views.rule_request,
        name="rule_request",
    ),
    path(
        "users/suggestions",
        chair_views.chair_confirmations,
        name="chair_confirmations",
    ),
    path("users/reviews", chair_review_views.chair_reviews, name="chair_reviews"),
    path("users/assignments", assignment_views.assignments, name="assignments"),
    path(
        "security-events/<uuid:event_id>/acknowledge",
        security_event_views.acknowledge_event,
        name="security_event_acknowledge",
    ),
    path(
        "configuration/ministries", ministry_views.ministry_activity, name="ministries"
    ),
    path(
        "configuration/requests/<uuid:request_id>",
        ministry_views.configuration_request,
        name="configuration_request",
    ),
    path("background", job_views.background_page, name="background"),
    path("logs", log_views.logs, name="logs"),
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
        "reports/weekly-digests/request/<uuid:campaign_id>/",
        weekly_manual_views.request_report,
        name="weekly_digest_manual",
    ),
    path(
        "reports/weekly-digests/<uuid:snapshot_id>/",
        weekly_views.snapshot,
        name="weekly_digest_snapshot",
    ),
    path(
        "reports/weekly-digests/<uuid:snapshot_id>/items/<uuid:item_id>/",
        weekly_views.snapshot,
        name="weekly_digest_item",
    ),
    path(
        "reports/daily-digests/<uuid:snapshot_id>/",
        digest_views.snapshot,
        name="daily_digest_snapshot",
    ),
    path(
        "reports/daily-digests/<uuid:snapshot_id>/chart.png",
        digest_views.snapshot,
        {"representation": "png"},
        name="daily_digest_chart",
    ),
    path(
        "reports/daily-digests/<uuid:snapshot_id>/download.png",
        digest_views.snapshot,
        {"representation": "download"},
        name="daily_digest_download",
    ),
    path(
        "background/tasks/<uuid:task_id>/retry-family-preparation",
        delivery_views.preparation_retry,
        name="retry_family_preparation",
    ),
    path(
        "background/tasks/<uuid:task_id>/retry-daily-digest",
        delivery_views.preparation_retry,
        {"daily": True},
        name="retry_daily_digest",
    ),
    path(
        "background/tasks/<uuid:task_id>/retry-weekly-digest",
        delivery_views.preparation_retry,
        {"weekly": True},
        name="retry_weekly_digest",
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

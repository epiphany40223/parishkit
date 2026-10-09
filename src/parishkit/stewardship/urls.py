"""Namespaced scaffold; ingress isolation is additionally owned by ARC-03/OPS-01."""

from django.urls import include, path

from . import views
from .accounts import (
    access_gate,
    assignment_views,
    authentication,
    automation_views,
    branding_views,
    campaign_views,
    chair_review_views,
    chair_views,
    content_views,
    critical_event_views,
    family_authentication,
    hosted_file_serving,
    presence,
    rule_autosave_views,
    security_event_views,
    session_views,
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
    source_form_views,
    system_health_views,
    user_rule_views,
    user_views,
)
from .admin_urls import campaign as admin_campaign
from .admin_urls import legacy as admin_legacy
from .admin_urls import mail as admin_mail
from .admin_urls import parish as admin_parish
from .admin_urls import reports as admin_reports
from .admin_urls import system as admin_system
from .jobs import views as job_views
from .reports import (
    directory_export_views,
    directory_views,
    exact_views,
    export_views,
)
from .responses import views as response_views

public_patterns = [
    path(
        "branding/<uuid:asset_id>.png",
        branding_views.branding_asset,
        name="branding_asset",
    ),
    path("", family_authentication.entry, name="entry"),
    path("access/<str:token>", family_authentication.access, name="access"),
    # Hosted files (#346): public, by unguessable token.
    path("files/<str:token>", hosted_file_serving.public_file, name="hosted_file"),
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
    # Groups already in the URL scheme (ADM-12), then every old address.
    *admin_system.patterns,
    *admin_parish.patterns,
    *admin_parish.change_patterns,
    *admin_mail.patterns,
    *admin_mail.polled_patterns,
    *admin_campaign.patterns,
    *admin_reports.patterns,
    *admin_legacy.patterns,
    path(
        "reports/<uuid:campaign_id>/postal/export",
        directory_export_views.create,
        {"postal": True},
        name="postal_directory_export",
    ),
    path(
        "reports/<uuid:campaign_id>/postal/",
        directory_views.directory,
        {"postal": True},
        name="postal_directory",
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
        "content/plain-text",
        content_views.plain_text_preview,
        name="content_plain_text",
    ),
    # Retired (decision 11): redirects to the current campaign's settings.
    path("campaign/new", campaign_views.retired_new, name="campaign_new"),
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
        "critical-events/acknowledge",
        critical_event_views.acknowledge_critical_events,
        name="critical_events_acknowledge",
    ),
    # The browser side of the Admin automation command line (ADM-11), under
    # Users and access in the #525 URL scheme: nouns, trailing slashes, and
    # actions posted to the collection or item they change.
    path("users/automation/", automation_views.access_view, name="automation_access"),
    path(
        "users/automation/approval/",
        automation_views.approval_view,
        name="automation_approval",
    ),
    path(
        "users/automation/sessions/<uuid:session_id>/",
        automation_views.session_view,
        name="automation_session",
    ),
    path(
        "users/automation/notices/",
        automation_views.notices_view,
        name="automation_notices",
    ),
    # System health (ADM-13): the System group's first entry, which
    # /admin/system/ opens, and the status fragment the open page polls.
    path("system/", system_health_views.system, name="system"),
    path("system/health/", system_health_views.system_health, name="system_health"),
    # Families whose ParishSoft data keeps the Family form from opening (#774).
    path("system/source-form/", source_form_views.source_form, name="source_form"),
    path(
        "system/health/status",
        system_health_views.system_health_status,
        name="system_health_status",
    ),
    path("background/tasks", job_views.task_list, name="background_tasks"),
    path("background/counts", job_views.task_counts, name="background_counts"),
    path("session/status", session_views.session_status, name="session_status"),
    path("session/renew", session_views.session_renew, name="session_renew"),
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
    path("setup/source", setup_views.setup_source, name="setup_source"),
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

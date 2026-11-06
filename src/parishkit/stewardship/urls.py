"""Namespaced scaffold; ingress isolation is additionally owned by ARC-03/OPS-01."""

from django.urls import include, path

from . import views
from .accounts import (
    assisted_entry,
    authentication,
    automation_views,
    branding_views,
    content_views,
    critical_event_views,
    family_authentication,
    hosted_file_serving,
    presence,
    public_pages,
    restore_review_views,
    security_event_views,
    session_views,
    setup_branding_views,
    setup_cancellation_views,
    setup_confirmation_views,
    setup_credential_views,
    setup_mail_views,
    setup_notification_views,
    setup_views,
    signed_in_views,
    source_form_views,
    system_health_views,
)
from .admin_urls import campaign as admin_campaign
from .admin_urls import mail as admin_mail
from .admin_urls import parish as admin_parish
from .admin_urls import reports as admin_reports
from .admin_urls import slashless as admin_slashless
from .admin_urls import system as admin_system
from .admin_urls import users as admin_users
from .jobs import views as job_views
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
    # The public information pages Google's OAuth verification needs (#2354).
    path("about/", public_pages.about, name="about"),
    path("privacy/", public_pages.privacy, name="privacy"),
    path("terms/", public_pages.terms, name="terms"),
]
family_patterns = [
    path("form", response_views.start, name="form"),
    path("submit", response_views.submit, name="submit"),
    path("presence", presence.heartbeat, name="presence"),
    path("", family_authentication.portal, name="entry"),
    path("keepalive", family_authentication.keepalive, name="keepalive"),
    # Open form's hand-off from the Admin portal (#529).
    path("assisted", assisted_entry.assisted, name="assisted"),
    path("logout", family_authentication.logout, name="logout"),
]
admin_patterns = [
    # Groups already in the URL scheme (ADM-12), then their pages' slashless
    # forms. Old Admin addresses are not kept (#864).
    *admin_system.patterns,
    *admin_parish.patterns,
    *admin_parish.change_patterns,
    *admin_mail.patterns,
    *admin_mail.polled_patterns,
    *admin_campaign.patterns,
    *admin_reports.patterns,
    *admin_users.patterns,
    *admin_slashless.patterns,
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
    # Every live interactive Admin session (#953); Sign out posts the chosen
    # session to the same address, as the shared confirmation dialog does.
    path(
        "users/sessions/",
        signed_in_views.signed_in_sessions_view,
        name="signed_in_sessions",
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
    # Revoke posts the chosen session to the collection, as the shared
    # confirmation dialog does (#879).
    path(
        "users/automation/sessions/",
        automation_views.sessions_view,
        name="automation_sessions",
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
        "setup/finishing",
        setup_cancellation_views.setup_cancellation,
        name="setup_finishing",
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
    path("setup/<str:step>", setup_views.setup_step, name="setup_step"),
    # The Restore review page (#537): where the access gate sends an
    # Administrator while a restore is under review.
    path("maintenance", restore_review_views.restore_review, name="maintenance"),
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

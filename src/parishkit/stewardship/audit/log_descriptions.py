"""Plain-language explanations of log entry types for the System logs page.

The Type column shows the stored machine name (it is also what the filter
matches). These sentences say what an entry means to an Administrator. Types
without a sentence here fall back to a readable form of their name, so a new
event type is never shown blank; add a sentence when a type is common or its
name is unclear.
"""

from django.utils.translation import gettext_lazy as _

DESCRIPTIONS = {
    # Background work: the worker claims a task, reports progress, finishes.
    "task_created": _("A background task was queued."),
    "task_claim": _("A background worker started a task."),
    "task_progress": _("A background worker reported progress on a task."),
    "task_heartbeat": _("A background worker confirmed it is still working."),
    "task_complete": _("A background task finished successfully."),
    "task_retryable_failure": _(
        "A background task hit a temporary problem and will be retried."
    ),
    "task_permanent_failure": _("A background task failed and will not be retried."),
    "task_lease_expired": _(
        "A background worker stopped reporting; the task will be recovered."
    ),
    "task_recovery_retry": _("An interrupted background task was queued again."),
    "task_recovery_fail": _("An interrupted background task could not be recovered."),
    "task_failed": _("A background task failed; see its task page for the reason."),
    # ParishSoft data.
    "source_promoted": _("New ParishSoft data became the current data."),
    "source_rejected": _(
        "A ParishSoft refresh was rejected, so the previous data was kept."
    ),
    "source_refresh_invalid": _(
        "ParishSoft returned data the system could not accept; the previous "
        "data was kept."
    ),
    "chair_reconciled": _("Ministry chairs were matched to the new ParishSoft data."),
    "facts_verified": _("Report totals were recalculated and checked."),
    # Sign-in and sessions.
    "admin_login": _("An Administrator or Staff member signed in with Google."),
    "admin_step_up": _(
        "An Administrator confirmed their sign-in again for a protected action."
    ),
    "family_login": _("A Family signed in to the Family form."),
    # Mail.
    "outbox_created": _("An email was prepared for sending."),
    "outbox_prepared": _("An email's content and recipients were finalized."),
    "outbox_submit": _("An email was handed to the mail provider."),
    "outbox_accept": _("The mail provider accepted an email for delivery."),
    "family_mail_test_queued": _("A test email to chosen Families was requested."),
    "family_mail_test_prepared": _("A test email to a chosen Family was prepared."),
    # Pages viewed (recorded for accountability).
    "dashboard_viewed": _("An Administrator opened the dashboard."),
    "system_logs_viewed": _("An Administrator opened System logs."),
    "background_viewed": _("An Administrator opened Background work."),
    "delivery_viewed": _("An Administrator opened Outgoing mail."),
    "family_directory_viewed": _("Someone opened the Family codes directory."),
    "postal_outreach_viewed": _("Someone opened the postal outreach list."),
    "ministry_report_viewed": _("Someone opened a Ministry report."),
    "participation_viewed": _("Someone opened the participation report."),
    "export_requested": _("A report download was requested."),
    "export_downloaded": _("A report file was downloaded."),
    # Setup and configuration.
    "setup_started": _("Initial setup was started."),
    "setup_expired": _("An unfinished initial setup expired."),
    "setup_credential_staged": _("A credential was entered during setup."),
    "setup_credential_scrubbed": _(
        "A credential entered during setup was cleared after installation."
    ),
    "setup_mail_submitting": _("The setup test email was being sent."),
    "credential_key_initialized": _("An encryption key was created."),
    "config_request_validating": _("A settings change is being checked."),
}


def describe(event):
    """Return the plain sentence for a log type, or a readable fallback."""
    text = DESCRIPTIONS.get(event)
    if text is not None:
        return text
    return event.replace("_", " ").capitalize() + "."

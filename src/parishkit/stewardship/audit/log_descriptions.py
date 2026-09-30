"""Plain-language explanations of log entry types for the System logs page.

The Type column shows the stored machine name (it is also what the filter
matches). These sentences say what an entry means to an Administrator. Types
without a sentence here fall back to a readable form of their name, so a new
event type is never shown blank; add a sentence when a type is common or its
name is unclear.

Every type the application itself defines (the audit ``Action`` and operational
``Event`` vocabularies, plus the audit types written directly by Python owners
and SQL triggers, listed in ``DIRECT_AUDIT_TYPES``) has a sentence; a guard test
enforces that. Types built from a prefix and a state, such as
``config_request_applied``, are explained by ``PREFIXES``.
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
    "task_timed_out": _("Work was stopped because it ran longer than its time limit."),
    "helper_timed_out": _(
        "A helper process was stopped because it ran longer than its time limit."
    ),
    "work_budget_reached": _(
        "Routine work reached its time budget and will continue on its next run."
    ),
    "task_lease_lost": _(
        "A background worker stopped reporting before finishing a task."
    ),
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
    # Operational diagnostics written by the application's processes.
    "configuration_rejected": _(
        "A settings file was refused at startup because it was not valid."
    ),
    "configuration_digest_mismatch": _(
        "The installed settings did not match what the database expects; the "
        "process stopped instead of running with them."
    ),
    "startup_rejected": _(
        "A process refused to start; the recorded detail says why (for "
        "example, a required backup was missing)."
    ),
    "startup_validated": _("A process started and passed its startup checks."),
    "request_completed": _("A web request finished."),
    "report_audit_failed": _(
        "A report could not be shown because recording who viewed it failed."
    ),
    "report_shaping_failed": _("A report could not be prepared for display."),
    "task_started": _("A background task started running."),
    "task_completed": _("A background task finished running."),
    "fact_drift": _(
        "Recalculated report totals differed from the stored ones; the stored "
        "totals will be rebuilt."
    ),
    "delivery_unknown": _(
        "The mail provider did not confirm whether an email was accepted; it "
        "needs review before it is retried."
    ),
    "campaign_boundary_lag": _(
        "A scheduled campaign date change ran later than planned."
    ),
    "due_work_lag": _("Scheduled background work started later than planned."),
    "production_cleanup_failed": _(
        "Clearing test data when the campaign went live did not finish."
    ),
    "source_retention_skipped": _(
        "Removing old ParishSoft copies was skipped this time; the refresh "
        "itself continued and the cleanup will try again next time."
    ),
    "source_tenant_mismatch": _(
        "ParishSoft answered for a different parish than the one configured, "
        "so its data was not used."
    ),
    "source_destructive_change": _(
        "A ParishSoft refresh would have removed an unusually large share of "
        "the data, so it was held for review."
    ),
    "source_member_unusable": _(
        "A ParishSoft Member record could not be used and was left out."
    ),
    # Process log only (once per process per Ministry and name); a durable
    # Admin notice is tracked in #342.
    "source_ministry_name_repaired": _(
        "A ParishSoft Ministry name was blank, too long or held unusual "
        "characters, so a cleaned-up name was shown instead."
    ),
    "source_refresh_held": _(
        "A ParishSoft refresh was held back instead of replacing the current data."
    ),
    "source_credential_failed": _(
        "The ParishSoft key was refused or could not be read."
    ),
    "source_provider_failed": _(
        "ParishSoft could not be reached or returned an error."
    ),
    "mail_provider_failed": _(
        "The mail provider could not be reached or refused a request."
    ),
    "installer_request_failed": _("A settings or key installation step failed."),
    "credential_handoff_key_mismatch": _(
        "A new key was prepared for a different installation key and was not used."
    ),
    "authentication_limits_weakened": _(
        "Sign-in attempt limits are set looser than recommended."
    ),
    "authentication_health_observation_failed": _(
        "The sign-in protection's health check could not be recorded."
    ),
    "unstructured_log_suppressed": _(
        "A library wrote a free-text message; it was withheld because only "
        "reviewed messages are kept."
    ),
    # Settings, roles and keys (audit).
    "configuration_requested": _("A settings change was requested."),
    "roles_applied": _("Portal user roles were changed."),
    "secret_replacement_requested": _("Replacing a stored key was requested."),
    "privileged_reauthentication": _(
        "An Administrator signed in again before a protected action."
    ),
    "destructive_confirmation": _(
        "An Administrator confirmed an action that cannot be undone."
    ),
    "credential_result_dismissed": _(
        "An Administrator dismissed the result of a key change."
    ),
    "family_maintenance_started": _(
        "An Administrator closed the Family portal for maintenance."
    ),
    "family_maintenance_ended": _(
        "An Administrator reopened the Family portal after maintenance."
    ),
    "security_event_acknowledged": _(
        "An Administrator acknowledged a security notice."
    ),
    "critical_events_acknowledged": _(
        "An Administrator acknowledged critical notices on the dashboard."
    ),
    "configuration_activated": _("A settings change took effect."),
    "credential_consumer_acknowledged": _(
        "A background process started using a newly installed key."
    ),
    "credential_key_activated": _("A new encryption key took effect."),
    "credential_keys_retired": _("Old encryption keys were retired."),
    "credential_cipher_batch_migrated": _(
        "Stored keys were re-encrypted with the current encryption key."
    ),
    "limiter_recovered": _("Sign-in attempt limiting is working again."),
    "admin_timeout": _("An Administrator or Staff session ended after inactivity."),
    # Families and their links.
    "family_link_invalid": _(
        "Someone opened a Family link that is not valid (mistyped, expired or "
        "replaced)."
    ),
    "family_mac_backfilled": _("Family link checks were prepared for Families."),
    "family_mac_migration_verified": _("Family link checks were verified."),
    "family_logout": _("A Family signed out of the Family form."),
    "family_session_ended": _("A Family's form session ended."),
    "family_submission": _("A Family submitted their response."),
    "submission_receipt_skipped": _(
        "No receipt email was sent for a submission (for example, the Family "
        "has no email address)."
    ),
    "family_link_rotated": _("A Family's link was replaced with a new one."),
    "family_link_population_extended": _(
        "Links were created for Families added since links were last prepared."
    ),
    "family_tokens_preparing": _("Family links are being prepared."),
    "family_tokens_ready": _("Family links are ready."),
    "family_tokens_cancelled": _("Preparing Family links was cancelled."),
    "family_presence_viewed": _("Someone opened the list of Families online now."),
    "family_codes_viewed": _("Someone opened the Family codes list."),
    # ParishSoft data (audit).
    "source_compacted": _("Old ParishSoft copies were removed to save space."),
    "facts_compacted": _("Old report totals were removed to save space."),
    "source_superseded": _(
        "A ParishSoft refresh was replaced by a newer one before it finished."
    ),
    "source_fallback_requested": _(
        "A full ParishSoft reload was requested instead of a quick update."
    ),
    "setup_source_started": _("The first ParishSoft load during setup started."),
    "setup_source_completed": _("The first ParishSoft load during setup finished."),
    "setup_frozen": _("Initial setup was completed and locked."),
    # Reports and pages (audit).
    "daily_digest_viewed": _("Someone opened a daily summary."),
    "weekly_digest_viewed": _("Someone opened a weekly report."),
    "information_viewed": _("Someone opened the information changes report."),
    "information_updated": _("Someone updated follow-up on an information change."),
    "ministry_followup_viewed": _("Someone opened the Ministry follow-up report."),
    "ministry_request_updated": _("Someone updated follow-up on a Ministry request."),
    "financial_report_viewed": _("Someone opened the financial report."),
    "talents_report_viewed": _("Someone opened the talents and limitations report."),
    "talents_report_exported": _(
        "Someone downloaded the talents and limitations report."
    ),
    "portal_users_viewed": _("An Administrator opened the portal users list."),
    "system_logs_exported": _("An Administrator downloaded the system logs."),
    "export_cancelled": _("A report download was cancelled."),
    "chair_review_decided": _(
        "An Administrator decided on a Ministry chairperson change."
    ),
    # Campaign schedule (audit, written by the database).
    "campaign_boundary_completed": _(
        "A scheduled campaign date change (such as opening or closing) happened."
    ),
    "campaign_boundary_skipped": _(
        "A scheduled campaign date change was skipped because it no longer applied."
    ),
    "catchup_failed": _("Scheduled work that was missed could not be caught up."),
    "schedule_selected": _("A mail schedule version was chosen."),
    "restore_hold_resolved": _(
        "The review required after restoring a backup was completed."
    ),
    "runtime_transition": _("The system changed mode (for example, Testing to live)."),
    "weekly_manual_requested": _("A weekly report was requested by hand."),
    "rehearsal_invalidated": _("A test run no longer counts after a change."),
    "rehearsal_gate_released": _("A required test run was completed."),
}

# Audit types some owners write directly rather than through ``Action``: Python
# code passing ``event_type=`` and SQL triggers. The guard test compares this
# with the source so a new direct type cannot appear without a sentence.
DIRECT_AUDIT_TYPES = frozenset(
    {
        "admin_login",
        "admin_step_up",
        "admin_timeout",
        "catchup_failed",
        "campaign_boundary_completed",
        "campaign_boundary_skipped",
        "chair_reconciled",
        "configuration_activated",
        "credential_cipher_batch_migrated",
        "credential_consumer_acknowledged",
        "credential_key_activated",
        "credential_keys_retired",
        "family_link_population_extended",
        "family_link_rotated",
        "family_login",
        "family_logout",
        "family_mac_migration_verified",
        "family_session_ended",
        "family_submission",
        "family_tokens_cancelled",
        "family_tokens_preparing",
        "family_tokens_ready",
        "limiter_recovered",
        "rehearsal_gate_released",
        "rehearsal_invalidated",
        "restore_hold_resolved",
        "runtime_transition",
        "schedule_selected",
        "submission_receipt_skipped",
        "weekly_manual_requested",
    }
)

# Types a SQL trigger builds as a prefix plus a state or action name. The state
# is shown in words after the sentence.
PREFIXES = (
    ("config_request_", _("A settings change request moved to a new step")),
    ("secret_request_", _("A key replacement request moved to a new step")),
    ("campaign_mail_", _("A campaign email batch moved to a new step")),
    ("campaign_", _("A campaign was changed")),
)

# Who, in words, when the actor is not a portal user the page can name. Audit
# contexts record this kind; operational entries do not.
ACTOR_KINDS = {
    "family": _("A Family (using their code or link)"),
    "system": _("The system itself"),
    "operator": _("The server operator (command line)"),
}


def words(identifier):
    """A readable form of an identifier, e.g. "Lag microseconds"."""
    return identifier.replace("_", " ").capitalize()


# Sentences for one type with a particular recorded outcome, where that
# outcome means something the type's own sentence does not cover.
OUTCOME_DESCRIPTIONS = {
    ("configuration_digest_mismatch", "changed"): _(
        "A backup was sealed to a different encryption key than the backup "
        "before it. Unless the server operator installed a new key on "
        "purpose, new backups may not open with the kept private key: ask the "
        "operator to open the newest backup with each kept copy of the private "
        "key (backup runbook, Checking the kept keys). Each backup's own "
        "output, and its row in stewardship_backup_run, names the key's "
        "recipient_fingerprint."
    ),
}


def describe(event, context=None):
    """Return the plain sentence for a log type, or a readable fallback.

    ``context`` (a stored entry's context) selects a more specific sentence
    for the outcomes ``OUTCOME_DESCRIPTIONS`` names.
    """
    outcome = (context or {}).get("outcome") if isinstance(context, dict) else None
    text = OUTCOME_DESCRIPTIONS.get((event, outcome)) or DESCRIPTIONS.get(event)
    if text is not None:
        return text
    for prefix, sentence in PREFIXES:
        if event.startswith(prefix) and len(event) > len(prefix):
            return f"{sentence}: {words(event[len(prefix) :]).lower()}."
    return words(event) + "."

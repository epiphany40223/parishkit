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
    "task_failed": _(
        "A background task failed. The detail says what failed and whether it "
        "will be retried; if it will not, open its task page (Background work) "
        "to retry it once the cause is fixed."
    ),
    "task_timed_out": _(
        "Work was stopped because it ran longer than its time limit. It is "
        "retried automatically; if this keeps happening, tell the server operator."
    ),
    "helper_timed_out": _(
        "A helper process was stopped because it ran longer than its time "
        "limit. Its work is retried; if this keeps happening, tell the server "
        "operator."
    ),
    "work_budget_reached": _(
        "Routine work reached its time budget and will continue on its next "
        "run. Nothing needs to be done."
    ),
    "task_lease_lost": _(
        "A background worker stopped reporting before finishing a task. The "
        "task is recovered and retried automatically; nothing needs to be done "
        "unless it keeps happening."
    ),
    # ParishSoft data.
    "source_promoted": _("New ParishSoft data became the current data."),
    "source_rejected": _(
        "A ParishSoft refresh was rejected, so the previous data was kept."
    ),
    "source_refresh_invalid": _(
        "ParishSoft returned data the system could not accept, or the "
        "ParishSoft settings were refused; the previous data was kept. The "
        "detail says which. Check the ParishSoft settings; if they are right, "
        "the next scheduled refresh tries again."
    ),
    "chair_reconciled": _("Ministry chairs were matched to the new ParishSoft data."),
    "facts_verified": _("Report totals were recalculated and checked."),
    # Sign-in and sessions.
    # The portal session covers every portal role, not just Administrators.
    "admin_login": _(
        "A portal user (Administrator, Staff or Ministry leader) signed in."
    ),
    "admin_step_up": _(
        "An Administrator confirmed their sign-in again for a protected action."
    ),
    "family_login": _("A Family signed in to the Family form."),
    # Session endings the sign-in code records under its ending reason.
    "admin_logout": _(
        "A portal user (Administrator, Staff or Ministry leader) signed out."
    ),
    "admin_reauthenticated": _(
        "A portal user signed in again, which ended their previous session."
    ),
    "admin_revoked": _(
        "A portal user's session was ended because their access was removed "
        "or all sessions were signed out."
    ),
    # Admin automation sessions (ADM-11): the host command line acting as an
    # Administrator who approved it once in the browser.
    "automation_session_approved": _(
        "An Administrator approved an automation session, which lets the "
        "server's command line act as them until it expires or is revoked."
    ),
    "automation_session_ended": _(
        "An automation session ended; its reason is shown on the "
        "Administrator's Automation access page."
    ),
    "automation_session_refused": _(
        "Use of an automation session was refused: an unknown session, one "
        "used from a different server, or a command session seen by the web."
    ),
    "automation_fresh_gate": _(
        "An automation session stood in for a recent Google sign-in on an "
        "action that needs one."
    ),
    "automation_notices_acknowledged": _(
        "An Administrator acknowledged automation notices on their own dashboard."
    ),
    "admin_cmd_schedule_confirm": _(
        "An automation session confirmed a reviewed change to mail schedules "
        "or campaign dates, which became a configuration request."
    ),
    "admin_cmd_delivery_refusal_clear": _(
        "An automation session cleared a refused email address after the "
        "Administrator confirmed verifying it, as the address's page does."
    ),
    "admin_cmd_delivery_resend": _(
        "An automation session resent an email whose delivery was uncertain, "
        "after the Administrator accepted the duplicate risk, as the email's "
        "page does."
    ),
    "admin_cmd_delivery_resolve": _(
        "An automation session resolved an outgoing email, as the email's "
        "page does: a note, external evidence or a retry."
    ),
    "admin_cmd_refresh_start": _(
        "An automation session asked for a full ParishSoft refresh, as the "
        "Source refresh page's confirmation does."
    ),
    "admin_cmd_test_families": _(
        "An automation session sent a test of one email for chosen Families "
        "to the Testing recipient, as the Send to chosen Families page does."
    ),
    "admin_cmd_test_sample": _(
        "An automation session sent a sample test email to the Testing "
        "recipient, as the Preview and test email page does."
    ),
    "admin_cmd_task_retry": _(
        "An automation session retried a failed background task, as the "
        "task's Retry button does."
    ),
    "admin_cmd_export_create": _(
        "An automation session requested a report export, as the report's "
        "export form does."
    ),
    "admin_cmd_export_cancel": _(
        "An automation session cancelled a report export, as the export's "
        "Cancel button does."
    ),
    "admin_cmd_export_retry": _(
        "An automation session retried a failed report export, as the "
        "export's Retry button does."
    ),
    "admin_cmd_export_regenerate": _(
        "An automation session requested an expired report export again, "
        "as the export's Regenerate button does."
    ),
    "admin_cmd_export_financial": _(
        "An automation session requested a financial report export, as the "
        "Financial report's export form does."
    ),
    "admin_cmd_export_information": _(
        "An automation session requested an additional information export, "
        "as the Additional information page's export form does."
    ),
    "admin_cmd_export_ministry": _(
        "An automation session requested a Ministry report export, as the "
        "Ministry report's export form does."
    ),
    "admin_cmd_export_ministry_packet": _(
        "An automation session requested a Ministry follow-up packet, as the "
        "Ministry report's packet form does."
    ),
    "admin_cmd_export_directory": _(
        "An automation session requested a Family-code directory export, as "
        "the Family directory's export form does."
    ),
    "admin_cmd_export_postal": _(
        "An automation session requested a postal mail-merge export, as the "
        "Family directory's export form does with mailing columns."
    ),
    "admin_cmd_digest_weekly_request": _(
        "An automation session requested a manual weekly report, as the "
        "Send a weekly report now page does."
    ),
    "admin_privileges_changed": _(
        "A portal user's roles changed, so their session was replaced with one "
        "carrying the new roles."
    ),
    "operator_admin_recovered": _(
        "The server operator restored Administrator access from the command "
        "line, which signed out every portal user."
    ),
    # Access-policy changes the settings activation records.
    "policy_security_event": _(
        "A settings change widened access (for example, a new Administrator or "
        "Staff domain) or replaced the backup encryption key; Administrators "
        "were notified."
    ),
    "policy_denial_namespace_reset": _(
        "A settings change gave someone more access, so earlier sign-in "
        "refusals no longer apply."
    ),
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
    "system_health_viewed": _("An Administrator opened System health."),
    "source_form_viewed": _(
        "An Administrator opened the list of Families the form cannot open."
    ),
    "background_viewed": _("An Administrator opened Background work."),
    "delivery_viewed": _("An Administrator opened Outgoing mail."),
    "family_directory_viewed": _(
        "Someone opened the active parishioner family directory."
    ),
    "postal_outreach_viewed": _(
        "Someone opened the active parishioner family directory with mailing columns."
    ),
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
    # Process log only (#541).
    "startup_waiting": _(
        "A service is starting while the database is not yet answering, and "
        "is waiting for it (up to the limit the detail gives). This is normal "
        "just after a server restart. A wait-ended entry follows when the "
        "database answers; if the limit runs out, a timeout entry follows and "
        "the service stops, to be restarted by its restart policy (as "
        "Production's services are)."
    ),
    # Process log only (#546); System health's debug logging panel shows
    # the same state for every running service.
    "debug_logging_enabled": _(
        "A service started with debug logging on, so its log lines keep "
        "free text and error details that can hold personal data. It keeps "
        "running this way until it is restarted without debug logging. In "
        "Production, turn it off: see the debug logging panel on System "
        "health."
    ),
    # Process log only (#584); advice, never an operational incident.
    "refresh_lead_window_conflict": _(
        "A scheduled full ParishSoft refresh falls inside the two hours "
        "before a Production reminder, when its emails are prepared. Nothing "
        "is sent wrongly or twice, but preparation pauses while the refreshed "
        "data is applied, which can delay the reminder. Move the full "
        "refresh outside those hours in the ParishSoft refresh schedule "
        "settings."
    ),
    "startup_wait_ended": _(
        "The database answered a starting service that had been waiting for "
        "it; the detail gives the seconds since the service started waiting. "
        "A second such entry can follow when the service waits again while "
        "checking its database access. Nothing needs to be done."
    ),
    "request_completed": _("A web request finished."),
    "report_audit_failed": _(
        "A report could not be shown because recording who viewed it failed."
    ),
    "report_shaping_failed": _("A report could not be prepared for display."),
    "task_started": _("A background task started running."),
    "task_completed": _("A background task finished running."),
    "fact_drift": _(
        "Recalculated report totals differed from the stored ones on the "
        "number of days the detail gives; the stored totals will be rebuilt. "
        "Nothing needs to be done unless it keeps happening."
    ),
    "delivery_unknown": _(
        "The mail provider did not confirm whether an email was accepted; "
        "review it on Outgoing mail before it is retried."
    ),
    "campaign_boundary_lag": _(
        "A scheduled campaign start or close ran later than planned; the "
        "detail says how late. It still ran; nothing needs to be done unless "
        "it keeps happening."
    ),
    "due_work_lag": _(
        "Scheduled background work started later than planned. The detail "
        "says what was late and by how much; a Recovered entry follows when "
        "it is back on time. If it does not recover, check that "
        "the worker and mail services are running."
    ),
    "web_unhealthy": _(
        "The web server has not answered three health checks in a row, a "
        "minute apart, so the Admin and Family portals may not be loading. "
        "Ask the server operator to restart web from the deployment's "
        "Compose directory (docker compose ... restart web) and to check its "
        "log. A Recovered entry follows once it has answered for five "
        "minutes."
    ),
    "incident_recovered": _(
        "A problem the system had detected has ended. The detail names it "
        "and how long it lasted, and says what to check when its end still "
        "needs follow-up (such as a backup encryption key change); Show "
        "related entries lists the entry that opened it."
    ),
    "production_cancel_requested": _(
        "An Administrator asked to stop clearing test data for the campaign going live."
    ),
    "recipient_refusal_cleared": _(
        "An Administrator cleared a refused email address so email to it can resume."
    ),
    "production_cleanup_failed": _(
        "Clearing test data when the campaign went live did not finish. Retry "
        "it from the campaign's go-live page; if it fails again, tell the "
        "server operator."
    ),
    "source_retention_skipped": _(
        "Removing old ParishSoft copies was skipped this time; the refresh "
        "itself continued and the cleanup will try again next time."
    ),
    "source_tenant_mismatch": _(
        "ParishSoft answered for a different parish than the one configured, "
        "so its data was not used. Check the ParishSoft organization in the "
        "ParishSoft settings."
    ),
    "source_destructive_change": _(
        "A ParishSoft refresh would have removed an unusually large share of "
        "the data, so it was held for review. If the change in ParishSoft is "
        "real, the server operator follows the launch runbook's “Accepting "
        "a large ParishSoft change”; otherwise correct ParishSoft."
    ),
    "source_member_unusable": _(
        "A ParishSoft Member record could not be used and was left out. The "
        "detail names the Family and Member numbers and the field to correct "
        "in ParishSoft."
    ),
    # Process log only (once per process per Ministry and name); a durable
    # Admin notice is tracked in #342.
    "source_ministry_name_repaired": _(
        "A ParishSoft Ministry name was blank, too long or held unusual "
        "characters, so a cleaned-up name was shown instead."
    ),
    "source_refresh_held": _(
        "A ParishSoft refresh was held back instead of replacing the current "
        "data, usually because other work was running; it waits and tries again."
    ),
    "source_credential_failed": _(
        "The ParishSoft key was refused or could not be read. If the detail "
        "says it will not be retried, replace the key on the Integrations page."
    ),
    "source_provider_failed": _(
        "ParishSoft could not be reached or returned an error. The detail says "
        "which, and whether the refresh will be retried; a refresh that gives "
        "up keeps the previous data until the next scheduled one."
    ),
    "mail_provider_failed": _(
        "The mail provider could not be reached or refused a request. Check "
        "the email settings and the provider's status page; sending resumes "
        "on its own once the provider accepts mail again."
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
    "service_status_failed": _(
        "A service could not record its status for the System health page; "
        "it kept working, but the page may show it as out of date."
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
    "hosted_file_uploaded": _("An Administrator uploaded a hosted file."),
    "hosted_file_slug_changed": _(
        "An Administrator changed a hosted file's placeholder name."
    ),
    "hosted_file_deleted": _("An Administrator deleted a hosted file."),
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
    "admin_timeout": _(
        "A portal user's session (Administrator, Staff or Ministry leader) ended "
        "after inactivity."
    ),
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
    "family_engagement_backfilled": _(
        "An operator filled the Families' response-progress records from "
        "retained sign-in and form history."
    ),
    "family_engagement_failed": _(
        "A Family's sign-in or form opening could not be recorded for response "
        "reporting; the Family was not affected and nothing needs to be done."
    ),
    # Past rows only: the Family codes page was removed (#873).
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
    "census_changes_viewed": _("Someone opened the Census changes worklist."),
    "census_changes_exported": _("Someone downloaded the Census changes worklist."),
    "response_dashboard_viewed": _("Someone opened the response dashboard."),
    "response_submitted_list_viewed": _(
        "Someone opened the list of Families that submitted."
    ),
    "response_submitted_list_exported": _(
        "Someone downloaded the list of Families that submitted."
    ),
    "response_started_list_viewed": _(
        "Someone opened the list of Families that started but did not submit."
    ),
    "response_started_list_exported": _(
        "Someone downloaded the list of Families that started but did not submit."
    ),
    "response_not_opened_list_viewed": _(
        "Someone opened the list of invited Families that never opened the form."
    ),
    "response_not_opened_list_exported": _(
        "Someone downloaded the list of invited Families that never opened the form."
    ),
    "response_repeat_list_viewed": _(
        "Someone opened the list of Families that submitted more than once."
    ),
    "response_repeat_list_exported": _(
        "Someone downloaded the list of Families that submitted more than once."
    ),
    "response_data_quality_list_viewed": _(
        "Someone opened the list of ParishSoft data to check."
    ),
    "response_data_quality_list_exported": _(
        "Someone downloaded the list of ParishSoft data to check."
    ),
    "family_timeline_viewed": _("Someone opened a Family's timeline."),
    "portal_users_viewed": _("An Administrator opened the portal users list."),
    "system_logs_exported": _("An Administrator downloaded the system logs."),
    "export_cancelled": _("A report download was cancelled."),
    "chair_review_decided": _(
        "An Administrator decided on a Ministry chairperson change."
    ),
    "campaign_ministries_requested": _(
        "An Administrator asked to change a live campaign's Ministries."
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
        "After a restore, an Administrator decided what to do with an email "
        "that may already have gone out (assume it was sent, send it again, "
        "or not needed)."
    ),
    "restore_review_started": _(
        "The site was restored from a backup and closed for review: Families "
        "cannot sign in and no Family email is sent until it is released."
    ),
    "restore_review_released": _(
        "An Administrator reopened the site after reviewing a restore."
    ),
    "runtime_transition": _("The system changed mode (for example, Testing to live)."),
    "weekly_manual_requested": _("A weekly report was requested by hand."),
    "rehearsal_invalidated": _("A test run no longer counts after a change."),
    "rehearsal_gate_released": _("A required test run was completed."),
}

# Audit types some owners write directly rather than through ``Action``: Python
# code passing ``event_type=`` (the Admin session owner passes its ending
# reason) and SQL triggers. The guard test compares this
# with the source so a new direct type cannot appear without a sentence.
DIRECT_AUDIT_TYPES = frozenset(
    {
        "admin_login",
        "admin_logout",
        "admin_privileges_changed",
        "admin_reauthenticated",
        "admin_revoked",
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
        "operator_admin_recovered",
        "policy_denial_namespace_reset",
        "policy_security_event",
        "production_cancel_requested",
        "recipient_refusal_cleared",
        "rehearsal_gate_released",
        "rehearsal_invalidated",
        "restore_hold_resolved",
        "restore_review_released",
        "restore_review_started",
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


# Recorded fields whose plain name is not just their identifier in words: an
# on-request report's choices (#556). Other fields use ``words``.
FIELD_LABELS = {
    "report_mode": _("Responses shown"),
    "report_filter": _("Filter chosen"),
    "talent_option_id": _("Talent chosen (its settings id)"),
    "snapshot_id": _("ParishSoft data read (its snapshot id)"),
    "search_used": _("Search box used"),
}
# Recorded closed values shown in words, by field (#556): each report's own
# menu wording (reports.response_lists and the Talents report's Show menu).
# "all" and "any" are each report's first, unfiltered choice.
VALUE_LABELS = {
    "report_mode": {"production": _("Production"), "testing": _("Testing")},
    "report_filter": {
        "all": _("Everything (no filter)"),
        "any": _("Everything (no filter)"),
        "invited": _("With a delivered invitation"),
        "uninvited": _("Without a delivered invitation"),
        "progressed": _("Got past the first step"),
        "opened": _("Opened the form only"),
        "followed": _("Link followed"),
        "unfollowed": _("Link not followed"),
        "mailing-name": _("Blank mailing name"),
        "envelope": _("Envelope number 0"),
        "cannot_serve": _("Cannot participate in ministries"),
        "cannot_attend": _("Families that cannot attend Mass"),
        "option": _("One talent"),
    },
}


def field_label(key):
    """The plain name of one recorded field, e.g. "Filter chosen"."""
    return FIELD_LABELS.get(key) or words(key)


def field_value(key, value):
    """One recorded value as shown: a closed word in words, else unchanged."""
    return VALUE_LABELS.get(key, {}).get(value, value)


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

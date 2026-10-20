"""Closed operational contexts, with no arbitrary messages, URLs or value dumps.

Extend these schemas intentionally alongside a privacy regression test. Caller
supplied dictionaries are never treated as already sanitized, even when their
keys look harmless. Domain-specific census audit is owned by its submission
service; this shared operational context is deliberately not a PII container.
"""

import re
from enum import StrEnum
from uuid import UUID

from parishkit.stewardship.audit import refusal_context
from parishkit.stewardship.jobs.operational_content import IncidentKind
from parishkit.stewardship.observability import FailureKind


class ContextKind(StrEnum):
    REQUEST = "request"
    TASK = "task"
    EMAIL = "email"
    SOURCE = "source"
    MEMBER_SOURCE = "member_source"
    PROVIDER = "provider"
    EXCEPTION = "exception"
    ACTION = "action"
    BOUNDARY = "boundary"
    SCHEDULE = "schedule"
    TIMEOUT = "timeout"
    DUE_WORK = "due_work"
    FAILURE = "failure"
    RECOVERY = "recovery"
    REFUSAL = "refusal"


class Outcome(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    DENIED = "denied"
    FAILED = "failed"
    RETRY = "retry"
    CANCELLED = "cancelled"
    CHANGED = "changed"


class ActorKind(StrEnum):
    PORTAL_USER = "portal_user"
    FAMILY = "family"
    SYSTEM = "system"
    OPERATOR = "operator"


class Action(StrEnum):
    CONFIGURATION_REQUEST = "configuration_requested"
    ROLES_APPLIED = "roles_applied"
    SECRET_REPLACEMENT = "secret_replacement_requested"
    # No page records this since the Family codes page was removed (#873);
    # it stays so past System log rows still validate and read.
    FAMILY_CODES_VIEWED = "family_codes_viewed"
    FAMILY_DIRECTORY_VIEWED = "family_directory_viewed"
    POSTAL_OUTREACH_VIEWED = "postal_outreach_viewed"
    MINISTRY_REPORT_VIEWED = "ministry_report_viewed"
    PRIVILEGED_REAUTH = "privileged_reauthentication"
    DESTRUCTIVE_CONFIRMATION = "destructive_confirmation"
    INVALID_LINK = "family_link_invalid"
    FAMILY_MAC_BACKFILLED = "family_mac_backfilled"
    SOURCE_COMPACTED = "source_compacted"
    FACTS_COMPACTED = "facts_compacted"
    FACTS_VERIFIED = "facts_verified"
    EXPORT_REQUESTED = "export_requested"
    EXPORT_CANCELLED = "export_cancelled"
    EXPORT_DOWNLOADED = "export_downloaded"
    SOURCE_PROMOTED = "source_promoted"
    # A promotion or a configuration activation changed who leads which
    # Ministry (#1034): ``count`` is the (address, Ministry) pairs gained,
    # ``matching_count`` those lost; never an address or a Member.
    MINISTRY_LEADERS_CHANGED = "ministry_leaders_changed"
    CHAIR_RECONCILED = "chair_reconciled"
    SOURCE_REJECTED = "source_rejected"
    SOURCE_SUPERSEDED = "source_superseded"
    SOURCE_FALLBACK = "source_fallback_requested"
    BACKGROUND_VIEWED = "background_viewed"
    DELIVERY_VIEWED = "delivery_viewed"
    # One Sent mail bulk retry or "confirm all not sent" (#382 M4); each
    # message it resolves also has its own resolution audit event.
    DELIVERY_BULK_RESOLVED = "delivery_bulk_resolved"
    DAILY_DIGEST_VIEWED = "daily_digest_viewed"
    WEEKLY_DIGEST_VIEWED = "weekly_digest_viewed"
    PARTICIPATION_VIEWED = "participation_viewed"
    # The Campaign comparison report (#1018): its context is the outcome and
    # the comparison day as ``count`` (the campaign days compared), no figure.
    CAMPAIGN_COMPARISON_VIEWED = "campaign_comparison_viewed"
    # A Campaign comparison download requested (#1018, slice 6): the export
    # lifecycle's request event for that kind, with the comparison day as
    # ``count``, no figure. Its cancel and download are the shared events.
    CAMPAIGN_COMPARISON_EXPORTED = "campaign_comparison_exported"
    # A campaign archive workbook requested (#1018 part (a)): the export
    # lifecycle's request event for that kind, with the cutoff's campaign
    # day as ``count``, no figure. The file's SHA-256 and row count are the
    # shared publication's; its cancel and download are the shared events.
    CAMPAIGN_ARCHIVE_EXPORTED = "campaign_archive_exported"
    INFORMATION_VIEWED = "information_viewed"
    INFORMATION_UPDATED = "information_updated"
    LIMITATION_REVIEWED = "limitation_reviewed"
    MINISTRY_FOLLOWUP_VIEWED = "ministry_followup_viewed"
    # The queue's Export these requests download (#1157).
    MINISTRY_FOLLOWUP_EXPORTED = "ministry_followup_exported"
    MINISTRY_REQUEST_UPDATED = "ministry_request_updated"
    # One bulk outcome on the Ministry follow-up queue (#1131): ``count`` is
    # the requests it resolved, ``matching_count`` those chosen. Each request
    # also has its own ministry_request_updated event, with the same
    # correlation id.
    MINISTRY_FOLLOWUP_BULK_APPLIED = "ministry_followup_bulk_applied"
    FINANCIAL_REPORT_VIEWED = "financial_report_viewed"
    TALENTS_REPORT_VIEWED = "talents_report_viewed"
    TALENTS_REPORT_EXPORTED = "talents_report_exported"
    CENSUS_CHANGES_VIEWED = "census_changes_viewed"
    CENSUS_CHANGES_EXPORTED = "census_changes_exported"
    CENSUS_CHANGE_UPDATED = "census_change_updated"
    # Migration 0113 (#1596) re-marked open census changes the live API test
    # showed publication cannot carry as By hand; count: how many. Written
    # once, by the migration, as a system event with no subject.
    CENSUS_HANDLING_CORRECTED = "census_handling_corrected"
    # The baseline refresh re-evaluation's Not seen start (#1689): written
    # once, by migration 0132, as a system event with no subject.
    REFRESH_CHECKS_STARTED = "refresh_checks_started"
    MINISTRY_ROSTER_ENTERED = "ministry_roster_entered"
    # Publication to ParishSoft (#528, DAT-09.01): a preflight's plan (count:
    # its entities), an Administrator's confirmation (count: entities to
    # write) and one entity's write attempt (count: fields it writes). The
    # subject is the plan, confirmation or outcome row, whose plan rows name
    # the fields; no event holds a value.
    CENSUS_PUBLICATION_PLANNED = "census_publication_planned"
    CENSUS_PUBLICATION_CONFIRMED = "census_publication_confirmed"
    CENSUS_PUBLICATION_RECORDED = "census_publication_recorded"
    # Who asked for a preflight or a rerun of a plan's failed run, and when
    # (the subject is the task run), and why a preflight or publish run was
    # refused, failing it with nothing written (the subject is the refused
    # run): the setting switched off, the contact read not confirmed yet, or
    # any other refusal. One event type per reason, so the reason needs no
    # new context field.
    CENSUS_PUBLICATION_PREFLIGHT_REQUESTED = "census_publication_preflight_requested"
    CENSUS_PUBLICATION_RERUN_REQUESTED = "census_publication_rerun_requested"
    CENSUS_PUBLICATION_SWITCHED_OFF = "census_publication_switched_off"
    CENSUS_PUBLICATION_READ_UNCONFIRMED = "census_publication_read_unconfirmed"
    CENSUS_PUBLICATION_REFUSED = "census_publication_refused"
    # The final full refresh an update run requested once its plan updated a
    # Member (#1688); the subject is the refresh command, so System logs
    # says the refresh followed an update.
    CENSUS_PUBLICATION_REFRESH_REQUESTED = "census_publication_refresh_requested"
    # Someone opened Update ParishSoft (#528, ADM-09; #1688); count: plan rows.
    CENSUS_PUBLICATION_VIEWED = "census_publication_viewed"
    ROSTER_CHANGES_VIEWED = "roster_changes_viewed"
    ROSTER_CHANGES_EXPORTED = "roster_changes_exported"
    # Pledges (#1686): opening and downloading it (count: rows),
    # and one decision on a Family's pledge, whose subject is its
    # stewardship_pledge_review row; no event holds an amount.
    PLEDGES_TO_ENTER_VIEWED = "pledges_to_enter_viewed"
    PLEDGES_TO_ENTER_EXPORTED = "pledges_to_enter_exported"
    PLEDGE_REVIEW_UPDATED = "pledge_review_updated"
    RESPONSE_DASHBOARD_VIEWED = "response_dashboard_viewed"
    # The lists of Families behind the response funnel (#477): one event type
    # per list, so the audit names the list without a new context field. The
    # list pages were removed (#933); nothing records these now, and they stay
    # so earlier entries still read.
    RESPONSE_SUBMITTED_LIST_VIEWED = "response_submitted_list_viewed"
    RESPONSE_SUBMITTED_LIST_EXPORTED = "response_submitted_list_exported"
    RESPONSE_STARTED_LIST_VIEWED = "response_started_list_viewed"
    RESPONSE_STARTED_LIST_EXPORTED = "response_started_list_exported"
    RESPONSE_NOT_OPENED_LIST_VIEWED = "response_not_opened_list_viewed"
    RESPONSE_NOT_OPENED_LIST_EXPORTED = "response_not_opened_list_exported"
    RESPONSE_REPEAT_LIST_VIEWED = "response_repeat_list_viewed"
    RESPONSE_REPEAT_LIST_EXPORTED = "response_repeat_list_exported"
    RESPONSE_DATA_QUALITY_LIST_VIEWED = "response_data_quality_list_viewed"
    RESPONSE_DATA_QUALITY_LIST_EXPORTED = "response_data_quality_list_exported"
    # One Family's timeline (#477): the subject is the Family's opaque record id.
    FAMILY_TIMELINE_VIEWED = "family_timeline_viewed"
    # Open form (#529): an Admin opened the Family form to enter a response
    # for the Family; the subject is the Family's opaque record id.
    FAMILY_FORM_OPENED = "family_form_opened"
    DASHBOARD_VIEWED = "dashboard_viewed"
    SYSTEM_LOGS_VIEWED = "system_logs_viewed"
    SYSTEM_LOGS_EXPORTED = "system_logs_exported"
    # Opening System health (ADM-13); its count is the problems it showed.
    SYSTEM_HEALTH_VIEWED = "system_health_viewed"
    SOURCE_FORM_VIEWED = "source_form_viewed"
    # Opening Data to fix in ParishSoft (#1011); its count is the total of
    # the section counts the viewer saw.
    DATA_TO_FIX_VIEWED = "data_to_fix_viewed"
    # Take a backup now (ADM-13 PR 3b); the subject is the request.
    BACKUP_REQUESTED = "backup_requested"
    # Turn off debug logging (ADM-13 PR 6b); the subject is the switch.
    DEBUG_LOGGING_TURNED_OFF = "debug_logging_turned_off"
    # The debug-off switch cleared: by an Administrator in Testing (Allow
    # debug logging again, ADM-13 PR 6c; the subject is the switch), or by
    # the server operator's offline ``debug-off-clear``, recorded with actor
    # kind ``operator``; both name the switch as the subject (the operator's
    # definer returns the id it cleared, #1229).
    DEBUG_LOGGING_ALLOWED = "debug_logging_allowed"
    # Clear the halt (ADM-13 PR 4a); the subject is the clear signal.
    MAIL_HALT_CLEARED = "mail_halt_cleared"
    # Accept this change once (ADM-13 PR 5b); the subject is the acceptance.
    SOURCE_DROP_ACCEPTED = "source_drop_accepted"
    PRESENCE_VIEWED = "family_presence_viewed"
    # The operator's one-time engagement backfill (#477), with its counts.
    FAMILY_ENGAGEMENT_BACKFILLED = "family_engagement_backfilled"
    USERS_VIEWED = "portal_users_viewed"
    # A Users page change recorded as a configuration request (#952): its
    # subject is the request, whose patch names the user; ``count`` is how
    # many users it touches.
    USER_CHANGE_REQUESTED = "user_change_requested"
    # New user's Name lookup (#952): ``count`` is the ParishSoft names found,
    # never the address looked up.
    USER_NAMES_LOOKED_UP = "user_names_looked_up"
    # The Users page's ParishSoft Ministry leaders without a user (#952
    # slice 6): each read, which the definer records itself (``count`` is
    # the rows answered, never an address); a reviewed import recorded as
    # one configuration request (its subject; ``count`` is the users added);
    # and each user it adds (the subject is the new user's record ID).
    USER_LEADERS_LISTED = "user_leaders_listed"
    USER_LEADERS_IMPORT_REQUESTED = "user_leaders_import_requested"
    USER_LEADER_IMPORTED = "user_leader_imported"
    # Email Ministry leaders (#1592), both recorded by SQL: each review read
    # (``count`` is the Ministry leader users answered) and each Apply's
    # request (its subject; ``count`` is the leaders emailed), never an
    # address.
    USER_LEADER_EMAILS_REVIEWED = "user_leader_emails_reviewed"
    USER_LEADER_EMAILS_REQUESTED = "user_leader_emails_requested"
    SECURITY_EVENT_ACKNOWLEDGED = "security_event_acknowledged"
    CRITICAL_EVENTS_ACKNOWLEDGED = "critical_events_acknowledged"
    CREDENTIAL_RESULT_DISMISSED = "credential_result_dismissed"
    # A ParishSoft key change refused because the key sees another
    # organization (#1495); the subject is the key change request. Recorded
    # by the target installer with the request's failure, never with values.
    CREDENTIAL_KEY_ORGANIZATION_MISMATCH = "credential_key_organization_mismatch"
    FAMILY_MAINTENANCE_STARTED = "family_maintenance_started"
    FAMILY_MAINTENANCE_ENDED = "family_maintenance_ended"
    HOSTED_FILE_UPLOADED = "hosted_file_uploaded"
    HOSTED_FILE_SLUG_CHANGED = "hosted_file_slug_changed"
    HOSTED_FILE_DELETED = "hosted_file_deleted"
    CHAIR_REVIEW_DECIDED = "chair_review_decided"
    CAMPAIGN_MINISTRIES_REQUESTED = "campaign_ministries_requested"
    # A live campaign end-date change (#912); its subject is the request.
    CAMPAIGN_END_DATE_REQUESTED = "campaign_end_date_requested"
    # A Campaign setup step kept as is (#1404); its subject is the review row.
    CAMPAIGN_SETUP_STEP_REVIEWED = "campaign_setup_step_reviewed"
    SETUP_STARTED = "setup_started"
    SETUP_SOURCE_STARTED = "setup_source_started"
    SETUP_SOURCE_COMPLETED = "setup_source_completed"
    # A finished setup load discarded by a ParishSoft connection change or
    # a Load again (#1353); written by the attempt's audit trigger.
    SETUP_SOURCE_VOIDED = "setup_source_voided"
    SETUP_FROZEN = "setup_frozen"
    SETUP_EXPIRED = "setup_expired"
    SETUP_CREDENTIAL_STAGED = "setup_credential_staged"
    SETUP_CREDENTIAL_SCRUBBED = "setup_credential_scrubbed"
    # Admin automation sessions (ADM-11): approval, ending (the reason is on
    # the session row), refused use, and an Administrator acknowledging the
    # automation notices on their own dashboard.
    AUTOMATION_SESSION_APPROVED = "automation_session_approved"
    AUTOMATION_SESSION_ENDED = "automation_session_ended"
    AUTOMATION_SESSION_REFUSED = "automation_session_refused"
    AUTOMATION_NOTICES_ACKNOWLEDGED = "automation_notices_acknowledged"
    # An automation session ran a privileged action (ADM-11 PR 5), in that
    # action's transaction. The name is historical (#1547).
    AUTOMATION_FRESH_GATE = "automation_fresh_gate"
    # Admin automation commands that change state (ADM-11): one event per
    # change, admin_cmd_<area>_<verb>, whose subject is the automation
    # session (see the specification's "Audit attribution").
    ADMIN_CMD_SCHEDULE_CONFIRM = "admin_cmd_schedule_confirm"
    ADMIN_CMD_USERS_CONFIRM = "admin_cmd_users_confirm"
    # parish confirm: the Parish settings page's Apply (ADM-11 PR 10).
    ADMIN_CMD_PARISH_CONFIRM = "admin_cmd_parish_confirm"
    # artwork remove: Campaign images' Apply of a removal (ADM-11 PR 10).
    ADMIN_CMD_ARTWORK_REMOVE = "admin_cmd_artwork_remove"
    # branding preview and confirm: Parish logos' upload Review and its
    # review dialog's Apply (#1865).
    ADMIN_CMD_BRANDING_PREVIEW = "admin_cmd_branding_preview"
    ADMIN_CMD_BRANDING_CONFIRM = "admin_cmd_branding_confirm"
    # content confirm: Pages and emails' Delete of unused emails (ADM-11 PR 10).
    ADMIN_CMD_CONTENT_CONFIRM = "admin_cmd_content_confirm"
    # integration confirm: an integration settings page's Apply (ADM-11 PR 10).
    ADMIN_CMD_INTEGRATION_CONFIRM = "admin_cmd_integration_confirm"
    # integration backup-probe: the Off-site backups page's Test access.
    ADMIN_CMD_INTEGRATION_BACKUP_PROBE = "admin_cmd_integration_backup_probe"
    # integration dismiss: a key change line's Dismiss (ADM-11 PR 10).
    ADMIN_CMD_INTEGRATION_DISMISS = "admin_cmd_integration_dismiss"
    # integration key finish-switching: Finish switching to the new key.
    ADMIN_CMD_INTEGRATION_KEY_FINISH_SWITCHING = (
        "admin_cmd_integration_key_finish_switching"
    )
    # integration key replace: a settings page's Save with a new key pasted.
    ADMIN_CMD_INTEGRATION_KEY_REPLACE = "admin_cmd_integration_key_replace"
    # integration backup-key confirm: the Backup encryption key page's Confirm.
    ADMIN_CMD_INTEGRATION_BACKUP_KEY_CONFIRM = (
        "admin_cmd_integration_backup_key_confirm"
    )
    # workgroup confirm: the No-reminders WorkGroup page's Apply (ADM-11 PR 10).
    ADMIN_CMD_WORKGROUP_CONFIRM = "admin_cmd_workgroup_confirm"
    # campaign confirm: Campaign settings' Apply (ADM-11 PR 10).
    ADMIN_CMD_CAMPAIGN_CONFIRM = "admin_cmd_campaign_confirm"
    # campaign shares-confirm: Share options' Apply (ADM-11 PR 10).
    ADMIN_CMD_CAMPAIGN_SHARES_CONFIRM = "admin_cmd_campaign_shares_confirm"
    ADMIN_CMD_USERS_IMPORT_LEADERS = "admin_cmd_users_import_leaders"
    # users email-leaders --confirm: Email Ministry leaders' Apply (#1622).
    ADMIN_CMD_USERS_EMAIL_LEADERS = "admin_cmd_users_email_leaders"
    # The operator's cancellation of a stuck live end-date change (#944).
    ADMIN_CMD_CONFIG_REQUEST_CANCEL = "admin_cmd_config_request_cancel"
    ADMIN_CMD_TASK_RETRY = "admin_cmd_task_retry"
    ADMIN_CMD_REFRESH_START = "admin_cmd_refresh_start"
    ADMIN_CMD_TEST_SAMPLE = "admin_cmd_test_sample"
    ADMIN_CMD_TEST_FAMILIES = "admin_cmd_test_families"
    ADMIN_CMD_DELIVERY_RESOLVE = "admin_cmd_delivery_resolve"
    ADMIN_CMD_EXPORT_CREATE = "admin_cmd_export_create"
    ADMIN_CMD_EXPORT_CANCEL = "admin_cmd_export_cancel"
    ADMIN_CMD_EXPORT_RETRY = "admin_cmd_export_retry"
    # No longer recorded: ``export regenerate`` was removed when exports
    # became point-in-time (#1398); earlier events still name it.
    ADMIN_CMD_EXPORT_REGENERATE = "admin_cmd_export_regenerate"
    ADMIN_CMD_DIGEST_WEEKLY_REQUEST = "admin_cmd_digest_weekly_request"
    ADMIN_CMD_EXPORT_FINANCIAL = "admin_cmd_export_financial"
    ADMIN_CMD_EXPORT_INFORMATION = "admin_cmd_export_information"
    ADMIN_CMD_EXPORT_MINISTRY = "admin_cmd_export_ministry"
    ADMIN_CMD_EXPORT_MINISTRY_PACKET = "admin_cmd_export_ministry_packet"
    ADMIN_CMD_EXPORT_DIRECTORY = "admin_cmd_export_directory"
    ADMIN_CMD_EXPORT_POSTAL = "admin_cmd_export_postal"
    ADMIN_CMD_EXPORT_COMPARISON = "admin_cmd_export_comparison"
    ADMIN_CMD_EXPORT_ARCHIVE = "admin_cmd_export_archive"
    # test families-preview --names: the review's names as an export (#817).
    ADMIN_CMD_EXPORT_FAMILY_TEST_NAMES = "admin_cmd_export_family_test_names"
    ADMIN_CMD_DELIVERY_RESEND = "admin_cmd_delivery_resend"
    ADMIN_CMD_DELIVERY_REFUSAL_CLEAR = "admin_cmd_delivery_refusal_clear"
    # delivery bounce-report: the Mail message page's bounce report (#1211).
    ADMIN_CMD_DELIVERY_BOUNCE_REPORT = "admin_cmd_delivery_bounce_report"
    # portal maintenance set: the Family portal switch (ADM-11 PR 7a).
    ADMIN_CMD_PORTAL_MAINTENANCE_SET = "admin_cmd_portal_maintenance_set"
    # send confirm: Pause and resume mail's Confirm (ADM-11 PR 7b, #463).
    ADMIN_CMD_SEND_CONFIRM = "admin_cmd_send_confirm"
    # system backup-now: System health's Take a backup now (ADM-13 PR 3b).
    ADMIN_CMD_SYSTEM_BACKUP_NOW = "admin_cmd_system_backup_now"
    # system debug-off: System health's Turn off debug logging (ADM-13 PR 6b).
    ADMIN_CMD_SYSTEM_DEBUG_OFF = "admin_cmd_system_debug_off"
    # system refresh-accept: System health's Accept this change once (PR 5b).
    ADMIN_CMD_SYSTEM_REFRESH_ACCEPT = "admin_cmd_system_refresh_accept"
    # system debug-allow: System health's Allow debug logging again (PR 6c).
    ADMIN_CMD_SYSTEM_DEBUG_ALLOW = "admin_cmd_system_debug_allow"
    # system mail-clear: System health's Clear the halt (PR 4a).
    ADMIN_CMD_SYSTEM_MAIL_CLEAR = "admin_cmd_system_mail_clear"
    # files delete and files rename: the Hosted files page's row actions.
    ADMIN_CMD_FILES_DELETE = "admin_cmd_files_delete"
    ADMIN_CMD_FILES_RENAME = "admin_cmd_files_rename"
    ADMIN_CMD_EXPORT_EXACT_CREATE = "admin_cmd_export_exact_create"
    ADMIN_CMD_EXPORT_EXACT_CANCEL = "admin_cmd_export_exact_cancel"
    ADMIN_CMD_EXPORT_EXACT_RETRY = "admin_cmd_export_exact_retry"
    # Imported past-campaign archives (#1018): a Review's preview, an Apply
    # (or a replacement's removal of the year's earlier import) and a
    # removal; and the history commands that make them.
    CAMPAIGN_ARCHIVE_PREVIEWED = "campaign_archive_previewed"
    CAMPAIGN_ARCHIVE_IMPORTED = "campaign_archive_imported"
    CAMPAIGN_ARCHIVE_REPLACED = "campaign_archive_replaced"
    CAMPAIGN_ARCHIVE_REMOVED = "campaign_archive_removed"
    ADMIN_CMD_HISTORY_PREVIEW = "admin_cmd_history_preview"
    ADMIN_CMD_HISTORY_CONFIRM = "admin_cmd_history_confirm"
    ADMIN_CMD_HISTORY_REMOVE = "admin_cmd_history_remove"
    ADMIN_CMD_EXPORT_FAMILY_TIMELINE = "admin_cmd_export_family_timeline"
    # events acknowledge: Home's security event and critical-problem
    # acknowledgements, with the page's own event in the same transaction.
    ADMIN_CMD_EVENTS_ACKNOWLEDGE = "admin_cmd_events_acknowledge"
    # census resolve: a Census changes row's outcome (#528, #463).
    ADMIN_CMD_CENSUS_RESOLVE = "admin_cmd_census_resolve"
    # census review: an Automatic row's review (#528, ADM-09).
    ADMIN_CMD_CENSUS_REVIEW = "admin_cmd_census_review"
    # followup update and followup bulk: a Ministry follow-up request's Save
    # and the queue's bulk outcome (#463 PR 11a).
    ADMIN_CMD_FOLLOWUP_UPDATE = "admin_cmd_followup_update"
    ADMIN_CMD_FOLLOWUP_BULK = "admin_cmd_followup_bulk"
    # census edit: an Automatic row's Edit value or Clear edit (#1434,
    # ADM-09.04).
    ADMIN_CMD_CENSUS_EDIT = "admin_cmd_census_edit"
    # census resolve-conflict: an Automatic Conflict's Use the Family's value
    # anyway (#1434, ADM-09.03).
    ADMIN_CMD_CENSUS_RESOLVE_CONFLICT = "admin_cmd_census_resolve_conflict"
    # census update-check, update-confirm and update-rerun: Update
    # ParishSoft's Check ParishSoft, Update and Run again (#1755).
    ADMIN_CMD_CENSUS_UPDATE_CHECK = "admin_cmd_census_update_check"
    ADMIN_CMD_CENSUS_UPDATE_CONFIRM = "admin_cmd_census_update_confirm"
    ADMIN_CMD_CENSUS_UPDATE_RERUN = "admin_cmd_census_update_rerun"
    # followup roster-entered and followup roster-bulk: a resolved request's
    # Entered in ParishSoft tick and a bulk Entered by hand;
    # followup information-update: an Additional information item's Save
    # (#463 PR 11b).
    ADMIN_CMD_FOLLOWUP_ROSTER_ENTERED = "admin_cmd_followup_roster_entered"
    ADMIN_CMD_FOLLOWUP_ROSTER_BULK = "admin_cmd_followup_roster_bulk"
    ADMIN_CMD_FOLLOWUP_INFORMATION_UPDATE = "admin_cmd_followup_information_update"
    # information decide and limitations decide: a worklist decision, one
    # item or in bulk (#1735).
    ADMIN_CMD_INFORMATION_DECIDE = "admin_cmd_information_decide"
    ADMIN_CMD_LIMITATIONS_DECIDE = "admin_cmd_limitations_decide"
    # pledges decide: a Pledges decision, one pledge or in bulk
    # (#1733).
    ADMIN_CMD_PLEDGES_DECIDE = "admin_cmd_pledges_decide"


# Closed field identifiers are operational metadata, never census values.
MEMBER_SOURCE_FIELDS = frozenset(
    {
        "prefix",
        "first_name",
        "middle_name",
        "last_name",
        "suffix",
        "nickname",
        "maiden_name",
        "birth_date",
        "gender",
        "email",
        "home_phone",
        "mobile_phone",
        "work_phone",
        "death_date",
        "marital_status",
        "language",
    }
)

FIELDS = {
    ContextKind.SCHEDULE: {
        "definition_id",
        "previous_revision_id",
        "selected_revision_id",
        "cancelled_messages",
        "skipped_occurrences",
        "failed_occurrences",
        "delivered_slots",
    },
    ContextKind.BOUNDARY: {
        "occurrence_id",
        "kind",
        "intended_unix_microseconds",
        "actual_unix_microseconds",
        "lag_microseconds",
        "before_state",
        "after_state",
    },
    ContextKind.REQUEST: {"method", "status", "outcome", "source_fingerprint"},
    ContextKind.TASK: {"task_id", "count", "version", "outcome"},
    # Work stopped by a time limit (#293). ``what`` names the limit that
    # stopped it; the seconds are whole numbers; ``count`` is how many times
    # it happened when one entry summarizes several.
    ContextKind.TIMEOUT: {
        "task_id",
        "task_type",
        "attempt",
        "limit_seconds",
        "elapsed_seconds",
        "what",
        "helper",
        "count",
        "outcome",
    },
    # What made scheduled work late (#634); mirrored in
    # stewardship_safe_context_v1. Either the task type with how many tasks
    # were late and the worst lateness against ``limit_seconds``, or the
    # Family send (its schedule ``definition_id`` and ``revision_id``) that
    # stalled or overran, with its counts, how long since its last progress
    # and how long since it fell due (durations, not times), plus how many
    # other tasks broke the per-task rule in the same check.
    ContextKind.DUE_WORK: {
        "task_type",
        "count",
        "lag_seconds",
        "limit_seconds",
        "definition_id",
        "revision_id",
        "remaining_count",
        "done_count",
        "stall_seconds",
        "elapsed_seconds",
        "other_late_count",
        # A campaign start or close that ran late (#633): its occurrence and
        # boundary task, with ``lag_seconds`` against ``limit_seconds``.
        "occurrence_id",
        "task_id",
    },
    # What failed and what happens next (#633); mirrored in
    # stewardship_safe_context_v1. ``failure`` is a closed word naming what
    # failed (``FAILURES``) and ``failure_kind`` the exception's category
    # (``observability.FailureKind``); ``reason`` a closed provider result
    # (``REASONS``) and ``status`` an HTTP status. ``outcome`` says whether it
    # will be retried (``retry``, after ``retry_seconds``, as ``attempt`` of
    # ``attempt_limit``) or gave up (``failed``). Ids name the task, message or
    # snapshot involved; ``count`` how many failed together. ``command`` is an
    # Admin command-line command's catalog name (#617).
    ContextKind.FAILURE: {
        "failure",
        "failure_kind",
        "task_id",
        "task_type",
        "message_id",
        "version",
        "attempt",
        "attempt_limit",
        "retry_seconds",
        "status",
        "reason",
        "count",
        "outcome",
        "command",
    },
    # An operational incident that ended (#633), written by the database when
    # it resolves; mirrored in stewardship_safe_context_v1. ``log_id`` is the
    # entry that opened it (the recovery entry shares its correlation), with
    # how long the incident lasted and how many times it was observed.
    ContextKind.RECOVERY: {
        "incident_id",
        "incident_kind",
        "log_id",
        "elapsed_seconds",
        "count",
    },
    # A refused Admin input (#1403); mirrored in stewardship_safe_context_v1.
    # Its own closed rules (audit.refusal_context): the page or command,
    # server-owned names and codes, the reason, never a submitted value.
    ContextKind.REFUSAL: set(refusal_context.KEYS),
    ContextKind.EMAIL: {"message_id", "recipient_count", "outcome", "reason"},
    ContextKind.SOURCE: {"snapshot_id", "generation", "count", "outcome"},
    ContextKind.MEMBER_SOURCE: {"family_duid", "member_duid", "field"},
    ContextKind.PROVIDER: {"status", "provider_fingerprint", "outcome"},
    ContextKind.EXCEPTION: {"outcome", "retryable"},
    ContextKind.ACTION: {
        "version",
        "before_version",
        "after_version",
        "outcome",
        "source_fingerprint",
        "candidate_fingerprint",
        "count",
        "matching_count",
        "page",
        "directory_reason",
        "directory_phone",
        "directory_response",
        "directory_sort",
        "directory_reach",
        # The directory's ParishSoft data to check filter and whether the
        # response columns were on (#933): closed values.
        "directory_data_check",
        "directory_response_columns",
        "search_used",
        "exact_code_used",
        "ministry_duid",
        "ministry_duids",
        "ministry_operational",
        # A live campaign's Ministry selection change (#342): the selections
        # before it and the DUIDs it adds and removes; `ministry_duids` holds
        # the selections after it.
        "previous_ministry_duids",
        "added_ministry_duids",
        "removed_ministry_duids",
        # An Administrator's decision on a suspended Chairperson seed and the
        # reason entered for it: the decision is a closed word; the reason is
        # the Administrator's own bounded text, refused when it carries an
        # address-like token, since this context is not a container for
        # personal data.
        "decision",
        "review_reason",
        # A hosted file (#346): its placeholder name (a restricted charset,
        # never free text), stored type, size and digest. The original file
        # name is free text and is never recorded here.
        "file_slug",
        "previous_file_slug",
        "file_kind",
        "file_size",
        "file_fingerprint",
        # An on-request report page or download (#556): which system mode it
        # showed, its closed filter choice, the parish's talent option when
        # that was the filter (old rows only; talents were retired in #970),
        # and the ParishSoft snapshot its names came
        # from. Never the search text, which can name a Family; whether a
        # search was used is the existing ``search_used``.
        "report_mode",
        "report_filter",
        "talent_option_id",
        "snapshot_id",
        # A response list's sort order (#851): its closed sort token.
        "report_sort",
        # An imported campaign archive (#1018): its pledge year, and how many
        # day and kept Family rows it holds (counts; the file's SHA-256 is
        # file_fingerprint and its Families responded is count).
        "archive_year",
        "archive_days",
        "archive_family_rows",
        # A discarded setup load (#1353): why it was discarded and how the
        # load had ended, both closed words.
        "void_reason",
        "task_state",
        # How old the Google sign-in was when a setup credential was sealed
        # (#1402), in whole seconds: an integer, as every unlisted key.
        "sign_in_age_seconds",
        # An export's report kind and file format (#1398), so the audit log
        # answers who exported what without its request row: closed words.
        "report",
        "format",
        # A report view the open page made by itself to bring newer figures
        # in (#1620): always true when present; a reader's own view has no
        # such key. Mirrored in stewardship_safe_context_v1.
        "automatic",
    },
}

# Sorted, distinct Ministry DUID lists; mirrored in stewardship_safe_context_v1.
MINISTRY_LIST_FIELDS = frozenset(
    {
        "ministry_duids",
        "previous_ministry_duids",
        "added_ministry_duids",
        "removed_ministry_duids",
    }
)
REVIEW_DECISIONS = frozenset({"keep_role", "restore", "remove"})
# Why a finished setup load was discarded, and how that load had ended
# (#1353); mirrored in stewardship_safe_context_v1.
VOID_REASONS = frozenset({"key_changed", "organization_changed", "reload"})
VOIDED_TASK_STATES = frozenset({"succeeded", "failed"})
# An export's report kind and file format (#1398), as export_report_known
# admits them; mirrored in stewardship_safe_context_v1 (frozen migration 0109).
EXPORT_REPORTS = frozenset(
    {
        "participation",
        "additional_information",
        "family_directory",
        "postal_outreach",
        "ministry",
        "financial",
        "family_test_names",
        "family_timeline",
        "campaign_comparison",
        "campaign_archive",
    }
)
EXPORT_FORMATS = frozenset({"csv", "xlsx", "pdf", "png"})
# An on-request report's system mode and filter choices (#556); mirrored in
# stewardship_safe_context_v1. The filters are each report's own fixed
# choices: the Limitations report's Show words (reports.limitations.SHOW_WORDS),
# plus the ``show`` keys of the response lists, which were removed in favor
# of the family directory's presets (#933) but whose earlier entries still
# hold them, and ``option``, which rows recorded before talents were retired
# (#970) hold for a talent filter, with its ``talent_option_id``. A test keeps
# this set equal to those choices.
REPORT_MODES = frozenset({"production", "testing"})
REPORT_FILTERS = frozenset(
    {
        "all",
        "invited",
        "uninvited",
        "progressed",
        "opened",
        "followed",
        "unfollowed",
        "mailing-name",
        "envelope",
        "any",
        "cannot_serve",
        "cannot_attend",
        "option",
    }
)
# A response list's sort order (#851); mirrored in stewardship_safe_context_v1.
# Each list sorted by any of its columns both ways: the column key ascending,
# ``-key`` descending. The lists were removed (#933); the tokens stay so their
# earlier audit entries still validate and read.
REPORT_SORT_COLUMNS = (
    "family",
    "duid",
    "envelope",
    "submitted",
    "submissions",
    "opened",
    "progressed",
    "invited",
    "link",
    "last",
    "mailing",
    "problem",
    # Entered by Staff (#529), admitted by migration 0065.
    "staff_entered",
)
# The active parishioner family directory's Response choices (#933), as
# stewardship_safe_context_v1 admits them; "yes" and "no" are the older
# Campaign response choices earlier entries recorded.
DIRECTORY_RESPONSES = frozenset(
    {
        "any",
        "yes",
        "no",
        "submitted",
        "more-than-once",
        "started",
        "progressed",
        "opened-only",
        "never-opened",
        "link-followed",
        "link-not-followed",
        "not-invited",
        "not-submitted",
    }
)
# The directory's sort tokens: Family, DUID and each response column, each
# either way (#932, #933).
DIRECTORY_SORTS = frozenset(
    {"name", "name_desc", "duid", "duid_desc"}
    | {
        f"{key}{suffix}"
        for key in (
            "invited",
            "link",
            "opened",
            "progressed",
            "submitted",
            "last",
            "submissions",
        )
        for suffix in ("", "_desc")
    }
)
REPORT_SORTS = frozenset(
    token for key in REPORT_SORT_COLUMNS for token in (key, f"-{key}")
)
# Stored hosted-file types (#346); mirrored in stewardship_safe_context_v1.
HOSTED_FILE_KINDS = frozenset({"pdf", "docx", "xlsx", "pptx", "png", "jpeg"})

# What failed, for a ``failure`` context (#633); mirrored in
# stewardship_safe_context_v1, and each has a plain-language sentence in
# ``log_details``.
FAILURES = frozenset(
    {
        # A ParishSoft read (source.failures.classify_read_failure).
        "organization_mismatch",
        "destructive_change",
        "shifted_scan",
        "invalid_payload",
        "incomplete_collection",
        "invalid_response",
        "lease_unavailable",
        "configuration_activating",
        "configuration_busy",
        "scope_changed",
        "credential_unreadable",
        "credential_changed",
        "provider_status",
        "provider_timeout",
        "provider_unreachable",
        # The source health check refused the configured ParishSoft scope.
        "source_configuration",
        "organization_changed",
        # A health check the operational intake runs on every page.
        "source_health_check",
        "mail_health_check",
        "due_work_health_check",
        "backup_health_check",
        "web_health_check",
        # Web did not answer its liveness check for several minutes in a row
        # (#392 L1; jobs.web_health).
        "web_unresponsive",
        # Background tasks that give up visibly.
        "export_cleanup",
        # An ended export's saved data could not be discarded this pass; the
        # next one tries again (#1398; reports.export_cleanup.produce_discards).
        "export_discard",
        "fact_verification",
        "source_retention",
        "family_engagement",
        # Administrator alerts and security notices (SQL triggers).
        "alert_mail",
        "security_mail",
        "slack_alert",
        # The mail provider as a whole (mail_health.sql).
        "smtp_systemic",
        "smtp_unavailable",
        # An Admin command-line command failed unexpectedly (#617;
        # admin_cli.record_failure): nothing changed, or, for a command that
        # may have committed a change, its outcome is unknown.
        "admin_command",
        "admin_command_outcome_unknown",
        # A daily digest chart the server renderer could not draw on the
        # compile's last attempt, so the digest was sent without its
        # server-drawn charts (#477; reports.digest_tasks.charts_withheld).
        "digest_funnel_chart",
        "digest_activity_chart",
        "digest_comparison_chart",
        # An address follow-up could not set aside an address it recorded
        # as refused permanently (#1201; jobs.address_retry.record_refusals).
        "retry_refusal",
    }
)
# Closed provider results a ``reason`` may hold; mirrored in
# stewardship_safe_context_v1. ``no_deliverable_recipient`` is the email
# context's; the rest are outbox reasons a failed delivery records (#633).
REASONS = frozenset(
    {
        "no_deliverable_recipient",
        "smtp_transient",
        "smtp_unavailable",
        "smtp_permanent",
        "smtp_systemic",
        "smtp_delivery_unknown",
        "preparation_failed",
        "slack_not_sent",
        "slack_delivery_unknown",
    }
)
# Identifier-shaped words (a task type, an incident kind, a failure
# category): the shape SQL checks; Python also checks the closed set.
_WORD = re.compile(r"[a-z][a-z0-9_]{0,63}")

# The limits that can stop work (#293); mirrored in stewardship_safe_context_v1.
TIMEOUT_KINDS = frozenset(
    {
        "read_guard",
        "lease",
        "retention_budget",
        "drive_copy_budget",
        "drive_retry_budget",
        "drive_request",
        "drive_probe_wait",
        "statement_timeout",
        "lock_timeout",
        "transaction_timeout",
        "mail_helper",
        "source_helper",
        "provider_check",
        "renewal_drain",
        "control_lock",
        # A web worker the Gunicorn master killed because it was still
        # serving at the graceful-stop limit, or had stopped reporting
        # to the master past its timeout (#374; web_supervisor).
        "web_drain",
        "web_heartbeat",
        # Background work that stopped waiting for a configuration change
        # to finish activating (#429; activation_hold).
        "configuration_activation",
        # The scheduler's web liveness probe ran out of time (#392 L1;
        # jobs.web_health).
        "web_probe",
        # A full ParishSoft load stopped at its own time bound (#834;
        # source.failures.record_load_budget).
        "source_load_budget",
        # The archive workbook check's helper killed at its time limit
        # during a Review (#1018; archive.imports).
        "archive_check",
        # The chart engine's server renderer killed at its time limit while
        # drawing a daily digest's comparison charts (#1018 slice 7;
        # reports.digest_comparison).
        "digest_chart",
    }
)
# The helper processes a deadline can kill (#293), by their entry point;
# mirrored in stewardship_safe_context_v1.
TIMEOUT_HELPERS = frozenset(
    {
        "readiness_delivery_worker",
        "readiness_notification_worker",
        "family_delivery_worker",
        "digest_delivery_worker",
        "weekly_delivery_worker",
        "operational_mail_worker",
        "operational_slack_worker",
        "security_mail_worker",
        "provider_check_worker",
        "parishsoft_http_worker",
        # The archive workbook check (#1018; archive.check_worker).
        "archive_check_worker",
        # The chart engine's server renderer (#477; chart_render_worker).
        "chart_render_worker",
    }
)


def admin_commands():
    """The Admin command-line catalog's command names, a closed set (#617).

    Imported lazily: the catalog module logs through modules that import
    this one. SQL checks only the name's shape (lower-case words).
    """
    from parishkit.stewardship.admin_cli import BY_NAME

    return BY_NAME.keys()


def sanitize(kind, values):
    """Reject unknown fields/types rather than merely hiding secret-looking names."""
    if not isinstance(kind, ContextKind) or type(values) is not dict:
        raise ValueError("Context requires a canonical schema and mapping.")
    if values.keys() - FIELDS[kind]:
        raise ValueError("Context contains fields outside its approved schema.")
    if (
        kind in {ContextKind.MEMBER_SOURCE, ContextKind.BOUNDARY}
        and values.keys() != FIELDS[kind]
    ):
        raise ValueError("Structured diagnostics require complete identifiers.")
    if kind is ContextKind.REFUSAL:
        # Its keys mean something else here (a reason code, a lower-case
        # method), so it has its own rules; a value they would drop is
        # refused here, as any other context's would be.
        if not (refusal_context.is_clean(values) and refusal_context.complete(values)):
            raise ValueError("Context value does not match its approved type.")
        return {
            key: list(value) if type(value) is list else value
            for key, value in values.items()
        }
    safe = {}
    for key, value in values.items():
        if key == "outcome":
            valid = isinstance(value, Outcome)
            safe[key] = value.value if valid else None
        elif key == "reason":
            valid = type(value) is str and value in REASONS
            safe[key] = value
        elif key == "failure":
            valid = type(value) is str and value in FAILURES
            safe[key] = value
        elif key == "command":
            valid = type(value) is str and value in admin_commands()
            safe[key] = value
        elif key == "failure_kind":
            valid = isinstance(value, FailureKind)
            safe[key] = value.value if valid else None
        elif key == "incident_kind":
            valid = isinstance(value, IncidentKind)
            safe[key] = value.value if valid else None
        elif key == "kind":
            valid = type(value) is str and value in {"start", "close"}
            safe[key] = value
        elif key in {"before_state", "after_state"}:
            valid = type(value) is str and value in {
                "draft",
                "scheduled",
                "active",
                "closed",
                "archived",
                "purging",
                "purge_cleanup_failed",
                "purged",
            }
            safe[key] = value
        elif key == "field":
            valid = type(value) is str and value in MEMBER_SOURCE_FIELDS
            safe[key] = value
        elif key in {"family_duid", "member_duid", "ministry_duid"}:
            valid = type(value) is int and 0 < value < 2**31
            safe[key] = value
        elif key in MINISTRY_LIST_FIELDS:
            valid = (
                type(value) is list
                and all(type(item) is int and 0 < item < 2**31 for item in value)
                and value == sorted(set(value))
            )
            safe[key] = list(value) if valid else None
        elif key == "method":
            valid = type(value) is str and value in {"GET", "HEAD", "POST"}
            safe[key] = value
        elif key == "task_type":
            valid = type(value) is str and _WORD.fullmatch(value)
            safe[key] = value
        elif key == "what":
            valid = type(value) is str and value in TIMEOUT_KINDS
            safe[key] = value
        elif key == "helper":
            valid = type(value) is str and value in TIMEOUT_HELPERS
            safe[key] = value
        elif key == "decision":
            valid = type(value) is str and value in REVIEW_DECISIONS
            safe[key] = value
        elif key == "void_reason":
            valid = type(value) is str and value in VOID_REASONS
            safe[key] = value
        elif key == "task_state":
            valid = type(value) is str and value in VOIDED_TASK_STATES
            safe[key] = value
        elif key == "report":
            valid = type(value) is str and value in EXPORT_REPORTS
            safe[key] = value
        elif key == "format":
            valid = type(value) is str and value in EXPORT_FORMATS
            safe[key] = value
        elif key in {"file_slug", "previous_file_slug"}:
            valid = type(value) is str and re.fullmatch(
                r"[a-z0-9]+(-[a-z0-9]+)*", value
            )
            valid = valid and len(value) <= 64
            safe[key] = value
        elif key == "file_kind":
            valid = type(value) is str and value in HOSTED_FILE_KINDS
            safe[key] = value
        elif key == "report_mode":
            valid = type(value) is str and value in REPORT_MODES
            safe[key] = value
        elif key == "report_filter":
            valid = type(value) is str and value in REPORT_FILTERS
            safe[key] = value
        elif key == "report_sort":
            valid = type(value) is str and value in REPORT_SORTS
            safe[key] = value
        elif key == "review_reason":
            valid = type(value) is str and 0 < len(value) <= 500 and "@" not in value
            safe[key] = value if valid else None
        elif key.endswith("_id"):
            valid = isinstance(value, UUID)
            safe[key] = str(value) if valid else None
        elif key.endswith("_fingerprint"):
            valid = type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value)
            safe[key] = value
        elif key in {
            "retryable",
            "search_used",
            "exact_code_used",
            "ministry_operational",
            "directory_response_columns",
            "automatic",
        }:
            valid = type(value) is bool
            safe[key] = value
        elif key.startswith("directory_"):
            choices = {
                "directory_reason": {
                    "any",
                    "no_head",
                    "no_address",
                    "invalid_address",
                    "provider_refused",
                    "deliverable",
                },
                "directory_phone": {"any", "yes", "no"},
                # reports.directories.RESPONSES plus "any" (#933), and the
                # older "yes" and "no" earlier entries recorded.
                "directory_response": DIRECTORY_RESPONSES,
                # reports.directories.DIRECTORY_SORTING's tokens (#933).
                "directory_sort": DIRECTORY_SORTS,
                # reports.directories.REACH plus "any" (#388 L1).
                "directory_reach": {"any", "email", "mail", "neither"},
                # reports.directories.CHECKS plus "any" (#933).
                "directory_data_check": {"any", "anything", "mailing-name", "envelope"},
            }
            valid = type(value) is str and value in choices[key]
            safe[key] = value
        else:
            valid = type(value) is int and 0 <= value <= 2**63 - 1
            if key == "status":
                valid = valid and 100 <= value <= 599
            safe[key] = value
        if not valid:
            raise ValueError("Context value does not match its approved type.")
    return safe

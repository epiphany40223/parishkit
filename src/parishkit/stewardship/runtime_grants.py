"""Closed runtime SQL grants for the implemented foundation, not future features.

Provisioning and startup share this reviewed registry. Future application tables
receive no automatic access. Offline recovery retains the config-installer OS
identity/implementation, but uses its own operator-only SQL login so revocation
and operator-request INSERT never become online config-installer privileges.
"""

from parishkit.config import ConfigError

from .deployment import ServiceRole
from .reports.archive_grants import ARCHIVE_TABLE, CHILD_TABLES

# Explicit snapshot/read vocabulary; no wildcard over future application tables.
WEB_READ_TABLES = frozenset(
    [
        # Recorded skips and holds of refresh slots (#632), which the pages'
        # data age and send-hold wording read.
        "stewardship_source_slot_decision",
        "stewardship_setup_completion",
        "stewardship_campaign_mail_test",
        "stewardship_setup_prepared",
        "stewardship_setup_attempt",
        "stewardship_setup_draft_section",
        "stewardship_setup_config_intent",
        "stewardship_setup_config_abort",
        "stewardship_branding_bundle",
        "stewardship_branding_asset",
        "stewardship_provider_context",
        "stewardship_public_credential_handoff",
        "stewardship_source_current",
        # The manual refresh page records a refresh command through the
        # domain's own admission, which reads any waiting request to coalesce
        # and the lease owner through the column grant below; identifiers
        # and window digests only.
        "stewardship_source_refresh_request",
        "stewardship_source_refresh_command",
        "stewardship_chair_suggestion",
        "stewardship_chair_seed_intent",
        # The suspended-assignment review reads the episodes, the receipts
        # that opened them and the retained Member: record ids, reasons and
        # DUIDs, never contact data.
        "stewardship_chair_review",
        "stewardship_chair_reconciliation",
        "stewardship_chair_seed_evidence",
        "stewardship_snapshot_ministry",
        "stewardship_source_ministry",
        "stewardship_snapshot_fund",
        "stewardship_source_fund",
        "stewardship_snapshot_family",
        "stewardship_source_family",
        "django_migrations",
        "django_session",
        "socialaccount_socialapp",
        "stewardship_oauth_consumption",
        "stewardship_auth_incident",
        "stewardship_limiter_health",
        "stewardship_configuration_version",
        "stewardship_parish",
        "stewardship_applied_integration",
        "stewardship_domain_rule",
        "stewardship_address_rule",
        "stewardship_address_grant",
        "stewardship_ministry_assignment",
        "stewardship_content_version",
        "stewardship_portal_user",
        "stewardship_assignment_overlay",
        "stewardship_policy_security_event",
        "stewardship_policy_security_ack",
        "stewardship_policy_epoch",
        "stewardship_admin_revocation",
        "stewardship_config_request",
        "stewardship_config_checkpoint",
        "stewardship_system_configuration",
        "stewardship_config_activation",
        "stewardship_secret_request",
        "stewardship_secret_checkpoint",
        "stewardship_credential_consumer_ack",
        "stewardship_portal_session",
        "stewardship_campaign_configuration",
        "stewardship_campaign",
        "stewardship_schedule_revision",
        "stewardship_credential_deployment",
        "stewardship_credential_key_state",
        "stewardship_family_campaign",
        "stewardship_family_eligibility",
        "stewardship_family_code_mac",
        "stewardship_family_token_generation",
        "stewardship_family_token",
        "stewardship_rehearsal_epoch",
        "stewardship_campaign_credentials",
        "stewardship_rehearsal_credential",
        "stewardship_rehearsal_code_mac",
        "stewardship_rehearsal_reservation",
        "stewardship_family_session",
        "stewardship_campaign_work_gate",
        "stewardship_campaign_transition",
        "stewardship_runtime_transition",
        "stewardship_campaign_config_intent",
        "stewardship_campaign_control",
        "stewardship_campaign_config_abort",
        "stewardship_campaign_boundary",
        "stewardship_activation_catchup",
        "stewardship_catchup_failure",
        "stewardship_catchup_checkpoint",
        "stewardship_schedule_definition",
        "stewardship_schedule_selection",
        "stewardship_schedule_occurrence",
        "stewardship_occurrence_transition",
        "stewardship_schedule_fulfillment",
        "stewardship_schedule_work_summary",
        "stewardship_restore_delivery_hold",
        "stewardship_restore_hold_resolution",
        "stewardship_postclose_resolution",
        "stewardship_task_run",
        "stewardship_family_mail_preparation",
        "stewardship_family_mail_test",
        "stewardship_task_event",
        "stewardship_audit_event",
        "stewardship_audit_context",
        "stewardship_operational_log",
        # Off-site backup status and Drive access checks (Integrations page).
        "stewardship_backup_upload",
        "stewardship_backup_drive_probe",
        # Hosted files (#346): the library and where each file is used.
        "stewardship_hosted_file",
        "stewardship_hosted_file_use",
        # System health (ADM-13): what each online process last reported
        # (web also writes its own, see service_status_grants), and every
        # count a refused ParishSoft load was checked on (counts only).
        "stewardship_service_status",
        "stewardship_source_drop_count",
        # Take a backup now's requests (ADM-13 PR 3): who asked, when, and
        # what became of each; no path, host or credential.
        "stewardship_backup_request",
        # Turn off debug logging (ADM-13 PR 6b): the panel says who set the
        # debug-off switch and when; no path, host or credential.
        "stewardship_debug_off_switch",
        # Accept this change once (ADM-13 PR 5b): who accepted the refused
        # large change, when and until when, and the counts reviewed; counts
        # and identifiers only, no parish data.
        "stewardship_source_drop_acceptance",
        # Accept this change once's example Families (ADM-13 PR 5c): the
        # DUID and directory name of a few Families a refused change would
        # remove or change. The web already reads every Family's directory
        # name and DUID from the snapshot; the page shows only the examples
        # stewardship_source_drop_example_attempt_v1() still allows.
        "stewardship_source_drop_example",
        # Clear the halt (ADM-13 PR 4a): the current halt (its identity, kind
        # and start), the clear signals and the mailbox checks, with who
        # asked and when; no message, address, host or credential.
        "stewardship_mail_halt",
        "stewardship_mail_halt_clear",
        "stewardship_mailbox_check",
    ]
)

# The newest backup's key and when it completed (#198): what the Backup
# encryption key page shows, and what a portal key change's security alert
# names as the key before it. Sizes, digests and versions stay unread.
BACKUP_KEY_COLUMNS = frozenset({"id", "completed_at", "recipient_fingerprint"})
# System health's backups panel (ADM-13) also shows the newest backup's size
# and the version it was taken with, and its key check compares when the
# newest backup started with when the configured key was set (#1214): a
# backup reads its key as it starts. Digests and paths stay unread.
BACKUP_PANEL_COLUMNS = BACKUP_KEY_COLUMNS | {
    "started_at",
    "database_bytes",
    "files_bytes",
    "application_version",
}
# What request mode may change on a backup request (ADM-13 PR 3); the guard
# in schema/migrations/0062_backup_request.sql decides which moves are valid.
BACKUP_REQUEST_COLUMNS = frozenset(
    {"state", "held_at", "claimed_at", "finished_at", "backup_run_id", "failure_kind"}
)

WEB_INSERT_TABLES = frozenset(
    [
        "stewardship_hosted_file",
        "stewardship_backup_drive_probe",
        # Take a backup now (ADM-13 PR 3): its guard admits only a live
        # Administrator session, one request at a time.
        "stewardship_backup_request",
        # Turn off debug logging (ADM-13 PR 6b): its guard (0078) admits only
        # a live Administrator session, while no switch is set. The web
        # clears one only in Testing (a column UPDATE below, 0088).
        "stewardship_debug_off_switch",
        # Accept this change once (ADM-13 PR 5b): its guard (0080) admits
        # only a live Administrator session, for the newest refusal with
        # its counts exactly as recorded, while no other acceptance is live.
        # No login may update or delete one.
        "stewardship_source_drop_acceptance",
        # Clear the halt (ADM-13 PR 4a): each guard (0115) admits only an
        # enabled Administrator with a live session (no recent sign-in,
        # #1547); the clear's also repeats every check the preview makes,
        # and a mailbox check only while none is live. No login may update
        # or delete a clear; only mail dispatch settles a check.
        "stewardship_mail_halt_clear",
        "stewardship_mailbox_check",
        "stewardship_source_refresh_request",
        "stewardship_source_refresh_command",
        "stewardship_chair_seed_intent",
        "stewardship_setup_attempt",
        "stewardship_setup_draft_section",
        "stewardship_branding_bundle",
        "stewardship_branding_asset",
        "stewardship_provider_context",
        "django_session",
        "stewardship_oauth_consumption",
        "stewardship_auth_incident",
        "stewardship_limiter_health",
        "stewardship_portal_user",
        "stewardship_portal_session",
        "stewardship_config_request",
        "stewardship_config_checkpoint",
        "stewardship_secret_request",
        "stewardship_secret_checkpoint",
        "stewardship_credential_consumer_ack",
        # No stewardship_family_session: only stewardship_family_login_v1
        # creates a Family session, after proving the presented credential
        # (#306 M3). Web keeps UPDATE for activity, presence and revocation.
        "stewardship_audit_event",
        "stewardship_audit_context",
        "stewardship_operational_log",
        "stewardship_task_run",
        "stewardship_task_event",
        "stewardship_campaign_work_gate",
        "stewardship_campaign_transition",
        "stewardship_runtime_transition",
        "stewardship_campaign_config_intent",
        "stewardship_campaign_control",
        "stewardship_campaign_config_abort",
        "stewardship_activation_catchup",
        # The restore review (#537): listing held emails (only during a
        # review; holds only suppress mail), and an Administrator's decision
        # on each, which the guard admits only for a live Administrator
        # session.
        "stewardship_restore_delivery_hold",
        "stewardship_restore_hold_resolution",
    ]
)

WEB_UPDATE_TABLES = frozenset(
    [
        # Slug changes, and the FOR KEY SHARE pin a configuration request
        # takes on each file its content names (PostgreSQL needs UPDATE).
        "stewardship_hosted_file",
        "stewardship_setup_attempt",
        "stewardship_setup_draft_section",
        "stewardship_branding_bundle",
        "django_session",
        "stewardship_auth_incident",
        "stewardship_limiter_health",
        "stewardship_portal_user",
        "stewardship_portal_session",
        "stewardship_config_request",
        "stewardship_secret_request",
        "stewardship_family_session",
        "stewardship_system_configuration",
        "stewardship_campaign",
        "stewardship_campaign_work_gate",
        "stewardship_campaign_control",
        "stewardship_activation_catchup",
        # A settled held email's state (#537); its guard admits only the
        # change its exact, append-only decision record names.
        "stewardship_restore_delivery_hold",
    ]
)

# Only response-guard and fresh Admin authorization reads exist in this phase.
# Report owners add their data explicitly; credential/session-link inventories,
# audit payloads and worker queues do not belong to the streaming login.
DOWNLOAD_READ_TABLES = frozenset(
    {
        "stewardship_campaign",
        "stewardship_system_configuration",
        "stewardship_configuration_version",
        "stewardship_applied_integration",
        "stewardship_campaign_configuration",
        "stewardship_schedule_revision",
        "stewardship_parish",
        "stewardship_domain_rule",
        "stewardship_address_rule",
        "stewardship_address_grant",
        "stewardship_ministry_assignment",
        "stewardship_content_version",
        "stewardship_portal_user",
        "stewardship_assignment_overlay",
        "stewardship_policy_epoch",
        "stewardship_admin_revocation",
        "stewardship_portal_session",
    }
)


# SECURITY DEFINER routines a login may call directly, as PostgreSQL's
# oidvectortypes spells their signatures. Admission otherwise refuses every
# definer EXECUTE, and database-grants grants exactly these.
FAMILY_LOGIN_FUNCTION = (
    "stewardship_family_login_v1(character varying, uuid, uuid, jsonb, text)"
)
# Promoted Ministry catalog DUIDs, for a live campaign's added Ministries (#342).
MINISTRY_CATALOG_FUNCTION = "stewardship_ministry_catalog_v1()"
# The automation maintenance task's Django session purge for ended Admin
# sessions (ADM-11): the worker never reads a session key itself.
SESSION_PURGE_FUNCTION = "stewardship_admin_session_purge_v1(uuid[])"
# The mail consumers' 24-hour Family mail count, for System health (ADM-13):
# one number, without the web reading any routed recipient address.
DAILY_SENDS_FUNCTION = "stewardship_family_daily_sends_v1()"
# When that count falls below the Family limit ("Sending resumes at about
# 3:10 PM", #530): one instant, for the same reason.
DAILY_RESUME_FUNCTION = "stewardship_family_daily_resume_v1()"
# Whether a sign-in instant recorded on a secret request is a live full-scope
# automation session's (ADM-11 PR 5): the web's intake guard and each
# credential installer's staged-to-testing guard call it, and the installers
# read no automation table.
AUTOMATION_FRESH_FUNCTION = (
    "stewardship_automation_fresh_principal_v1(uuid, timestamp with time zone)"
)
# Whether that instant is a full-scope automation session's and every such
# session has ended (0114, #1547): the same guards call it now that a
# request's sign-in may be of any age, so ending an automation session still
# stops its secret requests. Granted to the same logins.
AUTOMATION_ENDED_FUNCTION = (
    "stewardship_automation_instant_ended_v1(uuid, timestamp with time zone)"
)
# The credential installers whose secret requests an automation session may
# make (key replacement); the other targets have no such request.
AUTOMATION_SECRET_TARGETS = frozenset({"parishsoft", "google_workspace", "slack"})
# The general worker's export recovery counts a task's read-guard stops
# (#386, L3) without reading the operational log's context.
READ_GUARD_KILLS_FUNCTION = "stewardship_read_guard_kills_v1(uuid)"
# The general worker's hourly maintenance prunes long-finished runs'
# liveness events (#386, L2).
TASK_EVENT_PRUNE_FUNCTION = "stewardship_task_event_prune_v1(integer, integer, integer)"
# One portal user's Ministry scope from ParishSoft roster roles (#922): every
# login that evaluates a principal or a Ministry scope (web, the worker's
# export and retry checks, and a download's fresh authorization) learns only
# the scope, never the Member contact data that decides it.
LEADER_SCOPE_FUNCTION = "stewardship_ministry_leader_scope_v1(uuid)"
# Records a count-only audit event when a change moved who leads which
# Ministry (#1034): source promotion (worker) and configuration activation
# (installer) call it after their change. It writes the event itself and
# returns nothing, so neither login reads an address, a Member DUID or even
# the counts; it also refuses any other session.
LEADER_CHANGES_FUNCTION = (
    "stewardship_ministry_leader_changes_v1(uuid, uuid, uuid, uuid, uuid, uuid, uuid)"
)
# The offline users upgrade (#952) reads each recorded Google identity and
# whether it ever signed in, and the ParishSoft Member names of the addresses
# it names (a new user's default Name), without audit-log or source grants.
# Web reads them too, for the same read-only upgrade preview on Portal users
# and in pk-admin users preview-upgrade (#952 slice 2): web already reads
# the identities and the audit log, and the names definer takes no address:
# it discloses only the names of the active rules' and recorded identities'
# addresses, never the rest of a Member's contact record.
PORTAL_IDENTITIES_FUNCTION = "stewardship_portal_identities_v1()"
MEMBER_NAMES_FUNCTION = "stewardship_member_names_v1()"
# The two the users upgrade itself needs (``upgrade-users`` requires them;
# admin recovery's other definers stay optional, as on the upgrade's base).
USERS_UPGRADE_FUNCTIONS = frozenset({PORTAL_IDENTITIES_FUNCTION, MEMBER_NAMES_FUNCTION})
# The New user page's Name default (#952 slice 5): the ordered names of the
# current ParishSoft Members listing one typed address. Web learns only the
# names, never another contact field. The definer refuses unless the
# caller's Admin session is live and its user an Administrator now. Since
# #995 (migration 0057) the database stores only a digest of each Admin
# session key and the definer digests the key it is handed, so a session
# value web can read is no key; against a login that holds a live cookie's
# key, the cap of 60 lookups an hour for each Administrator and an audit
# event per lookup still hold.
MEMBER_NAMES_FOR_FUNCTION = "stewardship_member_names_for_v1(text, text, uuid)"
# The Users page's ParishSoft Ministry leaders without a user (#952 slice 6):
# each leader address that is no user yet, with the Name, Member DUID and
# Ministries the table shows, and a count of leaders with no usable address.
# It takes no address, so it is no oracle; it answers only a live
# Administrator session (the same digest check as the Name lookup, #995)
# and audits every call with the number of rows. It has no rate cap,
# deliberately: one call already returns everything, so repeated calls
# disclose nothing more; the audit and the digest check are the protections.
LEADERS_WITHOUT_USER_FUNCTION = "stewardship_leaders_without_user_v1(text, uuid)"
# The Users page's Email Ministry leaders review (#1592): each Ministry
# leader user's campaign Ministries and their open request counts. Like the
# leaders list it takes no address, answers only a live Administrator
# session and audits every call with the number of leaders; it returns no
# parishioner data, only counts and Ministry names.
LEADER_EMAILS_FUNCTION = "stewardship_leader_emails_v1(text, uuid)"
# One email's recipient addresses for its Mail message page (#1024): web
# reads them, for an Administrator only, without any grant on the email's
# rendering (its subject and body).
RECIPIENTS_FUNCTION = "stewardship_delivery_recipients_v1(uuid, uuid)"
# What each of those addresses' latest attempt recorded (#806), for the same
# page and caller, without any grant on the attempt's evidence.
RECIPIENT_OUTCOMES_FUNCTION = "stewardship_delivery_recipient_outcomes_v1(uuid, uuid)"
# What a delivered email's follow-up to its addresses refused for now did
# (#806 slice 2), for the same page and caller: positions and words only.
ADDRESS_RETRIES_FUNCTION = "stewardship_delivery_address_retries_v1(uuid, uuid)"
# Sent mail's Recipient filter and column (#1562), for an Administrator
# only: the ids of the emails meant for one address, and the addresses of
# a page's Administrator reports and notices, without any grant on the
# renderings that hold them.
ADDRESS_DELIVERIES_FUNCTION = "stewardship_delivery_ids_for_address_v1(text, uuid)"
PAGE_RECIPIENTS_FUNCTION = "stewardship_delivery_page_recipients_v1(uuid[], uuid)"
# An Administrator's report that a delivered email bounced at one address
# (#806 slice 4): the report's only writer, so web keeps SELECT only on the
# refusal table; the definer and the refusal guard admit it only from web.
BOUNCE_REPORT_FUNCTION = (
    "stewardship_report_recipient_bounce_v1(uuid, uuid, text, uuid, text)"
)
# A source refresh refreshes its staged snapshot's planner statistics
# before the ready transition, outside the work-order lock (#895 change 8):
# the worker gains no ANALYZE or MAINTAIN authority of its own.
SNAPSHOT_ANALYZE_FUNCTION = "stewardship_source_snapshot_analyze_v1(uuid)"
# Mail dispatch opens that follow-up (and its task, which it may not insert
# itself) under its live delivery claim, and reads the Family's link token
# for it under the follow-up's own claim, both in frozen migration 0077.
ADDRESS_RETRY_OPEN_FUNCTION = (
    "stewardship_address_retry_open_v1(uuid, uuid, bigint, uuid, text, text, integer)"
)
ADDRESS_RETRY_TOKEN_FUNCTION = (
    "stewardship_address_retry_token_v1(uuid, uuid, uuid, bigint, uuid)"
)
# The campaign comparison report's figures that read each Family's own rows
# (#1018): web learns only their aggregates, never a kept archive Family's id.
COMPARISON_FUNCTION = "stewardship_campaign_comparison_v1(uuid, date, numeric[], uuid)"
ARCHIVE_RENEWAL_FUNCTION = (
    "stewardship_campaign_archive_renewal_v1(uuid, uuid, integer)"
)
# The campaign archive's parts of each live answer under one fact set's pins
# (#1018 part (a)): the general worker writes the archive workbook without
# any grant on the answers column.
ARCHIVE_ANSWERS_FUNCTION = (
    "stewardship_campaign_archive_answers_v1(uuid, timestamp with time zone)"
)
# Mail dispatch's one token read (#389 L1): the ciphertext and digest of
# one unsent Family message's own link token, only while the caller's
# delivery task for that message is live. Mail dispatch holds the token
# private key, so it has no read grant on the ciphertext columns themselves.
DISPATCH_TOKEN_FUNCTION = (
    "stewardship_dispatch_family_token_v1(uuid, uuid, uuid, bigint, uuid)"
)
# Whether System health's debug-off switch is set (ADM-13, #530): one
# boolean, read by every process that reaches the database
# (``debug_switch``), without any login reading who set it.
DEBUG_OFF_FUNCTION = "stewardship_debug_logging_off_v1()"
# The server operator's offline ``pk-stewardship debug-off-clear``: it
# returns the cleared switch's id (or NULL), which the command's audit
# event names (0098, #1229, which dropped 0078's count-only v1).
DEBUG_OFF_CLEAR_FUNCTION = "stewardship_debug_off_clear_v2()"
# Whether one exact publication audit event is recorded (#528, DAT-09.01):
# the worker writes plan and outcome events but cannot read audit rows, so
# its deferred checks ask this, which answers for those two types only.
PUBLICATION_AUDITED_FUNCTION = (
    "stewardship_publication_audited_v1(text, uuid, uuid, jsonb)"
)
# Point-in-time exports (#1398, migration 0109): the scheduler discards the
# saved data of every ended export, a small batch at a time, through the
# owner-run discard functions (reports.export_cleanup.produce_discards); a
# batch of 0 only asks whether any is due.
EXPORT_DISCARD_FUNCTION = "stewardship_export_discard_due_v1(integer)"
# Publication plans and outcomes (#528, DAT-09.01, migration 0087): the
# worker writes a preflight's plan and each write attempt's outcome; web
# reads them all and writes only an Administrator's confirmation.
PUBLICATION_WORKER_TABLES = (
    "stewardship_publication_plan",
    "stewardship_publication_entity",
    "stewardship_publication_field",
    "stewardship_publication_outcome",
)
PUBLICATION_TABLES = (
    *PUBLICATION_WORKER_TABLES,
    "stewardship_publication_confirmation",
)
# Each definer routine above that a migration in the users swap's closure
# creates (#952), with that migration. `migrate --users-upgrade-base` leaves
# the closure pending, and database-grants runs on that base before the
# conversion, so it grants such a routine only once its migration is
# applied (``pending_functions``). A test proves this lists every registry
# routine the closure's migrations create.
CLOSURE_FUNCTIONS = {
    MEMBER_NAMES_FOR_FUNCTION: ("stewardship_accounts", "0014_user_name_lookup"),
    LEADERS_WITHOUT_USER_FUNCTION: (
        "stewardship_accounts",
        "0015_leaders_without_user",
    ),
    RECIPIENTS_FUNCTION: ("stewardship_jobs", "0022_mail_message_recipients"),
    # The campaign comparison report's figures (0067, #1018) follow 0066.
    **dict.fromkeys(
        (COMPARISON_FUNCTION, ARCHIVE_RENEWAL_FUNCTION),
        ("stewardship_reports", "0018_campaign_comparison"),
    ),
    # What each address got (0068, #806) follows 0067.
    RECIPIENT_OUTCOMES_FUNCTION: (
        "stewardship_jobs",
        "0024_mail_message_address_outcomes",
    ),
    # Ministry leader changes (0070, #1034) follow 0069.
    LEADER_CHANGES_FUNCTION: ("stewardship_accounts", "0018_leader_changes"),
    # Mail dispatch's one token read (0073, #389 L1) follows 0072.
    DISPATCH_TOKEN_FUNCTION: ("stewardship_jobs", "0025_dispatch_token_access"),
    # The daily limit's resume time (0076, #530) follows 0075.
    DAILY_RESUME_FUNCTION: ("stewardship_jobs", "0026_daily_send_resume"),
    # Address follow-ups (0077, #806) follow 0076.
    **dict.fromkeys(
        (
            ADDRESS_RETRIES_FUNCTION,
            ADDRESS_RETRY_OPEN_FUNCTION,
            ADDRESS_RETRY_TOKEN_FUNCTION,
        ),
        ("stewardship_jobs", "0027_address_retry"),
    ),
    # The debug-off switch (0078, #530) follows 0077.
    DEBUG_OFF_FUNCTION: ("stewardship_jobs", "0028_debug_off_switch"),
    # The bounce report (0081, #806) follows 0080.
    BOUNCE_REPORT_FUNCTION: ("stewardship_jobs", "0030_bounce_report"),
    # The campaign archive's answers read (0084, #1018).
    ARCHIVE_ANSWERS_FUNCTION: (
        "stewardship_reports",
        "0023_campaign_archive_exports",
    ),
    # The staged snapshot's statistics (0086, #895) follow 0085.
    SNAPSHOT_ANALYZE_FUNCTION: (
        "stewardship_source",
        "0010_snapshot_statistics",
    ),
    # Publication plans (0087, #528) follow 0086.
    PUBLICATION_AUDITED_FUNCTION: (
        "stewardship_responses",
        "0004_publication_plans",
    ),
    # The operator's debug-off clear naming its switch (0098, #1229).
    DEBUG_OFF_CLEAR_FUNCTION: (
        "stewardship_jobs",
        "0032_debug_off_clear_subject",
    ),
    # The discard of ended exports' saved data (0109, #1398).
    EXPORT_DISCARD_FUNCTION: (
        "stewardship_reports",
        "0029_point_in_time_exports",
    ),
    # The ended-automation check of secret requests (0114, #1547).
    AUTOMATION_ENDED_FUNCTION: ("stewardship_accounts", "0027_no_sign_in_age"),
    # Sent mail's recipient reads (0116, #1562).
    **dict.fromkeys(
        (ADDRESS_DELIVERIES_FUNCTION, PAGE_RECIPIENTS_FUNCTION),
        ("stewardship_jobs", "0034_sent_mail_recipients"),
    ),
    # The Email Ministry leaders review (0120, #1592).
    LEADER_EMAILS_FUNCTION: ("stewardship_jobs", "0035_leader_emails"),
}


# Each granted table a migration in the users swap's closure creates, with
# that migration, for the same reason: database-grants on the users
# upgrade's base cannot name a table that is not there yet, so it grants on
# one only once its migration is applied (``pending_tables``). The same test
# proves this lists every granted table the closure's migrations create.
# #1018's archive tables (0059) follow 0058 (#1024), so they are in it, and
# so is #528's census resolution history (0060), which follows 0059, and
# its roster Entered in ParishSoft history (0061), which follows 0060.
# Take a backup now's request record (0062, #530) follows 0061, the
# Family timeline export's capture (0063, #463) follows 0062, and the
# Campaign comparison export's capture (0071, #1018) follows 0070.
_ARCHIVES = ("stewardship_reports", "0014_campaign_archives")
# Clear the halt's three tables (0115, #530).
MAIL_HALT_TABLES = (
    "stewardship_mail_halt",
    "stewardship_mail_halt_clear",
    "stewardship_mailbox_check",
)
# What mail dispatch may change on a mailbox check (ADM-13 PR 4a): its claim
# and outcome; the guard in schema/migrations/0115_mail_halt_clear.sql
# decides which moves are valid.
MAILBOX_CHECK_COLUMNS = frozenset({"state", "claimed_at", "finished_at"})
# What mail dispatch reads of the service status records to see a halted
# consumer (ADM-13 PR 4a): no version, process start, target or log level.
MAIL_HALT_STATUS_COLUMNS = frozenset({"service", "sender_state", "reported_at"})
CLOSURE_TABLES = {
    **dict.fromkeys((ARCHIVE_TABLE, *CHILD_TABLES), _ARCHIVES),
    "stewardship_proposal_resolution": (
        "stewardship_responses",
        "0002_proposal_resolution",
    ),
    "stewardship_ministry_roster_entry": (
        "stewardship_workflows",
        "0002_roster_entries",
    ),
    "stewardship_backup_request": ("stewardship_jobs", "0023_backup_request"),
    # Point-in-time exports' discard record (0109, #1398), which fact
    # compaction reads (reports.fact_grants).
    "stewardship_export_discard": (
        "stewardship_reports",
        "0029_point_in_time_exports",
    ),
    "stewardship_timeline_export_snapshot": (
        "stewardship_reports",
        "0015_timeline_exports",
    ),
    "stewardship_comparison_export_snapshot": (
        "stewardship_reports",
        "0020_comparison_exports",
    ),
    # A delivered email's follow-ups to addresses refused for now (0077, #806).
    "stewardship_outbox_address_retry": ("stewardship_jobs", "0027_address_retry"),
    # System health's debug-off switch (0078, #530), which web sets and reads.
    "stewardship_debug_off_switch": ("stewardship_jobs", "0028_debug_off_switch"),
    # An Administrator's one-time acceptance of a refused large ParishSoft
    # change (0080, #530), which web records and reads and the worker reads.
    "stewardship_source_drop_acceptance": (
        "stewardship_source",
        "0008_drop_acceptance",
    ),
    # A refused large change's example Families (0085, #530), which the
    # worker records and deletes and web reads.
    "stewardship_source_drop_example": ("stewardship_source", "0009_drop_examples"),
    # Clear the halt's halt records, clear signals and mailbox checks (0115,
    # #530), which mail dispatch and web write and read.
    **dict.fromkeys(MAIL_HALT_TABLES, ("stewardship_jobs", "0033_mail_halt_clear")),
    # Publication plans and outcomes (0087, #528), which the worker writes
    # and web reads and confirms.
    **dict.fromkeys(
        PUBLICATION_TABLES, ("stewardship_responses", "0004_publication_plans")
    ),
    # An Administrator's review of an Automatic census change (0096, #528),
    # which web records and reads.
    "stewardship_proposal_review": ("stewardship_responses", "0006_proposal_review"),
    # A census change the parish added and its later step (0137, #1710),
    # which web records and reads.
    **dict.fromkeys(
        ("stewardship_census_addition", "stewardship_census_addition_resolution"),
        ("stewardship_responses", "0014_census_additions"),
    ),
    # A Campaign setup step an Administrator kept as is (0105, #1404), which
    # web records and reads.
    "stewardship_campaign_setup_review": (
        "stewardship_accounts",
        "0025_campaign_setup_review",
    ),
    # Ministry leader emails (0120, #1592): web records requests and lists
    # recipients, the worker prepares them and mail dispatch sends them.
    **dict.fromkeys(
        (
            "stewardship_leader_email_request",
            "stewardship_leader_email_cohort",
            "stewardship_leader_email_recipient",
        ),
        ("stewardship_jobs", "0035_leader_emails"),
    ),
}


# Each granted column a migration in the users swap's closure adds to a
# table the base already has, with that migration, for the same reason:
# database-grants on the base cannot name a column that is not there yet,
# so it grants on one only once its migration is applied
# (``pending_columns``). #1018's digest comparison (0072) gives web the
# daily digest ready row's new comparison column.
CLOSURE_COLUMNS = {
    ("stewardship_daily_digest_ready", "comparison"): (
        "stewardship_reports",
        "0021_digest_comparison",
    ),
    # #477's 0083 gives web the ready row's funnel_chart column, which the
    # saved page read until #1370 removed the funnel charts; kept until a
    # forward migration drops the column.
    ("stewardship_daily_digest_ready", "funnel_chart"): (
        "stewardship_reports",
        "0022_digest_funnel_charts",
    ),
}


# Column grants an earlier release declared that a migration in the users
# swap's closure revokes, with that migration: role -> table -> privilege ->
# columns. On the users upgrade's base the login still holds them, and this
# release no longer declares them, so database-grants tolerates exactly
# these (never grants them) until their migration is applied
# (``pending_revokes``); the migration then revokes them, and the grants
# after the full migration admit only what is declared.
CLOSURE_REVOKES = {
    ("stewardship_jobs", "0025_dispatch_token_access"): {
        ServiceRole.MAIL_DISPATCH: {
            "stewardship_family_token": {"SELECT": frozenset({"ciphertext"})},
            "stewardship_rehearsal_credential": {
                "SELECT": frozenset({"token_ciphertext"})
            },
        },
    },
}


def pending_functions(applied):
    """The registry routines whose creating migration is not in ``applied``.

    ``applied`` is the ``(app, name)`` pairs of ``django_migrations``.
    """
    return frozenset(
        function
        for function, migration in CLOSURE_FUNCTIONS.items()
        if migration not in applied
    )


def pending_tables(applied):
    """The registry tables whose creating migration is not in ``applied``."""
    return frozenset(
        table for table, migration in CLOSURE_TABLES.items() if migration not in applied
    )


def pending_columns(applied):
    """The registry ``(table, column)`` pairs whose adding migration is pending."""
    return frozenset(
        column
        for column, migration in CLOSURE_COLUMNS.items()
        if migration not in applied
    )


def pending_revokes(role, applied):
    """The column grants ``role`` may still hold until a pending revoke applies.

    ``applied`` is the ``(app, name)`` pairs of ``django_migrations``; the
    answer has ``runtime_grants``' column shape (table -> privilege ->
    columns), for admission only.
    """
    tolerated = {}
    for migration, roles in CLOSURE_REVOKES.items():
        if migration in applied:
            continue
        for table, privileges in roles.get(role, {}).items():
            for privilege, names in privileges.items():
                tolerated.setdefault(table, {}).setdefault(privilege, set()).update(
                    names
                )
    return tolerated


def runtime_functions(role, *, target=None):
    """Return the definer routines this login may EXECUTE.

    Web signs Families in, reads the 24-hour Family mail count and when
    it falls below the Family limit (System health) and, for an
    Administrator, one email's recipient addresses and what its latest
    attempt recorded for each (its Mail message page, #1024, #806); the
    configuration installer checks that a Ministry added to a live
    campaign is in the promoted catalog, without source grants; the general
    worker purges the Django sessions of ended Admin sessions, counts an
    export's read-guard stops, prunes old task events and refreshes a staged
    source snapshot's planner statistics (#895).
    Web and the parishsoft, google_workspace and slack credential installers
    check a secret request's automation sign-in instant (ADM-11 PR 5). Web,
    the general worker and the download login read a user's role-derived
    Ministry scope (#922); the general worker and the configuration
    installer record the Ministry leader pairs a promotion or an activation
    gained and lost (#1034). The offline admin-recovery login reads recorded
    identities and Member names for the users upgrade (#952), and web reads
    them for that upgrade's read-only preview, one typed address's Member
    names for the New user page's Name default, and the ParishSoft Ministry
    leaders who are no user yet for the Users page's import, and each
    Ministry leader user's Ministries and open request counts for its Email
    Ministry leaders review (#1592). Web reads the
    campaign comparison report's aggregates (#1018), and so does the general
    worker, for the daily digest's Compared with past campaigns section.
    The general worker also reads the archive's parts of each live answer
    to write the campaign archive workbook (#1018).
    Mail dispatch reads the one link token of a Family message it holds a
    live delivery claim on (#389 L1). Web reads what a delivered email's
    follow-up to its addresses refused for now did (#806); mail dispatch
    opens that follow-up and reads its link token. Web records an
    Administrator's report that a delivered email bounced (#806 slice 4),
    and, for an Administrator, reads the emails meant for one address and
    the addresses of a Sent mail page's reports and notices (#1562).
    The general worker asks whether a publication plan's or outcome's audit
    event is recorded (#528), since it cannot read audit rows itself.
    The scheduler discards ended exports' saved data (#1398), through the
    owner-run batch that rechecks each export has ended.
    Every login a running service or an operator command uses reads whether
    the debug-off switch is set (#530); the offline admin-recovery login
    also clears it (``pk-stewardship debug-off-clear``). The backup login
    reads the table through pg_read_all_data instead: its admission refuses
    any definer grant.
    """
    role = _identity_role(role, target)
    if role is ServiceRole.CREDENTIAL_INSTALLER:
        functions = {DEBUG_OFF_FUNCTION}
        if target in AUTOMATION_SECRET_TARGETS:
            functions |= {AUTOMATION_FRESH_FUNCTION, AUTOMATION_ENDED_FUNCTION}
        return frozenset(functions)
    return frozenset(
        {
            ServiceRole.WEB: {
                FAMILY_LOGIN_FUNCTION,
                DAILY_SENDS_FUNCTION,
                DAILY_RESUME_FUNCTION,
                AUTOMATION_FRESH_FUNCTION,
                AUTOMATION_ENDED_FUNCTION,
                LEADER_SCOPE_FUNCTION,
                PORTAL_IDENTITIES_FUNCTION,
                MEMBER_NAMES_FUNCTION,
                MEMBER_NAMES_FOR_FUNCTION,
                LEADERS_WITHOUT_USER_FUNCTION,
                LEADER_EMAILS_FUNCTION,
                RECIPIENTS_FUNCTION,
                COMPARISON_FUNCTION,
                ARCHIVE_RENEWAL_FUNCTION,
                RECIPIENT_OUTCOMES_FUNCTION,
                ADDRESS_RETRIES_FUNCTION,
                BOUNCE_REPORT_FUNCTION,
                ADDRESS_DELIVERIES_FUNCTION,
                PAGE_RECIPIENTS_FUNCTION,
                DEBUG_OFF_FUNCTION,
            },
            ServiceRole.CONFIG_INSTALLER: {
                MINISTRY_CATALOG_FUNCTION,
                LEADER_CHANGES_FUNCTION,
                DEBUG_OFF_FUNCTION,
            },
            ServiceRole.MAIL_DISPATCH: {
                DISPATCH_TOKEN_FUNCTION,
                ADDRESS_RETRY_OPEN_FUNCTION,
                ADDRESS_RETRY_TOKEN_FUNCTION,
                DEBUG_OFF_FUNCTION,
            },
            ServiceRole.SCHEDULER: {DEBUG_OFF_FUNCTION, EXPORT_DISCARD_FUNCTION},
            ServiceRole.WORKER: {
                SESSION_PURGE_FUNCTION,
                READ_GUARD_KILLS_FUNCTION,
                TASK_EVENT_PRUNE_FUNCTION,
                LEADER_SCOPE_FUNCTION,
                LEADER_CHANGES_FUNCTION,
                COMPARISON_FUNCTION,
                ARCHIVE_RENEWAL_FUNCTION,
                SNAPSHOT_ANALYZE_FUNCTION,
                DEBUG_OFF_FUNCTION,
                ARCHIVE_ANSWERS_FUNCTION,
                PUBLICATION_AUDITED_FUNCTION,
            },
            "download": {LEADER_SCOPE_FUNCTION},
            ServiceRole.ADMIN_RECOVERY: {
                PORTAL_IDENTITIES_FUNCTION,
                MEMBER_NAMES_FUNCTION,
                DEBUG_OFF_FUNCTION,
                DEBUG_OFF_CLEAR_FUNCTION,
            },
        }.get(role, ())
    )


def _identity_role(role, target):
    """Normalize both resolvers identically and reject ignored qualifiers."""
    from .deployment import SECRET_NAMES

    if role != "download":
        try:
            role = ServiceRole(role)
        except (TypeError, ValueError):
            raise ConfigError("Unknown runtime database identity.") from None
    if role is ServiceRole.CREDENTIAL_INSTALLER:
        if target not in SECRET_NAMES - {"handoff_private"}:
            raise ConfigError("A recognized installer target is required.")
    elif target is not None:
        raise ConfigError("Only credential installers accept a target.")
    return role


def runtime_grants(role, *, target=None):
    """Return fresh table/column maps so callers cannot broaden the shared policy."""
    from .accounts.configuration_service import (
        CONFIGURATION_COLUMNS,
        CONFIGURATION_GRANTS,
    )
    from .accounts.credential_database import installer_permissions
    from .accounts.secret_models import SECRET_TARGETS

    role = _identity_role(role, target)
    from .jobs.service_status_grants import (
        add_service_status_housekeeping_grants,
        add_service_status_writer_grants,
    )

    if role is ServiceRole.MAIL_DISPATCH:
        from .accounts.setup_mail_grants import mail_runtime_grants

        tables, columns = mail_runtime_grants()
        tables["stewardship_delivery_message_release"] = {"SELECT"}
        tables["stewardship_postclose_current"] = {"SELECT"}
        columns.setdefault("stewardship_postclose_resolution", {}).setdefault(
            "SELECT", set()
        ).update({"id", "campaign_id", "mode", "obligation_key", "occurrence_id"})
        add_service_status_writer_grants(tables, columns)
        # Clear the halt (ADM-13 PR 4a): a halted consumer records its halt
        # and reads the clear signal for it; the main process claims and
        # settles a mailbox check. Its guards (0115) admit nothing else.
        tables["stewardship_mail_halt"] = {"SELECT", "INSERT"}
        tables["stewardship_mail_halt_clear"] = {"SELECT"}
        tables["stewardship_mailbox_check"] = {"SELECT"}
        columns["stewardship_mailbox_check"] = {"UPDATE": set(MAILBOX_CHECK_COLUMNS)}
        # The main process claims a check also while a mail-dispatch status
        # record says Halted (a halt the other process holds with no
        # marker): it reads only which service, its sender state and when.
        columns["stewardship_service_status"]["SELECT"].update(MAIL_HALT_STATUS_COLUMNS)
        return tables, columns
    if role in {ServiceRole.WORKER, ServiceRole.SCHEDULER}:
        from .jobs.grants import task_runtime_grants

        tables, columns = task_runtime_grants(role)
        tables["stewardship_delivery_message_release"] = {"SELECT"}
        tables["stewardship_postclose_current"] = {"SELECT"}
        columns.setdefault("stewardship_postclose_resolution", {}).setdefault(
            "SELECT", set()
        ).update({"id", "campaign_id", "mode", "obligation_key", "occurrence_id"})
        if role is ServiceRole.WORKER:
            tables.setdefault("stewardship_delivery_pause_hold", set()).add("SELECT")
            # An Administrator's one-time acceptance of a refused large
            # ParishSoft change (ADM-13 PR 5, #530): a full refresh whose drop
            # check fails reads the live one. Counts and identifiers only; its
            # guard admits no write from this login.
            tables["stewardship_source_drop_acceptance"] = {"SELECT"}
            # Its example Families (ADM-13 PR 5c): recorded with the
            # refusal, and deleted by the hourly maintenance once they may
            # no longer be shown. The guard admits no update, and inserts
            # only in the refusal's own transaction.
            tables["stewardship_source_drop_example"] = {"SELECT", "INSERT", "DELETE"}
            # Publication (#528, DAT-09.01): the preflight writes its plan and
            # the publish step each attempt's outcome, and both read the
            # Administrator's confirmation. Identifiers, versions, counts and
            # hashes only; every guard admits INSERT alone.
            for table in PUBLICATION_WORKER_TABLES:
                tables[table] = {"SELECT", "INSERT"}
            tables["stewardship_publication_confirmation"] = {"SELECT"}
            add_service_status_housekeeping_grants(tables, columns)
        else:
            add_service_status_writer_grants(tables, columns)
        return tables, columns
    if role is ServiceRole.CONFIG_INSTALLER:
        return {table: set(grants) for table, grants in CONFIGURATION_GRANTS.items()}, {
            table: {privilege: set(names) for privilege, names in columns.items()}
            for table, columns in CONFIGURATION_COLUMNS.items()
        }
    if role is ServiceRole.CREDENTIAL_INSTALLER:
        if target not in SECRET_TARGETS:
            raise ConfigError("A recognized installer target is required.")
        installer_tables, installer_metadata = installer_permissions(target)
        tables = {
            table: set(grants)
            for table, grants in installer_tables.items()
            if table not in installer_metadata
        }
        columns = {
            table: {"SELECT": set(names)} for table, names in installer_metadata.items()
        }
        # The installer's new write grant (ADM-13): its own status record.
        add_service_status_writer_grants(tables, columns)
        return tables, columns
    if role in {ServiceRole.BOOTSTRAP, ServiceRole.ADMIN_RECOVERY}:
        from .runtime_database import offline_columns, offline_grants

        return offline_grants(role), offline_columns(role)
    if role is ServiceRole.BACKUP_WORKER:
        # The dump itself reads through the pg_read_all_data membership that
        # provisioning grants; the registry holds the one row it may write and
        # read back (the database supplies the completion time), and the
        # off-site copy outcome it appends after each set. A copy stopped by
        # its whole-copy deadline, or a backup task's lost lease, is logged
        # (#293): insert only, and a trigger admits only timeout events.
        # Request mode (ADM-13 PR 3) reads requests and settles them through
        # a column UPDATE; the request's guard checks every move.
        return {
            "stewardship_backup_run": {"SELECT", "INSERT"},
            "stewardship_backup_upload": {"SELECT", "INSERT"},
            "stewardship_operational_log": {"INSERT"},
            "stewardship_backup_request": {"SELECT"},
        }, {"stewardship_backup_request": {"UPDATE": set(BACKUP_REQUEST_COLUMNS)}}
    if role not in {ServiceRole.WEB, "download"}:
        raise ConfigError(
            "This service's runtime database authority is not implemented."
        )
    tables = {
        table: {"SELECT"}
        for table in (
            WEB_READ_TABLES if role is ServiceRole.WEB else DOWNLOAD_READ_TABLES
        )
    }
    tables["stewardship_download_policy"] = {"SELECT"}
    # The short pre-stream FOR SHARE claim needs one UPDATE privilege; its SQL
    # guard rejects an id-only update, and the actual stream is READ ONLY.
    columns = {"stewardship_download_policy": {"UPDATE": {"id"}}}
    if role is ServiceRole.WEB:
        # Login incidents and critical diagnostics retain atomic safe intent,
        # never rendered messages, recipients or provider mutation authority.
        tables["stewardship_ops_incident"] = {"SELECT", "INSERT"}
        # An Administrator's acknowledgement of a security event is one
        # append-only row; the event itself stays readable and immutable.
        tables["stewardship_policy_security_ack"] = {"SELECT", "INSERT"}
        # Acknowledging the critical-events banner is likewise one append-only
        # row read back by every Admin page's banner count.
        tables["stewardship_critical_event_ack"] = {"SELECT", "INSERT"}
        # The banner says a listed problem has ended once the incident that
        # took in each of its entries has resolved (#633): which incident took
        # in which entry, and nothing else of the receipt.
        columns["stewardship_ops_log_receipt"] = {"SELECT": {"log_id", "incident_id"}}
        columns["stewardship_ops_incident"] = {
            "UPDATE": {
                "signal_level",
                "action",
                "version",
                "actor_id",
                "correlation_id",
            }
        }
        # The Backup encryption key page names the key the newest backup used
        # until an Administrator sets one (#198), and System health shows the
        # newest backup's size and version (ADM-13): those columns only.
        columns["stewardship_backup_run"] = {"SELECT": set(BACKUP_PANEL_COLUMNS)}
        # Accept this change once (#530) acknowledges the critical problem its
        # refusal raised, whose log entry names the refused task: which task
        # each refused attempt ran, and nothing else of the attempt (its
        # manifest, configuration and credential fingerprint stay private).
        columns["stewardship_source_refresh_attempt"] = {"SELECT": {"id", "task_id"}}
        # Setup progress observes live source ownership, never mutates the lease.
        columns["stewardship_source_lease"] = {
            "SELECT": {
                "owner_id",
                "task_fence",
                "worker_id",
                "expires_at",
                "heartbeat_at",
            }
        }
        # Readiness verifies the full-observation anchor using permanent public
        # manifest metadata. Cursor load evidence contains completeness counts
        # and the campaign's Reminder WorkGroup name and member Family DUIDs
        # (#861; identifiers this login already reads on Family rows), not
        # source payloads; validation/content/corpus columns remain private.
        columns["stewardship_source_snapshot"] = {
            "SELECT": {
                "id",
                "organization_id",
                "kind",
                "state",
                "started_at",
                "completed_at",
                "promoted_at",
                "cursor",
            }
        }
        # Login holds these current-epoch/credential rows against rotation and
        # cleanup. PostgreSQL requires an UPDATE privilege for a row lock;
        # id-only grants permit locking, not guarded credential mutation.
        for table in (
            "stewardship_credential_deployment",
            "stewardship_campaign_credentials",
            "stewardship_rehearsal_credential",
            "stewardship_family_token",
            "stewardship_family_token_generation",
        ):
            columns[table] = {"UPDATE": {"id"}}
        columns["stewardship_family_campaign"] = {
            "UPDATE": {"last_activity_at", "version"}
        }
        # The statement-level Family update trigger executes this UPDATE even
        # for activity-only changes; eligibility predicates and row guards still
        # prevent web from changing source-population authority.
        columns["stewardship_campaign_credentials"]["UPDATE"].update(
            {"population_dirty", "version"}
        )
        for privilege, names in (
            ("INSERT", WEB_INSERT_TABLES),
            ("UPDATE", WEB_UPDATE_TABLES),
        ):
            for table in names:
                tables.setdefault(table, set()).add(privilege)
        tables["django_session"].add("DELETE")
        # Web creates tasks and cancels an Admin's waiting cleanup, never
        # claims or executes one; the task login guard enforces the same.
        from .source.grants import SCHEDULER_CANCEL_COLUMNS

        columns["stewardship_task_run"] = {"UPDATE": set(SCHEDULER_CANCEL_COLUMNS)}
        # Allow debug logging again (ADM-13 PR 6c, #1207): the switch's guard
        # (0088, 0114) admits a web clear only in Testing mode, for a live
        # Administrator session, once; it keeps who set the switch and when.
        columns["stewardship_debug_off_switch"] = {
            "UPDATE": {"cleared_at", "cleared_by", "cleared_actor_id"}
        }
        # Deleting a hosted file; its guard refuses one still in use.
        tables["stewardship_hosted_file"].add("DELETE")
        tables["stewardship_sealed_credential_staging"] = {"INSERT"}
        columns["stewardship_sealed_credential_staging"] = {
            "SELECT": {"reference", "request_id", "target", "fingerprint"}
        }
        tables["stewardship_setup_sealed_credential"] = {"INSERT"}
        columns["stewardship_setup_sealed_credential"] = {
            "SELECT": {
                "id",
                "created_at",
                "updated_at",
                "version",
                "actor_id",
                "correlation_id",
                "attempt_id",
                "target",
                "fingerprint",
                "settings",
                "scrubbed_at",
            },
            "UPDATE": {
                "ciphertext",
                "fingerprint",
                "settings",
                "scrubbed_at",
                "actor_id",
                "correlation_id",
                "version",
            },
        }
        from .accounts.setup_exchange_grants import (
            add_exchange_cleanup_grants,
            add_web_setup_catalog_grants,
        )

        add_exchange_cleanup_grants(columns)
        add_web_setup_catalog_grants(tables, columns)
        from .accounts.setup_mail_grants import add_setup_mail_cleanup_grants

        add_setup_mail_cleanup_grants(tables, columns, read_payload=True)
        tables["stewardship_setup_mail_delivery"].add("INSERT")
        tables["stewardship_campaign_mail_test"].add("INSERT")
        tables["stewardship_family_mail_test"].add("INSERT")
        tables["stewardship_setup_slack_delivery"].add("INSERT")
        tables["stewardship_setup_config_intent"].add("INSERT")
        tables["stewardship_setup_readiness_binding"] = {"SELECT", "INSERT"}
        # A Campaign setup step kept as is (#1404): insert-only, so a
        # reviewed step can never be withdrawn or rewritten.
        tables["stewardship_campaign_setup_review"] = {"SELECT", "INSERT"}
        tables["stewardship_setup_credential_install"] = {"SELECT"}
        from .responses.grants import add_response_web_grants

        add_response_web_grants(tables, columns)
        from .campaigns.engagement_grants import add_engagement_web_grants

        add_engagement_web_grants(tables, columns)
        from .accounts.automation_grants import add_automation_web_grants

        add_automation_web_grants(tables, columns)
        add_service_status_writer_grants(tables, columns, reads_table=True)
        # Verified clearance is append-only and independently admitted by SQL;
        # its trigger owns the one-Family eligibility effect, not the web login.
        tables["stewardship_recipient_refusal"] = {"SELECT"}
        tables["stewardship_recipient_resolution"] = {"SELECT", "INSERT"}
        tables["stewardship_delivery_resolution"] = {"SELECT", "INSERT"}
        from .jobs.family_dispatch_grants import METADATA_FIELDS
        from .reports.digest_grants import add_digest_web_grants
        from .reports.weekly_grants import add_weekly_web_grants

        add_digest_web_grants(tables, columns)
        add_weekly_web_grants(tables, columns)
        from .jobs.leader_email_grants import add_leader_email_web_grants

        add_leader_email_web_grants(tables, columns)
        from .reports.archive_grants import add_archive_web_grants

        add_archive_web_grants(tables, columns)

        columns["stewardship_outbox_message"] = {
            "SELECT": set(METADATA_FIELDS) | {"created_at", "updated_at", "finished_at"}
        }
        columns["stewardship_outbox_event"] = {
            "SELECT": {
                "id",
                "message_id",
                "created_at",
                "version",
                "state",
                "action",
                "attempt",
                "actor_id",
                "reason",
                "run_id",
            }
        }
        from .campaigns.cleanup_grants import (
            add_cleanup_web_commands,
            add_cleanup_web_reads,
        )

        add_cleanup_web_reads(tables, columns)
        add_cleanup_web_commands(tables, columns)
        tables["stewardship_activation_impact"] = {"SELECT"}
        tables["stewardship_production_confirmation"] = {"SELECT", "INSERT"}
        tables["stewardship_production_withdrawal"] = {"SELECT", "INSERT"}
        tables["stewardship_withdrawal_inventory"] = {"SELECT"}
        tables["stewardship_delivery_control"] = {"SELECT", "INSERT"}
        tables["stewardship_delivery_control_inventory"] = {"SELECT"}
        tables["stewardship_delivery_control_health"] = {"SELECT"}
        tables["stewardship_delivery_family_recovery_summary"] = {"SELECT"}
        tables["stewardship_delivery_digest_recovery_summary"] = {"SELECT"}
        tables["stewardship_delivery_closed_coverage_summary"] = {"SELECT"}
        for table in (
            "stewardship_production_tokens",
            "stewardship_production_token_cancel",
        ):
            tables[table] = {"SELECT", "INSERT"}
    from .reports.export_grants import add_export_grants

    add_export_grants(
        tables, columns, role=role.value if role is ServiceRole.WEB else role
    )
    if role == "download":
        from .reports.digest_grants import add_digest_download_grants

        add_digest_download_grants(columns)
    if role is ServiceRole.WEB:
        tables["stewardship_delivery_message_release"] = {"SELECT"}
        tables["stewardship_postclose_current"] = {"SELECT"}
    return tables, columns


def login_name(role, *, target=None):
    """Resolve only implemented runtime identities, never caller-supplied SQL names."""
    from .deployment import SECRET_NAMES

    role = _identity_role(role, target)

    if role is ServiceRole.CREDENTIAL_INSTALLER:
        if target not in SECRET_NAMES - {"handoff_private"}:
            raise ConfigError("A recognized installer target is required.")
    elif (
        role
        not in {
            ServiceRole.WEB,
            ServiceRole.WORKER,
            ServiceRole.SCHEDULER,
            ServiceRole.MAIL_DISPATCH,
            ServiceRole.CONFIG_INSTALLER,
            ServiceRole.BOOTSTRAP,
            ServiceRole.ADMIN_RECOVERY,
            ServiceRole.BACKUP_WORKER,
            "download",
        }
        or target is not None
    ):
        raise ConfigError("Unknown runtime database identity.")
    suffix = (
        "credential_" + target
        if role is ServiceRole.CREDENTIAL_INSTALLER
        else str(role).replace("-", "_")
    )
    return "pk_stewardship_" + suffix


def admit_runtime_database(configuration):
    """Validate actual login/table/column authority before any online operation.

    During an online service's startup window the whole admission is retried
    while the database is not ready yet (runtime_database.during_startup,
    #453); elsewhere it runs once.
    """
    from .runtime_database import during_startup

    return during_startup(lambda: _admit_runtime_database(configuration))


def _admit_runtime_database(configuration):
    """One admission pass: actual login, grants, schema and capacity."""
    from .accounts.configuration_service import admit_configuration_database
    from .accounts.credential_database import (
        _identity,
        admit_grants,
        admit_installer_database,
        admit_web_staging_grants,
    )
    from .runtime_database import (
        require_capacity,
        require_current_schema,
        require_no_temporary_authority,
        require_role_capacity,
    )

    role = configuration.service_role
    if role is ServiceRole.CREDENTIAL_INSTALLER:
        admit_installer_database(configuration.credential_target)
    elif role is ServiceRole.CONFIG_INSTALLER:
        admit_configuration_database()
    elif role in {
        ServiceRole.WEB,
        ServiceRole.WORKER,
        ServiceRole.SCHEDULER,
        ServiceRole.MAIL_DISPATCH,
    }:
        _identity(login_name(role))
        tables, columns = runtime_grants(role)
        allowed = {table: set(grants) for table, grants in tables.items()}
        for table, grants in columns.items():
            allowed.setdefault(table, set()).update(grants)
        admit_grants(allowed, functions=runtime_functions(role))
        if role is ServiceRole.WEB:
            admit_web_staging_grants()
        from django.db import connection

        admit_columns(connection, tables, columns)
    else:
        raise ConfigError("This online runtime is not implemented.")
    require_no_temporary_authority()
    require_current_schema()
    require_capacity(configuration)
    require_role_capacity(configuration)


def admit_columns(database, tables, columns):
    """A column-only grant must not hide a whole-table privilege of the same kind."""
    for table, privileges in columns.items():
        for privilege, allowed in privileges.items():
            if privilege in tables.get(table, set()):
                continue
            with database.cursor() as cursor:
                cursor.execute(
                    "SELECT a.attname FROM pg_attribute a JOIN pg_class c "
                    "ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='public' AND c.relname=%s AND a.attnum>0 "
                    "AND NOT a.attisdropped "
                    "AND has_column_privilege(current_user,c.oid,a.attnum,%s)",
                    [table, privilege],
                )
                if {row[0] for row in cursor.fetchall()} - allowed:
                    raise ConfigError("Runtime database column grants are excessive.")


def admit_download_database(configuration, database):
    """The dedicated stream role cannot mutate application data or resize its cap."""
    from .accounts.credential_database import _identity, admit_grants
    from .runtime_database import require_no_temporary_authority

    _identity(login_name("download"), database=database)
    require_no_temporary_authority(database)
    tables, columns = runtime_grants("download")
    allowed = {table: set(grants) for table, grants in tables.items()}
    for table, grants in columns.items():
        allowed.setdefault(table, set()).update(grants)
    admit_grants(allowed, functions=runtime_functions("download"), database=database)
    admit_columns(database, tables, columns)
    budget = configuration.runtime_budget
    with database.cursor() as cursor:
        cursor.execute(
            "SELECT capacity FROM public.stewardship_download_policy WHERE id=1"
        )
        if cursor.fetchone() != (budget.download_capacity,):
            raise ConfigError("Download capacity requires offline reconciliation.")
        cursor.execute("SELECT rolconnlimit FROM pg_roles WHERE rolname=current_user")
        expected = (
            budget.replicas
            * budget.rollout_overlap
            * budget.web_processes
            * budget.download_pool_per_process
        )
        if cursor.fetchone() != (expected,):
            raise ConfigError("Download SQL connection limit differs from its budget.")

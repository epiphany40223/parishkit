"""Closed filters and display rows for the Administrator's combined log screen.

Pure shaping. The stored context was already reduced to reviewed, closed fields
when it was written; this module still shows only scalar values it recognizes,
so a future schema that stores something richer cannot leak through the page.
"""

import re
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID

from django.utils.datastructures import MultiValueDict
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.observability import Event
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.dates import UnknownZone, browser_day_start
from parishkit.stewardship.web.tables import PAGE_SIZES, Sorting, TablePage

from .log_contract import SERIOUS
from .log_descriptions import (
    ACTOR_KINDS,
    built_types,
    describe,
    field_label,
    field_value,
)
from .log_details import explain
from .schemas import FIELDS, Action

PAGE_SIZE = 50
# Only the Time column sorts. Both directions are served by the (created_at,
# id) index each source has (operational_created_id, audit_event_created_id),
# so a page reads index-ordered keys and merges them. Excluded columns:
# Level (audit entries have none, so it cannot order the union), Type (the
# audit log's audit_event_time index on (event_type, created_at) orders its
# types, but the operational log's event column is unindexed; its
# operational_level_time index orders by level. No index orders the union
# by type, so every view would sort every matching operational row), Actor
# (the name shown is an email looked up for display, not a stored value) and
# Related / Recorded detail (links and key-value lists, not scalar values).
LOG_SORTING = Sorting({"newest": ("time", True), "oldest": ("time", False)}, "newest")
# Fields that page through one snapshot. The filter form keeps only the
# rows-per-page and sort choices (``TablePage.view_fields``), never the
# snapshot or page, so applying filters starts a new snapshot at page 1.
PAGING = frozenset({"through", "page", "size", "sort"})
# Filters that may travel in a web address, so a filtered view can be
# bookmarked or linked (#536): the closed choices, the Activity group
# (#953), the type, the Ministry, the days with their zone, and the view's
# size and sort. Private filters
# stay in POST state, as the Admin tables rule requires: an address is kept
# in the web server's access log and the browser's history (the page writes
# its link there) and can travel on when pasted. So the identifiers (actor,
# correlation, campaign, subject), one of which ties a person to what they
# did, never do; nor does the search text, which can name a person (a
# review reason, a shown DUID), as the Find a Family search keeps its text
# out of URLs. Nor does the snapshot (``through``, ``page``), which belongs
# to one reading. Those travel only in CSRF POST bodies, as before (#519).
LINK_FIELDS = frozenset(
    {
        "activity",
        "applied",
        "debug",
        "info",
        "warning",
        "error",
        "critical",
        "audit",
        "event",
        "ministry",
        "start",
        "end",
        "zone",
        "size",
        "sort",
    }
)
# Search text is a short phrase. Text with an "@" (an address) is refused
# with a hint: no log value holds an address, so it could only find nothing.
TEXT_LIMIT = 64
# The only detail ever shown: fields some reviewed context schema names. A key
# that merely looks like an identifier is not enough, because a future flat text
# field or a trigger-written context would otherwise be rendered verbatim.
DETAIL_FIELDS = frozenset().union(*FIELDS.values())
DETAIL_LIMIT = 128
# Entries cannot predate the application, and a far-future day cannot be advanced
# to its exclusive upper bound without overflowing.
EARLIEST, LATEST = date(2020, 1, 1), date(2999, 12, 31)
# A word and an icon, never color alone, distinguish the five levels, listed
# least severe first; the order is the form's and the one source of LEVELS. The
# level choices show the word beside the icon; the table's Level column shows
# the icon with the word as screen-reader text plus a tooltip.
LEVEL_LABELS = {
    "DEBUG": ("·", _("Debug")),
    "INFO": ("i", _("Information")),
    "WARNING": ("!", _("Warning")),
    "ERROR": ("×", _("Error")),
    "CRITICAL": ("‼", _("Critical")),
}
LEVELS = tuple(LEVEL_LABELS)
# Audit records have no level. They are the sixth kind of entry the filter row
# offers (#601), with their own icon (a clipboard, distinct in shape from the
# five level icons), named for screen readers and as a tooltip.
AUDIT_LABEL = _("Audit record")
# The retired Source select's values (#601). A tab opened before it was
# removed may still send one, so for one release it is mapped onto the
# checkboxes (``LogQuery._from_source``) instead of refused.
LEGACY_SOURCES = frozenset({"both", "operational", "audit"})
# Suggestions only. Many audit types are written directly by their owners and by
# SQL triggers, such as `admin_login`, so the two vocabularies here are not the
# whole set; hiding the rest behind a fixed list would make them unsearchable.
EVENTS = tuple(sorted({item.value for item in Action} | {item.value for item in Event}))
# Fixed groups of types the Activity choice lists together (#953), so a
# reader never types internal type names one at a time. Each is a closed
# list kept only here (``pk-admin logs --activity`` and the page take its
# keys), and every member is a type something writes: an ``Action``, a
# directly written audit type, a trigger-built type from ``BUILT_TYPES`` or an
# operational entry on the writer manifest (test_log_rows guards it). Every
# written type is either in a group or in ``UNGROUPED`` below with its
# reason, and a test fails on a new one until it is placed.
#
# - Sign-in activity: every recorded Admin portal sign-in, re-confirmation
#   and session ending (including a session replaced because the person's
#   roles changed), plus refused sign-ins: a verified Google account policy
#   refused, named and bounded per account, and the sampled, anonymous
#   record of any other refusal (no actor, by design). Both are
#   ``accounts.auth_incidents``. An Administrator signing a session out on
#   Signed-in sessions is here too (``accounts.signed_in_sessions``).
# - Configuration changes: settings requests through Review and Apply to
#   applied, refused (``config_request_failed``) or cancelled, and what an
#   applied change did to access; mail schedules and live campaign changes;
#   portal users and their roles, asked for on the Users page or confirmed
#   from the command line, including ParishSoft Ministry leaders imported
#   as users (and the operator's one-time conversion of sign-in rules into
#   users); hosted files; stored key replacements (and each
#   process acknowledging the new key); and initial setup, from starting it
#   and entering its credentials, through its test email and Slack message,
#   to locking it or letting it expire.
# - Family mail: each outgoing email's steps (sent, retried, refused,
#   delivery unknown), the Administrator's resolutions, cleared refused
#   addresses, delivery pauses, test emails and the mail provider failing.
#   An email's own steps do not say whom it was for, so staff report and
#   alert emails are listed too; the page's help says so.
# - ParishSoft refreshes: full refreshes and quick updates accepted,
#   rejected, replaced or fallen back to a full reload, their failures and
#   holds, an Administrator accepting a refused large change once, and the
#   Ministry leader changes they cause (#1034). That one type
#   is also recorded when a settings change (leader roles or Ministries)
#   changes who leads; it stays here, with the refreshes
#   that cause most of them, because each type belongs to one group.
#   Timeouts are left out: every kind of background work shares their
#   types, so a type list cannot pick a refresh's (#953 comment).
# - Reports and downloads: requesting, making, cancelling or downloading a
#   report or export, by hand or from the command line. Opening a report
#   page is not a download and stays ungrouped.
# - Security events: access a settings change widened (also a
#   configuration change: the one type in two groups) and its
#   acknowledgement, automation sessions that let the command line act as
#   an Administrator, restoring Administrator access or sign-in limiting,
#   and the installation's own encryption keys.
# - Family portal activity: Families signing in, out and responding,
#   invalid links, preparing and replacing Family links, and closing the
#   Family portal for maintenance.
ACTIVITY_TYPES = {
    "sign_in": (
        "admin_login",
        "admin_login_denied",
        "admin_login_refused",
        "admin_logout",
        "admin_privileges_changed",
        "admin_reauthenticated",
        "admin_revoked",
        "admin_session_signed_out",
        "admin_step_up",
        "admin_timeout",
    ),
    "configuration": (
        "configuration_requested",
        *built_types("config_request_"),
        "configuration_activated",
        "policy_security_event",
        "policy_denial_namespace_reset",
        "schedule_selected",
        "admin_cmd_schedule_confirm",
        "admin_cmd_config_request_cancel",
        "campaign_ministries_requested",
        "campaign_end_date_requested",
        "roles_applied",
        "user_change_requested",
        "admin_cmd_users_confirm",
        "admin_cmd_parish_confirm",
        "admin_cmd_artwork_remove",
        "admin_cmd_integration_confirm",
        "admin_cmd_integration_backup_probe",
        "admin_cmd_integration_dismiss",
        "admin_cmd_integration_key_finish_switching",
        "admin_cmd_workgroup_confirm",
        "user_leaders_import_requested",
        "user_leader_imported",
        "admin_cmd_users_import_leaders",
        "operator_users_upgraded",
        "hosted_file_uploaded",
        "hosted_file_slug_changed",
        "hosted_file_deleted",
        "admin_cmd_files_delete",
        "admin_cmd_files_rename",
        # Past campaigns (#1018): a parish record, as hosted files are.
        "campaign_archive_previewed",
        "campaign_archive_imported",
        "campaign_archive_replaced",
        "campaign_archive_removed",
        "admin_cmd_history_preview",
        "admin_cmd_history_confirm",
        "admin_cmd_history_remove",
        "secret_replacement_requested",
        *built_types("secret_request_"),
        "credential_consumer_acknowledged",
        "setup_started",
        "setup_credential_staged",
        "setup_credential_scrubbed",
        *built_types("setup_mail_"),
        *built_types("setup_slack_"),
        "setup_frozen",
        "setup_expired",
    ),
    "family_mail": (
        *built_types("outbox_"),
        "delivery_unknown",
        *built_types("delivery_resolution_"),
        *built_types("delivery_held_"),
        "delivery_bulk_resolved",
        "restore_hold_resolved",
        "recipient_bounce_reported",
        "recipient_refusal_cleared",
        "admin_cmd_delivery_resolve",
        "admin_cmd_delivery_resend",
        "admin_cmd_delivery_refusal_clear",
        "admin_cmd_delivery_bounce_report",
        "submission_receipt_skipped",
        *built_types("campaign_", "pause", "resume", "first_delivery"),
        *built_types("family_mail_test_"),
        *built_types("campaign_mail_"),
        "admin_cmd_test_sample",
        "admin_cmd_test_families",
        "mail_provider_failed",
        "retry_refusal_unrecorded",
    ),
    "parishsoft": (
        "source_promoted",
        "source_rejected",
        "source_superseded",
        "source_fallback_requested",
        "ministry_leaders_changed",
        "chair_reconciled",
        "admin_cmd_refresh_start",
        "setup_source_started",
        "setup_source_completed",
        "source_refresh_invalid",
        "source_provider_failed",
        "source_credential_failed",
        "source_tenant_mismatch",
        "source_destructive_change",
        "source_refresh_held",
        # Accept this change once (ADM-13 PR 5b, #530) on System health.
        "source_drop_accepted",
        "admin_cmd_system_refresh_accept",
    ),
    "downloads": (
        *(
            "admin_cmd_export_" + verb
            for verb in (
                "archive",
                "cancel",
                "comparison",
                "create",
                "directory",
                "exact_cancel",
                "exact_create",
                "exact_retry",
                "family_test_names",
                "family_timeline",
                "financial",
                "information",
                "ministry",
                "ministry_packet",
                "postal",
                "regenerate",
                "retry",
            )
        ),
        "admin_cmd_digest_weekly_request",
        "weekly_manual_requested",
        "campaign_comparison_exported",
        "campaign_archive_exported",
        "export_requested",
        "export_cancelled",
        "export_downloaded",
        "census_changes_exported",
        "response_data_quality_list_exported",
        "response_not_opened_list_exported",
        "response_repeat_list_exported",
        "response_started_list_exported",
        "response_submitted_list_exported",
        "roster_changes_exported",
        "ministry_followup_exported",
        "talents_report_exported",
        "system_logs_exported",
    ),
    "security": (
        "policy_security_event",
        "security_event_acknowledged",
        "automation_session_approved",
        "automation_session_refused",
        "automation_session_ended",
        "automation_fresh_gate",
        "operator_admin_recovered",
        "limiter_recovered",
        "credential_key_activated",
        "credential_keys_retired",
        "credential_cipher_batch_migrated",
    ),
    "family_portal": (
        "family_login",
        "family_logout",
        "family_session_ended",
        "family_submission",
        *built_types("campaign_", "first_submission"),
        "family_link_invalid",
        "family_link_rotated",
        "family_link_population_extended",
        "family_tokens_preparing",
        "family_tokens_ready",
        "family_tokens_cancelled",
        "family_maintenance_started",
        "family_maintenance_ended",
        "admin_cmd_portal_maintenance_set",
    ),
}
# Written types no Activity group lists, under the reason each is left
# out. A reader still finds them with the type search. Together with the
# groups these are exactly the written types (test_log_rows), so a newly
# written type fails until it joins a group or is listed here. Not written
# to either log, so in neither: ``authentication_limits_weakened`` goes only
# to the process log at startup (``AuthenticationPolicy.warn_if_weaker``).
UNGROUPED_REASONS = {
    "Opening a page or report, or looking something up, reads; it changes nothing": (
        "background_viewed",
        "campaign_comparison_viewed",
        "census_changes_viewed",
        "daily_digest_viewed",
        "dashboard_viewed",
        "data_to_fix_viewed",
        "delivery_viewed",
        "family_codes_viewed",
        "family_directory_viewed",
        "family_presence_viewed",
        "family_timeline_viewed",
        "financial_report_viewed",
        "information_viewed",
        "ministry_followup_viewed",
        "ministry_report_viewed",
        "participation_viewed",
        "portal_users_viewed",
        "postal_outreach_viewed",
        "response_dashboard_viewed",
        "response_data_quality_list_viewed",
        "response_not_opened_list_viewed",
        "response_repeat_list_viewed",
        "response_started_list_viewed",
        "response_submitted_list_viewed",
        "roster_changes_viewed",
        "source_form_viewed",
        "system_health_viewed",
        "system_logs_viewed",
        "talents_report_viewed",
        "user_leaders_listed",
        "user_names_looked_up",
        "weekly_digest_viewed",
    ),
    "Staff follow-up on Family requests, not a setting": (
        "admin_cmd_census_resolve",
        "census_change_updated",
        "chair_review_decided",
        "information_updated",
        "ministry_request_updated",
        "ministry_followup_bulk_applied",
        "ministry_roster_entered",
    ),
    "Staff opening a Family's form through Open form, and the Family sign-in "
    "it starts (#529); not a portal sign-in": (
        "family_assisted_login",
        "family_form_opened",
    ),
    "The campaign's lifecycle and going live; no group covers them yet": (
        *built_types(
            "campaign_",
            "activate",
            "start",
            "close",
            "withdraw",
            "reopen",
            "archive",
            "unarchive",
        ),
        "campaign_boundary_completed",
        "campaign_boundary_skipped",
        *built_types("production_"),
        "production_cancel_requested",
        "runtime_transition",
        "rehearsal_gate_released",
        "rehearsal_invalidated",
        "catchup_failed",
    ),
    # Take a backup now (#530): no group is about backups yet; the request's
    # own progress is on System health's Backups panel.
    "Asking the server to take a backup now; System health follows it": (
        "backup_requested",
        "admin_cmd_system_backup_now",
    ),
    # Turn off debug logging (#530): no group is about the server's logging;
    # System health's Debug logging panel shows the switch.
    "Turning debug logging off, or allowing it again; System health shows the switch": (
        "debug_logging_turned_off",
        "admin_cmd_system_debug_off",
        "debug_logging_allowed",
        "admin_cmd_system_debug_allow",
    ),
    "Reviewing a restore from backup, an operator's recovery": (
        "restore_review_started",
        "restore_review_released",
    ),
    "Acknowledging a notice or confirming another, separately recorded action": (
        "critical_events_acknowledged",
        "automation_notices_acknowledged",
        # Either acknowledgement from the command line; the page's own
        # event, recorded with it, says which.
        "admin_cmd_events_acknowledge",
        "credential_result_dismissed",
        "destructive_confirmation",
    ),
    "One-time data upgrades": (
        "family_mac_backfilled",
        "family_mac_migration_verified",
        "family_engagement_backfilled",
        "family_engagement_failed",
    ),
    "Retention and verification housekeeping, not a ParishSoft refresh": (
        "facts_compacted",
        "facts_verified",
        "source_compacted",
        "source_retention_skipped",
    ),
    # Its record names only the automation session, never which task it
    # retried (a refresh, a preparation or an export clean-up), so no group
    # can claim it.
    "Retries any failed background task without saying which": (
        "admin_cmd_task_retry",
    ),
    "Shared by every kind of background work; a type cannot say which": (
        "task_failed",
        "task_timed_out",
        "task_lease_lost",
        *built_types("task_"),
        "helper_timed_out",
        "work_budget_reached",
        "due_work_lag",
        "campaign_boundary_lag",
    ),
    "Service health and command-line failures, shown on System health": (
        "admin_command_failed",
        "configuration_digest_mismatch",
        "fact_drift",
        "incident_recovered",
        "web_unhealthy",
    ),
    "Found while using already-loaded ParishSoft data, not in a refresh": (
        "source_member_unusable",
    ),
    # Publication plans (#528, DAT-09.01): no page shows them yet (ADM-09).
    "Publishing census changes to ParishSoft; no page shows it yet": (
        "census_publication_planned",
        "census_publication_confirmed",
        "census_publication_recorded",
    ),
    "In the Action vocabulary, but nothing records it today": (
        "privileged_reauthentication",
    ),
}
UNGROUPED = frozenset().union(*UNGROUPED_REASONS.values())
# The groups that hold operational entries as well as audit records. The
# others are audit records only: while one is chosen the page greys the five
# level boxes and waits for Audit record, and the read leaves operational
# entries out.
OPERATIONAL_ACTIVITIES = frozenset(
    key
    for key, types in ACTIVITY_TYPES.items()
    if set(types) & {item.value for item in Event}
)
# The Activity choice's options, in the order the filter bar lists them.
ACTIVITY_LABELS = (
    ("", _("All activity")),
    ("sign_in", _("Sign-in activity")),
    ("configuration", _("Configuration changes")),
    ("family_mail", _("Family mail")),
    ("parishsoft", _("ParishSoft refreshes")),
    ("downloads", _("Reports and downloads")),
    ("security", _("Security events")),
    ("family_portal", _("Family portal activity")),
)
# A type is searched as an exact identifier, never as free text: the same shape
# the audit table's own check constraint enforces on every stored type.
EVENT = re.compile(r"[a-z][a-z0-9_]{0,63}")
INSTANT = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}\+00:00"
)


class NothingShown(ValueError):
    """A submitted filter form ticked none of the six kinds of entry (#601).

    The view words this refusal on its own: no value the reader typed was
    wrong, they only left every Show choice unticked.
    """


def _ministry(value):
    """Accept only a canonical positive Ministry DUID, as entries store it."""
    if value and not (
        value.isascii() and value.isdecimal() and value[0] != "0" and int(value) < 2**31
    ):
        raise ValueError("Invalid Ministry DUID.")
    return value


def _text(value):
    """Accept short printable search text without an address; trim spaces."""
    value = value.strip()
    if len(value) > TEXT_LIMIT or "@" in value or not value.isprintable():
        raise ValueError("Invalid log search text.")
    return value


def _identifier(value):
    """Accept only a canonical lowercase UUID, or nothing."""
    if value and str(UUID(value)) != value:
        raise ValueError("Identifiers must be canonical.")
    return value


@dataclass(frozen=True, repr=False)
class LogQuery:
    """Closed log filters; identifiers never reach a URL.

    The page's forms send every filter in CSRF POST bodies. A GET may carry
    only ``LINK_FIELDS`` (#536), so a filtered view can be bookmarked or
    linked; ``link_values`` builds that link.

    The five operational levels and audit records (`audit`) are six
    checkboxes (#601). The first visit's default, before `applied` marks a
    submitted form, is every level but DEBUG plus audit records; a submitted
    form shows exactly what it ticks and must tick at least one. An older
    tab's `source` choice is mapped onto the ticks (``_from_source``).
    The log grows while it is being read, so paging is anchored to a snapshot:
    the first view records `through`, the database time it read at, and every
    later page, sort or size change carries it and lists only entries created
    at or before it. New entries therefore cannot shift an offset page; they
    appear once the filters are applied again. ``created_at`` is the insert
    statement's start time, so an entry whose transaction began before the
    snapshot but committed after it can still appear on a later page view,
    shifting that page by one entry; such overlaps are rare and brief.

    From (`start`) and Through (`end`) are calendar days in the viewer's
    browser time zone (#558), which the page script sends as `zone` and every
    navigator, heading and export form carries on with the other filters.
    """

    activity: str = ""
    applied: str = ""
    debug: str = ""
    info: str = ""
    warning: str = ""
    error: str = ""
    critical: str = ""
    audit: str = ""
    source: str = ""
    event: str = ""
    actor: str = ""
    correlation: str = ""
    campaign: str = ""
    subject: str = ""
    text: str = ""
    ministry: str = ""
    start: str = ""
    end: str = ""
    zone: str = ""
    through: str = ""
    page: str = ""
    size: str = ""
    sort: str = ""

    @classmethod
    def parse(cls, parameters):
        """Accept only single bounded values from the closed vocabularies."""
        if type(parameters) is dict:
            if any(type(value) is not str for value in parameters.values()):
                raise ValueError("Log filters require text values.")
            parameters = MultiValueDict(
                {key: [value] for key, value in parameters.items()}
            )
        query = cls(**filters(parameters, allowed=set(cls.__dataclass_fields__)))
        ticks = (
            query.debug,
            query.info,
            query.warning,
            query.error,
            query.critical,
            query.audit,
        )
        if (
            query.applied not in {"", "yes"}
            or any(tick not in {"", "yes"} for tick in ticks)
            # Ticks mean something only on a submitted form.
            or (any(ticks) and not query.applied)
            # A legacy source never comes with the audit tick that replaced it.
            or (query.source and (query.source not in LEGACY_SOURCES or query.audit))
            or (query.event and EVENT.fullmatch(query.event) is None)
            or (query.activity and query.activity not in ACTIVITY_TYPES)
        ):
            raise ValueError("Invalid log filters.")
        if query.source:
            query = query._from_source()
        if query.applied and not (query.levels or query.audit):
            # The page's gate keeps Apply unavailable with nothing ticked;
            # a form that bypasses it is refused, not answered with nothing.
            raise NothingShown("Log filters must show at least one kind of entry.")
        for value in (query.actor, query.correlation, query.campaign, query.subject):
            _identifier(value)
        _ministry(query.ministry)
        query = replace(query, text=_text(query.text))
        for value in (query.start, query.end):
            if not value:
                continue
            day = date.fromisoformat(value)
            if day.isoformat() != value or not EARLIEST <= day <= LATEST:
                raise ValueError("Invalid log date filter.")
        if query.start and query.end and query.start > query.end:
            raise ValueError("Invalid log date interval.")
        if query.zone not in timezone_names():
            # Days cannot be placed without the browser's zone (a blank one
            # from a tab opened before #558, or a zone outside the catalog).
            if query.start or query.end:
                raise UnknownZone("Log dates need the browser's time zone.")
            # Without days the zone is unused; drop one the catalog does not
            # know rather than refuse filters that never needed it.
            query = replace(query, zone="")
        if query.through:
            if INSTANT.fullmatch(query.through) is None:
                raise ValueError("Invalid log snapshot.")
            datetime.fromisoformat(query.through)
        if query.page and (
            not (query.page.isascii() and query.page.isdecimal()) or len(query.page) > 9
        ):
            raise ValueError("Invalid log page.")
        if query.size and query.size not in {str(size) for size in PAGE_SIZES}:
            raise ValueError("Invalid log page size.")
        if query.sort:
            LOG_SORTING.parse({"sort": query.sort})
        return query

    @property
    def levels(self):
        """Every level but DEBUG by default; exactly the ticked ones otherwise."""
        if not self.applied:
            return LEVELS[1:]
        return tuple(level for level in LEVELS if getattr(self, level.lower()))

    @property
    def audits(self):
        """Whether audit records are shown: by default, or when ticked."""
        return not self.applied or bool(self.audit)

    def _from_source(self):
        """Map a retired Source choice (#601) onto the six checkboxes.

        The levels it came with (or the default ones, on a form that was not
        applied, such as the old "Same campaign" action) are kept, except that
        "audit" (audit only) clears them; "operational" leaves audit records
        out. The result is an ordinary applied query, so paging and the export
        carry the checkboxes, never ``source`` again.
        """
        levels = () if self.source == "audit" else self.levels
        return replace(
            self,
            applied="yes",
            source="",
            audit="" if self.source == "operational" else "yes",
            **{level.lower(): "yes" if level in levels else "" for level in LEVELS},
        )

    @property
    def activity_types(self):
        """The types the Activity choice lists, or None for all activity."""
        return ACTIVITY_TYPES.get(self.activity)

    @property
    def activity_audit_only(self):
        """Whether the chosen Activity group is audit records only (#953)."""
        return bool(self.activity) and self.activity not in OPERATIONAL_ACTIVITIES

    @property
    def bounds(self):
        """The From and Through days as a UTC interval ``[lower, upper)``.

        From starts at local midnight of its day in the browser's zone;
        Through ends where the next local day starts, so a daylight-saving day
        is 23 or 25 hours long. Either bound is None when its day is unset.
        """
        start, end = (
            date.fromisoformat(value) if value else None
            for value in (self.start, self.end)
        )
        return (
            browser_day_start(start, self.zone) if start else None,
            browser_day_start(end + timedelta(days=1), self.zone) if end else None,
        )

    @property
    def snapshot(self):
        """The instant this reading is anchored to, or None for a fresh one."""
        return datetime.fromisoformat(self.through) if self.through else None

    @property
    def page_number(self):
        """The requested 1-based page; the view clamps it to what exists."""
        return max(1, int(self.page)) if self.page else 1

    @property
    def page_size(self):
        """Entries per page."""
        return int(self.size) if self.size else PAGE_SIZE

    @property
    def order(self):
        """The validated sort token."""
        return self.sort or LOG_SORTING.default

    @property
    def oldest(self):
        """Whether the page lists the oldest entries first."""
        return not LOG_SORTING.tokens[self.order][1]

    @property
    def private(self):
        """Whether an identifier filter is applied."""
        return any((self.actor, self.correlation, self.campaign, self.subject))

    @property
    def unlinked(self):
        """Whether an applied filter is one a link leaves out (search or an
        identifier), so the page says the link does not carry it."""
        return self.private or bool(self.text)

    def link_values(self):
        """The applied filters a web address may carry (``LINK_FIELDS``).

        The zone goes with the days, which mean nothing without it, and is
        left out otherwise.
        """
        values = {
            key: getattr(self, key)
            for key in self.__dataclass_fields__
            if key in LINK_FIELDS and getattr(self, key)
        }
        if not (self.start or self.end):
            values.pop("zone", None)
        return values

    def form_values(self):
        """Filter fields to carry into other pages, without paging or sort."""
        return {
            key: getattr(self, key)
            for key in self.__dataclass_fields__
            if key not in PAGING and getattr(self, key)
        }


def _details(context):
    """Only reviewed fields with short scalar values; nothing else is rendered."""
    if type(context) is not dict:
        return []
    # Filter before sorting, so an unexpected key can never break the page.
    known = {
        key: value
        for key, value in context.items()
        if type(key) is str and key in DETAIL_FIELDS
    }
    shown = []
    for key, value in sorted(known.items()):
        if value is None or type(value) in (int, bool):
            shown.append((key, "" if value is None else str(value)))
        elif type(value) is str and len(value) <= DETAIL_LIMIT:
            shown.append((key, value))
        elif type(value) is list and all(type(item) is int for item in value):
            shown.append((key, ", ".join(str(item) for item in value)))
    return shown


def detail_labels(details):
    """The recorded detail with each field named in words, for display only.

    Exports keep the stored field names, which are what a filter or script
    matches; the page shows "Lag microseconds" instead of "lag_microseconds",
    and a report's recorded choices in its own menu's words (#556).
    """
    return [(field_label(key), field_value(key, value)) for key, value in details]


def log_table(query, rows, *, through, action, number=1, total=None, capped=False):
    """Describe one page of the log for the shared POST navigator.

    ``total`` defaults to the rows given (a fixture's single page);
    ``capped`` marks a bounded total, so the navigator says "more than" and
    pages on while ``has_next`` allows. The snapshot instant travels with the
    filters on every navigator and heading form, never in a URL.
    """
    size = query.page_size
    total = len(rows) if total is None else total
    return TablePage(
        rows=list(rows),
        number=number,
        pages=max(1, -(-total // size)),
        count=total,
        size=size,
        prefix="",
        carried=(
            *query.form_values().items(),
            ("through", through.astimezone(UTC).isoformat(timespec="microseconds")),
        ),
        has_next=number * size < total,
        allow_all=False,
        sorting=LOG_SORTING,
        sort=query.order,
        method="post",
        action=action,
        capped=capped,
    )


def page_context(query, table, *, depth_limited=False, linked=False, ministries=()):
    """The one template context, shared by the view and its browser fixtures.

    ``table`` is the page's ``web.tables.TablePage``; ``depth_limited`` says a
    requested page lay past the paging depth and the last reachable one is
    shown instead. ``linked`` says the filters came from a web address (a
    bookmark or link, #536).
    ``link`` is this view's own address: the page's address plus the
    filters a link may carry. ``link_zone`` names the zone a followed link's
    days are in; the page notes it when it is not the browser's own.
    ``ministries`` is the Ministry filter's choices, ``(DUID, name)`` pairs
    in name order: entries record a Ministry's DUID, but staff pick it by
    name (#1123).
    """
    values = query.link_values()
    listed = {str(duid) for duid, _name in ministries}
    return {
        "link": table.action + (f"?{urlencode(values)}" if values else ""),
        "link_zone": values.get("zone", "") if linked else "",
        "rows": table.rows,
        "table": table,
        "depth_limited": depth_limited,
        "query": query,
        "query_fields": query.form_values(),
        # The six kinds of entry, in the filter row's order: the five levels,
        # least severe first, then audit records.
        "levels": [
            *(
                (level.lower(), label, level in query.levels)
                for level, (_symbol, label) in LEVEL_LABELS.items()
            ),
            ("audit", AUDIT_LABEL, query.audits),
        ],
        "events": EVENTS,
        "activities": ACTIVITY_LABELS,
        "ministries": ministries,
        # A filtered Ministry the current ParishSoft data no longer has (an
        # old link or bookmark) stays chosen under a plain label, never its
        # DUID, so applying the filters again keeps it.
        "ministry_unlisted": bool(query.ministry) and query.ministry not in listed,
        # The conditional-field rules for the Show boxes (#953): the levels
        # are enabled for all activity and the groups with operational
        # entries, and Audit record is required for the audit-only groups.
        "levels_rule": "activity=" + "|".join(["", *sorted(OPERATIONAL_ACTIVITIES)]),
        "audit_rule": "activity="
        + "|".join(
            sorted(key for key in ACTIVITY_TYPES if key not in OPERATIONAL_ACTIVITIES)
        ),
    }


def _summary(record, details):
    """The entry's sentence (#633), or a plain note when a serious entry has none.

    A WARNING-or-above entry whose context neither explains itself nor lists
    any field (one written before #633, or whose detail was refused) says so
    rather than leaving its detail blank.
    """
    text = explain(record.get("schema"), record["context"])
    if text is None and not details and record["level"] in SERIOUS:
        return _("This entry was recorded without detail.")
    return text


def operational_row(record):
    """One diagnostic entry from its stored closed event, level and context."""
    symbol, label = LEVEL_LABELS[record["level"]]
    return {
        "id": record["id"],
        "created_at": record["created_at"],
        "source": _("Operational"),
        # The stored name of the source, which the command line prints.
        "source_kind": "operational",
        "level": record["level"],
        "level_symbol": symbol,
        "level_label": label,
        "icon": record["level"].lower(),
        "event": record["event"],
        "description": describe(record["event"], record["context"]),
        # What happened this time, in words, from the stored context (#633).
        "summary": _summary(record, _details(record["context"])),
        "actor_id": record["actor_id"],
        "correlation_id": record["correlation_id"],
        "campaign_id": None,
        "subject_id": None,
        "details": _details(record["context"]),
        "detail_rows": detail_labels(_details(record["context"])),
        "actor_kind": None,
        "actor_kind_label": None,
    }


def audit_row(record):
    """One audit entry. Audit records carry no severity level of their own."""
    return {
        "id": record["id"],
        "created_at": record["created_at"],
        "source": _("Audit"),
        "source_kind": "audit",
        "level": None,
        "level_symbol": "",
        "level_label": AUDIT_LABEL,
        "icon": "audit",
        "event": record["event_type"],
        "description": describe(record["event_type"]),
        "summary": None,
        "actor_id": record["actor_id"],
        "correlation_id": record["correlation_id"],
        "campaign_id": record["campaign_reference"],
        "subject_id": record["subject_id"],
        "details": _details(record["auditcontext__context"]),
        "detail_rows": detail_labels(_details(record["auditcontext__context"])),
        # Audit contexts say what kind of actor acted; the page names portal
        # users and workers itself and uses the label for Families, the
        # system and the operator, which have no portal account to look up.
        # The stored kind is what the command line's ``logs list`` prints.
        "actor_kind": record.get("auditcontext__actor_kind"),
        "actor_kind_label": ACTOR_KINDS.get(record.get("auditcontext__actor_kind")),
    }


def task_subject(row):
    """Whether the entry's subject is a background task with its own page.

    Task entries name the task as their subject, and so does a view of one
    task's page; other subjects (sessions, link generations and the like)
    have no Admin page to open.
    """
    return bool(
        row["subject_id"]
        and (
            row["event"].startswith("task_")
            or row["event"] == Action.BACKGROUND_VIEWED.value
        )
    )


def merge(operational, audit, *, oldest=False):
    """Order both sources' rows as one log: by time, then identifier.

    Each source supplies its first rows in the same order its own query uses
    ((created_at, id), in the chosen direction), so the first N of the merge
    are exactly the first N of the union.
    """
    return sorted(
        [*operational, *audit],
        key=lambda row: (row["created_at"], row["id"]),
        reverse=not oldest,
    )

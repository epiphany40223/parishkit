"""The route-parity ledger of the Admin automation command line (ADM-11).

Every ``admin:`` URL name maps to the ``pk-stewardship admin`` command that
does the same thing, or to an exemption (see the specification's "Action
inventory" and "Rules for new Admin actions"):

- ``command``: the commands that cover the route, all in the catalog now;
  ``owed`` names the pull request that adds the rest of a partly covered
  route (``go_live`` reads today, its preview and confirmation come in PR 12);
- ``pending``: the pull request that will add its commands, which it names;
- ``permanent``: web-only by nature, with the reason;
- ``deferred``: waiting on a decision, which it names.

``tests/stewardship/test_admin_route_parity.py`` fails when an ``admin:`` URL
name has no entry, an entry names a route that does not exist, or a
``command`` entry names a command missing from the catalog. A new Admin page
or action lands with its command, or with an exemption here.
"""

from typing import NamedTuple

from parishkit.stewardship.admin_urls.legacy import TARGETS as LEGACY_TARGETS


class Parity(NamedTuple):
    """One route's command line counterpart, or why there is none yet."""

    kind: str
    # For "command" and "pending": the command names. For "permanent" and
    # "deferred": the reason or the decision awaited.
    detail: tuple
    # For "pending": the ADM-11 pull request that removes the exemption.
    pr: str | None = None
    # For a partly covered route: the ADM-11 pull request owing the rest.
    owed: str | None = None


def command(*names, owed=None):
    """Routes the named commands cover today, and what is still owed."""
    return Parity("command", names, owed=owed)


def pending(pr, *names, owed=None):
    """Routes whose commands a later ADM-11 pull request adds."""
    return Parity("pending", names, pr, owed)


NAME_SEARCH = (
    "a search by name; the command line takes no text that names a Family "
    "(it would stay in shell history), and the directory export is the file"
)


def permanent(reason):
    """Routes that stay web-only, with the reason."""
    return Parity("permanent", (reason,))


SESSION_CHROME = (
    "browser sign-in and session chrome; the session commands cover automation"
)
SETUP = (
    "the first-Admin setup wizard is a one-time browser-led flow; "
    "pk-stewardship bootstrap is its offline counterpart"
)
IMAGES = "image bytes for the browser; the data is in the matching read"
UPLOAD = "file uploads; a later pull request may accept --file"
LEGACY = (
    "an old address that only redirects to its new page (#525); the "
    "new page's entry covers it"
)

GROUP_ROOT = (
    "a menu group's root URL, which only redirects to the group's first "
    "open entry; that entry covers it"
)

LEDGER = {
    # Status and session routes.
    "index": command("status"),
    "background_counts": command("status"),
    # Folded into status as its presence count (counts only): a word cannot
    # be both a command and an area. Default, pending Administrator
    # confirmation.
    "presence": command("status"),
    "presence_count": command("status"),
    "login": permanent(SESSION_CHROME),
    "logout": permanent(SESSION_CHROME),
    "session_status": permanent(SESSION_CHROME),
    "session_renew": permanent(SESSION_CHROME),
    "maintenance": permanent("the status page the access gate shows"),
    # The human side of the automation interface (the specification's action
    # inventory): approving, listing and revoking sessions, and acknowledging
    # their notices, stay in the browser. ``sessions`` lists one's own
    # sessions with the page's filter and sort (``--include-ended`` and
    # ``--sort``, #621), and ``status`` counts the unacknowledged notices.
    "automation_approval": permanent(
        "the human side of the automation interface: approving a session"
    ),
    "automation_access": permanent(
        "the human side of the automation interface: listing every session"
    ),
    "automation_session": permanent(
        "the human side of the automation interface: revoking a session"
    ),
    "automation_notices": permanent(
        "the human side of the automation interface: acknowledging notices"
    ),
    # Source refresh.
    "source_refresh": command("refresh start", "refresh status"),
    "background_task_page": command("task show"),
    "background_task_status": command("task show"),
    # Schedules and configuration.
    "schedule_settings": command(
        "schedule show", "schedule preview", "schedule confirm"
    ),
    "configuration_request": command("config request show"),
    "campaign_settings": pending(
        "PR 10", "campaign show", "campaign preview", "campaign confirm"
    ),
    "campaign_new": pending("PR 10", "campaign preview", "campaign confirm"),
    "campaign_clone": pending("PR 10", "campaign clone"),
    "campaign_ministries": pending("PR 10", "campaign ministries"),
    "share_settings": pending("PR 10", "campaign shares"),
    "talent_settings": pending("PR 10", "campaign talents"),
    "reminder_workgroup": pending("PR 10", "campaign reminder-workgroup"),
    "content_catalog": pending("PR 10", "content list"),
    "content_edit": pending("PR 10", "content show", "content preview"),
    "content_revision": pending("PR 10", "content confirm"),
    "content_history": pending("PR 10", "content history"),
    "content_history_revision": pending("PR 10", "content history"),
    "content_plain_text": pending("PR 10", "content show"),
    "parish_settings": pending("PR 10", "parish"),
    "ministries": pending("PR 10", "ministries"),
    "hosted_files": pending("PR 10", "files list"),
    "hosted_file_delete": pending("PR 10", "files delete"),
    "hosted_file_rename": pending("PR 10", "files rename"),
    "artwork_settings": pending("PR 10", "artwork show"),
    "artwork_remove": pending("PR 10", "artwork remove"),
    "branding_settings": pending("PR 10", "branding show", "branding confirm"),
    "hosted_file_upload": permanent(UPLOAD),
    "artwork_upload": permanent(UPLOAD),
    "artwork_preview": permanent(IMAGES),
    "branding_preview": permanent(IMAGES),
    "branding_asset": permanent(IMAGES),
    # Production transition and withdrawal.
    # The verified preview with the DNS check and cleanup come in PR 12.
    "go_live": command("go-live readiness", owed="PR 12"),
    "go_live_families": pending("PR 12", "go-live families"),
    "go_live_cleanup": pending(
        "PR 12", "go-live cleanup-status", "go-live cleanup-retry"
    ),
    "go_live_links": pending("PR 12", "go-live links", "go-live links-status"),
    "production_confirmation": pending(
        "PR 12", "go-live confirm-preview", "go-live confirm"
    ),
    # The page's "Retry failed mail preparation" control comes in PR 12.
    "production_progress": command("go-live progress", owed="PR 12"),
    "production_withdrawal": pending(
        "PR 12", "go-live withdraw-preview", "go-live withdraw"
    ),
    # Family email history and Pause and resume mail.
    "family_email_progress": command("send progress"),
    "family_email_progress_status": command("send progress"),
    "family_email_sends": command("send history"),
    "delivery_control": pending(
        "PR 7", "send pause-preview", "send resume-preview", "send confirm"
    ),
    "family_portal": pending(
        "PR 7", "portal maintenance show", "portal maintenance set"
    ),
    # Testing sends.
    "campaign_mail": command("test sample-preview", "test sample"),
    # The preview's --names export waits for PR 8b's export lifecycle.
    "campaign_mail_families": command(
        "test families-preview",
        "test families",
        "test status",
        owed="PR 8b",
    ),
    # Reports and exports. PR 8 lands in parts: 8a the logs, 8b the export
    # lifecycle and its fetch, 8c the aggregate report reads, 8d the
    # digests, 8e the Family-level exports that create an export record
    # without the Family keys, 8f the Family directory's exports (the code
    # MAC keyring loaded only for them), and 8g the rest: the one-Family
    # timeline (a new export kind, a schema change), the in-memory downloads
    # (talents, response lists; after #752's audit fields) and the exact
    # daily exports.
    # The aggregate report reads (PR 8c): counts and summaries only; the
    # rows behind them are the Family-level exports (PR 8e and 8f).
    "reports": command("report list"),
    "participation": command("report participation"),
    "participation_chart": permanent(IMAGES),
    "daily_digest_chart": permanent(IMAGES),
    "daily_digest_download": permanent(IMAGES),
    "financial_report": command("report financial"),
    "talents_report": command("report talents"),
    "information_queue": command("report information"),
    # One Family's submission and its follow-up history: read with the
    # follow-up commands, as the Ministry follow-up items are, which print
    # only the item's state and history; its content is Family-level and
    # comes through ``export information``.
    "information_item": pending("PR 11", "followup"),
    "ministry_report": command("report ministry"),
    # The join and leave lists: ``report ministry --requests`` counts them.
    "ministry_joiners": command("report ministry"),
    "ministry_leavers": command("report ministry"),
    # A packet of the chosen Ministries' Members and contacts: an export.
    "ministry_packet": command("export ministry-packet"),
    "response_dashboard": command("report responses"),
    "response_list": pending("PR 8g", "export responses"),
    "response_list_export": pending("PR 8g", "export responses"),
    "family_directory": command("export directory", "export postal"),
    "family_timeline": pending("PR 8g", "export family-timeline"),
    # The header's Find a Family box (#561) runs the directory's search.
    "find_family": permanent(NAME_SEARCH),
    "postal_directory": command("export postal"),
    # Creating a financial export is fresh-gated (#547): the command calls
    # the caller-aware require_fresh, as the page's view does.
    "financial_export": command("export financial"),
    "talents_export": pending("PR 8g", "export talents"),
    "ministry_export": command("export ministry"),
    "information_export": command("export information"),
    # Both directory exports are fresh-gated (#547), as above.
    "family_directory_export": command("export directory", "export postal"),
    "postal_directory_export": command("export postal"),
    # The export lifecycle (PR 8b): the status page and its buttons, the
    # Participation page's export form, and the JSON routes behind them.
    # The download's grant is issued and consumed inside ``export download``.
    "report_export_create": command("export create"),
    "report_export": command("export status"),
    "report_export_cancel": command("export cancel"),
    "report_export_retry": command("export retry"),
    "report_export_regenerate": command("export regenerate"),
    "report_export_download": command("export download"),
    "export_create": command("export create"),
    "export_status": command("export status"),
    "export_cancel": command("export cancel"),
    "export_download": command("export download"),
    "export_download_grant": command("export download"),
    "report_exact_create": pending("PR 8g", "export exact"),
    "report_exact": pending("PR 8g", "export exact"),
    "report_exact_cancel": pending("PR 8g", "export exact"),
    "report_exact_retry": pending("PR 8g", "export exact"),
    "exact_export_create": pending("PR 8g", "export exact"),
    "exact_export_status": pending("PR 8g", "export exact"),
    "exact_export_cancel": pending("PR 8g", "export exact"),
    "exact_export_retry": pending("PR 8g", "export exact"),
    # The digests (PR 8d): the retained reports an emailed digest links to,
    # and the manual weekly report.
    "daily_digest_snapshot": command("digest daily"),
    "weekly_digest_snapshot": command("digest weekly"),
    # One item of a weekly report: its states are in ``digest weekly``'s
    # items; the Family's text is Family-level and stays on the page.
    "weekly_digest_item": command("digest weekly"),
    "weekly_digest_manual": command("digest weekly-request"),
    "logs": command("logs list"),
    "logs_export": command("logs export"),
    # System health (ADM-13): the page, its polled fragment (``--watch``)
    # and /admin/system/, which only redirects to the page.
    "system": command("system health"),
    "campaign_root": permanent(GROUP_ROOT),
    "mail_root": permanent(GROUP_ROOT),
    "parish_root": permanent(GROUP_ROOT),
    "system_health": command("system health"),
    "system_health_status": command("system health"),
    # Operations.
    "background": command("task list"),
    "background_tasks": command("task list"),
    "background_task": command("task show"),
    "retry_family_preparation": command("task retry"),
    "retry_daily_digest": command("task retry"),
    "retry_weekly_digest": command("task retry"),
    "retry_export_cleanup": command("task retry"),
    "deliveries": command("delivery list"),
    "delivery": command("delivery show"),
    # Every retry, a Family email's too (#682). The duplicate-risk resend is
    # its own command because it asks for the page's acknowledgement at the
    # prompt (PR 9c); delivery resolve, which never prompts, does the rest.
    "delivery_resolve": command("delivery resolve", "delivery resend"),
    "delivery_refusals": command("delivery refusals"),
    "delivery_refusal": command("delivery refusal-show"),
    # Asks for the page's verification acknowledgement at the prompt.
    "delivery_refusal_clear": command("delivery refusal-clear"),
    # Users and follow-up.
    "users": pending("PR 11", "users list"),
    "user_rules": pending("PR 11", "rules show"),
    "rule_apply": pending("PR 11", "rules apply"),
    "rule_base": pending("PR 11", "rules show"),
    "rule_request": pending("PR 11", "rules request show"),
    "chair_confirmations": pending("PR 11", "chairs"),
    "chair_reviews": pending("PR 11", "chairs"),
    "assignments": pending("PR 11", "assignments"),
    "security_event_acknowledge": pending("PR 11", "events acknowledge"),
    "critical_events_acknowledge": pending("PR 11", "events acknowledge"),
    "information_update": pending("PR 11", "followup"),
    "ministry_followup": pending("PR 11", "followup"),
    "ministry_followup_item": pending("PR 11", "followup"),
    "ministry_followup_update": pending("PR 11", "followup"),
    # Integrations and credentials.
    "integrations": pending("PR 10", "integration show"),
    "integration_settings": pending(
        "PR 10", "integration show", "integration set", "integration key replace"
    ),
    "integration_status": pending("PR 10", "integration status"),
    "refresh_schedule_check": pending("PR 10", "integration schedule-preview"),
    "credential_status": pending("PR 10", "integration key status"),
    "dismiss_credential_result": pending("PR 10", "integration dismiss"),
    "select_credential": pending("PR 10", "integration key finish-switching"),
    # The first-Admin setup wizard.
    **{
        name: permanent(SETUP)
        for name in (
            "setup",
            "setup_cancel",
            "setup_source",
            "setup_source_progress",
            "setup_branding",
            "setup_branding_asset",
            "setup_credential",
            "setup_campaign",
            "setup_shares",
            "setup_preview",
            "setup_confirmation",
            "setup_mail",
            "setup_mail_status",
            "setup_notification",
            "setup_notification_status",
            "setup_schedules",
            "setup_content",
            "setup_content_edit",
            "setup_step",
        )
    },
    # Old Admin addresses (the URL scheme, #525): redirects, not actions.
    **{name: permanent(LEGACY) for name in LEGACY_TARGETS},
}

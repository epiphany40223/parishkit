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

LEDGER = {
    # Status and session routes.
    "index": command("status"),
    "background_counts": command("status"),
    # Folded into status as its presence count (counts only): a word cannot
    # be both a command and an area. Default, pending Administrator
    # confirmation.
    "presence": command("status"),
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
    "source_refresh": pending("PR 6", "refresh start", "refresh status"),
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
    "campaign_mail": pending("PR 6", "test sample"),
    "campaign_mail_families": pending(
        "PR 6", "test families-preview", "test families", "test status"
    ),
    # Reports and exports.
    "reports": pending("PR 8", "report list"),
    "report_campaigns": pending("PR 8", "report list"),
    "participation": pending("PR 8", "report participation"),
    "participation_chart": permanent(IMAGES),
    "daily_digest_chart": permanent(IMAGES),
    "daily_digest_download": permanent(IMAGES),
    "financial_report": pending("PR 8", "report financial"),
    "talents_report": pending("PR 8", "report talents"),
    "information_queue": pending("PR 8", "report information"),
    "information_item": pending("PR 8", "report information"),
    "ministry_reports": pending("PR 8", "report ministry"),
    "ministry_report_campaigns": pending("PR 8", "report ministry"),
    "ministry_report": pending("PR 8", "report ministry"),
    "ministry_joiners": pending("PR 8", "report ministry"),
    "ministry_leavers": pending("PR 8", "report ministry"),
    "ministry_packet": pending("PR 8", "report ministry"),
    "response_dashboard": pending("PR 8", "report responses"),
    "response_list": pending("PR 8", "export responses"),
    "response_list_export": pending("PR 8", "export responses"),
    "family_directory": pending("PR 8", "export directory"),
    "family_timeline": pending("PR 8", "report family-timeline"),
    "postal_directory": pending("PR 8", "export postal"),
    "family_codes": pending("PR 8", "export family-codes"),
    "financial_export": pending("PR 8", "export financial"),
    "talents_export": pending("PR 8", "export talents"),
    "ministry_export": pending("PR 8", "export ministry"),
    "information_export": pending("PR 8", "export information"),
    "family_directory_export": pending("PR 8", "export directory"),
    "postal_directory_export": pending("PR 8", "export postal"),
    "report_export_create": pending("PR 8", "export create"),
    "report_export": pending("PR 8", "export status"),
    "report_export_cancel": pending("PR 8", "export cancel"),
    "report_export_retry": pending("PR 8", "export retry"),
    "report_export_regenerate": pending("PR 8", "export regenerate"),
    "report_export_download": pending("PR 8", "export download"),
    "export_create": pending("PR 8", "export create"),
    "export_status": pending("PR 8", "export status"),
    "export_cancel": pending("PR 8", "export cancel"),
    "export_download": pending("PR 8", "export download"),
    "export_download_grant": pending("PR 8", "export download"),
    "report_exact_create": pending("PR 8", "export exact"),
    "report_exact": pending("PR 8", "export exact"),
    "report_exact_cancel": pending("PR 8", "export exact"),
    "report_exact_retry": pending("PR 8", "export exact"),
    "exact_export_create": pending("PR 8", "export exact"),
    "exact_export_status": pending("PR 8", "export exact"),
    "exact_export_cancel": pending("PR 8", "export exact"),
    "exact_export_retry": pending("PR 8", "export exact"),
    "daily_digest_snapshot": pending("PR 8", "digest show"),
    "weekly_digest_snapshot": pending("PR 8", "digest show"),
    "weekly_digest_item": pending("PR 8", "digest show"),
    "weekly_digest_manual": pending("PR 8", "digest weekly-request"),
    "logs": pending("PR 8", "logs list"),
    "logs_export": pending("PR 8", "logs export"),
    # Operations.
    "background": command("task list"),
    "background_tasks": command("task list"),
    "background_task": command("task show"),
    "retry_family_preparation": pending("PR 9", "task retry"),
    "retry_daily_digest": pending("PR 9", "task retry"),
    "retry_weekly_digest": pending("PR 9", "task retry"),
    "retry_export_cleanup": pending("PR 9", "task retry"),
    "deliveries": pending("PR 9", "delivery list"),
    "delivery": pending("PR 9", "delivery show"),
    "delivery_resolve": pending("PR 9", "delivery resolve"),
    "delivery_refusals": pending("PR 9", "delivery refusals"),
    "delivery_refusal": pending("PR 9", "delivery refusals"),
    "delivery_refusal_clear": pending("PR 9", "delivery refusal-clear"),
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
}

"""The route-parity ledger of the Admin automation command line (ADM-11).

Every ``admin:`` URL name maps to the ``pk-stewardship admin`` command that
does the same thing, or to an exemption (see the specification's "Action
inventory" and "Rules for new Admin actions"):

- ``command``: the commands that cover the route, all in the catalog now;
  ``owed`` names the pull request that adds the rest of a partly covered
  route (``go_live`` reads today, its preview and confirmation come in PR 12);
- ``pending``: the pull request that will add its commands, which it names
  (an ADM-11 pull request, or a feature's own later slice, "#528 slice 4");
- ``permanent``: web-only by nature, with the reason;
- ``deferred``: waiting on a decision, which it names.

``tests/stewardship/test_admin_route_parity.py`` fails when an ``admin:`` URL
name has no entry, an entry names a route that does not exist, or a
``command`` entry names a command missing from the catalog. A new Admin page
or action lands with its command, or with an exemption here.
"""

from typing import NamedTuple

from parishkit.stewardship.admin_urls.slashless import TARGETS as SLASHLESS_TARGETS


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
SLASHLESS = (
    "a page URL without its trailing slash, which only redirects to the "
    "page (#525); the page's entry covers it"
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
    "held_emails": permanent(
        "held-email decisions belong to a restore review (the maintenance "
        "page, also web-only) and its follow-up after release, each with a "
        "preview, a required note and an exact-count check; a command for "
        "them would be a new feature"
    ),
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
    "automation_sessions": permanent(
        "the human side of the automation interface: revoking a session"
    ),
    "automation_notices": permanent(
        "the human side of the automation interface: acknowledging notices"
    ),
    # Listing and signing out other people's browser sessions (#953) is
    # session chrome too; the operator's offline recovery signs everyone out.
    "signed_in_sessions": permanent(SESSION_CHROME),
    # Source refresh.
    "source_refresh": command("refresh start", "refresh status"),
    "background_task_page": command("task show"),
    "background_task_status": command("task show"),
    # Schedules and configuration.
    "schedule_settings": command(
        "schedule show", "schedule preview", "schedule confirm"
    ),
    # New, Edit and Delete build the same change document the schedule
    # commands take (#878).
    "schedule_new": command("schedule preview", "schedule confirm"),
    "schedule_edit": command("schedule show", "schedule preview", "schedule confirm"),
    "schedule_delete": command("schedule preview", "schedule confirm"),
    # A test of New and Edit scheduled email's unsaved email text (#446):
    # the command line takes no unsaved text; ``test sample`` tests a saved
    # email, and the content commands save text to test.
    "schedule_email_test": permanent(
        "a test of text typed on the page and not saved; test sample tests "
        "a saved email"
    ),
    # The read-only calendar of every send (#469) draws the saved schedules
    # and campaign dates that ``schedule show`` lists.
    "schedule_calendar": command("schedule show"),
    # Change status; ``config request cancel`` (#944) is the operator's way
    # out of a stuck live end-date change, which the page offers no button for.
    "configuration_request": command("config request show", "config request cancel"),
    # One editor and one Review (#1119): a draft's settings, a live
    # campaign's end date and leader roles, and its Ministries all go
    # through ``campaign preview`` and ``campaign confirm``.
    "campaign_settings": command(
        "campaign show", "campaign preview", "campaign confirm"
    ),
    # The live end-date check validates what ``schedule preview`` reviews.
    "campaign_end_date_check": command("schedule preview"),
    # The command line has no first-campaign path yet: ``campaign confirm``
    # edits the current campaign and refuses any added one. When PR 10 adds
    # one, it must admit the first campaign only through
    # ``single_campaign.first_campaign_admitted`` (and
    # ``refuse_campaign_creation(first=True)``), as this page does.
    "campaign_create": pending("PR 10", "campaign create-preview", "campaign create"),
    "campaign_clone": pending("PR 10", "campaign clone"),
    # A Campaign setup step's Continue (#1404), and Pages and emails'
    # editor's Keep as is and go back (one item, #1404 PR 6b), only record
    # the browser stepper's "kept as is" and open the next step page (or
    # Pages and emails); the command line applies settings directly and has
    # no stepper to complete.
    "campaign_step_continue": permanent(
        "the browser Campaign setup stepper's own progress; the command line "
        "has no stepper"
    ),
    # The page's read, Review and Apply (#463).
    "share_settings": command(
        "campaign shares", "campaign shares-preview", "campaign shares-confirm"
    ),
    # The page's read, Review and Apply (#750), all on the page itself.
    "reminder_workgroup": command(
        "workgroup show", "workgroup preview", "workgroup confirm"
    ),
    "content_catalog": command("content list"),
    # The editor as it opens; its Review and Apply are still owed.
    "content_edit": command("content show", owed="PR 10"),
    "content_revision": command("content show", owed="PR 10"),
    # The email tables' Delete, as a preview and a confirm (#879).
    "content_delete": command("content preview", "content confirm"),
    "content_history": command("content history"),
    "content_history_revision": command("content history"),
    # The editors' live preview of typed HTML: ``content preview --html``
    # prints its cleaned HTML, generated plain text and removed markup.
    "content_plain_text": command("content preview"),
    "parish_settings": command("parish show", "parish preview", "parish confirm"),
    # A read-only catalog since #1267, so nothing is owed.
    "ministries": command("ministries list"),
    "hosted_files": command("files list"),
    "hosted_file_delete": command("files delete"),
    "hosted_file_rename": command("files rename"),
    # Past campaigns (#1018): the page's list, Review (the workbook comes on
    # standard input), Apply and Remove, through the same import service.
    "past_campaigns": command("history list"),
    "past_campaign_review": command("history preview"),
    "past_campaign_import": command("history confirm"),
    "past_campaign_removal": command("history remove"),
    # Download campaign archive (#1018 part (a)): the same export request.
    "past_campaign_archive": command("export archive"),
    # Reviews and removals happen on the page itself (#750).
    "artwork_settings": command(
        "artwork show", "artwork remove-preview", "artwork remove"
    ),
    # Applying logos is owed with the uploads: staged logos belong to the
    # sign-in that uploaded them, and each pk-admin run is its own sign-in,
    # so a command cannot apply logos staged in the browser (#463).
    "branding_settings": command("branding show", owed="PR 10"),
    "hosted_file_upload": permanent(UPLOAD),
    "artwork_upload": permanent(UPLOAD),
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
    # Its read, and its pause, resume and resolution previews and their
    # confirmation (PR 7b, #463).
    "delivery_control": command(
        "send controls",
        "send pause-preview",
        "send resume-preview",
        "send resolve-preview",
        "send confirm",
    ),
    # The Family portal switch (PR 7a); the delivery controls above are PR 7b.
    "family_portal": command("portal maintenance show", "portal maintenance set"),
    # Testing sends.
    "campaign_mail": command("test sample-preview", "test sample"),
    # The names the page shows: test families-preview --names exports
    # them for export fetch (#817).
    "campaign_mail_families": command(
        "test families-preview",
        "test families",
        "test status",
    ),
    # Reports and exports. PR 8 lands in parts: 8a the logs, 8b the export
    # lifecycle and its fetch, 8c the aggregate report reads, 8d the
    # digests, 8e the Family-level exports that create an export record
    # without the Family keys, 8f the Family directory's exports (the code
    # MAC keyring loaded only for them), 8g the one-Family timeline export (a
    # new export kind, migration 0063, in its own pull request), and 8h the
    # exact daily exports (part 1) and the in-memory Limitations download
    # (part 2, after #752's audit fields; the response lists were removed,
    # #933).
    # The aggregate report reads (PR 8c): counts and summaries only; the
    # rows behind them are the Family-level exports (PR 8e and 8f).
    "reports": command("report list"),
    # The figures status the Response dashboard and Campaign comparison poll
    # (#1586): the dashboard's own report status.
    "response_dashboard_status": command("report participation"),
    # The Campaign comparison page (#1018): its figures for a day.
    "campaign_comparison": command("report comparison"),
    # Its download form (#1018, slice 6): the same capture and export.
    "campaign_comparison_export": command("export comparison"),
    "daily_digest_chart": permanent(IMAGES),
    "daily_digest_download": permanent(IMAGES),
    "financial_report": command("report financial"),
    "limitations_report": command("report limitations"),
    # Counts only (#463): its rows name Families and Members, so they are
    # export census's. A row's Entered in ParishSoft, Ignore and Reopen post
    # here too (#528): census resolve; so does an Automatic row's Review
    # (ADM-09): census review, its Edit value and Clear edit (ADM-09.04):
    # census edit, and its Use the Family's value anyway (ADM-09.03): census
    # resolve-conflict (#1434).
    "census_changes": command(
        "report census",
        "census resolve",
        "census review",
        "census edit",
        "census resolve-conflict",
    ),
    # Publish to ParishSoft (#528, ADM-09): its check and plan come to the
    # command line in the page's own fourth slice.
    "census_publication": pending(
        "#528 slice 4", "census publish-check", "census publish-plan"
    ),
    "information_queue": command("report information"),
    # One Family's submission and its follow-up history: ``followup
    # information-show`` (#463 PR 11b), which prints only the item's state
    # and history, as the Ministry follow-up items are read; its content is
    # Family-level and comes through ``export information``.
    "information_item": command("followup information-show"),
    # Ministry requests (#1133) is the follow-up queue with the summary's
    # By Ministry counts. ``report ministry`` prints those counts, and
    # ``export ministry`` writes one Ministry's join or leave list, the file
    # the retired Members joining and leaving pages offered (#1157); the
    # queue itself is ``followup list``'s (#463 PR 11a).
    "ministry_report": command("report ministry", "export ministry", "followup list"),
    # A packet of the chosen Ministries' Members and contacts: an export.
    "ministry_packet": command("export ministry-packet"),
    # The Response dashboard shows the funnel and, since #1537, the
    # statistics and daily table that were Participation's.
    "response_dashboard": command("report responses", "report participation"),
    "family_directory": command("export directory", "export postal"),
    # A Family's Timeline (#1570) is Family-level, so it is a file only:
    # the page's export form (PR 8g) and ``export family-timeline``.
    "timeline": command("export family-timeline"),
    "timeline_export": command("export family-timeline"),
    "family_open_form": permanent(
        "Open form signs this browser in to the Family form for a Family on "
        "the phone; a browser hand-off by nature (#529)"
    ),
    # The header's Find a Family box (#561) runs the directory's search.
    "find_family": permanent(NAME_SEARCH),
    # Creating a financial export is privileged (#547): the command calls
    # the caller-aware sign_in_instant, as the page's view does.
    "financial_export": command("export financial"),
    # The page's download, through the page's own writer.
    "limitations_export": command("export limitations"),
    # The page's download, and JSON Lines with each change's id and version.
    "census_changes_export": command("export census"),
    "information_export": command("export information"),
    # Both directory exports are privileged (#547), as above.
    "family_directory_export": command("export directory", "export postal"),
    # The export lifecycle (PR 8b): the status page and its buttons, and the
    # Response dashboard's export form. The download's grant is issued and
    # consumed inside ``export download``. The latest-data export lost its
    # page form with #1537; its command stays, beside the page's one export
    # form, as the other way to export the daily figures.
    "report_export_create": command("export create", "export exact create"),
    # The export pages also serve an exact request (made by ``export exact
    # create``), whose commands are the export exact ones (PR 8h).
    "report_export": command("export status", "export exact status"),
    "report_export_cancel": command("export cancel", "export exact cancel"),
    "report_export_retry": command("export retry", "export exact retry"),
    "report_export_download": command("export download"),
    # The digests (PR 8d): the retained reports an emailed digest links to,
    # and the manual weekly report.
    "daily_digest_snapshot": command("digest daily"),
    "weekly_digest_snapshot": command("digest weekly"),
    # One item of a weekly report: its states are in ``digest weekly``'s
    # items; the Family's text is Family-level and stays on the page.
    "weekly_digest_item": command("digest weekly"),
    "weekly_digest_manual": command("digest weekly-request"),
    # The Emailed reports page (NAV-14) lists the retained reports; its
    # ``?requested=`` is ``digest list --requested``.
    "emailed_reports": command("digest list"),
    "logs": command("logs list"),
    "logs_export": command("logs export"),
    # System health (ADM-13): the page, its polled fragment (``--watch``)
    # and /admin/system/, which only redirects to the page.
    "system": command("system health"),
    "campaign_root": permanent(GROUP_ROOT),
    "mail_root": permanent(GROUP_ROOT),
    "parish_root": permanent(GROUP_ROOT),
    # Take a backup now, Accept this change once, Turn off debug logging and
    # (in Testing) Allow debug logging again post to the page; ``system
    # backup-now``, ``system refresh-accept-preview`` with ``system
    # refresh-accept``, ``system debug-off`` and ``system debug-allow`` are
    # their commands (each row's guard binds the command session as it does a
    # browser session), and ``system health`` shows their progress.
    # Clear the halt's commands, ``system mail-clear-preview`` and ``system
    # mail-clear``, come first (ADM-13 PR 4a); its button posts here with
    # PR 4b. Neither needs a recent sign-in (#1547).
    "system_health": command(
        "system health",
        "system backup-now",
        "system refresh-accept-preview",
        "system refresh-accept",
        "system debug-off",
        "system debug-allow",
        "system mail-clear-preview",
        "system mail-clear",
    ),
    "system_health_status": command("system health"),
    # Families the form cannot open (#774): counts and DUIDs only.
    "source_form": command("report source-form"),
    # Data to fix in ParishSoft (#1011): its section counts only.
    "parishsoft_fixes": command("report data-to-fix"),
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
    # Report a bounced address (#806 slice 4): asks for the page's
    # acknowledgement at the prompt, as refusal-clear does (#1211).
    "delivery_bounce_report": command("delivery bounce-report"),
    "delivery_refusals": command("delivery refusals"),
    "delivery_refusal": command("delivery refusal-show"),
    # Asks for the page's verification acknowledgement at the prompt.
    "delivery_refusal_clear": command("delivery refusal-clear"),
    # Users and follow-up.
    # The Users page (#952): the table, and Apply of a reviewed change. New,
    # Edit and Delete build the change document ``users preview`` takes, and
    # a new user's Name defaults from ParishSoft there as on the page.
    # ``users preview-upgrade`` was the interim page's Upgrade preview panel; it
    # answers not_available once the configuration holds users.
    "users": command(
        "users list", "users preview", "users confirm", "users preview-upgrade"
    ),
    "user_new": command("users preview", "users confirm"),
    "user_edit": command("users list", "users preview", "users confirm"),
    "user_delete": command("users preview", "users confirm"),
    "user_name_suggestions": command("users preview"),
    # The leaders table and its import review (#952 slice 6).
    "user_leader_import": command("users import-leaders"),
    # Email Ministry leaders (#1592): its review and Apply (#1622).
    "user_leader_emails": command("users email-leaders"),
    # Home's Acknowledge buttons; events list reads what they acknowledge.
    "security_event_acknowledge": command("events list", "events acknowledge"),
    "critical_events_acknowledge": command("events list", "events acknowledge"),
    # An item's Save (#463 PR 11b).
    "information_update": command(
        "followup information-show", "followup information-update"
    ),
    # A Ministry request's page and its Save (#463 PR 11a).
    "ministry_followup_item": command("followup show"),
    "ministry_followup_update": command("followup show", "followup update"),
    # Bulk outcomes on the queue (#1131): followup bulk (#463 PR 11a).
    "ministry_followup_bulk": command("followup list", "followup bulk"),
    # Export these requests (#1157): the queue's filtered set with contacts,
    # through the page's own writer (#463 PR 11c).
    "ministry_followup_export": command("followup export"),
    # The roster Entered in ParishSoft tick and its list (#528), one at a
    # time or in bulk (#463 PR 11b).
    "ministry_followup_roster": command("followup show", "followup roster-entered"),
    # Counts only (#463): its rows name Members, so they are export roster's.
    "ministry_roster": command("report roster"),
    # The page's download, through the page's own writer.
    "ministry_roster_export": command("export roster"),
    # The list's bulk Mark entered in ParishSoft (#879).
    "ministry_roster_bulk": command("report roster", "followup roster-bulk"),
    # Integrations and credentials.
    "integrations": command("integration list"),
    # The settings page as it opens, its Save, its Remove, the backup
    # folder check, its Save with a new key and the Backup encryption key's
    # three steps (#463).
    "integration_settings": command(
        "integration show",
        "integration preview",
        "integration confirm",
        "integration backup-probe",
        "integration key replace",
        # Finish switching to the new key, in the page's review dialog (#1568).
        "integration key finish-switching-preview",
        "integration key finish-switching",
        "integration backup-key challenge",
        "integration backup-key preview",
        "integration backup-key confirm",
    ),
    # The settings page's status line, which is also a key change's status
    # (#1568).
    "integration_status": command("integration status", "integration key status"),
    # The ParishSoft page's live refresh schedule check (#632).
    "refresh_schedule_check": command("integration schedule-preview"),
    "dismiss_credential_result": command("integration dismiss"),
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
            "setup_preview",
            "setup_confirmation",
            "setup_mail",
            "setup_mail_status",
            "setup_notification",
            "setup_notification_status",
            "setup_step",
        )
    },
    # Pages' slashless forms (the URL scheme, #525): redirects, not actions.
    **{name: permanent(SLASHLESS) for name in SLASHLESS_TARGETS},
}
# Commands whose page lands in a later pull request: they cover no admin:
# route yet, so the parity test accepts them by name until the page's route
# joins the ledger as their "command" entry. Empty now: Past campaigns'
# import commands (#1018 slice 2) were the first, until their page (slice 4).
AHEAD_OF_PAGE = frozenset()

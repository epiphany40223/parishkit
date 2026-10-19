"""Declarative Admin portal navigation: menu groups, menu entries and breadcrumbs.

One registry describes every Admin page: which menu group it belongs to, its
parent page and its label. ``MENU`` lists the menu entries in order, each with
the capability its page checks and the reason it is unavailable right now. The
sidebar and the breadcrumb trail are both built from them, so the two cannot
drift, and templates never hand-write breadcrumbs.

The menu has a stable shape (admin-portal spec, "Stable menu shape"): only the
viewer's role hides an entry. An entry the role may open but that the mode or
the campaign's state does not allow right now stays in place, greyed out, with
its reason.

Everything here is presentation only. It grants no authority: each view still
checks its own capability, and ``portal_chrome`` applies the same capability
decisions the pages use. Building navigation reads only the campaign and mode
the chrome already loaded and reverses URLs from static route metadata and the
current request's resolved arguments. Its one database read is whether
ParishSoft data has been downloaded (``MenuState.parishsoft_data``), which
the caller supplies and which runs at most once, only when an entry's reason
check asks for it.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from typing import NamedTuple

from django.urls import NoReverseMatch, Resolver404, get_resolver, resolve, reverse
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.admin_urls.slashless import TARGETS as SLASHLESS_TARGETS
from parishkit.stewardship.campaigns.configuration import REMINDER_WORKGROUP_NAME
from parishkit.stewardship.campaigns.domain import CampaignState
from parishkit.stewardship.campaigns.lifecycle import structural_edit_admitted
from parishkit.stewardship.web.error_pages import ERROR_PAGE_ATTRIBUTE
from parishkit.stewardship.web.refusals import UserFacingUnavailable

from .policy import Capability

NAMESPACE = "admin"


@dataclass(frozen=True)
class Section:
    """A menu group: a collapsible group in the sidebar and the second breadcrumb.

    ``segment`` is the first URL segment after ``/admin/`` of the group's
    pages (admin-portal spec, "URL scheme"); it is the key unless the group
    shares another group's segment. Review work keeps its pages under
    ``/admin/reports/``, where they were before the group was split from
    Reports and queries (R39 finding 4), so no address changes.
    """

    key: str
    label: str
    segment: str | None = None

    @property
    def path(self):
        """The group's URL segment: ``segment``, or the key itself."""
        return self.segment or self.key


@dataclass(frozen=True)
class Page:
    """One Admin page: its section, its parent page (URL name) and its label.

    ``linkable`` is False for a page a link cannot reliably reopen: one that
    only answers a POST (a review step), or one that reviews a single
    pending change and refuses once that change is confirmed (a staged
    image, a new campaign). Its crumb is shown for orientation but never
    linked, and "Return to" skips it, because a GET would fail.

    ``entry`` names the menu entry a page outside the menu only redirects to
    (a report root): it is linked only while the viewer's menu offers that
    entry, because the page checks the same capability (#457 L3).
    """

    section: str | None
    label: str
    parent: str | None = None
    linkable: bool = True
    entry: str | None = None


@dataclass(frozen=True)
class Placement:
    """Where a view says the current request sits, beyond its static route.

    A view knows things its route does not: which email a test page sends,
    which report an export came from, which settings page a configuration
    change was confirmed on. ``parent`` replaces the registered parent,
    ``arguments`` supplies route arguments that ancestor links need,
    ``labels`` names ancestor pages more specifically (by URL name),
    ``queries`` adds a query string to an ancestor's link (by URL name; the
    view's own closed choice, such as the timeline's Testing mode, never the
    request's query), and ``flow``/``step`` select a step indicator from
    ``FLOWS``. Presentation only: it grants nothing, and every linked page
    rechecks its own access.
    """

    parent: str | None = None
    arguments: dict = field(default_factory=dict)
    labels: dict = field(default_factory=dict)
    queries: dict = field(default_factory=dict)
    flow: str | None = None
    step: str | None = None


# The menu groups, in the order a campaign runs: set it up, send it, review
# the responses and read the reports, then the parish data, users and system
# pages that change rarely. Home sits above them (admin-portal spec, "Menu
# groups"). Review work comes before Reports and queries, because reviewing
# is the daily work while a campaign runs (R39 finding 4).
SECTIONS = (
    Section("campaign", _("Campaign setup")),
    Section("mail", _("Mail and Family portal")),
    Section("review", _("Review work"), segment="reports"),
    Section("reports", _("Reports and queries")),
    Section("parish", _("Parish data")),
    Section("users", _("Users and access")),
    Section("system", _("System")),
)
SECTION_LABELS = {section.key: section.label for section in SECTIONS}
SECTION_PATHS = {section.key: section.path for section in SECTIONS}

# Every Admin HTML page, keyed by URL name. Menu entries are listed in MENU
# below; a page without a parent that is not a menu entry (a redirect, a
# digest) stands alone under its group (or, with no group, under Home). A
# view may override the last crumb's label with a ``breadcrumb_label``
# context variable, e.g. the name of the email being edited.
PAGES = {
    "index": Page(None, _("Home")),
    # Campaign
    "campaign_settings": Page("campaign", _("Campaign settings")),
    # Create the campaign (#142) is offered from Home only while there is no
    # campaign, so its trail runs from Home, outside the Campaign setup group;
    # once a campaign exists it refuses, so a change's status page names it
    # but never links it.
    "campaign_create": Page(None, _("Create the campaign"), "index", linkable=False),
    # Copy campaign is refused until the single-campaign change (#145), so a
    # change's status page names it but never links it. (New campaign is
    # retired outright; its old address is a non-page redirect below.)
    "campaign_clone": Page(
        "campaign", _("Copy campaign"), "campaign_settings", linkable=False
    ),
    "content_catalog": Page("campaign", _("Pages and emails")),
    "content_edit": Page("campaign", _("Edit page or email"), "content_catalog"),
    "content_revision": Page("campaign", _("Content revision"), "content_catalog"),
    # Answers only the email tables' confirmation dialog, with a change's status.
    "content_delete": Page(
        "campaign",
        _("Delete email templates"),
        "content_catalog",
        linkable=False,
    ),
    # Retained content can belong to an earlier campaign, which the editing
    # catalog refuses, so its trail runs through Campaign settings instead.
    "content_history": Page("campaign", _("Content history"), "campaign_settings"),
    "content_history_revision": Page(
        "campaign", _("Earlier version"), "content_history"
    ),
    # A test page sends one email revision, so its trail runs through that
    # revision's editor; the view supplies the kind and slot the route lacks.
    "campaign_mail": Page("campaign", _("Preview and test email"), "content_revision"),
    "campaign_mail_families": Page(
        "campaign", _("Send to chosen Families"), "campaign_mail"
    ),
    "schedule_settings": Page("campaign", _("Dates and mail schedules")),
    # One schedule's page (#878): New, or Edit filled in from a saved one.
    "schedule_new": Page("campaign", _("New scheduled email"), "schedule_settings"),
    "schedule_edit": Page("campaign", _("Edit scheduled email"), "schedule_settings"),
    # Every send by date, read-only (#469), opened from the list.
    "schedule_calendar": Page("campaign", _("Campaign calendar"), "schedule_settings"),
    # Answers only the list's confirmation dialog, with a change's status.
    "schedule_delete": Page(
        "campaign",
        _("Delete scheduled emails"),
        "schedule_settings",
        linkable=False,
    ),
    "share_settings": Page("campaign", _("Share options")),
    "artwork_settings": Page("campaign", _("Campaign images")),
    # A POST-only re-render of Campaign images with the upload's error, so it
    # shares that page's name.
    "artwork_upload": Page("campaign", _("Campaign images"), "artwork_settings"),
    "reminder_workgroup": Page("campaign", _(REMINDER_WORKGROUP_NAME)),
    "go_live": Page("campaign", _("Go-live readiness")),
    "go_live_families": Page("campaign", _("Testing submissions"), "go_live"),
    "go_live_cleanup": Page("campaign", _("Testing cleanup"), "go_live"),
    "go_live_links": Page("campaign", _("Prepare Family links"), "go_live_cleanup"),
    "production_confirmation": Page(
        "campaign", _("Confirm Production"), "go_live_links"
    ),
    # A menu entry of its own, available once Production is confirmed.
    "production_progress": Page("campaign", _("Production activation")),
    # Cancel go-live (formerly Withdraw from Production) is offered only on
    # Production activation, so its trail runs through that page. The name
    # avoids a clash with the after-archive Return to Testing (decision 9).
    "production_withdrawal": Page(
        "campaign", _("Cancel go-live"), "production_progress"
    ),
    # Mail and Family portal
    "delivery_control": Page("mail", _("Pause and resume mail")),
    # Watching a launch or reminder send (#413): a sidebar entry of its own,
    # beside Pause and resume mail, so it is found without knowing where it is.
    "family_email_progress": Page("mail", _("Family email progress")),
    # The permanent record of every send (#432), listed beside the live panel.
    "family_email_sends": Page("mail", _("Family email history")),
    "deliveries": Page("mail", _("Sent mail")),
    "delivery": Page("mail", _("Mail message"), "deliveries"),
    "delivery_refusals": Page("mail", _("Refused addresses"), "deliveries"),
    "delivery_refusal": Page("mail", _("Refused address"), "delivery_refusals"),
    "family_portal": Page("mail", _("Family portal availability")),
    "presence": Page("mail", _("Families on the form now")),
    # Held emails a restore left undecided at release (#757). Not a menu
    # entry: it matters only after a restore, so Home links to it while any
    # hold is undecided.
    "held_emails": Page("mail", _("Held emails")),
    # Reports and queries. Every report is a menu entry of its own. The
    # reports root only redirects to the current campaign's report (or shows
    # that there is none), so it stands alone, outside the menu.
    "reports": Page("reports", _("Response dashboard"), entry="response_dashboard"),
    "campaign_comparison": Page("reports", _("Campaign comparison")),
    "financial_report": Page("reports", _("Financial stewardship")),
    # Review work: each stream's review worklist (review pipeline spec).
    "limitations_report": Page("review", _("Limitations")),
    "census_changes": Page("review", _("Census changes")),
    # Not a menu entry: Census changes links to it (#528, ADM-09).
    "census_publication": Page("review", _("Publish to ParishSoft"), "census_changes"),
    "response_dashboard": Page("reports", _("Response dashboard")),
    "information_queue": Page("review", _("Additional information")),
    "information_item": Page("review", _("Information request"), "information_queue"),
    "report_export": Page("reports", _("Report export"), "reports"),
    # The reports sent by email, listed on Emailed reports (ADM-12.17), and
    # Send a weekly report now, its Administrator action.
    "emailed_reports": Page("reports", _("Emailed reports")),
    "weekly_digest_manual": Page(
        "reports", _("Send a weekly report now"), "emailed_reports"
    ),
    "weekly_digest_snapshot": Page("reports", _("Weekly report"), "emailed_reports"),
    "weekly_digest_item": Page(
        "reports", _("Weekly report item"), "weekly_digest_snapshot"
    ),
    "daily_digest_snapshot": Page("reports", _("Daily report"), "emailed_reports"),
    # Ministry requests is the follow-up queue (#1133); Ministry follow-up,
    # once a page of its own, was merged into it.
    "ministry_report": Page("review", _("Ministry requests")),
    "ministry_followup_item": Page("review", _("Follow-up request"), "ministry_report"),
    # Resolved Ministry joins and leaves to enter in ParishSoft by hand (#528),
    # a menu entry of its own with its count (#1660).
    "ministry_roster": Page("review", _("Roster changes to enter")),
    # The financial stream's review worklist and manual report (#1686), a
    # menu entry of its own after Ministry requests (R39 finding 1).
    "pledges_to_enter": Page("review", _("Pledges to enter")),
    "family_directory": Page("reports", _("Active parishioner family directory")),
    # One Family's summary and timeline (#477; the Timeline since #1570),
    # opened from every Family name the shared family_link tag draws.
    "timeline": Page("reports", _("Timeline"), "family_directory"),
    # Parish data
    "parish_settings": Page("parish", _("Parish settings")),
    "branding_settings": Page("parish", _("Parish logos")),
    "hosted_files": Page("parish", _("Hosted files")),
    # Answers only the library's confirmation dialog (#879), never a page.
    "hosted_file_delete": Page(
        "parish", _("Delete hosted files"), "hosted_files", linkable=False
    ),
    "hosted_file_rename": Page("parish", _("Change placeholder name"), "hosted_files"),
    # Archive workbooks of earlier campaigns (#1018): a parish record, read by
    # Campaign comparison. Its Review, Apply and Remove are form actions.
    "past_campaigns": Page("parish", _("Past campaigns")),
    "ministries": Page("parish", _("Ministries")),
    # A sidebar entry of its own, so a manual refresh is found without Home.
    "source_refresh": Page("parish", _("Refresh from ParishSoft")),
    # One summary of the data problems fixed in ParishSoft (#1011).
    "parishsoft_fixes": Page("parish", _("Data to fix in ParishSoft")),
    # A configuration change can come from any settings page, so its status
    # page is registered under Home; the view places it under the page the
    # change was confirmed on when this sign-in remembers it.
    "configuration_request": Page(None, _("Change status")),
    # Users
    # One table of users (#952), with New and Edit user under it.
    "users": Page("users", _("Users")),
    "user_new": Page("users", _("New user"), "users"),
    "user_edit": Page("users", _("Edit user"), "users"),
    # Answers only the table's confirmation dialog, with a change's status.
    "user_delete": Page("users", _("Delete user"), "users", linkable=False),
    # Answers only the leaders table's Import selected and Import all with
    # the review of the chosen ParishSoft Ministry leaders (#952 slice 6).
    "user_leader_import": Page(
        "users", _("Import Ministry leaders"), "users", linkable=False
    ),
    # Answers only the Email Ministry leaders table's review and its Apply
    # (#1592).
    "user_leader_emails": Page(
        "users", _("Email Ministry leaders"), "users", linkable=False
    ),
    # Every live interactive Admin session, each with Sign out (#953).
    "signed_in_sessions": Page("users", _("Signed-in sessions")),
    # The Administrator's own automation sessions (ADM-11), and the approval
    # of a pending one, which the command line links to.
    "automation_access": Page("users", _("Automation access")),
    "automation_approval": Page(
        "users", _("Approve an automation session"), "automation_access"
    ),
    # System
    "system_health": Page("system", _("System health")),
    "source_form": Page("system", _("Families the form cannot open"), "system_health"),
    "integrations": Page("system", _("Integrations")),
    "integration_settings": Page("system", _("Integration"), "integrations"),
    "background": Page("system", _("Background work")),
    "background_task_page": Page("system", _("Background task"), "background"),
    "logs": Page("system", _("System logs")),
}


class MenuState(NamedTuple):
    """What a menu entry's reason check may read: campaign, mode and source data.

    ``campaign`` is the current campaign row (or None), already loaded with
    its active configuration by ``portal_chrome``, and ``mode`` is the
    system's "testing" or "production". ``parishsoft_data`` is a callable
    answering whether a ParishSoft download has been promoted (#1242); the
    caller (``admin_context.menu_state``) makes it read the database at most
    once, and only an entry that needs the data calls it. Reason checks read
    nothing else.
    """

    campaign: object
    mode: str
    parishsoft_data: Callable[[], bool]


class MenuItem(NamedTuple):
    """One menu entry as the viewer sees it: ``url`` is None while unavailable.

    ``reason`` is the plain-language reason an unavailable entry is greyed
    out, shown in its tip and announced as its description; None when the
    entry may be opened now.
    """

    section: str
    name: str
    label: str
    url: str | None
    reason: str | None = None


# The reasons an entry is greyed out. Each is one string, shown in the menu's
# tip and, word for word, on the page itself when it is opened directly
# (``greyed_entry``, #1128), so the two cannot drift apart.
NO_CAMPAIGN = _("No current campaign")
ARCHIVED = _("The campaign is archived")
FINANCIAL_MISSING = _("This campaign does not include Financial stewardship")
MINISTRY_MISSING = _("This campaign does not include Ministry stewardship")
CENSUS_MISSING = _("This campaign does not include the census")
STRUCTURE_FIXED = _(
    "Can be changed only in Testing mode, before the campaign goes live"
)
ALREADY_LIVE = _("Only a draft campaign can go live; this one already has")
NOT_CONFIRMED = _(
    "Production has not been confirmed for this campaign; start at Go-live readiness"
)
PRODUCTION_ONLY = _("Available in Production mode")
NO_SOURCE_DATA = _("ParishSoft data has not been downloaded yet")


def _never(state):
    """The entry is always available (its page needs no campaign)."""
    return None


def _campaign(state):
    """Unavailable while there is no current campaign."""
    return None if state.campaign else NO_CAMPAIGN


def _unarchived(state):
    """Unavailable without a current campaign, or once it is archived."""
    if not state.campaign:
        return NO_CAMPAIGN
    if state.campaign.state == "archived":
        return ARCHIVED
    return None


def _modules(state):
    """The current campaign's modules (financial, ministry, census)."""
    return (state.campaign.active_configuration.values or {}).get("modules", ())


def _module(module, missing):
    """A reason check for a page that needs the campaign to include ``module``."""

    def reason(state):
        """Unavailable without a current campaign or without the module."""
        if not state.campaign:
            return NO_CAMPAIGN
        return None if module in _modules(state) else missing

    return reason


def _downloaded(base=_never):
    """A reason check for a page that lists ParishSoft data (#1242).

    ``base`` runs first, so a page that also needs a campaign says so
    before it says the data is missing. Without a promoted download the
    page has nothing to list, so it is greyed out with a forward link to
    Refresh from ParishSoft, never linked to a "try again" failure.
    """

    def reason(state):
        """Unavailable for ``base``'s reason, or until the first download."""
        return base(state) or (None if state.parishsoft_data() else NO_SOURCE_DATA)

    return reason


FINANCIAL = _module("financial", FINANCIAL_MISSING)
MINISTRY = _module("ministry", MINISTRY_MISSING)
CENSUS = _module("census", CENSUS_MISSING)


def _structural(module_reason):
    """A reason check for Share options.

    It edits the campaign's structure, which is open only on an unlocked
    Testing draft that has never been live; the page refuses anything else,
    so the entry is greyed out rather than linking a page that fails.
    """

    def reason(state):
        """Unavailable without the module, or once the structure is fixed."""
        missing = module_reason(state)
        if missing:
            return missing
        campaign = state.campaign
        if state.mode == "testing" and structural_edit_admitted(
            CampaignState(campaign.state),
            ever_active=campaign.ever_active,
            locked=campaign.structural_locked,
        ):
            return None
        return STRUCTURE_FIXED

    return reason


def _draft(state):
    """Go-live readiness: only a draft campaign can go live."""
    if not state.campaign:
        return NO_CAMPAIGN
    if state.campaign.state != "draft":
        return ALREADY_LIVE
    return None


def _confirmed(state):
    """Production activation: the page follows a confirmed Production switch.

    Confirming Production moves the campaign out of draft (to scheduled or
    active) and the system into Production mode, and Cancel go-live undoes
    both, so the two together stand for a stored confirmation without a
    query. The page itself refuses in Testing mode or without one.
    """
    if not state.campaign:
        return NO_CAMPAIGN
    if state.mode != "production" or state.campaign.state == "draft":
        return NOT_CONFIRMED
    return None


def _production(state):
    """Pause and resume mail acts on real mail, so Production only."""
    if not state.campaign:
        return NO_CAMPAIGN
    return None if state.mode == "production" else PRODUCTION_ONLY


@dataclass(frozen=True)
class Entry:
    """One menu entry: the page it opens, who may see it and when it is unavailable.

    ``capability`` is the capability the page itself checks, so the menu and
    the page cannot disagree (the menu is still not the security boundary).
    ``scoped`` marks a Ministry-scoped capability, which a Ministry leader
    holds through an assigned Ministry. No entry's route names a campaign
    (single-campaign interim, #865); each page finds the current one.
    ``reason`` returns why the entry is unavailable right now, or None.
    """

    name: str
    capability: Capability
    reason: Callable = _never
    scoped: bool = False


# The menu, in order within each group (admin-portal spec, "Menu groups").
# Labels come from PAGES, so the menu, the trail and the page share a name.
# Entries for pages that do not exist yet are added with those pages:
# Emailed reports (ADM-12).
_ADMIN = Capability.CONFIGURE
MENU = (
    # Campaign setup
    Entry("campaign_settings", _ADMIN, _campaign),
    Entry("content_catalog", _ADMIN, _unarchived),
    # Theme artwork (#248) stays editable while the campaign runs.
    Entry("artwork_settings", _ADMIN, _unarchived),
    Entry("schedule_settings", _ADMIN, _unarchived),
    Entry("share_settings", _ADMIN, _structural(FINANCIAL)),
    # Staff mark Families in ParishSoft while the campaign runs (#861).
    Entry("reminder_workgroup", _ADMIN, _unarchived),
    Entry("go_live", _ADMIN, _draft),
    Entry("production_progress", _ADMIN, _confirmed),
    # Mail and Family portal
    Entry("delivery_control", _ADMIN, _production),
    Entry("family_email_progress", _ADMIN, _campaign),
    Entry("family_email_sends", _ADMIN, _campaign),
    Entry("deliveries", _ADMIN),
    Entry("family_portal", _ADMIN),
    Entry("presence", _ADMIN),
    # Review work, in the Administrator's order (R39 finding 1).
    Entry("census_changes", Capability.VIEW_CENSUS, CENSUS),
    Entry("ministry_report", Capability.MINISTRY_REPORT, MINISTRY, scoped=True),
    # Administrators and Staff only: unscoped MINISTRY_FOLLOWUP is the
    # page's own check (workflows.roster.can_tick), which a Ministry leader,
    # holding it only for their Ministries, never passes (#1660).
    Entry("ministry_roster", Capability.MINISTRY_FOLLOWUP, MINISTRY),
    Entry("pledges_to_enter", Capability.FINANCIAL_DETAIL, FINANCIAL),
    Entry("information_queue", Capability.ADDITIONAL_FOLLOWUP, _downloaded(_campaign)),
    Entry("limitations_report", Capability.CAMPAIGN_REPORT, MINISTRY),
    # Reports and queries
    Entry("response_dashboard", Capability.CAMPAIGN_REPORT, _campaign),
    Entry("campaign_comparison", Capability.CAMPAIGN_REPORT, _campaign),
    Entry("financial_report", Capability.FINANCIAL_DETAIL, FINANCIAL),
    Entry("family_directory", Capability.FAMILY_CODES, _downloaded(_campaign)),
    # Staff see its daily reports; Send a weekly report now is on the page.
    Entry("emailed_reports", Capability.CAMPAIGN_REPORT),
    # Parish data
    Entry("parish_settings", _ADMIN),
    Entry("branding_settings", _ADMIN),
    Entry("ministries", _ADMIN, _downloaded()),
    Entry("hosted_files", _ADMIN),
    # CONFIGURE is the Administrator's alone, so it implies the page's
    # FINANCIAL_DETAIL too (Staff hold that one only).
    Entry("past_campaigns", _ADMIN),
    Entry("source_refresh", _ADMIN),
    # Last, so /admin/parish/ still opens Parish settings for an
    # Administrator; Staff see the group with this one entry (#1011). Its
    # sections' detail pages check SYSTEM_LOGS, BACKGROUND_WORK,
    # FAMILY_CODES or VIEW_CENSUS; every role holding any of them holds
    # FAMILY_CODES, which the page's own admission confirms per section.
    Entry("parishsoft_fixes", Capability.FAMILY_CODES),
    # Users and access
    Entry("users", Capability.MANAGE_USERS),
    Entry("signed_in_sessions", _ADMIN),
    Entry("automation_access", _ADMIN),
    # System. System health comes first, so /admin/system/ opens it (ADM-13).
    Entry("system_health", Capability.SYSTEM_LOGS),
    Entry("integrations", _ADMIN),
    Entry("background", _ADMIN),
    Entry("logs", Capability.SYSTEM_LOGS),
)
MENU_NAMES = frozenset(entry.name for entry in MENU)
# Each entry's reason check by page, for a page that greys out its own link
# to another menu page exactly as the menu does (Data to fix in ParishSoft).
MENU_REASONS = {entry.name: entry.reason for entry in MENU}
# Pages outside the menu that refuse for a menu reason, so their links are
# greyed out the same way: Families the form cannot open checks ParishSoft
# data that has not been downloaded yet (#1242).
PAGE_REASONS = {"source_form": _downloaded()}


# Where a greyed-out page opened directly leads (#1128): the menu entry that
# changes what the reason names, linked only while the viewer's menu offers
# it. A module is chosen on Campaign settings; Production starts at Go-live
# readiness; a campaign that has gone live is followed on Production
# activation. Missing ParishSoft data is fetched on Refresh from ParishSoft.
# Other reasons (no current campaign, an archived campaign, a
# structure fixed once the campaign went live) have no page that undoes them,
# so their page offers only the usual way back and Home.
FORWARD = (
    (FINANCIAL_MISSING, "campaign_settings"),
    (MINISTRY_MISSING, "campaign_settings"),
    (CENSUS_MISSING, "campaign_settings"),
    (NOT_CONFIRMED, "go_live"),
    (PRODUCTION_ONLY, "go_live"),
    (ALREADY_LIVE, "production_progress"),
    (NO_SOURCE_DATA, "source_refresh"),
)


class Greyed(NamedTuple):
    """A menu page the viewer may open by role but not now, and its way forward.

    ``label`` is the page's name and ``reason`` the menu entry's own reason
    string; ``forward`` is ``(label, url)`` of the entry that changes it, or
    None when the viewer's menu does not offer one now.
    """

    label: str
    reason: str
    forward: tuple | None

    def refusal(self):
        """The page's refusal: the menu's reason, word for word, and a way on.

        The heading names the page, the message is the reason as a sentence
        (the menu's tips have no final stop), and the link, when there is
        one, opens the entry that changes it. The error page adds the usual
        way back and Home.
        """
        reason = str(self.reason)
        label, url = self.forward or (None, None)
        return UserFacingUnavailable(
            reason if reason.endswith(".") else f"{reason}.",
            title=_("%(page)s isn't available right now") % {"page": self.label},
            fix=_(
                "This page opens again once that changes. Until then, choose "
                "another page from the menu."
            ),
            link=url,
            link_label=_("Go to %(page)s") % {"page": label} if url else None,
        )


def greyed_entry(name, items):
    """Why menu page ``name`` is greyed out for this viewer now, or None.

    ``items`` is the viewer's menu (``menu``). An entry the viewer's role
    hides is not in it, so a page refused by role is never explained here:
    its own view refuses it as before. The reason is matched by identity,
    since each check returns one of the module's reason strings.
    """
    item = next((entry for entry in items if entry.name == name), None)
    if item is None or item.reason is None:
        return None
    return greyed_page(name, item.reason, items)


def greyed_page(name, reason, items):
    """Page ``name`` greyed out for ``reason``, with the way forward ``items`` offer.

    ``greyed_entry`` uses it for a menu page; a page outside the menu that
    refuses for one of ``PAGE_REASONS`` uses it directly, after its own
    access check, so its refusal reads as the menu's would (#1242).
    """
    offered = {entry.name: entry.url for entry in items if entry.url}
    target = next((page for known, page in FORWARD if known is reason), None)
    forward = (PAGES[target].label, offered[target]) if target in offered else None
    return Greyed(PAGES[name].label, reason, forward)


def refuse_greyed(name, configuration, campaign):
    """Raise the shared reason page when menu entry ``name`` greys ``campaign``.

    For the pages under a greyed-out entry, by any method, which the access
    gate (``greyed_page``) does not reach: they are refused with the menu's
    own words (ui-conventions, "Unavailable menu pages"; #1255, #1258). Only
    the current campaign is judged; any other is left to the caller's own
    refusal. The caller passes rows read in its own snapshot or work lock,
    so a change of state cannot slip in between this check and a save.
    """
    if campaign is None or configuration.current_campaign_id != campaign.pk:
        return
    # Only the campaign's state is judged here; the first-download check is the
    # access gate's (``greyed_page``), so the data question is answered "yes".
    state = MenuState(campaign, configuration.mode, lambda: True)
    reason = MENU_REASONS[name](state)
    if reason is not None:
        raise Greyed(PAGES[name].label, reason, None).refusal()


def menu(state, may_open):
    """The menu entries the viewer's role may open, available or greyed out.

    ``may_open(entry)`` is the caller's capability decision for the entry,
    the same one its page makes. Only it hides an entry; the mode and the
    campaign's state only grey one out, so a role's menu keeps one shape.
    Returns ``MenuItem`` rows in menu order.
    """
    items = []
    for entry in MENU:
        if not may_open(entry):
            continue
        reason = entry.reason(state)
        url = None
        if reason is None:
            url = reverse(f"{NAMESPACE}:{entry.name}")
        page = PAGES[entry.name]
        items.append(MenuItem(page.section, entry.name, page.label, url, reason))
    return items


# Admin routes that are not navigable pages: form actions, downloads, images,
# JSON/fragment endpoints, sign-in and the setup wizard (which has its own
# stepper and is the only navigation until setup completes). Kept explicit so
# a test can require every Admin route to be classified one way or the other.
NON_PAGES = frozenset(
    {
        "background_counts",
        "background_task",
        "background_task_status",
        "background_tasks",
        "automation_notices",
        "automation_sessions",
        "branding_asset",
        # The Campaign comparison's download form (#1018), a POST.
        "campaign_comparison_export",
        # The figures status the Response dashboard and Campaign comparison
        # poll (#1586), a fragment.
        "response_dashboard_status",
        # A Campaign setup step's Continue (#1404), a POST that redirects.
        "campaign_step_continue",
        "content_plain_text",
        "critical_events_acknowledge",
        "daily_digest_chart",
        "daily_digest_download",
        # The Mail message page's Report a bounce form (#806), a POST.
        "delivery_bounce_report",
        "delivery_refusal_clear",
        "dismiss_credential_result",
        "delivery_resolve",
        "family_email_progress_status",
        # The header's presence count, polled at the old presence address.
        "presence_count",
        "timeline_export",
        "family_directory_export",
        # Open form's hand-off (#529): a POST that opens the Family form.
        "family_open_form",
        # The header's Find a Family results (#561), a fragment for its box.
        "find_family",
        "hosted_file_upload",
        # Past campaigns' Review (the upload), Apply and Remove (#1018).
        "past_campaign_review",
        "past_campaign_import",
        "past_campaign_removal",
        "past_campaign_archive",
        "financial_export",
        "information_export",
        "information_update",
        "integration_status",
        "login",
        "logout",
        "logs_export",
        "limitations_export",
        "census_changes_export",
        "maintenance",
        "ministry_followup_export",
        "ministry_followup_update",
        "ministry_followup_bulk",
        "ministry_followup_roster",
        "ministry_roster_export",
        "ministry_roster_bulk",
        "ministry_packet",
        "pledges_to_enter_export",
        # The ParishSoft refresh schedule editor's live check (#632), a
        # fragment the settings page reads.
        "refresh_schedule_check",
        # Campaign settings' live end-date check (#944), a fragment the
        # page reads.
        "campaign_end_date_check",
        # New and Edit scheduled email's test of an unsaved edit (#446), a
        # POST the page script makes.
        "schedule_email_test",
        "report_export_cancel",
        "report_export_create",
        "report_export_download",
        "report_export_retry",
        "retry_daily_digest",
        "retry_export_cleanup",
        "retry_family_preparation",
        "retry_weekly_digest",
        "security_event_acknowledge",
        # New user's in-place Email check and Name suggestions (#952), JSON.
        "user_name_suggestions",
        "session_renew",
        "session_status",
        "setup",
        "setup_branding",
        "setup_branding_asset",
        "setup_cancel",
        "setup_confirmation",
        "setup_credential",
        "setup_mail",
        "setup_mail_status",
        "setup_notification",
        "setup_notification_status",
        "setup_preview",
        "setup_source",
        "setup_source_progress",
        "setup_step",
        # Menu group roots only redirect to the group's first open entry.
        "campaign_root",
        "mail_root",
        "parish_root",
        # /admin/system/ only redirects to System health; the page polls its
        # status fragment.
        "system",
        "system_health_status",
    }
)


# Each page's URL without its trailing slash (the URL scheme, #525): it only
# redirects to the page, so it is neither a page nor a non-page.
SLASHLESS = frozenset(SLASHLESS_TARGETS)


# Multi-step flows: each is an ordered tuple of (step key, label). A view
# names its flow and current step with ``place``; the Admin layout shows the
# steps under the breadcrumb trail. The indicator is orientation only: it
# links nothing, so it can never skip a review or confirmation.
FLOWS = {
    # Every settings editor: edit a form, review the exact change, apply it.
    "change": (
        ("edit", _("Make changes")),
        ("review", _("Review")),
        ("apply", _("Apply")),
    ),
    # Sending one email to chosen real Families (Testing recipient only).
    "family_test": (
        ("choose", _("Choose Families")),
        ("review", _("Review")),
        ("send", _("Send and follow")),
    ),
    # Going live: check readiness, clean up Testing data, prepare Family
    # links, confirm Production, then follow its activation.
    "go_live": (
        ("readiness", _("Check readiness")),
        ("cleanup", _("Testing cleanup")),
        ("links", _("Family links")),
        ("confirm", _("Confirm Production")),
        ("activate", _("Activation")),
    ),
    # A report export: request it from a report, wait for it, download it.
    "export": (
        ("request", _("Choose report")),
        ("prepare", _("Prepare file")),
        ("download", _("Download")),
    ),
}

# The session remembers, per sign-in, which page each recent configuration
# change was confirmed on, so its status page can lead back there. Session
# data, never a query parameter: nothing a link carries can steer the trail.
ORIGINS_KEY = "pk_admin_change_origins"
ORIGINS_KEPT = 20
PLACEMENT_ATTRIBUTE = "_stewardship_admin_placement"


def place(request, **values):
    """Record the view's ``Placement`` for the Admin layout to render."""
    setattr(request, PLACEMENT_ATTRIBUTE, Placement(**values))


def placement(request):
    """The placement a view recorded for this request, or None.

    An error page ignores it: a view may record its step and then fail a
    later access recheck, and the refusal must not show that step.
    """
    if getattr(request, ERROR_PAGE_ATTRIBUTE, False):
        return None
    return getattr(request, PLACEMENT_ATTRIBUTE, None)


def remember_origin(request, request_id, path=None):
    """Remember the page a configuration change was confirmed on.

    ``request.path`` is the editor's own URL (every editor confirms by
    posting to itself); an action that posts elsewhere, such as the
    scheduled emails table's Delete (#878), names its page as ``path``. Only
    the most recent changes are kept, so the session stays small.
    """
    session = getattr(request, "session", None)
    if session is None:
        return
    key = str(request_id)
    origins = {
        name: path for name, path in session.get(ORIGINS_KEY, {}).items() if name != key
    }
    origins[key] = path or request.path
    session[ORIGINS_KEY] = dict(list(origins.items())[-ORIGINS_KEPT:])


def change_origin(request, request_id):
    """``(url name, route arguments)`` of a change's remembered origin, or None.

    The stored path is resolved again and must still name a registered
    Admin page, so a stale or unexpected value only loses the trail.
    """
    session = getattr(request, "session", None)
    path = session.get(ORIGINS_KEY, {}).get(str(request_id)) if session else None
    if not isinstance(path, str):
        return None
    try:
        match = resolve(path)
    except Resolver404:
        return None
    name = match.url_name
    if (
        match.namespace != NAMESPACE
        or name not in PAGES
        or name == "configuration_request"
    ):
        return None
    needed = route_parameters().get(name, ())
    return name, {key: match.kwargs[key] for key in needed if key in match.kwargs}


@cache
def route_parameters():
    """Map each Admin URL name to the argument names its route needs.

    Read once from the resolver so ancestor links can be reversed from the
    current request's resolved arguments without per-page configuration.
    """
    admin = next(
        pattern
        for pattern in get_resolver().url_patterns
        if getattr(pattern, "namespace", None) == NAMESPACE
    )
    return {
        pattern.name: tuple(re.findall(r"<(?:\w+:)?(\w+)>", str(pattern.pattern)))
        for pattern in admin.url_patterns
        if getattr(pattern, "name", None)
    }


def _sidebar_page(name):
    """Whether a page is a menu entry."""
    return name in MENU_NAMES


def _link(name, arguments, offered=None):
    """Reverse an Admin page with the arguments it needs, or None if unavailable.

    ``offered`` maps each entry the viewer's sidebar offers now to its URL.
    A menu page is linked only when the sidebar offers that exact URL: the
    sidebar already greys out entries their page would refuse (Share options
    once the campaign is locked, Campaign images for an archived campaign,
    Go-live readiness after the draft), so a trail or "Return to" link
    reuses that decision instead of repeating it (#196). A page standing for
    an entry (``Page.entry``) is linked only while that entry is offered. None
    skips the check.
    """
    if not PAGES[name].linkable:
        return None
    needed = route_parameters().get(name, ())
    if any(parameter not in arguments for parameter in needed):
        return None
    try:
        url = reverse(
            f"{NAMESPACE}:{name}",
            kwargs={parameter: arguments[parameter] for parameter in needed},
        )
    except NoReverseMatch:
        return None
    if offered is not None:
        if _sidebar_page(name) and offered.get(name) != url:
            return None
        if PAGES[name].entry and PAGES[name].entry not in offered:
            return None
    return url


def _chain(name, parent=None):
    """The page and its ancestors, root first; ``parent`` overrides the first."""
    chain = []
    seen = set()
    while name in PAGES and name not in seen:
        seen.add(name)
        chain.append(name)
        name = parent if parent and len(chain) == 1 else PAGES[name].parent
    return list(reversed(chain))


def _section(chain):
    """The section of a page chain: that of its first sectioned page.

    Registered chains share one section. A page registered under Home but
    placed below a sectioned page (a configuration change's status) joins
    that page's section.
    """
    return next((PAGES[name].section for name in chain if PAGES[name].section), None)


def _resolved(match, placed):
    """``(url name, page chain, route arguments)`` for the current request.

    Placement arguments win over the request's own: they name the
    ancestors' identifiers (an origin page's request, say), and the current
    page's crumb is never linked.
    """
    name = match.url_name if match and match.namespace == NAMESPACE else None
    arguments = dict(match.kwargs) if match else {}
    if placed:
        arguments |= placed.arguments
    parent = placed.parent if placed and placed.parent in PAGES else None
    chain = _chain(name, parent) if name in PAGES else []
    return name, chain, arguments


def steps(placed):
    """The step indicator for a placed flow: label and done/current/upcoming.

    Returns an empty list when the request names no known flow and step.
    """
    flow = FLOWS.get(placed.flow) if placed else None
    keys = [key for key, _label in flow or ()]
    if placed is None or placed.step not in keys:
        return []
    index = keys.index(placed.step)
    return [
        {
            "label": label,
            "state": "done"
            if position < index
            else "current"
            if position == index
            else "upcoming",
        }
        for position, (_key, label) in enumerate(flow)
    ]


def back(match, placed=None, items=None):
    """``{"label", "url"}`` of the nearest linked ancestor, falling back to Home.

    Multi-step pages use it for their "Return to …" link, so the link and
    the trail always agree about where the flow started. ``items`` are the
    sidebar entries, as for ``build``.
    """
    _name, chain, arguments = _resolved(match, placed)
    labels = placed.labels if placed else {}
    queries = placed.queries if placed else {}
    offered = _offered(items)
    for ancestor in reversed(chain[:-1]):
        url = _with_query(_link(ancestor, arguments, offered), queries.get(ancestor))
        if url:
            return {"label": labels.get(ancestor, PAGES[ancestor].label), "url": url}
    return {"label": PAGES["index"].label, "url": reverse(f"{NAMESPACE}:index")}


def build(match, items, placed=None):
    """Return ``(sections, breadcrumbs)`` for the current request.

    ``match`` is the request's resolver match (or None), and ``items`` is the
    ordered list of ``MenuItem`` entries the actor may see (``menu``), already
    filtered by the caller's capability checks; an unavailable one has no URL
    and carries its reason. Groups with no entry for the viewer are omitted.
    The entry whose page chain contains the current page is marked current,
    and so is its group, which the menu then always shows open. ``placed`` is
    the view's optional ``Placement``.
    """
    name, chain, arguments = _resolved(match, placed)
    current_item = next(
        (
            entry_name
            for entry_name in reversed(chain)
            if any(entry[1] == entry_name for entry in items)
        ),
        None,
    )
    current_section = _section(chain)
    sections = []
    for section in SECTIONS:
        entries = [
            {
                "name": item.name,
                "url": item.url,
                "label": item.label,
                "reason": item.reason,
                # "page" for the page itself; "true" when the entry is only
                # the nearest listed ancestor of the page being viewed.
                "current": (
                    ("page" if item.name == name else "true")
                    if item.name == current_item
                    else None
                ),
            }
            for item in map(_item, items)
            if item.section == section.key
        ]
        if entries:
            sections.append(
                {
                    "key": section.key,
                    "label": section.label,
                    "current": section.key == current_section,
                    "items": entries,
                }
            )
    labels = placed.labels if placed else {}
    queries = placed.queries if placed else {}
    return sections, _breadcrumbs(
        name, chain, arguments, sections, labels, _offered(items), queries
    )


def _with_query(url, query):
    """``url`` with ``query`` appended, or as it is when either is empty."""
    return f"{url}?{query}" if url and query else url


def _item(entry):
    """A ``MenuItem`` from a menu row, accepting a plain tuple as well."""
    return entry if isinstance(entry, MenuItem) else MenuItem(*entry)


def _offered(items):
    """``{entry name: URL}`` of the available menu entries, or None if not given."""
    if items is None:
        return None
    return {item.name: item.url for item in map(_item, items) if item.url}


def _breadcrumbs(name, chain, arguments, sections, labels, offered=None, queries=None):
    """Home, the section, then each ancestor page, ending at the current page."""
    if name is None or name not in PAGES:
        return []
    trail = [{"label": PAGES["index"].label, "url": reverse(f"{NAMESPACE}:index")}]
    if name == "index":
        trail[0]["url"] = None
        return trail
    section = _section(chain)
    if section:
        # Link the group to its first entry the actor may open now, if any.
        first = next(
            (
                item["url"]
                for entry in sections
                if entry["key"] == section
                for item in entry["items"]
                if item["url"]
            ),
            None,
        )
        trail.append({"label": SECTION_LABELS[section], "url": first})
    for ancestor in chain[:-1]:
        trail.append(
            {
                "label": labels.get(ancestor, PAGES[ancestor].label),
                "url": _with_query(
                    _link(ancestor, arguments, offered), (queries or {}).get(ancestor)
                ),
            }
        )
    trail.append({"label": PAGES[name].label, "url": None})
    return trail

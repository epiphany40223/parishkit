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
current request's resolved arguments; it never queries the database, so it
adds nothing to the Admin chrome's query budget.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from typing import NamedTuple

from django.urls import NoReverseMatch, Resolver404, get_resolver, resolve, reverse
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.admin_urls.legacy import TARGETS as LEGACY_TARGETS
from parishkit.stewardship.campaigns.domain import CampaignState
from parishkit.stewardship.campaigns.lifecycle import structural_edit_admitted
from parishkit.stewardship.web.error_pages import ERROR_PAGE_ATTRIBUTE

from .policy import Capability

NAMESPACE = "admin"


@dataclass(frozen=True)
class Section:
    """A menu group: a collapsible group in the sidebar and the second breadcrumb."""

    key: str
    label: str


@dataclass(frozen=True)
class Page:
    """One Admin page: its section, its parent page (URL name) and its label.

    ``linkable`` is False for a page a link cannot reliably reopen: one that
    only answers a POST (a review step), or one that reviews a single
    pending change and refuses once that change is confirmed (a staged
    image, a new campaign). Its crumb is shown for orientation but never
    linked, and "Return to" skips it, because a GET would fail.
    """

    section: str | None
    label: str
    parent: str | None = None
    linkable: bool = True


@dataclass(frozen=True)
class Placement:
    """Where a view says the current request sits, beyond its static route.

    A view knows things its route does not: which email a test page sends,
    which report an export came from, which settings page a configuration
    change was confirmed on. ``parent`` replaces the registered parent,
    ``arguments`` supplies route arguments that ancestor links need,
    ``labels`` names ancestor pages more specifically (by URL name), and
    ``flow``/``step`` select a step indicator from ``FLOWS``. Presentation
    only: it grants nothing, and every linked page rechecks its own access.
    """

    parent: str | None = None
    arguments: dict = field(default_factory=dict)
    labels: dict = field(default_factory=dict)
    flow: str | None = None
    step: str | None = None


# The menu groups, in the order a campaign runs: set it up, send it, read the
# responses, then the parish data, users and system pages that change rarely.
# Home sits above them (admin-portal spec, "Menu groups").
SECTIONS = (
    Section("campaign", _("Campaign setup")),
    Section("mail", _("Mail and Family portal")),
    Section("reports", _("Responses and reports")),
    Section("parish", _("Parish data")),
    Section("users", _("Users and access")),
    Section("system", _("System")),
)
SECTION_LABELS = {section.key: section.label for section in SECTIONS}

# Every Admin HTML page, keyed by URL name. Menu entries are listed in MENU
# below; a page without a parent that is not a menu entry (a redirect, a
# digest) stands alone under its group (or, with no group, under Home). A
# view may override the last crumb's label with a ``breadcrumb_label``
# context variable, e.g. the name of the email being edited.
PAGES = {
    "index": Page(None, _("Home")),
    # Campaign
    "campaign_settings": Page("campaign", _("Campaign settings")),
    # Copy campaign is refused until the single-campaign change (#145), so a
    # change's status page names it but never links it. (New campaign is
    # retired outright; its old address is a non-page redirect below.)
    "campaign_clone": Page(
        "campaign", _("Copy campaign"), "campaign_settings", linkable=False
    ),
    "content_catalog": Page("campaign", _("Pages and emails")),
    "content_edit": Page("campaign", _("Edit page or email"), "content_catalog"),
    "content_revision": Page("campaign", _("Content revision"), "content_catalog"),
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
    "share_settings": Page("campaign", _("Share options")),
    "artwork_settings": Page("campaign", _("Campaign images")),
    # A POST-only re-render of Campaign images with the upload's error, so it
    # shares that page's name.
    "artwork_upload": Page("campaign", _("Campaign images"), "artwork_settings"),
    # Each reviews one staged or current image and refuses once confirmed.
    "artwork_preview": Page(
        "campaign", _("Review campaign image"), "artwork_settings", linkable=False
    ),
    "artwork_remove": Page(
        "campaign", _("Remove campaign image"), "artwork_settings", linkable=False
    ),
    "talent_settings": Page("campaign", _("Member talents")),
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
    "deliveries": Page("mail", _("Outgoing mail")),
    "delivery": Page("mail", _("Mail message"), "deliveries"),
    "delivery_refusals": Page("mail", _("Refused addresses"), "deliveries"),
    "delivery_refusal": Page("mail", _("Refused address"), "delivery_refusals"),
    "family_portal": Page("mail", _("Family portal availability")),
    "presence": Page("mail", _("Families on the form now")),
    # Responses and reports. Every report is a menu entry of its own. The
    # two report roots only redirect to the current campaign's report (or
    # show that there is none), so they stand alone, outside the menu.
    "reports": Page("reports", _("Participation")),
    "participation": Page("reports", _("Participation")),
    "financial_report": Page("reports", _("Financial stewardship")),
    "talents_report": Page("reports", _("Talents and limitations")),
    "census_changes": Page("reports", _("Census changes")),
    "response_dashboard": Page("reports", _("Response dashboard")),
    "response_list": Page("reports", _("Response list"), "response_dashboard"),
    "information_queue": Page("reports", _("Additional information")),
    "information_item": Page("reports", _("Information request"), "information_queue"),
    "report_export": Page("reports", _("Report export"), "reports"),
    "report_exact": Page("reports", _("Latest-data export"), "reports"),
    "weekly_digest_manual": Page("reports", _("Send a weekly report now")),
    # The emailed reports stand alone under the group until NAV-14 gives
    # them the Emailed reports page; the reports root is Participation now,
    # which they do not belong under.
    "weekly_digest_snapshot": Page("reports", _("Weekly report")),
    "weekly_digest_item": Page(
        "reports", _("Weekly report item"), "weekly_digest_snapshot"
    ),
    "daily_digest_snapshot": Page("reports", _("Daily report")),
    "ministry_reports": Page("reports", _("Ministry requests")),
    "ministry_report": Page("reports", _("Ministry requests")),
    "ministry_joiners": Page("reports", _("Members joining"), "ministry_report"),
    "ministry_leavers": Page("reports", _("Members leaving"), "ministry_report"),
    "ministry_followup": Page("reports", _("Ministry follow-up")),
    "ministry_followup_item": Page(
        "reports", _("Follow-up request"), "ministry_followup"
    ),
    "family_directory": Page("reports", _("Family directory")),
    "family_codes": Page("reports", _("Family campaign codes"), "family_directory"),
    # One Family's summary and timeline (#477), opened from its directory row.
    "family_timeline": Page("reports", _("Family timeline"), "family_directory"),
    # Parish data
    "parish_settings": Page("parish", _("Parish settings")),
    "branding_settings": Page("parish", _("Parish logos")),
    # Reviews one staged logo and refuses once it is chosen.
    "branding_preview": Page(
        "parish", _("Review parish logos"), "branding_settings", linkable=False
    ),
    "hosted_files": Page("parish", _("Hosted files")),
    "hosted_file_delete": Page("parish", _("Delete hosted files"), "hosted_files"),
    "hosted_file_rename": Page("parish", _("Change placeholder name"), "hosted_files"),
    "ministries": Page("parish", _("Ministries")),
    # A live campaign's one editable structural setting (#342). One home per
    # concept: it runs through Ministries; Campaign settings keeps a link.
    "campaign_ministries": Page("parish", _("Campaign Ministries"), "ministries"),
    # A sidebar entry of its own, so a manual refresh is found without Home.
    "source_refresh": Page("parish", _("Refresh from ParishSoft")),
    # A configuration change can come from any settings page, so its status
    # page is registered under Home; the view places it under the page the
    # change was confirmed on when this sign-in remembers it.
    "configuration_request": Page(None, _("Change status")),
    # Users
    # Keeps its name until NAV-15 splits it into Sign-in rules, Ministry
    # assignments and Chairpersons; renaming the combined page earlier would
    # mislabel its other tables.
    "users": Page("users", _("Portal users")),
    # Only the review of a change started on Portal users (a POST from that
    # page) renders at these routes, so trails name them but never link them.
    "user_rules": Page("users", _("Review sign-in rules"), "users", linkable=False),
    "rule_request": Page("users", _("Rule change"), "user_rules"),
    "chair_confirmations": Page(
        "users", _("Review Chairperson suggestion"), "users", linkable=False
    ),
    "chair_reviews": Page(
        "users", _("Review Chairperson decision"), "users", linkable=False
    ),
    "assignments": Page(
        "users", _("Review Ministry assignment"), "users", linkable=False
    ),
    # The Administrator's own automation sessions (ADM-11), and the approval
    # of a pending one, which the command line links to.
    "automation_access": Page("users", _("Automation access")),
    "automation_approval": Page(
        "users", _("Approve an automation session"), "automation_access"
    ),
    # System
    "system_health": Page("system", _("System health")),
    "integrations": Page("system", _("Integrations")),
    "integration_settings": Page("system", _("Integration"), "integrations"),
    # A key's status and its Finish switching page sit under the integration
    # the key belongs to; their views supply the target the routes lack. The
    # status is readable only by the Administrator who saved the key, so
    # Finish switching (open to every Administrator) never runs through it.
    # Finish switching is a one-time review that needs a fresh Google
    # sign-in, so a switch's status page names it but returns to the
    # integration.
    "credential_status": Page(
        "system", _("Key replacement status"), "integration_settings"
    ),
    "select_credential": Page(
        "system",
        _("Finish switching to the new key"),
        "integration_settings",
        linkable=False,
    ),
    "background": Page("system", _("Background work")),
    "background_task_page": Page("system", _("Background task"), "background"),
    "logs": Page("system", _("System logs")),
}


class MenuState(NamedTuple):
    """What a menu entry's reason check may read: the chrome's campaign and mode.

    ``campaign`` is the current campaign row (or None), already loaded with
    its active configuration by ``portal_chrome``, and ``mode`` is the
    system's "testing" or "production". Reason checks read nothing else, so
    building the menu runs no query.
    """

    campaign: object
    mode: str


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


NO_CAMPAIGN = _("No current campaign")


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
        return _("The campaign is archived")
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


FINANCIAL = _module(
    "financial", _("This campaign does not include Financial stewardship")
)
MINISTRY = _module("ministry", _("This campaign does not include Ministry stewardship"))
CENSUS = _module("census", _("This campaign does not include the census"))


def _structural(module_reason):
    """A reason check for Share options and Member talents.

    Both edit the campaign's structure, which is open only on an unlocked
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
        return _("Can be changed only in Testing mode, before the campaign goes live")

    return reason


def _draft(state):
    """Go-live readiness: only a draft campaign can go live."""
    if not state.campaign:
        return NO_CAMPAIGN
    if state.campaign.state != "draft":
        return _("Only a draft campaign can go live; this one already has")
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
        return _(
            "Production has not been confirmed for this campaign; "
            "start at Go-live readiness"
        )
    return None


def _production(state):
    """Pause and resume mail acts on real mail, so Production only."""
    if not state.campaign:
        return NO_CAMPAIGN
    return None if state.mode == "production" else _("Available in Production mode")


@dataclass(frozen=True)
class Entry:
    """One menu entry: the page it opens, who may see it and when it is unavailable.

    ``capability`` is the capability the page itself checks, so the menu and
    the page cannot disagree (the menu is still not the security boundary).
    ``scoped`` marks a Ministry-scoped capability, which a Ministry leader
    holds through an assigned Ministry. ``campaign`` marks a route that names
    the current campaign; its ``reason`` must refuse when there is none.
    ``reason`` returns why the entry is unavailable right now, or None.
    """

    name: str
    capability: Capability
    reason: Callable = _never
    campaign: bool = False
    scoped: bool = False


# The menu, in order within each group (admin-portal spec, "Menu groups").
# Labels come from PAGES, so the menu, the trail and the page share a name.
# Entries for pages that do not exist yet are added with those pages:
# Emailed reports, Ministry assignments and Chairpersons (ADM-12).
_ADMIN = Capability.CONFIGURE
MENU = (
    # Campaign setup
    Entry("campaign_settings", _ADMIN, _campaign),
    Entry("content_catalog", _ADMIN, _unarchived),
    # Theme artwork (#248) stays editable while the campaign runs.
    Entry("artwork_settings", _ADMIN, _unarchived),
    Entry("schedule_settings", _ADMIN, _campaign),
    Entry("share_settings", _ADMIN, _structural(FINANCIAL)),
    Entry("talent_settings", _ADMIN, _structural(MINISTRY)),
    Entry("go_live", _ADMIN, _draft),
    Entry("production_progress", _ADMIN, _confirmed),
    # Mail and Family portal
    Entry("delivery_control", _ADMIN, _production),
    Entry("family_email_progress", _ADMIN, _campaign),
    Entry("family_email_sends", _ADMIN, _campaign),
    Entry("deliveries", _ADMIN),
    Entry("family_portal", _ADMIN),
    Entry("presence", _ADMIN),
    # Responses and reports
    Entry("response_dashboard", Capability.CAMPAIGN_REPORT, _campaign, campaign=True),
    Entry("participation", Capability.CAMPAIGN_REPORT, _campaign, campaign=True),
    Entry("financial_report", Capability.FINANCIAL_DETAIL, FINANCIAL, campaign=True),
    Entry("talents_report", Capability.CAMPAIGN_REPORT, MINISTRY, campaign=True),
    Entry("census_changes", Capability.VIEW_CENSUS, CENSUS, campaign=True),
    Entry(
        "information_queue", Capability.ADDITIONAL_FOLLOWUP, _campaign, campaign=True
    ),
    Entry(
        "ministry_report",
        Capability.MINISTRY_REPORT,
        MINISTRY,
        campaign=True,
        scoped=True,
    ),
    Entry(
        "ministry_followup",
        Capability.MINISTRY_FOLLOWUP,
        MINISTRY,
        campaign=True,
        scoped=True,
    ),
    Entry("family_directory", Capability.FAMILY_CODES, _campaign, campaign=True),
    # Moves to the Emailed reports page with NAV-14; until then it keeps its
    # menu entry so it stays reachable.
    Entry("weekly_digest_manual", _ADMIN, _campaign, campaign=True),
    # Parish data
    Entry("parish_settings", _ADMIN),
    Entry("branding_settings", _ADMIN),
    Entry("ministries", _ADMIN),
    Entry("hosted_files", _ADMIN),
    Entry("source_refresh", _ADMIN),
    # Users and access
    Entry("users", Capability.MANAGE_USERS),
    Entry("automation_access", _ADMIN),
    # System. System health comes first, so /admin/system/ opens it (ADM-13).
    Entry("system_health", Capability.SYSTEM_LOGS),
    Entry("integrations", _ADMIN),
    Entry("background", _ADMIN),
    Entry("logs", Capability.SYSTEM_LOGS),
)
MENU_NAMES = frozenset(entry.name for entry in MENU)


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
            arguments = [state.campaign.pk] if entry.campaign else []
            url = reverse(f"{NAMESPACE}:{entry.name}", args=arguments)
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
        "automation_session",
        "branding_asset",
        "content_plain_text",
        "critical_events_acknowledge",
        "daily_digest_chart",
        "daily_digest_download",
        "delivery_refusal_clear",
        "dismiss_credential_result",
        "delivery_resolve",
        "exact_export_cancel",
        "exact_export_create",
        "exact_export_retry",
        "exact_export_status",
        "family_email_progress_status",
        # The header's presence count, polled at the old presence address.
        "presence_count",
        "export_cancel",
        "export_create",
        "export_download",
        "export_download_grant",
        "export_status",
        "family_directory_export",
        # The header's Find a Family results (#561), a fragment for its box.
        "find_family",
        "hosted_file_upload",
        "financial_export",
        "information_export",
        "information_update",
        "integration_status",
        "login",
        "logout",
        "logs_export",
        "talents_export",
        "census_changes_export",
        "response_list_export",
        "maintenance",
        "ministry_export",
        "ministry_followup_update",
        "ministry_packet",
        "participation_chart",
        # Retired multi-campaign addresses (navigation rule 10, decision 11):
        # New campaign and the two campaign choosers only redirect.
        "campaign_new",
        "report_campaigns",
        "ministry_report_campaigns",
        # The old postal-outreach routes: a bookmark redirects to the Family
        # directory, and forms rendered before the merge still submit.
        "postal_directory",
        "postal_directory_export",
        "report_exact_cancel",
        "report_exact_create",
        "report_exact_retry",
        "report_export_cancel",
        "report_export_create",
        "report_export_download",
        "report_export_regenerate",
        "report_export_retry",
        "retry_daily_digest",
        "retry_export_cleanup",
        "retry_family_preparation",
        "retry_weekly_digest",
        "rule_apply",
        "rule_base",
        "security_event_acknowledge",
        "session_renew",
        "session_status",
        "setup",
        "setup_branding",
        "setup_branding_asset",
        "setup_campaign",
        "setup_cancel",
        "setup_confirmation",
        "setup_content",
        "setup_content_edit",
        "setup_credential",
        "setup_mail",
        "setup_mail_status",
        "setup_notification",
        "setup_notification_status",
        "setup_preview",
        "setup_schedules",
        "setup_shares",
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


# Old Admin addresses (the URL scheme, #525): each only redirects to the page
# that replaced it, so it is neither a page nor a non-page. ``LEGACY_TARGETS``
# maps each one to its new URL name.
LEGACY = frozenset(LEGACY_TARGETS)


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


def remember_origin(request, request_id):
    """Remember the page a configuration change was confirmed on.

    ``request.path`` is the editor's own URL (every editor confirms by
    posting to itself). Only the most recent changes are kept, so the
    session stays small.
    """
    session = getattr(request, "session", None)
    if session is None:
        return
    key = str(request_id)
    origins = {
        name: path for name, path in session.get(ORIGINS_KEY, {}).items() if name != key
    }
    origins[key] = request.path
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
    # A path remembered before its page moved resolves to the old address's
    # redirect; it names the same page, so follow it to the new one.
    name = LEGACY_TARGETS.get(match.url_name, match.url_name)
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

    ``offered`` is the set of URLs the viewer's sidebar offers now. A menu
    page is linked only when the sidebar offers that exact URL: the sidebar
    already greys out entries their page would refuse (Share options once
    the campaign is locked, Campaign images for an archived campaign,
    Go-live readiness after the draft), so a trail or "Return to" link
    reuses that decision instead of repeating it (#196). None skips the check.
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
    if offered is not None and _sidebar_page(name) and url not in offered:
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
    offered = _offered(items)
    for ancestor in reversed(chain[:-1]):
        url = _link(ancestor, arguments, offered)
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
    return sections, _breadcrumbs(
        name, chain, arguments, sections, labels, _offered(items)
    )


def _item(entry):
    """A ``MenuItem`` from a menu row, accepting a plain tuple as well."""
    return entry if isinstance(entry, MenuItem) else MenuItem(*entry)


def _offered(items):
    """The URLs of the available menu entries, or None when none were given."""
    if items is None:
        return None
    return {item.url for item in map(_item, items) if item.url}


def _breadcrumbs(name, chain, arguments, sections, labels, offered=None):
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
                "url": _link(ancestor, arguments, offered),
            }
        )
    trail.append({"label": PAGES[name].label, "url": None})
    return trail

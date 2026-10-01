"""Declarative Admin portal navigation: sections, sidebar items and breadcrumbs.

One registry describes every Admin page: which section it belongs to, its
parent page and its label. The sidebar and the breadcrumb trail are both built
from it, so the two cannot drift, and templates never hand-write breadcrumbs.

Everything here is presentation only. It grants no authority: each view still
checks its own capability, and ``portal_chrome`` passes in the same capability
decisions the pages use. Building navigation reverses URLs from static route
metadata and the current request's resolved arguments; it never queries the
database, so it adds nothing to the Admin chrome's query budget.
"""

import re
from dataclasses import dataclass, field
from functools import cache

from django.urls import NoReverseMatch, Resolver404, get_resolver, resolve, reverse
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.web.error_pages import ERROR_PAGE_ATTRIBUTE

NAMESPACE = "admin"


@dataclass(frozen=True)
class Section:
    """A top-level group in the sidebar and the second breadcrumb."""

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


SECTIONS = (
    Section("campaign", _("Campaign")),
    Section("reports", _("Reports")),
    Section("parish", _("Parish and integrations")),
    Section("users", _("Users")),
    Section("system", _("System")),
)
SECTION_LABELS = {section.key: section.label for section in SECTIONS}

# Every Admin HTML page, keyed by URL name. Pages without a parent are the
# sidebar entries of their section (or, with no section, stand alone under
# Home). A view may override the last crumb's label with a ``breadcrumb_label``
# context variable, e.g. the name of the email being edited.
PAGES = {
    "index": Page(None, _("Home")),
    # Campaign
    "campaign_settings": Page("campaign", _("Campaign settings")),
    # Creating or copying a campaign is refused once a campaign is current,
    # so a change's status page names these editors but never links them.
    "campaign_new": Page("campaign", _("New campaign"), linkable=False),
    "campaign_clone": Page(
        "campaign", _("Copy campaign"), "campaign_settings", linkable=False
    ),
    "content_catalog": Page("campaign", _("Pages and emails")),
    "content_edit": Page("campaign", _("Edit page or email"), "content_catalog"),
    "content_revision": Page("campaign", _("Content revision"), "content_catalog"),
    # Retained content can belong to an earlier campaign, which the editing
    # catalog refuses, so its trail runs through Campaign settings instead.
    "content_history": Page("campaign", _("Content history"), "campaign_settings"),
    "content_history_revision": Page("campaign", _("Revision"), "content_history"),
    # A test page sends one email revision, so its trail runs through that
    # revision's editor; the view supplies the kind and slot the route lacks.
    "campaign_mail": Page("campaign", _("Preview and test email"), "content_revision"),
    "campaign_mail_families": Page(
        "campaign", _("Send to chosen Families"), "campaign_mail"
    ),
    "schedule_settings": Page("campaign", _("Mail schedules")),
    "share_settings": Page("campaign", _("Share options")),
    "artwork_settings": Page("campaign", _("Campaign images")),
    "artwork_upload": Page("campaign", _("Campaign image upload"), "artwork_settings"),
    # Each reviews one staged or current image and refuses once confirmed.
    "artwork_preview": Page(
        "campaign", _("Review campaign image"), "artwork_settings", linkable=False
    ),
    "artwork_remove": Page(
        "campaign", _("Remove campaign image"), "artwork_settings", linkable=False
    ),
    "talent_settings": Page("campaign", _("Member talents")),
    # A live campaign's one editable structural setting (#342).
    "campaign_ministries": Page(
        "campaign", _("Campaign Ministries"), "campaign_settings"
    ),
    "go_live": Page("campaign", _("Go-live readiness")),
    "go_live_families": Page("campaign", _("Testing Families"), "go_live"),
    "go_live_cleanup": Page("campaign", _("Testing cleanup"), "go_live"),
    "go_live_links": Page("campaign", _("Family links"), "go_live_cleanup"),
    "production_confirmation": Page(
        "campaign", _("Confirm Production"), "go_live_links"
    ),
    "production_progress": Page(
        "campaign", _("Production activation"), "campaign_settings"
    ),
    "production_withdrawal": Page(
        "campaign", _("Return to Testing"), "campaign_settings"
    ),
    "delivery_control": Page("campaign", _("Delivery controls")),
    # Watching a launch or reminder send (#413): a sidebar entry of its own,
    # beside Delivery controls, so it is found without knowing where it is.
    "family_email_progress": Page("campaign", _("Family email progress")),
    # Reports
    "reports": Page("reports", _("Campaign reports")),
    "report_campaigns": Page("reports", _("Choose a campaign"), "reports"),
    "participation": Page("reports", _("Participation"), "reports"),
    "financial_report": Page("reports", _("Financial report"), "reports"),
    "talents_report": Page("reports", _("Talents and limitations"), "reports"),
    "information_queue": Page("reports", _("Additional information"), "reports"),
    "information_item": Page("reports", _("Information item"), "information_queue"),
    "report_export": Page("reports", _("Report export"), "reports"),
    "report_exact": Page("reports", _("Exact export"), "reports"),
    "weekly_digest_manual": Page("reports", _("Manual information report")),
    "weekly_digest_snapshot": Page("reports", _("Weekly summary"), "reports"),
    "weekly_digest_item": Page(
        "reports", _("Weekly summary item"), "weekly_digest_snapshot"
    ),
    "daily_digest_snapshot": Page("reports", _("Daily report"), "reports"),
    "ministry_reports": Page("reports", _("Ministry reports")),
    "ministry_report_campaigns": Page(
        "reports", _("Choose a campaign"), "ministry_reports"
    ),
    "ministry_report": Page("reports", _("Ministry report"), "ministry_reports"),
    "ministry_joiners": Page("reports", _("Joining"), "ministry_report"),
    "ministry_leavers": Page("reports", _("Leaving"), "ministry_report"),
    "ministry_followup": Page("reports", _("Follow-up"), "ministry_report"),
    "ministry_followup_item": Page(
        "reports", _("Follow-up request"), "ministry_followup"
    ),
    "family_directory": Page("reports", _("Family directory")),
    "family_codes": Page("reports", _("Family campaign codes"), "family_directory"),
    # Parish and integrations
    "parish_settings": Page("parish", _("Parish settings")),
    "branding_settings": Page("parish", _("Parish logos")),
    # Reviews one staged logo and refuses once it is chosen.
    "branding_preview": Page(
        "parish", _("Logo preview"), "branding_settings", linkable=False
    ),
    "hosted_files": Page("parish", _("Hosted files")),
    "hosted_file_delete": Page("parish", _("Delete hosted files"), "hosted_files"),
    "hosted_file_rename": Page("parish", _("Change placeholder name"), "hosted_files"),
    "integrations": Page("parish", _("Integrations")),
    "integration_settings": Page("parish", _("Integration"), "integrations"),
    # A key's status and its Finish switching page sit under the integration
    # the key belongs to; their views supply the target the routes lack. The
    # status is readable only by the Administrator who saved the key, so
    # Finish switching (open to every Administrator) never runs through it.
    # Finish switching is a one-time review that needs a fresh Google
    # sign-in, so a switch's status page names it but returns to the
    # integration.
    "credential_status": Page(
        "parish", _("Key replacement status"), "integration_settings"
    ),
    "select_credential": Page(
        "parish",
        _("Finish switching to the new key"),
        "integration_settings",
        linkable=False,
    ),
    "ministries": Page("parish", _("Ministry activity")),
    # A sidebar entry of its own, so a manual refresh is found without Home.
    "source_refresh": Page("parish", _("ParishSoft refresh")),
    # A configuration change can come from any settings page, so its status
    # page is registered under Home; the view places it under the page the
    # change was confirmed on when this sign-in remembers it.
    "configuration_request": Page(None, _("Configuration change")),
    # Users
    "users": Page("users", _("Portal users")),
    # Only the review of a change started on Portal users (a POST from that
    # page) renders at these routes, so trails name them but never link them.
    "user_rules": Page("users", _("Sign-in rules"), "users", linkable=False),
    "rule_request": Page("users", _("Rule change"), "user_rules"),
    "chair_confirmations": Page(
        "users", _("Chair suggestions"), "users", linkable=False
    ),
    "chair_reviews": Page("users", _("Chair reviews"), "users", linkable=False),
    "assignments": Page("users", _("Assignments"), "users", linkable=False),
    # System
    "background": Page("system", _("Background work")),
    "background_task_page": Page("system", _("Background task"), "background"),
    "deliveries": Page("system", _("Outgoing mail")),
    "delivery": Page("system", _("Message"), "deliveries"),
    "delivery_refusals": Page("system", _("Refused addresses"), "deliveries"),
    "delivery_refusal": Page("system", _("Refused address"), "delivery_refusals"),
    "logs": Page("system", _("System logs")),
    "presence": Page("system", _("Families on the form now")),
    "family_portal": Page("system", _("Family portal availability")),
}

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
        "export_cancel",
        "export_create",
        "export_download",
        "export_download_grant",
        "export_status",
        "family_directory_export",
        "hosted_file_upload",
        "financial_export",
        "information_export",
        "information_update",
        "integration_status",
        "login",
        "logout",
        "logs_export",
        "talents_export",
        "maintenance",
        "ministry_export",
        "ministry_followup_assign",
        "ministry_followup_update",
        "ministry_packet",
        "participation_chart",
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
    }
)


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
    """Whether a page is a sidebar entry: a sectioned page with no parent."""
    return PAGES[name].section is not None and PAGES[name].parent is None


def _link(name, arguments, offered=None):
    """Reverse an Admin page with the arguments it needs, or None if unavailable.

    ``offered`` is the set of URLs the viewer's sidebar offers now. A sidebar
    page is linked only when the sidebar offers that exact URL: the sidebar
    already hides entries its page would refuse (Share options once the
    campaign is locked, Campaign images for a campaign no longer current,
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
    ordered list of ``(section, url_name, label, url)`` entries the actor may
    see, already filtered by the caller's capability checks. Sections with no
    visible entry are omitted. The entry whose page chain contains the current
    page is marked current, and so is its section. ``placed`` is the view's
    optional ``Placement``.
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
                "url": url,
                "label": label,
                # "page" for the page itself; "true" when the entry is only
                # the nearest listed ancestor of the page being viewed.
                "current": (
                    ("page" if entry_name == name else "true")
                    if entry_name == current_item
                    else None
                ),
            }
            for key, entry_name, label, url in items
            if key == section.key
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


def _offered(items):
    """The URLs of the sidebar entries, or None when none were given."""
    return None if items is None else {entry[3] for entry in items}


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
        # Link the section to its first entry the actor can see, if any.
        first = next(
            (entry["items"][0]["url"] for entry in sections if entry["key"] == section),
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

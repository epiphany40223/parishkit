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
from dataclasses import dataclass
from functools import cache

from django.urls import NoReverseMatch, get_resolver, reverse
from django.utils.translation import gettext_lazy as _

NAMESPACE = "admin"


@dataclass(frozen=True)
class Section:
    """A top-level group in the sidebar and the second breadcrumb."""

    key: str
    label: str


@dataclass(frozen=True)
class Page:
    """One Admin page: its section, its parent page (URL name) and its label."""

    section: str | None
    label: str
    parent: str | None = None


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
    "campaign_new": Page("campaign", _("New campaign")),
    "campaign_clone": Page("campaign", _("Copy campaign"), "campaign_settings"),
    "content_catalog": Page("campaign", _("Pages and emails")),
    "content_edit": Page("campaign", _("Edit page or email"), "content_catalog"),
    "content_revision": Page("campaign", _("Content revision"), "content_catalog"),
    # Retained content can belong to an earlier campaign, which the editing
    # catalog refuses, so its trail runs through Campaign settings instead.
    "content_history": Page("campaign", _("Content history"), "campaign_settings"),
    "content_history_revision": Page("campaign", _("Revision"), "content_history"),
    "campaign_mail": Page("campaign", _("Preview and test email"), "content_catalog"),
    "campaign_mail_families": Page(
        "campaign", _("Send to chosen Families"), "campaign_mail"
    ),
    "schedule_settings": Page("campaign", _("Mail schedules")),
    "share_settings": Page("campaign", _("Share options")),
    "talent_settings": Page("campaign", _("Member talents")),
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
    "family_directory": Page("reports", _("Family codes")),
    "postal_directory": Page("reports", _("Postal outreach")),
    "family_codes": Page("reports", _("Family campaign codes"), "family_directory"),
    # Parish and integrations
    "parish_settings": Page("parish", _("Parish settings")),
    "branding_settings": Page("parish", _("Parish logos")),
    "branding_preview": Page("parish", _("Logo preview"), "branding_settings"),
    "integrations": Page("parish", _("Integrations")),
    "integration_settings": Page("parish", _("Integration"), "integrations"),
    "replace_credential": Page(
        "parish", _("Replace credential"), "integration_settings"
    ),
    "credential_status": Page("parish", _("Credential change"), "integrations"),
    "select_credential": Page("parish", _("Choose credential"), "integrations"),
    "ministries": Page("parish", _("Ministry activity")),
    "source_refresh": Page("parish", _("ParishSoft refresh"), "integrations"),
    # A configuration change can come from any settings page, so its status
    # page stands alone under Home.
    "configuration_request": Page(None, _("Configuration change")),
    # Users
    "users": Page("users", _("Portal users")),
    "user_rules": Page("users", _("Sign-in rules"), "users"),
    "rule_request": Page("users", _("Rule change"), "user_rules"),
    "chair_confirmations": Page("users", _("Chair suggestions"), "users"),
    "chair_reviews": Page("users", _("Chair reviews"), "users"),
    "assignments": Page("users", _("Assignments"), "users"),
    # System
    "background": Page("system", _("Background work")),
    "background_task_page": Page("system", _("Background task"), "background"),
    "deliveries": Page("system", _("Outgoing mail")),
    "delivery": Page("system", _("Message"), "deliveries"),
    "delivery_refusals": Page("system", _("Refused addresses"), "deliveries"),
    "delivery_refusal": Page("system", _("Refused address"), "delivery_refusals"),
    "logs": Page("system", _("System logs")),
    "presence": Page("system", _("Families on the form now")),
}

# Admin routes that are not navigable pages: form actions, downloads, images,
# JSON/fragment endpoints, sign-in and the setup wizard (which has its own
# stepper and is the only navigation until setup completes). Kept explicit so
# a test can require every Admin route to be classified one way or the other.
NON_PAGES = frozenset(
    {
        "background_counts",
        "background_task",
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
        "export_cancel",
        "export_create",
        "export_download",
        "export_download_grant",
        "export_status",
        "family_directory_export",
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


def _link(name, arguments):
    """Reverse an Admin page with the arguments it needs, or None if unavailable."""
    needed = route_parameters().get(name, ())
    if any(parameter not in arguments for parameter in needed):
        return None
    try:
        return reverse(
            f"{NAMESPACE}:{name}",
            kwargs={parameter: arguments[parameter] for parameter in needed},
        )
    except NoReverseMatch:
        return None


def _chain(name):
    """The page and its ancestors, root first."""
    chain = []
    seen = set()
    while name in PAGES and name not in seen:
        seen.add(name)
        chain.append(name)
        name = PAGES[name].parent
    return list(reversed(chain))


def build(match, items):
    """Return ``(sections, breadcrumbs)`` for the current request.

    ``match`` is the request's resolver match (or None), and ``items`` is the
    ordered list of ``(section, url_name, label, url)`` entries the actor may
    see, already filtered by the caller's capability checks. Sections with no
    visible entry are omitted. The entry whose page chain contains the current
    page is marked current, and so is its section.
    """
    name = match.url_name if match and match.namespace == NAMESPACE else None
    arguments = dict(match.kwargs) if match else {}
    chain = _chain(name) if name in PAGES else []
    current_item = next(
        (
            entry_name
            for entry_name in reversed(chain)
            if any(entry[1] == entry_name for entry in items)
        ),
        None,
    )
    current_section = PAGES[name].section if name in PAGES else None
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
    return sections, _breadcrumbs(name, chain, arguments, sections)


def _breadcrumbs(name, chain, arguments, sections):
    """Home, the section, then each ancestor page, ending at the current page."""
    if name is None or name not in PAGES:
        return []
    trail = [{"label": PAGES["index"].label, "url": reverse(f"{NAMESPACE}:index")}]
    if name == "index":
        trail[0]["url"] = None
        return trail
    section = PAGES[name].section
    if section:
        # Link the section to its first entry the actor can see, if any.
        first = next(
            (entry["items"][0]["url"] for entry in sections if entry["key"] == section),
            None,
        )
        trail.append({"label": SECTION_LABELS[section], "url": first})
    for ancestor in chain[:-1]:
        trail.append(
            {"label": PAGES[ancestor].label, "url": _link(ancestor, arguments)}
        )
    trail.append({"label": PAGES[name].label, "url": None})
    return trail

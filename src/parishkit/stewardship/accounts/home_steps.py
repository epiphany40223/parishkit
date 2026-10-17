"""Home's Next steps list and Today line (ADM-12.21, NAV-18).

Home is a starting point (admin-portal spec, "Home page"): below its status
panels it lists a few links chosen by the current campaign's state and the
viewer's role, headed by one Today line. Every link comes from the viewer's
own menu, so a next step is offered exactly when its menu entry is, greys out
with the menu's own reason when the entry does, and is left out when the
viewer's role has no such entry. Nothing here runs a query: the menu (with
its open counts and the outgoing-mail warning count) is the chrome's, and the
Today counts ride on Home's existing statements (``admin_dashboard``).
"""

from typing import NamedTuple

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from .admin_navigation import PAGES

# Campaign states that read as "Closed or archived" in the spec's table.
# Purging states never reach Home as the current campaign; reading them as
# closed keeps the list sensible if one ever does.
_CLOSED = frozenset({"closed", "archived", "purging", "purge_cleanup_failed"})

# The spec's table, by state and role: menu entry names in order. Two names
# are not plain menu entries: ``campaign_mail`` is Preview and test email for
# the initial invitation's current revision, offered while Pages and emails
# is, and ``mail_problems`` is Sent mail filtered to emails that may not
# have arrived, with their count.
_ACTIVE = {
    "administrator": (
        "response_dashboard",
        "information_queue",
        "ministry_report",
        "family_email_progress",
        "mail_problems",
    ),
    "staff": ("response_dashboard", "information_queue", "ministry_report"),
    "ministry_leader": ("ministry_report",),
}
STEPS = {
    "testing": {
        "administrator": (
            "campaign_settings",
            "content_catalog",
            "schedule_settings",
            "campaign_mail",
            "go_live",
            "response_dashboard",
        ),
        "staff": ("response_dashboard", "participation"),
        "ministry_leader": ("ministry_report",),
    },
    "activating": {
        "administrator": ("production_progress", "deliveries"),
        "staff": ("response_dashboard",),
        "ministry_leader": ("ministry_report",),
    },
    # Still a draft while Testing cleanup runs and Production links are not
    # yet activated: readiness (which shows the cleanup) leads.
    "activating_draft": {
        "administrator": ("go_live", "production_progress", "deliveries"),
        "staff": ("response_dashboard",),
        "ministry_leader": ("ministry_report",),
    },
    "active": _ACTIVE,
    # Pause and resume mail comes first for an Administrator; Staff and
    # Ministry leaders cannot resume mail, so theirs is the Active list.
    "paused": _ACTIVE
    | {"administrator": ("delivery_control", *_ACTIVE["administrator"])},
    "closed": {
        "administrator": ("participation", "emailed_reports"),
        "staff": ("participation",),
        "ministry_leader": ("ministry_report",),
    },
}


class Step(NamedTuple):
    """One next step: a page name, its link (None while unavailable) and extras.

    ``reason`` is the menu's reason while the step is greyed out; ``count``
    an open count shown after the name, and ``note`` what the count means.
    """

    label: str
    url: str | None
    reason: str | None = None
    count: int | None = None
    note: str | None = None


def campaign_state(campaign, mode, *, go_live=False):
    """Which row of the spec's table the current campaign is in, or None.

    ``go_live`` is the chrome's flag for Production preparation in progress.
    A draft is being tested; a confirmed Production switch that has not
    started (scheduled), or one still preparing, is being activated; live
    mail paused in Production outranks both active and scheduled, because
    resuming is then the Administrator's first job.
    """
    if campaign is None:
        return None
    if campaign.state in _CLOSED:
        return "closed"
    if mode == "production" and campaign.delivery_paused:
        return "paused"
    if go_live or campaign.state == "scheduled":
        return "activating"
    if campaign.state == "draft":
        return "testing"
    return "active"


def role(roles):
    """The viewer's highest role: the column of the table Home reads."""
    for name in ("administrator", "staff", "ministry_leader"):
        if name in roles:
            return name
    return None


def next_steps(
    state, viewer_role, menu, *, test_email=None, mail_unknown=None, setup_next=None
):
    """Home's Next steps for ``state`` and ``viewer_role``, as ``Step`` rows.

    ``menu`` is the chrome's menu entries by name (dicts with ``url``,
    ``label``, ``reason`` and ``count``), already filtered by the viewer's
    role. A step whose entry the menu leaves out is left out here too, so a
    role never sees a step it may not open. ``test_email`` is the initial
    invitation's current revision (or None), ``mail_unknown`` the
    chrome's count of emails that may not have arrived, and ``setup_next``
    the Campaign setup stepper's first step not done yet (or None), which
    leads an Administrator's Testing list as Continue campaign setup.
    """
    steps = []
    if setup_next is not None and state == "testing" and viewer_role == "administrator":
        # The Campaign setup stepper's way back in (#1404): Leave campaign
        # setup opens Home, which offers the first step not done yet.
        steps.append(
            Step(
                str(
                    _("Continue campaign setup: %(step)s") % {"step": setup_next.label}
                ),
                setup_next.url,
            )
        )
    for name in STEPS.get(state, {}).get(viewer_role, ()):
        if name == "campaign_mail":
            step = _test_email(menu.get("content_catalog"), test_email)
        elif name == "mail_problems":
            step = _mail_problems(menu.get("deliveries"), mail_unknown)
        else:
            entry = menu.get(name)
            step = entry and Step(
                str(entry["label"]),
                entry["url"],
                entry["reason"],
                entry.get("count"),
                # The menu shows a queue's count only while it is a link.
                _("open") if entry["url"] and entry.get("count") is not None else None,
            )
        if step:
            steps.append(step)
    return steps


def _test_email(catalog, revision):
    """Preview and test email for the initial invitation, while it can work.

    It hangs below Pages and emails, so it follows that entry: greyed with
    its reason while it is greyed, and offered only when the invitation has
    a current revision to test.
    """
    if catalog is None or (catalog["url"] and revision is None):
        return None
    label = str(PAGES["campaign_mail"].label)
    if not catalog["url"]:
        return Step(label, None, catalog["reason"])
    return Step(label, reverse("admin:campaign_mail", args=[revision]))


def _mail_problems(deliveries, unknown):
    """Sent mail, filtered to emails that may not have arrived when some are."""
    if deliveries is None:
        return None
    label = str(deliveries["label"])
    if not deliveries["url"] or not unknown:
        return Step(label, deliveries["url"], deliveries["reason"])
    return Step(
        label,
        f"{deliveries['url']}?state=delivery_unknown",
        count=unknown,
        note=_("may not have arrived"),
    )


def today(dashboard):
    """The Today line's counts for this viewer, by kind; empty for none.

    Administrators and Staff (who may read campaign reports) get
    ``responded``, the Families who responded in the past 24 hours; a
    Ministry leader with My Ministries gets ``requests``, the open requests
    in those Ministries. The next planned Family mailing is already in the
    campaign panel and problems are listed above, so the line does not
    repeat them. The template words each count.
    """
    counts = {}
    families = dashboard.get("families") or {}
    if families.get("recent") is not None:
        counts["responded"] = families["recent"]
    mine = dashboard.get("my_ministries")
    if mine:
        counts["requests"] = sum(
            item["join"] + item["leave"] for item in mine["ministries"]
        )
    return counts


def home(context):
    """Next steps and Today for the Home template, from its rendering context.

    Reads the chrome (``admin_chrome``: menu sections, the Production
    preparation flag and the outgoing-mail warning count), the dashboard and
    the viewer. Returns None when there is no current campaign or no
    chrome, because Home's campaign panel then says what to do (Create the
    campaign, or why there is none) and there is no next step to list.
    """
    chrome = context.get("admin_chrome") or {}
    dashboard = context.get("dashboard") or {}
    configuration = context.get("configuration")
    principal = context.get("principal")
    if not chrome.get("sections") or configuration is None or principal is None:
        return None
    state = campaign_state(
        dashboard.get("campaign"), configuration.mode, go_live=chrome.get("go_live")
    )
    if state is None:
        return None
    if state == "activating" and dashboard["campaign"].state == "draft":
        state = "activating_draft"
    menu = {
        entry["name"]: entry
        for section in chrome["sections"]
        for entry in section["items"]
    }
    return {
        "steps": next_steps(
            state,
            role(principal.roles),
            menu,
            test_email=dashboard.get("test_email"),
            mail_unknown=chrome.get("delivery_unknown"),
            setup_next=dashboard.get("setup_next"),
        ),
        "today": today(dashboard),
    }

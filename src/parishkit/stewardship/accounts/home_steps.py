"""Home's Next steps list (ADM-12.21, NAV-18).

Home is a starting point (admin-portal spec, "Home page"): below its
overview it lists a few link cards chosen by the current campaign's state.
Home is the Administrator's: Staff and Ministry leaders sign in to My review
work instead (#1690, ``review_work``), so there is one list per state
(#1726). Every link comes from the Administrator's own menu, so a next step
is offered exactly when its menu entry is, greys out with the menu's own
reason when the entry does, and is left out when the menu has no such entry.
Nothing here runs a query: the menu (with its open counts and the
outgoing-mail warning count) is the chrome's.
"""

from typing import NamedTuple

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from .admin_navigation import PAGES

# Campaign states that read as "Closed or archived" in the spec's table.
# Purging states never reach Home as the current campaign; reading them as
# closed keeps the list sensible if one ever does.
_CLOSED = frozenset({"closed", "archived", "purging", "purge_cleanup_failed"})

# The spec's table, by state: menu entry names in order. Two names
# are not plain menu entries: ``campaign_mail`` is Preview and test email
# for the initial invitation's current revision, offered while Pages and
# emails is, and ``mail_problems`` is Sent mail filtered to emails that may
# not have arrived, with their count.
# The four lists Staff work through at campaign end (#1590), each with its
# count, while the campaign runs and after it closes. The Response dashboard
# is not a step: Home's campaign panel links it (#1673).
_WORK = (
    "census_changes",
    "ministry_roster",
    "information_queue",
    "ministry_report",
)
_ACTIVE = (*_WORK, "family_email_progress", "mail_problems")
# What each menu count means, after the number: the page's default view.
_NOTES = {
    "census_changes": _("to do"),
    "ministry_roster": _("not yet entered"),
}
STEPS = {
    "testing": (
        "campaign_settings",
        "content_catalog",
        "schedule_settings",
        "campaign_mail",
        "go_live",
    ),
    "activating": ("production_progress", "deliveries"),
    # Still a draft while Testing cleanup runs and Production links are not
    # yet activated: readiness (which shows the cleanup) leads.
    "activating_draft": ("go_live", "production_progress", "deliveries"),
    "active": _ACTIVE,
    # Resuming paused mail is the first job.
    "paused": ("delivery_control", *_ACTIVE),
    "closed": (*_WORK, "emailed_reports"),
}


class Other(NamedTuple):
    """A step's second, separately linked count: the other mode's (#1576).

    ``note`` is the short visible text after the count ("in Testing");
    ``more`` finishes the link's name for screen readers ("may not have
    arrived"), so the small link inside its card stays one short line.
    """

    url: str
    count: int
    note: str
    more: str


# What an Other count means, by the mode it counts.
_OTHER_NOTES = {
    "testing": _("in Testing"),
    "production": _("in Production"),
}


class Step(NamedTuple):
    """One next step: a page name, its link (None while unavailable) and extras.

    ``reason`` is the menu's reason while the step is greyed out; ``count``
    an open count shown after the name, and ``note`` what the count means.
    ``other`` is a second count with its own link (``Other``), or None.
    """

    label: str
    url: str | None
    reason: str | None = None
    count: int | None = None
    note: str | None = None
    other: Other | None = None


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


def next_steps(state, menu, *, test_email=None, mail_unknown=None, setup_next=None):
    """Home's Next steps for ``state``, as ``Step`` rows.

    ``menu`` is the chrome's menu entries by name (dicts with ``url``,
    ``label``, ``reason`` and ``count``), already filtered by the viewer's
    role. A step whose entry the menu leaves out is left out here too, so
    no one sees a step they may not open. ``test_email`` is the initial
    invitation's current revision (or None), ``mail_unknown`` the
    chrome's count of emails that may not have arrived, and ``setup_next``
    the Campaign setup stepper's first step not done yet (or None), which
    leads the Testing list as Continue campaign setup.
    """
    steps = []
    if setup_next is not None and state == "testing":
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
    for name in STEPS.get(state, ()):
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
                _NOTES.get(name, _("open"))
                if entry["url"] and entry.get("count") is not None
                else None,
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
    """Sent mail, filtered to emails that may not have arrived when some are.

    ``unknown`` is the chrome's split count (``delivery_metadata.mode_split``)
    or None. The operating mode's count links Sent mail filtered to that
    mode, which lists exactly those emails; the other mode's, when it has
    some, is a separate link (``Step.other``), never added in (#1576).
    """
    if deliveries is None:
        return None
    label = str(deliveries["label"])
    url = deliveries["url"]
    if not url or not unknown or not (unknown["count"] or unknown["other"]):
        return Step(label, url, deliveries["reason"])
    other = None
    if unknown["other"]:
        other = Other(
            f"{url}?state=needs_review&mode={unknown['other_mode']}",
            unknown["other"],
            _OTHER_NOTES[unknown["other_mode"]],
            _("may not have arrived"),
        )
    if not unknown["count"]:
        return Step(label, url, other=other)
    return Step(
        label,
        f"{url}?state=needs_review&mode={unknown['mode']}",
        count=unknown["count"],
        note=_("may not have arrived"),
        other=other,
    )


def home(context):
    """Next steps for the Home template, from its rendering context.

    Reads the chrome (``admin_chrome``: menu sections, the Production
    preparation flag and the outgoing-mail warning count), the dashboard and
    the viewer. Returns None when there is no current campaign or no
    chrome, because Home's campaign panel then says what to do (Create the
    campaign, or why there is none) and there is no next step to list, and
    for anyone but an Administrator, who alone is shown Home (#1726).
    """
    chrome = context.get("admin_chrome") or {}
    dashboard = context.get("dashboard") or {}
    configuration = context.get("configuration")
    principal = context.get("principal")
    if (
        not chrome.get("sections")
        or configuration is None
        or principal is None
        or "administrator" not in principal.roles
    ):
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
    return next_steps(
        state,
        menu,
        test_email=dashboard.get("test_email"),
        mail_unknown=chrome.get("delivery_unknown"),
        setup_next=dashboard.get("setup_next"),
    )

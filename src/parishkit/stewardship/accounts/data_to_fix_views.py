"""Data to fix in ParishSoft: one summary of the data problems fixed there (#1011).

The page (admin-portal spec, "Data to fix in ParishSoft") shows, for
Administrators and Staff, one block per kind of problem, most blocking
first: Families the form cannot open, refused email addresses, Families no
mail can reach, and census changes to enter. Each block is a count and a
link to the existing page that lists and fixes those problems.

Nothing here is a new query of its own: each count calls the read its
detail page (or Home) already uses, with that page's own default filters,
so a count always matches the list its link opens. A role sees a section
only when it holds the capability that section's detail page checks, so
the summary never shows a count the viewer could not follow.

Counts are read once, when the page loads; nothing polls, so nothing on the
page moves. Each section stands alone: a read that cannot run, or fails,
leaves its block in place saying why, and the other sections still count.
``pk-admin report data-to-fix`` (``admin_data_to_fix``) prints the same
counts through ``read_sections``.
"""

import logging
from dataclasses import dataclass

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.db.models import Count
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_safe

from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.read_guards import CampaignReadGuard
from parishkit.stewardship.jobs.delivery_views import UNAVAILABLE
from parishkit.stewardship.load_check import LoadCheckRefused
from parishkit.stewardship.observability import Event, emit_failure
from parishkit.stewardship.web.contracts import (
    ErrorCode,
    FieldError,
    filters,
    validation_response,
)
from parishkit.stewardship.web.report_errors import report_unavailable

from . import admin_context, admin_navigation
from .authentication import denial, runtime
from .policy import Capability, allows
from .runtime_models import SystemConfiguration
from .sessions import authenticated_admin

TEMPLATE = "stewardship/data-to-fix.html"

# Why a section shows no count. Each is shown in place of the count; the
# menu's own words are reused where the menu greys the detail page out too.
NO_CAMPAIGN = admin_navigation.NO_CAMPAIGN
NO_SOURCE = _("ParishSoft data has not been read yet, so there is nothing to count.")
FAILED = _("These could not be counted just now. Reload this page to try again.")


@dataclass(frozen=True)
class Section:
    """One kind of data problem: who may see it and where its list is.

    ``capability`` is the capability the detail page itself checks (its own
    ``_principal``), so the section shows exactly when the viewer may open
    that page. ``page`` is the detail page's URL name and ``query`` the
    preset its link opens. ``reason`` is the detail page's menu reason
    check, or None for a page that opens whatever the campaign's state:
    when it gives a reason, the page would refuse the visit too, so the
    link is greyed out with that reason.
    """

    key: str
    capability: Capability
    page: str
    query: str = ""
    reason: object = None

    @property
    def label(self):
        """The detail page's own name, as the menu and its heading say it."""
        return admin_navigation.PAGES[self.page].label


# The sections, in the Administrator's order (2026-10-11, #1011): the
# problem that blocks a Family most directly first, the routine worklist
# last. Keys are the command line's names for them.
SECTIONS = (
    # Families the form cannot open: System health's list (SYSTEM_LOGS).
    # Greyed out, as System health's link is, until ParishSoft data is
    # downloaded (#1242).
    Section(
        "form_cannot_open",
        Capability.SYSTEM_LOGS,
        "source_form",
        reason=admin_navigation.PAGE_REASONS["source_form"],
    ),
    # Refused addresses: the delivery views' admission (BACKGROUND_WORK).
    Section("refused_addresses", Capability.BACKGROUND_WORK, "delivery_refusals"),
    # Families no mail can reach: the directory's "neither" reach
    # selection (Home no longer shows it, #1673); the directory needs a
    # campaign.
    Section(
        "unreachable_families",
        Capability.FAMILY_CODES,
        "family_directory",
        query="?reach=neither",
        reason=admin_navigation.MENU_REASONS["family_directory"],
    ),
    # Census changes: the page's default view; greyed out, as its menu
    # entry is, for a campaign without the census.
    Section(
        "census_changes",
        Capability.VIEW_CENSUS,
        "census_changes",
        reason=admin_navigation.MENU_REASONS["census_changes"],
    ),
)


def visible_sections(principal):
    """The sections whose detail page ``principal`` may open, in page order."""
    return [section for section in SECTIONS if allows(principal, section.capability)]


def _principal(request, store, *, read_only=False):
    """Administrators and Staff: anyone who may open at least one section.

    Every request reloads current policy. A Ministry leader may open none
    of the detail pages, so is refused, as the menu leaves the entry out.
    """
    principal = authenticated_admin(
        request, store=store, activity=not read_only, read_only=read_only
    )
    if not visible_sections(principal):
        raise PermissionError("Data to fix in ParishSoft is unavailable.")
    return principal


def _no_abort():
    """A page-load count has no transport to terminate on a deadline."""


def _guarded(campaign_id, read, *, admit=None):
    """Run one section's read under the campaign read guard, as its page does.

    ``admit`` repeats the detail page's own campaign admission inside the
    guard (``admit_report_read`` for the report pages); System health's
    source-form count admits nothing more, as System health does.
    """
    with CampaignReadGuard(
        [campaign_id],
        authorize=lambda guard: admit(campaign_id) if admit else None,
        abort=_no_abort,
    ):
        return read()


def _form_cannot_open(principal, campaign_id):
    """System health's count of Families the form cannot open.

    Before any ParishSoft data is promoted the scan refuses to run
    (``LoadCheckRefused``); that is not a failure, so the section says
    there is nothing to count yet, as the unreachable count does.
    """
    from parishkit.stewardship.reports.source_form import blocked_families

    try:
        found = _guarded(campaign_id, lambda: blocked_families(campaign_id))
    except LoadCheckRefused:
        return {"reason": NO_SOURCE}
    return {"count": found["families"]}


def _refused_addresses(principal, campaign_id):
    """Sent mail's unresolved refusals, and the Families they belong to.

    One query over the same set behind Sent mail's link-line count. The
    refusals belong to Families, not to a campaign, so this needs none.
    """
    from parishkit.stewardship.jobs.delivery_reads import unresolved_refusals

    return unresolved_refusals().aggregate(
        count=Count("pk"), families=Count("family_id", distinct=True)
    )


def _unreachable_families(principal, campaign_id):
    """This page's count of Families with no deliverable email and no address."""
    from parishkit.stewardship.reports.read_admission import admit_report_read

    from .admin_dashboard import unreachable_families

    count = _guarded(
        campaign_id,
        lambda: unreachable_families(campaign_id),
        admit=admit_report_read,
    )
    return {"reason": NO_SOURCE} if count is None else {"count": count}


def _census_changes(principal, campaign_id):
    """The Census changes rows at To review or Conflict, counted.

    The same rule as the menu's count (``census_changes.open_count``) and
    the same for every role (Administrator decision, 2026-10-18): the rows
    waiting on a reviewer, never Ready to update or To enter by hand.
    """
    from parishkit.stewardship.reports.census_changes import (
        EVERY_STAGE,
        OPEN_STAGES,
        CensusQuery,
        census_changes,
    )
    from parishkit.stewardship.reports.read_admission import admit_report_read

    result = _guarded(
        campaign_id,
        lambda: census_changes(campaign_id, CensusQuery(stage=EVERY_STAGE), principal),
        admit=admit_report_read,
    )
    stages = {stage.value for stage in OPEN_STAGES}
    return {"count": sum(row["stage"] in stages for row in result["rows"])}


READS = {
    "form_cannot_open": _form_cannot_open,
    "refused_addresses": _refused_addresses,
    "unreachable_families": _unreachable_families,
    "census_changes": _census_changes,
}


def _sentence(text):
    """A reason as a sentence on the page: the menu's tips have no final stop."""
    text = str(text)
    return text if text.endswith(".") else f"{text}."


def _read_one(section, principal, state):
    """One section's count, or why it has none; never raises for a read.

    Returns ``{"count": n}`` (and ``"families"`` for refused addresses), or
    ``{"reason": text}``; ``"available"`` says whether the link may be
    followed now. A section the campaign's state rules out is not read at
    all. A failed read is logged as a failed display step (WARNING, the
    section named by its shaping step) and the block says it could not
    count; it never takes the page down with it. ``state`` is the menu
    state (``admin_context.menu_state``) the section's reason check reads.
    """
    campaign = state.campaign
    blocked = section.reason(state) if section.reason else None
    if blocked:
        return {"reason": _sentence(blocked), "available": False}
    if campaign is None and section.key != "refused_addresses":
        # The source-form list opens without a campaign and says so.
        return {"reason": _sentence(NO_CAMPAIGN), "available": True}
    try:
        found = READS[section.key](principal, campaign and campaign.pk)
    except Exception as error:
        # A guard refusal, an outage or an unexpected fault all end here:
        # the spec keeps every other section counting, so this one says it
        # could not count, and the line names which section failed.
        emit_failure(
            error,
            event=Event.REPORT_SHAPING_FAILED,
            level=logging.WARNING,
            shaping=f"data_to_fix_{section.key}",
            name_class=True,
        )
        return {"reason": FAILED, "available": True}
    return found | {"available": True}


def read_sections(principal, configuration, campaign):
    """Every section ``principal`` may see, each counted or saying why not.

    ``configuration`` is the system configuration row and ``campaign`` the
    current campaign with its active configuration loaded, or None. Each
    returned row has the section's ``key``, ``label`` and link ``url`` (None
    while greyed out), plus ``_read_one``'s result. Must run outside any
    transaction: each guarded read owns its own.
    """
    rows = []
    # One state for every section, so the download is read at most once.
    state = admin_context.menu_state(campaign, configuration)
    for section in visible_sections(principal):
        found = _read_one(section, principal, state)
        url = None
        if found.pop("available"):
            url = reverse(f"{admin_navigation.NAMESPACE}:{section.page}") + (
                section.query
            )
        rows.append({"key": section.key, "label": section.label, "url": url} | found)
    return rows


def total(rows):
    """The problems the viewer saw across every counted section."""
    return sum(row.get("count", 0) for row in rows)


def current(store):
    """The system configuration and its current campaign, or None in a restore.

    The campaign (None without one) carries its active configuration, which
    the menu reason checks and the page's data line read.
    """
    from parishkit.stewardship.campaigns.models import Campaign

    configuration = SystemConfiguration.objects.get()
    if configuration.restore_review_required:
        return None
    campaign = None
    if configuration.current_campaign_id is not None:
        campaign = Campaign.objects.select_related("active_configuration").get(
            pk=configuration.current_campaign_id
        )
    return configuration, campaign


def audit(principal, outcome, count):
    """Record the view with the total the viewer saw, never a name or value.

    One ``data_to_fix_viewed`` event. Its ``count`` is the sum of the shown
    sections' counts: the audit context's fields are a closed list mirrored
    in SQL, so per-section counts would need a schema change.
    """
    with transaction.atomic():
        record_action(
            Action.DATA_TO_FIX_VIEWED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=principal.identity,
            context={"outcome": outcome, "count": count},
        )


@require_safe
def data_to_fix(request):
    """The summary page: admit, count each section, render, recheck, audit.

    The counts are read before rendering; before the HTML is released the
    session and the restore review are checked again, so a viewer signed
    out or demoted, or a restore begun, while the page was read withholds
    it, as System health does.
    """
    from .admin_dashboard import promoted_at

    try:
        filters(request.GET, allowed=set())
        service = runtime()
        actor = _principal(request, service.store)
        found = current(service.store)
        if found is None:
            return report_unavailable()
        configuration, campaign = found
        rows = read_sections(actor, configuration, campaign)
        response = render(
            request,
            TEMPLATE,
            {
                "campaign": campaign,
                "refreshed_at": promoted_at(),
                "sections": rows,
                "keys": [row["key"] for row in rows],
                # Every shown section counted and found nothing.
                "nothing_to_fix": all(row.get("count") == 0 for row in rows),
                "can_refresh": allows(actor, Capability.CONFIGURE),
            },
        )
        with transaction.atomic():
            fresh = _principal(request, service.store, read_only=True)
            if fresh.identity != actor.identity:
                raise PermissionError("Data to fix reader changed.")
            if SystemConfiguration.objects.filter(
                restore_review_required=True
            ).exists():
                return report_unavailable()
            audit(fresh, Outcome.SUCCEEDED, total(rows))
        response["Cache-Control"] = "no-store"
        return response
    # Answered as the report pages answer (ui-conventions, request errors):
    # the shared denial, and the report error page for an outage or a bad
    # address, never Sent mail's error page, whose links Staff cannot
    # open.
    except PermissionError:
        return denial()
    except (DatabaseError, *UNAVAILABLE, ObjectDoesNotExist):
        return report_unavailable()
    except ValueError:
        return validation_response([FieldError(ErrorCode.INVALID)], status=400)

"""Admin/Staff live reports with exact fact selection and response-owned guards."""

import json
from contextlib import ExitStack
from uuid import uuid4

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.observability import Event, emit_failure
from parishkit.stewardship.source.catalog_names import fund_display_name
from parishkit.stewardship.source.version_models import SnapshotFund
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.report_errors import report_unavailable
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.security import private_response
from parishkit.stewardship.web.tables import paginate

from .chart_specs import PROGRESS, participation_chart, population_progress
from .daily_digest import statistics_cards
from .digest_presentation import participation_table_context
from .export_services import admit_campaign
from .export_views import SAFE_FAILURES, _principal
from .facts import FactUnavailable
from .ministries import can_report, unscoped
from .read_admission import admit_report_read
from .selection import guarded_participation
from .statistics import StatisticsUnavailable, calculate_statistics
from .statistics_selection import capture_statistics
from .workspace import DAILY_SORTING, SCOPE_LABELS, RenderedReport, ReportQuery

REPORTABLE = ("draft", "scheduled", "active", "closed", "archived")


def campaign_ids():
    """Discover only lock identities; labels and report data wait for admission."""
    return tuple(
        Campaign.objects.filter(state__in=REPORTABLE)
        .order_by("-created_at", "id")
        .values_list("id", flat=True)
    )


@require_GET
def index(request):
    """Open the current campaign's Participation, or Ministry requests.

    ``/admin/reports/`` keeps its old meaning rather than opening the group's
    first entry (admin-portal spec, "Menu groups"): Participation, or Ministry
    requests for a viewer who may not open Participation (a Ministry leader),
    which checks its own access. Default navigation is per request, never a
    mutation of the current pointer.
    """
    try:
        service = runtime()
        actor = authenticated_admin(request, store=service.store, activity=True)
        if not allows(actor, Capability.CAMPAIGN_REPORT):
            if not can_report(actor):
                raise unscoped(actor, "Campaign reporting is unavailable.")
            response = redirect("admin:ministry_report")
            response["Cache-Control"] = "no-store"
            return response
        query = ReportQuery.parse(request.GET)
        choices = campaign_ids()
        current = SystemConfiguration.objects.values_list(
            "current_campaign_id", flat=True
        ).get()
        # Only the current campaign is reported until #145 (rule 10). With no
        # current campaign the root still opens Participation, whose greyed
        # page says why in the menu's words (#1128), rather than a page of
        # its own that no link reaches (navigation rule 1).
        if current not in choices:
            response = redirect("admin:participation")
        else:
            admit_report_read(current)
            response = redirect(query.url())
        response["Cache-Control"] = "no-store"
        return response
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except SAFE_FAILURES:
        return report_unavailable()
    except ValueError:
        return private_response("Invalid report filters.\n", status=400)


def _audit(principal, campaign_id, outcome):
    """Parish-owned references survive purge without copying reported values."""
    # An audit append takes no row locks, so it need not join the
    # writers' work order; waiting there stalled report pages behind
    # every source promotion and installer.
    with transaction.atomic():
        system = SystemConfiguration.objects.select_related(
            "active_configuration__parish"
        ).get()
        record_action(
            Action.PARTICIPATION_VIEWED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=principal.identity,
            parish_id=system.active_configuration.parish.pk,
            campaign_id=campaign_id,
            subject_id=campaign_id,
            context={"outcome": outcome},
        )


@require_GET
def participation(request, campaign_id):
    """The Participation page, its daily chart drawn in the browser (#720)."""
    finish, handed_off = None, False
    try:
        service = runtime()
        principal = _principal(request, service.store)
        query = ReportQuery.parse(request.GET)
        identities = (campaign_id,)
        admit_report_read(campaign_id)
        _audit(principal, campaign_id, Outcome.STARTED)
        finalized = False
        response_guard = None

        def finish(completed):
            """Audit only server completion after the read-only transaction closes."""
            nonlocal finalized
            if finalized:
                return
            finalized = True
            try:
                _audit(
                    principal,
                    campaign_id,
                    Outcome.SUCCEEDED if completed else Outcome.FAILED,
                )
            except (DatabaseError, StorageInvariantError) as error:
                emit_failure(error, event=Event.REPORT_AUDIT_FAILED)

        def authorize(guard):
            """Refresh roles and lifecycle inside all acquired campaign barriers."""
            nonlocal response_guard
            fresh = _principal(request, service.store, read_only=True)
            if fresh.identity != principal.identity:
                raise PermissionError("Report access changed.")
            for identifier in identities:
                admit_report_read(identifier)
            response_guard = guard

        def content():
            """Prepare before headers and transfer generation protection to WSGI."""
            with ExitStack() as stack:
                selected = stack.enter_context(
                    guarded_participation(
                        response_guard,
                        campaign_id=campaign_id,
                        population_scope=query.scope,
                        browser_timezone=query.timezone,
                    )
                )
                context = _page_context(campaign_id, query, selected, principal)
                payload = render_to_string(
                    "stewardship/participation.html", context, request=request
                ).encode()
                response_guard.check()
                return RenderedReport(payload, stack.pop_all())

        response = campaign_response(
            request,
            identities,
            authorize=authorize,
            open_content=content,
            content_type="text/html; charset=utf-8",
            on_close=finish,
        )
        handed_off = response.status_code == 200 and response.streaming
        return response
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (StatisticsUnavailable, FactUnavailable, StorageInvariantError):
        return report_unavailable()
    except SAFE_FAILURES:
        return report_unavailable()
    except ValueError:
        return private_response("Invalid report filters.\n", status=400)
    finally:
        if finish is not None and not handed_off:
            finish(False)


def daily_table(chart, presented, query):
    """The daily table's sorted, paged context for one participation chart.

    ``presented`` is ``participation_table_context(chart)``. Each day is paired
    with its formatted cells, so the server sorts by the exact stored values
    while the table shows the shared formatting. The whole document is
    already in memory, so the shared navigator shows the total and allows
    any page or "All".
    """
    table = paginate(
        list(zip(chart.days, presented["rows"], strict=True)),
        {"page": str(query.page), "size": query.size, "sort": query.sort},
        carry=query.carried(),
        sorting=DAILY_SORTING,
    )
    keys = ["date", "first", "participation", "pledge"]
    return {
        "table": table,
        "rows": [cells for _day, cells in table.rows],
        "columns": list(zip(keys, presented["headings"], strict=False)),
    }


def fund_names(statistics):
    """Each financial period's fund names, read from the statistics' snapshot.

    The page names the funds, never their DUIDs (#1123). A fund that
    snapshot lacks reads "Unavailable fund". None without financial periods.
    """
    financial = statistics.financial
    if financial is None:
        return None
    wanted = set(financial.upcoming.funds) | set(financial.comparison.funds)
    rows = (
        SnapshotFund.objects.filter(
            snapshot_id=statistics.source_id,
            source_key__in=[str(duid) for duid in wanted],
        ).values_list("source_key", "payload__canonical")
        if statistics.source_id
        else ()
    )
    names = {
        int(key): fund_display_name(int(key), json.loads(text).get("name"))
        for key, text in rows
    }
    return {
        period: [
            names.get(duid, _("Unavailable fund"))
            for duid in getattr(financial, period).funds
        ]
        for period in ("upcoming", "comparison")
    }


def statistics_panel(statistics):
    """The statistics panel's tiles and its progress chart (#720).

    The responded and email cards are drawn as progress bars against active
    Families; the other cards stay tiles. Without an observation nothing is
    drawn, and every card, those three included, is a tile reading
    Unavailable.
    """
    progress = population_progress(statistics.active)
    drawn = set(dict(PROGRESS)) if progress else set()
    return {
        "tiles": tuple(
            card for card in statistics_cards(statistics) if card[0] not in drawn
        ),
        "progress": progress,
    }


def scope_links(query):
    """Each Chart population segment's address (#1535): this page with that
    scope, the daily table back on its first page, its sort and size and
    the zone kept."""
    return {scope: query.url(page=1, scope=scope) for scope in SCOPE_LABELS}


def _page_context(campaign_id, query, selected, principal):
    """Format detached shared observations; never recompute money in templates."""
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=campaign_id
    )
    statistics = calculate_statistics(capture_statistics(campaign_id))
    mutable = True
    try:
        admit_campaign(campaign_id, mutating=True)
    except PermissionError:
        mutable = False
    context = {
        "campaign": campaign,
        "query": query,
        "scope_links": scope_links(query),
        "selection": selected,
        "statistics": statistics,
        **statistics_panel(statistics),
        "fund_names": fund_names(statistics),
        "export_key": uuid4(),
        "exact_key": uuid4(),
        "exact_row_count": max(
            0,
            (
                selected.expected.through_date
                - campaign.active_configuration.start_date
            ).days
            + 1,
        )
        if selected.expected is not None
        else None,
        # No list of earlier exports: exports are point-in-time (#1398).
        "export_allowed": mutable,
    }
    if selected.document is not None:
        context.update(participation_table_context(selected.document))
        context.update(daily_table(selected.document, context, query))
        context["row_count"] = len(selected.document.days)
        # The daily chart drawn in the browser (#720); the exports and the
        # daily email keep the static drawing.
        context["daily_chart"] = participation_chart(selected.document)
    return context

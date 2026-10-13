"""Admin/Staff live reports with exact fact selection and response-owned guards."""

import json
from contextlib import ExitStack
from urllib.parse import urlencode
from uuid import uuid4

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse
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

from .charts import participation_png
from .daily_digest import statistics_cards
from .digest_presentation import participation_context
from .documents import participation_document
from .exact_models import ExactExportRequest
from .export_models import ExportRequest
from .export_services import admit_campaign
from .export_views import SAFE_FAILURES, _principal
from .facts import FactUnavailable, read_fact_set
from .ministries import can_report, unscoped
from .read_admission import admit_report_read
from .selection import guarded_participation
from .statistics import StatisticsUnavailable, calculate_statistics
from .statistics_selection import capture_statistics
from .workspace import DAILY_SORTING, RenderedReport, ReportQuery

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
def participation(request, campaign_id, *, fact_set_id=None):
    """Chart subrequests name the displayed generation, never today's pointer."""
    finish, handed_off = None, False
    try:
        service = runtime()
        principal = _principal(
            request, service.store, read_only=fact_set_id is not None
        )
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
                if fact_set_id is not None:
                    facts = stack.enter_context(
                        read_fact_set(
                            fact_set_id,
                            admit=lambda action, inputs: (
                                action == "read"
                                and inputs.campaign_id == campaign_id
                                and inputs.population_scope == query.scope
                            ),
                        )
                    )
                    system = SystemConfiguration.objects.select_related(
                        "active_configuration__parish"
                    ).get()
                    document = participation_document(
                        facts,
                        parish_name=system.active_configuration.parish.name,
                        browser_timezone=query.timezone,
                        requested_at=facts.created_at,
                    )
                    payload = participation_png(document)
                else:
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
            content_type="image/png"
            if fact_set_id is not None
            else "text/html; charset=utf-8",
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

    ``presented`` is ``participation_context(chart)``. Each day is paired
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
        "selection": selected,
        "statistics": statistics,
        "cards": statistics_cards(statistics),
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
        "export_allowed": mutable,
        "recent_exports": list(
            ExportRequest.objects.filter(
                campaign_id=campaign_id, requester_id=principal.identity
            ).order_by("-created_at", "id")[:10]
        ),
        "recent_exact_exports": list(
            ExactExportRequest.objects.filter(
                campaign_id=campaign_id, requester_id=principal.identity
            ).order_by("-created_at", "id")[:10]
        ),
    }
    if selected.document is not None:
        context.update(participation_context(selected.document))
        context.update(daily_table(selected.document, context, query))
        context["row_count"] = len(selected.document.days)
        context["chart_url"] = (
            reverse(
                "admin:participation_chart",
                args=[selected.document.fact_set_id],
            )
            + "?"
            + urlencode({"scope": query.scope, "timezone": query.timezone})
        )
    return context

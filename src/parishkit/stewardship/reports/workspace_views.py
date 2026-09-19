"""Admin/Staff live reports with exact fact selection and response-owned guards."""

from contextlib import ExitStack
from io import BytesIO
from urllib.parse import urlencode
from uuid import uuid4

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.observability import Event, emit_failure
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.contracts import PageWindow
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.security import private_response

from .charts import render_participation
from .daily_digest import population_cards, statistics_cards
from .digest_presentation import participation_context
from .documents import participation_document
from .export_models import ExportRequest
from .export_services import admit_campaign
from .export_views import SAFE_FAILURES, _principal
from .facts import FactUnavailable, read_fact_set
from .read_admission import admit_report_read
from .selection import guarded_participation
from .statistics import StatisticsUnavailable, calculate_statistics
from .statistics_selection import capture_statistics
from .workspace import RenderedReport, ReportQuery

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
    """Default navigation is per request, never a mutation of the current pointer."""
    try:
        service = runtime()
        _principal(request, service.store)
        query = ReportQuery.parse(request.GET)
        choices = campaign_ids()
        current = SystemConfiguration.objects.values_list(
            "current_campaign_id", flat=True
        ).get()
        if not choices:
            response = render(request, "stewardship/report-empty.html")
        else:
            selected = current if current in choices else choices[0]
            admit_report_read(selected)
            response = redirect(query.url(selected))
        response["Cache-Control"] = "no-store"
        return response
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except SAFE_FAILURES:
        return denial(status=503, retry=5)
    except ValueError:
        return private_response("Invalid report filters.\n", status=400)


def _audit(principal, campaign_id, outcome):
    """Parish-owned references survive purge without copying reported values."""
    with work_transaction():
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
                    output = BytesIO()
                    render_participation(document, output, format="png")
                    payload = output.getvalue()
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
        return denial(status=503, retry=5)
    except SAFE_FAILURES:
        return denial(status=503, retry=5)
    except ValueError:
        return private_response("Invalid report filters.\n", status=400)
    finally:
        if finish is not None and not handed_off:
            finish(False)


def _page_context(campaign_id, query, selected, principal):
    """Format detached shared observations; never recompute money in templates."""
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=campaign_id
    )
    statistics = calculate_statistics(
        capture_statistics(campaign_id), include_inactive=query.inactive
    )
    mutable = True
    try:
        admit_campaign(campaign_id, mutating=True)
    except PermissionError:
        mutable = False
    context = {
        "campaign": campaign,
        "picker_url": reverse("admin:report_campaigns")
        + "?"
        + query.url(campaign_id).split("?", 1)[1],
        "query": query,
        "selection": selected,
        "statistics": statistics,
        "cards": statistics_cards(statistics),
        "inactive_cards": population_cards(
            statistics.inactive,
            financial_enabled=statistics.financial_enabled,
            inactive=True,
        )
        if query.inactive
        else (),
        "timezones": sorted(timezone_names()),
        "export_key": uuid4(),
        "export_allowed": mutable,
        "recent_exports": list(
            ExportRequest.objects.filter(
                campaign_id=campaign_id, requester_id=principal.identity
            ).order_by("-created_at", "id")[:10]
        ),
        "previous_url": query.url(campaign_id, page=query.page - 1)
        if query.page > 1
        else None,
    }
    if selected.document is not None:
        context.update(participation_context(selected.document))
        rows = (
            context["rows"]
            if query.sort == "date_asc"
            else list(reversed(context["rows"]))
        )
        context["rows"], more = PageWindow(page=query.page).rows(rows)
        context["next_url"] = (
            query.url(campaign_id, page=query.page + 1) if more else None
        )
        context["row_count"] = len(selected.document.days)
        context["chart_url"] = (
            reverse(
                "admin:participation_chart",
                args=[campaign_id, selected.document.fact_set_id],
            )
            + "?"
            + urlencode({"scope": query.scope, "timezone": query.timezone})
        )
    return context

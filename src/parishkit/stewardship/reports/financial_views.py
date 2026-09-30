"""Private native Admin/Staff financial stewardship detail report."""

from uuid import uuid4

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.observability import Event, debug_swallowed, emit_failure
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.tables import report_table

from .export_services import admit_campaign
from .export_views import SAFE_FAILURES
from .financial import (
    FINANCIAL_SORTING,
    FREQUENCY_LABELS,
    PAGE_SIZE,
    PAGE_SIZES,
    FinancialQuery,
    financial_page,
    giving_proof,
)
from .read_admission import admit_report_read
from .report_paging import carried_filters, clamp_query, pop_page_size


def _principal(request, store, *, read_only=False):
    """Every page reloads current policy; a Ministry leader never qualifies."""
    principal = authenticated_admin(
        request, store=store, activity=not read_only, read_only=read_only
    )
    if not allows(principal, Capability.FINANCIAL_DETAIL):
        raise PermissionError("Financial stewardship detail is unavailable.")
    return principal


def _error(campaign_id, *, status):
    """No private filter or exception values, and a way back to the report."""
    debug_swallowed("report request refused")
    response = HttpResponse(
        render_to_string(
            "stewardship/financial-report-error.html",
            {"campaign_id": campaign_id, "status": status},
        ),
        status=status,
        headers={"Cache-Control": "no-store"},
    )
    response.stewardship_safe_error = True
    if status == 503:
        response["Retry-After"] = "5"
    return response


def _audit(principal, campaign_id, outcome, count=0, total=0):
    """Retain parish-owned access evidence, never a filter, name or amount."""
    # An audit append takes no row locks, so it need not join the
    # writers' work order; waiting there stalled report pages behind
    # every source promotion and installer.
    with transaction.atomic():
        system = SystemConfiguration.objects.select_related(
            "active_configuration__parish"
        ).get()
        record_action(
            Action.FINANCIAL_REPORT_VIEWED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=principal.identity,
            subject_id=campaign_id,
            parish_id=system.active_configuration.parish.pk,
            campaign_id=campaign_id,
            context={"outcome": outcome, "count": count, "matching_count": total},
        )


@require_http_methods(["GET", "POST"])
def report(request, campaign_id):
    """Hold purge protection through projection, rendering and streaming.

    Default GET is safe; identifying filters and pagination use native POST.
    """
    finish, handed_off = None, False
    try:
        service = runtime()
        principal = _principal(request, service.store)
        # Admit the campaign before judging the filters, so an unknown campaign
        # is denied rather than described as a filter problem with a dead link.
        admit_report_read(campaign_id)
        try:
            if request.GET:
                raise ValueError("Financial filters require private POST state.")
            parameters = request.POST.copy()
            parameters.pop("csrfmiddlewaretoken", None)
            size = pop_page_size(parameters, PAGE_SIZES, default=PAGE_SIZE)
            query = FinancialQuery.parse(parameters)
        except ValueError:
            # Only the requester's own filters are a 400. A later ValueError is
            # a data problem that no change of filters could fix.
            return _error(campaign_id, status=400)
        _audit(principal, campaign_id, Outcome.STARTED)
        finalized, count, total = False, 0, 0

        def finish(completed):
            """Audit completion once, after releasing the read transaction."""
            nonlocal finalized
            if finalized:
                return
            finalized = True
            try:
                _audit(
                    principal,
                    campaign_id,
                    Outcome.SUCCEEDED if completed else Outcome.FAILED,
                    count,
                    total,
                )
            except (DatabaseError, StorageInvariantError) as error:
                emit_failure(error, event=Event.REPORT_AUDIT_FAILED)

        def authorize(guard):
            """Replace the outer actor: a Staff-to-leader change ends this report."""
            nonlocal principal
            fresh = _principal(request, service.store, read_only=True)
            if fresh.identity != principal.identity:
                raise PermissionError("Financial report access changed.")
            principal = fresh
            admit_report_read(campaign_id)

        def content():
            """Only detached authorized data reaches the native report template."""
            nonlocal count, total, query
            campaign = Campaign.objects.select_related("active_configuration").get(
                pk=campaign_id
            )
            configuration = campaign.active_configuration.values
            system = SystemConfiguration.objects.select_related(
                "active_configuration__parish"
            ).get()

            def read():
                """One SQL page of ``size`` rows for the current query."""
                return financial_page(
                    campaign_id,
                    query,
                    principal,
                    # SQL honors this only for the snapshot and configuration
                    # it then selects itself, so a concurrent change withholds
                    # money.
                    proof=giving_proof(campaign),
                    parish_name=system.active_configuration.parish.name,
                    configuration=configuration,
                    # The same size drives the table's paging arithmetic.
                    page_size=size,
                )

            result = read()
            # A stale Next click after the result shrank shows the last page.
            moved = clamp_query(query, result["total"], size)
            if moved is not None:
                query = moved
                result = read()
            count, total = len(result["rows"]), result["total"]
            # The export form is offered disabled while the campaign cannot
            # accept new work, so the page never invites a request it refuses.
            mutable = True
            try:
                admit_campaign(campaign_id, mutating=True)
            except PermissionError:
                mutable = False
            context = result | {
                "campaign_id": campaign_id,
                "query": query,
                "query_fields": query.form_values(),
                "frequencies": FREQUENCY_LABELS,
                "table": report_table(
                    result["rows"],
                    number=query.page,
                    size=size,
                    total=total,
                    carry=carried_filters(query),
                    sorting=FINANCIAL_SORTING,
                    sort=query.sort,
                    action=reverse("admin:financial_report", args=(campaign_id,)),
                    sizes=PAGE_SIZES,
                ),
                "mutable": mutable,
                "request_key": uuid4(),
                "export_timezones": sorted(timezone_names()),
            }
            return iter(
                (
                    render_to_string(
                        "stewardship/financial-report.html", context, request=request
                    ).encode(),
                )
            )

        response = campaign_response(
            request,
            [campaign_id],
            authorize=authorize,
            open_content=content,
            on_close=finish,
        )
        if response.status_code == 503 and not response.streaming:
            # The shared guard has already released its read transaction and
            # answered unavailable inputs itself. Give this report its safe
            # recovery navigation, never private contents.
            return _error(campaign_id, status=503)
        handed_off = response.status_code == 200 and response.streaming
        return response
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return _error(campaign_id, status=503)
    except ValueError as error:
        # A value the read model could not shape is a persistent defect that
        # every retry reproduces, unlike the transient failures above. Record it
        # for operators before the recovery page, so the page cannot hide it.
        emit_failure(error, event=Event.REPORT_SHAPING_FAILED)
        return _error(campaign_id, status=503)
    finally:
        if finish is not None and not handed_off:
            finish(False)

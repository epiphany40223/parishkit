"""Private native Admin/Staff financial stewardship detail report."""

from uuid import uuid4

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.domain import Money
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.observability import Event, debug_swallowed, emit_failure
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.dates import UnknownZone
from parishkit.stewardship.web.filter_errors import (
    FilterError,
    error_context,
    line_sizes,
    refused,
)
from parishkit.stewardship.web.refusals import with_status_code
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.stat_tiles import tile
from parishkit.stewardship.web.tables import report_table

from .chart_specs import financial_charts
from .export_services import admit_campaign
from .export_views import SAFE_FAILURES
from .family_links import link_families
from .financial import (
    FIELD_MESSAGES,
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


def summary_tiles(summary):
    """The summary of matching pledges as stat tiles (#720, #1540).

    The matching Families, their total annual pledges and how many said they
    cannot contribute financially. A total that cannot be added up reads "—"
    with its reason, never zero (ui-conventions "Stat tiles").
    """
    total = summary["annual_total"]
    # Money refuses totals above $999,999,999.99, which MoneyAmount does
    # not; one parish's annual pledges never come near that, so the bound
    # is left to raise rather than handled.
    total = Money(total.cents) if total.available else None
    return [
        tile("families", _("Families"), summary["families"]),
        tile(
            "annual-total",
            _("Total annual pledges"),
            total,
            unknown_reason=_(
                "The total is added up only when every matching Family's "
                "pledge amount is known."
            )
            if total is None
            else "",
        ),
        tile("cannot-give", _("Cannot contribute financially"), summary["cannot_give"]),
    ]


# The element id of each filter field a refusal can be shown at (#1332).
FIELD_IDS = {
    "first_start": "first-start",
    "first_end": "first-end",
    "latest_start": "latest-start",
    "latest_end": "latest-end",
    "pledge_min": "pledge-min",
    "pledge_max": "pledge-max",
}
# The reserved message line each of them is described by: one per range,
# shown once after both its ends (UX conventions, Field errors).
FIELD_LINES = {
    "first_start": "first-response-dates",
    "first_end": "first-response-dates",
    "latest_start": "latest-response-dates",
    "latest_end": "latest-response-dates",
    "pledge_min": "pledge-range",
    "pledge_max": "pledge-range",
}


def _principal(request, store, *, read_only=False):
    """Every page reloads current policy; a Ministry leader never qualifies."""
    principal = authenticated_admin(
        request, store=store, activity=not read_only, read_only=read_only
    )
    if not allows(principal, Capability.FINANCIAL_DETAIL):
        raise PermissionError("Financial stewardship detail is unavailable.")
    return principal


def _error(campaign_id, *, status, zone=False):
    """No private filter or exception values, and a way back to the report.

    ``zone`` marks dates sent without the browser's time zone (#558).
    """
    debug_swallowed("report request refused")
    response = HttpResponse(
        render_to_string(
            "stewardship/financial-report-error.html",
            {"campaign_id": campaign_id, "status": status, "zone": zone},
        ),
        status=status,
        headers={"Cache-Control": "no-store"},
    )
    response.stewardship_safe_error = True
    if status == 503:
        response["Retry-After"] = "5"
    # The refusal log's codes for this page of its own (#1403).
    return with_status_code(response)


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
    Filters refused at a field (``FilterError``) draw the page again,
    answered 400, with the error at that field and the results for no
    filters, as Campaign comparison shows its current day for a refused
    day (#1332); the audit then ends denied, not succeeded.
    """
    finish, handed_off, refusal = None, False, None
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
        except FilterError as error:
            # Every closed choice was accepted before a field rule was
            # checked, so the submitted sort is one the selection knows.
            refusal, query = error, FinancialQuery(sort=error.query.sort)
        except UnknownZone:
            return _error(campaign_id, status=400, zone=True)
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
            outcome = Outcome.SUCCEEDED if completed else Outcome.FAILED
            if completed and refusal is not None:
                # The page was shown, but not for the filters asked for.
                outcome = Outcome.DENIED
            try:
                _audit(
                    principal,
                    campaign_id,
                    outcome,
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
            # Each Family name opens its Timeline (#1570) for a viewer who
            # may open it: one read of the page's Family record ids.
            if allows(principal, Capability.CAMPAIGN_REPORT):
                link_families(campaign_id, result["rows"])
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
                # The summary's tiles and charts (#720), drawn by chart-v1.js.
                "summary_tiles": summary_tiles(result["summary"]),
                "charts": financial_charts(result["summary"], result["share_choices"]),
                "table": report_table(
                    result["rows"],
                    number=query.page,
                    size=size,
                    total=total,
                    carry=carried_filters(query),
                    sorting=FINANCIAL_SORTING,
                    sort=query.sort,
                    action=reverse("admin:financial_report"),
                    sizes=PAGE_SIZES,
                ),
                "mutable": mutable,
                "request_key": uuid4(),
                # Per request, in the request's language.
                "filter_sizes": line_sizes(FIELD_MESSAGES, FIELD_LINES),
            }
            if refusal is not None:
                # The filter form shows what was sent, with the error at its
                # field; the panel counts, and the export carries, only the
                # filters the results use (none).
                context |= error_context(refusal, FIELD_IDS, FIELD_LINES) | {
                    "applied": query,
                    "query": refusal.query,
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
        if handed_off and refusal is not None:
            # The refusal was known before the response was made, so its
            # status is set before any byte is sent.
            refused(response)
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

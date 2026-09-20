"""Private native Admin/Staff financial stewardship detail report."""

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError
from django.template.loader import render_to_string
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.observability import Event, emit_failure
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.security import private_response

from .export_views import SAFE_FAILURES
from .financial import (
    FREQUENCY_LABELS,
    PAGE_SIZE,
    FinancialQuery,
    financial_page,
    giving_proven,
)
from .read_admission import admit_report_read


def _principal(request, store, *, read_only=False):
    """Every page reloads current policy; a Ministry leader never qualifies."""
    principal = authenticated_admin(
        request, store=store, activity=not read_only, read_only=read_only
    )
    if not allows(principal, Capability.FINANCIAL_DETAIL):
        raise PermissionError("Financial stewardship detail is unavailable.")
    return principal


def _audit(principal, campaign_id, outcome, count=0, total=0):
    """Retain parish-owned access evidence, never a filter, name or amount."""
    with work_transaction():
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
        if request.GET:
            raise ValueError("Financial filters require private POST state.")
        parameters = request.POST.copy()
        parameters.pop("csrfmiddlewaretoken", None)
        query = FinancialQuery.parse(parameters)
        admit_report_read(campaign_id)
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
            nonlocal count, total
            campaign = Campaign.objects.select_related("active_configuration").get(
                pk=campaign_id
            )
            configuration = campaign.active_configuration.values
            system = SystemConfiguration.objects.select_related(
                "active_configuration__parish"
            ).get()
            # The one completeness rule shared with the Family form. An archived
            # campaign has no giving window, so its comparison is unavailable.
            current = SourceCurrent.objects.filter(singleton=True).first()
            giving = bool(current and current.snapshot_id) and giving_proven(
                campaign_id, configuration, current.snapshot_id
            )
            result = financial_page(
                campaign_id,
                query,
                principal,
                giving=giving,
                parish_name=system.active_configuration.parish.name,
                configuration=configuration,
            )
            count, total = len(result["rows"]), result["total"]
            context = result | {
                "campaign_id": campaign_id,
                "query": query,
                "query_fields": query.form_values(),
                "frequencies": FREQUENCY_LABELS,
                "previous_page": query.page - 1 if query.page > 1 else None,
                "next_page": query.page + 1 if query.page * PAGE_SIZE < total else None,
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
        handed_off = response.status_code == 200 and response.streaming
        return response
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return denial(status=503, retry=5)
    except ValueError:
        return private_response("Invalid financial report filters.\n", status=400)
    finally:
        if finish is not None and not handed_off:
            finish(False)

"""Assigned-leader reporting without granting parish-wide campaign access."""

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.observability import Event, emit_failure
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.security import private_response

from .export_views import SAFE_FAILURES
from .ministries import PAGE_SIZE, STATES, MinistryQuery, can_report, ministry_page
from .read_admission import admit_report_read
from .workspace_views import campaign_ids


def _principal(request, store, *, read_only=False):
    """Authenticate native Google sessions and require a current reporting scope."""
    actor = authenticated_admin(
        request, store=store, activity=not read_only, read_only=read_only
    )
    if not can_report(actor):
        raise PermissionError("Ministry report access is unavailable.")
    return actor


@require_GET
def index(request):
    """Discover only campaign identities; selected report admission owns all data."""
    try:
        service = runtime()
        _principal(request, service.store)
        if request.GET:
            raise ValueError("Invalid Ministry navigation.")
        choices = campaign_ids()
        current = SystemConfiguration.objects.values_list(
            "current_campaign_id", flat=True
        ).get()
        response = (
            redirect(
                "admin:ministry_report",
                campaign_id=current if current in choices else choices[0],
            )
            if choices
            else render(request, "stewardship/report-empty.html")
        )
        response["Cache-Control"] = "no-store"
        return response
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except SAFE_FAILURES:
        return denial(status=503, retry=5)
    except ValueError:
        return private_response("Invalid Ministry filters.\n", status=400)


def _audit(actor, campaign_id, query, outcome, count=0, total=0, scope=()):
    """Record each displayed Ministry DUID, never names, contacts or search text."""
    with work_transaction():
        system = SystemConfiguration.objects.select_related(
            "active_configuration__parish"
        ).get()
        for ministry in scope or (None,):
            context = {
                "outcome": outcome,
                "count": count,
                "matching_count": total,
                "page": query.page,
                "search_used": bool(query.search),
            }
            if ministry is not None:
                context["ministry_duid"] = ministry
            record_action(
                Action.MINISTRY_REPORT_VIEWED,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=actor.identity,
                subject_id=campaign_id,
                parish_id=system.active_configuration.parish.pk,
                campaign_id=campaign_id,
                context=context,
            )


@require_http_methods(["GET", "POST"])
def report(request, campaign_id, *, ministry_id=None, action="join"):
    """Hold purge protection through projection, rendering and streaming."""
    finish, handed_off = None, False
    try:
        service = runtime()
        actor = _principal(request, service.store)
        if request.GET:
            raise ValueError("Ministry filters require private POST state.")
        parameters = request.POST.copy()
        parameters.pop("csrfmiddlewaretoken", None)
        query = MinistryQuery.parse(parameters, detail=ministry_id is not None)
        admit_report_read(campaign_id)
        _audit(actor, campaign_id, query, Outcome.STARTED)
        finalized, count, total, scope = False, 0, 0, ()

        def finish(completed):
            """Audit completion once, after releasing the read transaction."""
            nonlocal finalized
            if finalized:
                return
            finalized = True
            try:
                _audit(
                    actor,
                    campaign_id,
                    query,
                    Outcome.SUCCEEDED if completed else Outcome.FAILED,
                    count,
                    total,
                    scope,
                )
            except (DatabaseError, StorageInvariantError) as error:
                emit_failure(error, event=Event.REPORT_AUDIT_FAILED)

        def authorize(guard):
            """Replace the outer actor: a Staff-to-leader change narrows projection."""
            nonlocal actor
            fresh = _principal(request, service.store, read_only=True)
            if fresh.identity != actor.identity:
                raise PermissionError("Ministry report access changed.")
            actor = fresh
            admit_report_read(campaign_id)

        def content():
            """Only detached authorized data is passed to the native report template."""
            nonlocal count, total, scope
            result = ministry_page(
                campaign_id, query, actor, ministry_id=ministry_id, action=action
            )
            count = len(
                result["rows"] if ministry_id is not None else result["summaries"]
            )
            total = result["total"]
            scope = (
                (ministry_id,)
                if ministry_id is not None
                else tuple(row["duid"] for row in result["summaries"])
            )
            context = result | {
                "campaign_id": campaign_id,
                "ministry_id": ministry_id,
                "action": action,
                "query": query,
                "query_fields": query.form_values(),
                "states": STATES,
                "report_url": request.path_info,
                "summary_url": reverse("admin:ministry_report", args=[campaign_id]),
                "previous_page": query.page - 1 if query.page > 1 else None,
                "next_page": query.page + 1 if query.page * PAGE_SIZE < total else None,
            }
            return iter(
                (
                    render_to_string(
                        "stewardship/ministry-report.html", context, request=request
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
        return private_response("Invalid Ministry filters.\n", status=400)
    finally:
        if finish is not None and not handed_off:
            finish(False)

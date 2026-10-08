"""Assigned-leader reporting without granting parish-wide campaign access."""

from uuid import uuid4

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from parishkit.stewardship.accounts.admin_navigation import PAGES
from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.observability import Event, emit_failure
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.report_errors import report_unavailable
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.security import private_response
from parishkit.stewardship.web.tables import report_table
from parishkit.stewardship.workflows.models import MinistryRequest
from parishkit.stewardship.workflows.roster import mark, roster_marks

from .export_services import admit_campaign
from .export_views import SAFE_FAILURES
from .ministries import (
    DETAIL_SORTING,
    STATES,
    SUMMARY_SORTING,
    MinistryQuery,
    campaign_ids,
    can_report,
    ministry_page,
)
from .read_admission import admit_report_read
from .report_paging import clamp_query


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
        actor = _principal(request, service.store)
        if request.GET:
            raise ValueError("Invalid Ministry navigation.")
        choices = campaign_ids(actor)
        current = SystemConfiguration.objects.values_list(
            "current_campaign_id", flat=True
        ).get()
        # Only the current campaign is reported until #145 (rule 10); with no
        # current campaign the reader sees the "no campaign" page.
        response = (
            redirect("admin:ministry_report", campaign_id=current)
            if current in choices
            else render(
                request,
                "stewardship/report-empty.html",
                # Named after the report the reader opened (one name per page).
                {"page_name": PAGES["ministry_reports"].label},
            )
        )
        response["Cache-Control"] = "no-store"
        return response
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except SAFE_FAILURES:
        return report_unavailable()
    except ValueError:
        return private_response("Invalid Ministry filters.\n", status=400)


@require_GET
def picker(request):
    """The retired Ministry campaign chooser: go to Ministry requests.

    Reports show the current campaign only until the single-campaign change
    (#145; navigation rule 10). The redirect reveals nothing, so it needs no
    sign-in check of its own; Ministry requests checks access as before. It
    is temporary (302) for the same reason as the report chooser's
    (``campaign_picker``): NAV-11 moves its target and adds the permanent
    redirects.
    """
    response = redirect("admin:ministry_reports")
    response["Cache-Control"] = "no-store"
    return response


def _audit(actor, campaign_id, query, outcome, count=0, total=0, scope=()):
    """Record each displayed Ministry DUID, never names, contacts or search text."""
    # An audit append takes no row locks, so it need not join the
    # writers' work order; waiting there stalled report pages behind
    # every source promotion and installer.
    with transaction.atomic():
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
def report(request, campaign_id, *, action=None):
    """Hold purge protection through projection, rendering and streaming."""
    finish, handed_off = None, False
    try:
        service = runtime()
        actor = _principal(request, service.store)
        if request.GET:
            raise ValueError("Ministry filters require private POST state.")
        parameters = request.POST.copy()
        parameters.pop("csrfmiddlewaretoken", None)
        ministry_id = None
        if action is not None:
            values = parameters.pop("ministry", [])
            if (
                len(values) != 1
                or not values[0].isascii()
                or not values[0].isdecimal()
                or len(values[0]) > 10
                or str(int(values[0])) != values[0]
                or not 0 < int(values[0]) < 2**31
            ):
                raise ValueError("A canonical Ministry selection is required.")
            ministry_id = int(values[0])
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
            nonlocal count, total, scope, query

            def read():
                """One page of the role-scoped selection for the current query."""
                return ministry_page(
                    campaign_id,
                    query,
                    actor,
                    ministry_id=ministry_id,
                    action=action or "join",
                )

            result = read()
            # A page past the end (typed, bookmarked or a stale Next) shows
            # the last page, as on every other Admin table.
            moved = clamp_query(query, result["total"], query.page_size)
            if moved is not None:
                query = moved
                result = read()
            count = len(
                result["rows"] if ministry_id is not None else result["summaries"]
            )
            total = result["total"]
            scope = (
                (ministry_id,)
                if ministry_id is not None
                else tuple(row["duid"] for row in result["summaries"])
            )
            # The roster tick (#528), read-only here, for the shown rows only:
            # the frozen selection and its stored export are unchanged.
            # The selection's rows do not say whether a refresh resolved a
            # request, so that comes from one query for the shown rows; then
            # each row reads Entered, Not yet entered or Already in
            # ParishSoft, as on the follow-up queue.
            shown = [row["id"] for row in result["rows"]]
            from_source = {
                str(pk)
                for pk in MinistryRequest.objects.filter(
                    pk__in=shown, resolution_source__isnull=False
                ).values_list("pk", flat=True)
            }
            marks = roster_marks(shown)
            for row in result["rows"]:
                row["source_resolved"] = str(row["id"]) in from_source
                mark(row, marks)
            mutable = True
            try:
                admit_campaign(campaign_id, mutating=True)
            except PermissionError:
                mutable = False
            context = result | {
                "mutable": mutable,
                "export_key": uuid4(),
                "packet_key": uuid4(),
                "export_fields": query.form_values(),
                "export_timezones": sorted(timezone_names()),
                "campaign_id": campaign_id,
                "ministry_id": ministry_id,
                "action": action,
                "query": query,
                "states": STATES,
                "report_url": request.path_info,
                "summary_url": reverse("admin:ministry_report", args=[campaign_id]),
                # One shared navigator and sortable headings for whichever
                # list this page pages: the summary, or one Ministry's rows.
                "table": report_table(
                    result["rows"] if ministry_id is not None else result["summaries"],
                    number=query.page,
                    size=query.page_size,
                    total=total,
                    carry=[
                        (key, value)
                        for key, value in query.form_values().items()
                        if key != "sort"
                    ]
                    + ([("ministry", str(ministry_id))] if ministry_id else []),
                    sorting=DETAIL_SORTING if ministry_id else SUMMARY_SORTING,
                    sort=query.sort,
                    action=request.path_info,
                ),
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
        return report_unavailable()
    except ValueError:
        return private_response("Invalid Ministry filters.\n", status=400)
    finally:
        if finish is not None and not handed_off:
            finish(False)

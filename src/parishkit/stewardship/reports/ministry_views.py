"""Assigned-leader reporting without granting parish-wide campaign access."""

from urllib.parse import urlencode
from uuid import uuid4

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.accounts.admin_navigation import PAGES
from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.observability import Event, emit_failure
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.dates import UnknownZone
from parishkit.stewardship.web.report_errors import report_unavailable
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.security import private_response
from parishkit.stewardship.web.tables import report_table

from .chart_specs import ministry_followup
from .export_services import admit_campaign
from .export_views import SAFE_FAILURES
from .ministries import (
    SUMMARY_SORTING,
    ZONE_MESSAGE,
    MinistryQuery,
    campaign_ids,
    can_report,
    matching_summaries,
    ministry_page,
    packet_choices,
    reads_every_ministry,
    summary_totals,
    unscoped,
)
from .ministry_exports import MAX_PACKET_MINISTRIES
from .read_admission import admit_report_read
from .report_paging import clamp_query


def _principal(request, store, *, read_only=False):
    """Authenticate native Google sessions and require a current reporting scope."""
    actor = authenticated_admin(
        request, store=store, activity=not read_only, read_only=read_only
    )
    if not can_report(actor):
        raise unscoped(actor, "Ministry report access is unavailable.")
    return actor


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


# What each summary count opens in Ministry follow-up (#1133): the queue's
# own filter names and closed values, so the queue shows exactly the
# requests counted. Joining and Leaving count every current request of that
# kind, whatever its status, so they choose Any status; Unresolved is the
# queue's default status with either request, the Home panel's address.
QUEUE_FILTERS = {
    "joining": {"action": "join", "state": "any"},
    "leaving": {"action": "leave", "state": "any"},
    "unresolved": {},
}


def queue_links(actor, summary):
    """The follow-up queue addresses one summary row's counts link to (#1133).

    A count links only when it is not zero and the reader may follow up
    that Ministry, the check the queue itself makes on the link: a Ministry
    leader gets links only for the Ministries they lead. The address carries
    only the Ministry number and closed filter values, never a search.
    """
    if not allows(actor, Capability.MINISTRY_FOLLOWUP, ministry_id=summary["duid"]):
        return {}
    base = reverse("admin:ministry_followup")
    return {
        count: f"{base}?{urlencode({'ministry': summary['duid']} | values)}"
        for count, values in QUEUE_FILTERS.items()
        if summary[count]
    }


@require_http_methods(["GET", "POST"])
def report(request, campaign_id):
    """Hold purge protection through projection, rendering and streaming.

    Ministry requests is the campaign-wide summary only: one Ministry's
    Members joining and Members leaving lists were retired (#1157), since
    each request's contact details are on its follow-up page.
    """
    finish, handed_off = None, False
    try:
        service = runtime()
        actor = _principal(request, service.store)
        if request.GET:
            raise ValueError("Ministry filters require private POST state.")
        if campaign_id is None or campaign_id not in campaign_ids(actor):
            # Reports show the current campaign (rule 10). With none, or one
            # this reader has no Ministry report in (no Ministry module, or
            # none of the leader's Ministries), the reader sees the "no
            # campaign" page named after this report, as the retired
            # /admin/ministry-reports/ root showed (NAV-11).
            response = render(
                request,
                "stewardship/report-empty.html",
                {"page_name": PAGES["ministry_report"].label},
            )
            response["Cache-Control"] = "no-store"
            return response
        parameters = request.POST.copy()
        parameters.pop("csrfmiddlewaretoken", None)
        query = MinistryQuery.parse(parameters)
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
                """One page of the role-scoped summary for the current query."""
                return ministry_page(campaign_id, query, actor)

            result = read()
            # A page past the end (typed, bookmarked or a stale Next) shows
            # the last page, as on every other Admin table.
            moved = clamp_query(query, result["total"], query.page_size)
            if moved is not None:
                query = moved
                result = read()
            for summary in result["summaries"]:
                summary["queue_links"] = queue_links(actor, summary)
            count = len(result["summaries"])
            total = result["total"]
            scope = tuple(row["duid"] for row in result["summaries"])
            mutable = True
            try:
                admit_campaign(campaign_id, mutating=True)
            except PermissionError:
                mutable = False
            # Every Ministry matching the search, not just this page's rows,
            # for the totals and the follow-up chart (#720).
            matching = matching_summaries(
                result,
                query,
                lambda given: ministry_page(campaign_id, given, actor, every=True),
            )
            totals = summary_totals(matching)
            context = result | {
                "totals": totals,
                "followup_chart": ministry_followup(totals),
                # The packet's choices, on the summary only: every Ministry
                # in scope, not just this page's rows (#533). The matching
                # rows are already the whole scope without a search.
                "packet_choices": packet_choices(
                    {"summaries": matching, "total": total},
                    query,
                    lambda: ministry_page(
                        campaign_id, MinistryQuery(), actor, every=True
                    ),
                ),
                "packet_limit": MAX_PACKET_MINISTRIES,
                # The "Not in ParishSoft data" and campaign marks, for
                # Staff and Administrators only (#1135).
                "show_marks": reads_every_ministry(actor),
                "mutable": mutable,
                "packet_key": uuid4(),
                "campaign_id": campaign_id,
                "query": query,
                "report_url": request.path_info,
                # The shared navigator and sortable headings of the summary.
                "table": report_table(
                    result["summaries"],
                    number=query.page,
                    size=query.page_size,
                    total=total,
                    carry=[
                        (key, value)
                        for key, value in query.form_values().items()
                        if key != "sort"
                    ],
                    sorting=SUMMARY_SORTING,
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
    except UnknownZone:
        return private_response(ZONE_MESSAGE, status=400)
    except ValueError:
        return private_response("Invalid Ministry filters.\n", status=400)
    finally:
        if finish is not None and not handed_off:
            finish(False)

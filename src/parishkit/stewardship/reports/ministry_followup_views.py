"""Private native Ministry follow-up queue, history and optimistic editing."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.observability import Event, emit_failure
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError
from parishkit.stewardship.web.contracts import expected_version, filters
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.workflows.followup import (
    MAX_BULK,
    WorkflowChange,
    assign_requests,
    update_request,
)
from parishkit.stewardship.workflows.models import STAFF_STATES, MinistryRequest

from .export_services import admit_campaign
from .export_views import SAFE_FAILURES
from .information import parse_page
from .ministry_followup import (
    CHANNELS,
    OUTCOMES,
    PAGE_SIZE,
    STATES,
    FollowupQuery,
    assignable,
    can_follow_up,
    followup_history,
    followup_page,
    valid_ministry,
)
from .read_admission import admit_report_read

TEMPLATE = "stewardship/ministry-followup.html"
FORMER = "Former portal user"


def _principal(request, store, *, read_only=False):
    """Every queue, request and mutation reloads the current follow-up scope."""
    principal = authenticated_admin(
        request, store=store, activity=not read_only, read_only=read_only
    )
    if not can_follow_up(principal):
        raise PermissionError("Ministry follow-up is unavailable.")
    return principal


def _error(campaign_id, *, request_id=None, status=400):
    """No private form/exception values or database-dependent context processors."""
    response = HttpResponse(
        render_to_string(
            "stewardship/ministry-followup-error.html",
            {"campaign_id": campaign_id, "request_id": request_id, "status": status},
        ),
        status=status,
        headers={"Cache-Control": "no-store"},
    )
    response.stewardship_safe_error = True
    if status == 503:
        response["Retry-After"] = "5"
    return response


def _audit(principal, campaign_id, outcome):
    """Retain parish-owned access evidence, not private search or displayed values."""
    with work_transaction():
        system = SystemConfiguration.objects.select_related(
            "active_configuration__parish"
        ).get()
        record_action(
            Action.MINISTRY_FOLLOWUP_VIEWED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=principal.identity,
            subject_id=campaign_id,
            parish_id=system.active_configuration.parish.pk,
            campaign_id=campaign_id,
            context={"outcome": outcome},
        )


def _labels(identities):
    """Map retained actor UUIDs to current emails; removed users stay anonymous."""
    return {
        str(key): email
        for key, email in PortalUser.objects.filter(
            pk__in=set(identities) - {None}
        ).values_list("id", "email")
    }


def _page_response(request, campaign_id, *, request_id=None):
    """Guard every data query, template render and byte; audit response completion."""
    finish, handed_off = None, False
    try:
        service = runtime()
        principal = _principal(request, service.store)
        if request_id is None:
            if request.GET:
                raise ValueError("Search and filters require a POST body.")
            parameters = request.POST.copy()
            parameters.pop("csrfmiddlewaretoken", None)
            query, history_page = FollowupQuery.parse(parameters), 1
        else:
            values = filters(request.GET, allowed={"page"})
            history_page = parse_page(values.get("page", "1"))
            query = FollowupQuery(state="any", history="all")
        admit_report_read(campaign_id)
        _audit(principal, campaign_id, Outcome.STARTED)
        finalized = False

        def finish(completed):
            """Read-only guards close before writing permanent access evidence."""
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
            """Replace the outer actor: a Staff-to-leader change narrows projection."""
            nonlocal principal
            fresh = _principal(request, service.store, read_only=True)
            if fresh.identity != principal.identity:
                raise PermissionError("Ministry follow-up access changed.")
            principal = fresh
            admit_report_read(campaign_id)

        def content():
            """All lazy SQL and rendering stay within the response-owned barrier."""
            result = followup_page(campaign_id, query, principal, request_id=request_id)
            mutable = True
            try:
                admit_campaign(campaign_id, mutating=True)
            except PermissionError:
                mutable = False
            item = result["rows"][0] if request_id else None
            history, more_history = (
                followup_history(request_id, history_page) if item else ([], False)
            )
            # Assignee choices exist only for one Ministry: the open request's,
            # or the queue's when it is filtered to a single Ministry for bulk use.
            ministry = (
                item["ministry_duid"]
                if item
                else int(query.ministry)
                if query.ministry
                else None
            )
            assignees = assignable(ministry) if ministry and mutable else []
            labels = _labels(
                [row["assignee_id"] for row in result["rows"]]
                + [row.actor_id for row in history]
                + [row.assignee_id for row in history]
            )
            for row in result["rows"]:
                row["assignee_label"] = (
                    labels.get(row["assignee_id"], FORMER) if row["assignee_id"] else ""
                )
            for row in history:
                row.actor_label = labels.get(str(row.actor_id), FORMER)
                row.assignee_label = (
                    labels.get(str(row.assignee_id), FORMER) if row.assignee_id else ""
                )
                row.state_label = STATES[row.state]
                row.outcome_label = OUTCOMES.get(row.outcome, "")
                row.channel_label = CHANNELS.get(row.contact_channel, "")
            context = dict(
                result,
                campaign_id=campaign_id,
                query=query,
                query_fields=query.form_values(),
                mutable=mutable,
                item=item,
                history=history,
                previous_history=history_page - 1 if history_page > 1 else None,
                next_history=history_page + 1 if more_history else None,
                request_key=uuid4(),
                assignees=assignees,
                bulk_ministry=ministry if not item else None,
                states=STATES,
                staff_states=[(key, STATES[key]) for key in STAFF_STATES],
                outcomes=OUTCOMES,
                channels=CHANNELS,
                viewer=str(principal.identity),
                previous_page=query.page - 1 if query.page > 1 else None,
                next_page=query.page + 1
                if query.page * PAGE_SIZE < result["total"]
                else None,
            )
            return iter(
                (render_to_string(TEMPLATE, context, request=request).encode(),)
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
        return _error(campaign_id, request_id=request_id, status=503)
    except ValueError:
        return _error(campaign_id, request_id=request_id)
    finally:
        if finish is not None and not handed_off:
            finish(False)


@require_http_methods(["GET", "POST"])
def queue(request, campaign_id):
    """Default GET is safe; private filtering and pagination use native POST."""
    return _page_response(request, campaign_id)


@require_GET
def detail(request, campaign_id, request_id):
    """Expose current workflow and bounded history for one in-scope request."""
    return _page_response(request, campaign_id, request_id=request_id)


def _assignee(value):
    """An empty selection unassigns; anything else is one canonical UUID."""
    return UUID(value) if value else None


def change_values(parameters):
    """Strict closed form grammar; absent optional fields are never ambiguous."""
    required = {
        "expected_version",
        "request_key",
        "state",
        "outcome",
        "assignee",
        "notes",
        "contact_channel",
        "contact_date",
        "contact_time",
        "contact_notes",
    }
    keys = set(parameters) - {"csrfmiddlewaretoken"}
    if keys != required or any(len(parameters.getlist(key)) != 1 for key in keys):
        raise ValueError("Invalid follow-up form.")
    text = {
        # Native forms encode textarea line breaks as CRLF, while maxlength
        # counts the browser's LF representation. Keep storage/replay canonical.
        key: parameters[key].replace("\r\n", "\n").replace("\r", "\n")
        for key in ("notes", "contact_notes")
    }
    channel = parameters["contact_channel"] or None
    moment = None
    if channel is not None:
        # The native date/time controls carry no zone; the page states UTC.
        moment = datetime.fromisoformat(
            f"{parameters['contact_date']}T{parameters['contact_time']}"
        ).replace(tzinfo=UTC)
    elif parameters["contact_date"] or parameters["contact_time"]:
        raise ValueError("A contact time requires its channel.")
    return dict(
        expected_version=expected_version(parameters["expected_version"]),
        request_key=UUID(parameters["request_key"]),
        change=WorkflowChange(
            assignee_id=_assignee(parameters["assignee"]),
            state=parameters["state"],
            outcome=parameters["outcome"] or None,
            notes=text["notes"],
            contact_channel=channel,
            contact_at=moment,
            contact_notes=text["contact_notes"],
        ),
    )


def _in_campaign(campaign_id, identities):
    """Bind the route's campaign before the owner rechecks scope and versions."""
    found = MinistryRequest.objects.filter(
        pk__in=identities,
        submission__campaign_id=campaign_id,
        submission__mode="live",
    ).count()
    if found != len(identities):
        raise PermissionError("This Ministry request is unavailable.")


def _mutation(request, campaign_id, request_id, work):
    """Shared denial, stale, outage and validation mapping for both mutations."""
    try:
        service = runtime()
        principal = _principal(request, service.store)
        if request.GET:
            raise ValueError("Follow-up edits require a POST body.")
        target = work(service.store, principal.identity)
        response = redirect(target[0], campaign_id=campaign_id, **target[1])
        response["Cache-Control"] = "no-store"
        return response
    except StaleRecordError:
        return _error(campaign_id, request_id=request_id, status=409)
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return _error(campaign_id, request_id=request_id, status=503)
    except ValueError:
        return _error(campaign_id, request_id=request_id)


@require_POST
def update(request, campaign_id, request_id):
    """Apply one optimistic edit, then redirect to the request's fresh detail."""

    def work(store, actor):
        values = change_values(request.POST)
        _in_campaign(campaign_id, [request_id])
        update_request(store, actor, request_id, **values)
        return "admin:ministry_followup_item", {"request_id": request_id}

    return _mutation(request, campaign_id, request_id, work)


def assignment_values(parameters):
    """One Ministry, one assignee and an exact selected `uuid:version` set."""
    keys = set(parameters) - {"csrfmiddlewaretoken"}
    if keys != {"request_key", "ministry", "assignee", "selected"} or any(
        len(parameters.getlist(key)) != 1 for key in keys - {"selected"}
    ):
        raise ValueError("Invalid bulk assignment form.")
    selected = parameters.getlist("selected")
    if not 1 <= len(selected) <= MAX_BULK or not valid_ministry(parameters["ministry"]):
        raise ValueError("Invalid bulk assignment selection.")
    versions = {}
    for value in selected:
        identity, _, version = value.partition(":")
        versions[UUID(identity)] = expected_version(version)
    if len(versions) != len(selected):
        raise ValueError("A request was selected more than once.")
    return dict(
        request_key=UUID(parameters["request_key"]),
        assignee_id=_assignee(parameters["assignee"]),
        versions=versions,
    ), int(parameters["ministry"])


@require_POST
def assign(request, campaign_id):
    """Assign an exact visible selection in one Ministry: all of it or none of it."""

    def work(store, actor):
        values, ministry = assignment_values(request.POST)
        _in_campaign(campaign_id, list(values["versions"]))
        # The form offered assignees for one Ministry only; never let a crafted
        # selection spread that choice across Ministries it was not offered for.
        if (
            MinistryRequest.objects.filter(pk__in=values["versions"])
            .exclude(ministry_duid=ministry)
            .exists()
        ):
            raise ValueError("A bulk assignment covers exactly one Ministry.")
        assign_requests(store, actor, **values)
        return "admin:ministry_followup", {}

    return _mutation(request, campaign_id, None, work)

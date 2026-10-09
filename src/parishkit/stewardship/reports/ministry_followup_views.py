"""Private native Ministry follow-up queue, history and optimistic editing."""

from dataclasses import replace
from uuid import UUID, uuid4

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.observability import Event, debug_swallowed, emit_failure
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError
from parishkit.stewardship.web.contracts import expected_version, filters
from parishkit.stewardship.web.dates import UnknownZone, browser_instant
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.tables import PAGE_SIZES, report_table
from parishkit.stewardship.workflows.followup import (
    FollowupRefusal,
    WorkflowChange,
    authorize_ministry,
    outcomes_for,
    update_request,
)
from parishkit.stewardship.workflows.models import (
    OPEN_STATES,
    STAFF_STATES,
    MinistryRequest,
)

from .export_services import admit_campaign
from .export_views import SAFE_FAILURES
from .information import parse_page
from .ministry_followup import (
    CHANNELS,
    HISTORY_STATES,
    OUTCOMES,
    SORTING,
    STATES,
    FollowupQuery,
    can_follow_up,
    followup_history,
    followup_page,
    valid_ministry,
)
from .read_admission import admit_report_read
from .report_paging import (
    clamp_query,
    next_open,
    pop_navigation,
    queue_token,
    recall_queue,
    remember_queue,
    with_queue,
)

TEMPLATE = "stewardship/ministry-followup.html"
FORMER = "Former portal user"
# The name this queue's views are remembered under (report_paging.py).
QUEUE = "ministry_followup"
# Rows per read while finding the next open request (the selection takes any
# LIMIT, so the largest page size keeps the scan to a few reads).
SCAN_SIZE = max(PAGE_SIZES)


def _recalled(request, campaign_id, token):
    """The remembered queue view's filters, or the default queue's.

    A token that is unknown here (another sign-in, an older view, or values a
    later release no longer accepts) shows the default queue, not an error.
    """
    values = recall_queue(request, QUEUE, campaign_id, token)
    try:
        return FollowupQuery.parse(values) if values is not None else FollowupQuery()
    except ValueError:
        return FollowupQuery()


def _view_values(query):
    """What a queue view remembers: its filters, sort, page and page size."""
    return query.form_values() | {"page": str(query.page), "size": query.size}


def _principal(request, store, *, read_only=False):
    """Every queue, request and mutation reloads the current follow-up scope."""
    principal = authenticated_admin(
        request, store=store, activity=not read_only, read_only=read_only
    )
    if not can_follow_up(principal):
        raise PermissionError("Ministry follow-up is unavailable.")
    return principal


def _linked_ministry(value, principal):
    """A Ministry number from a link (#533), checked against this person's scope.

    Links from Home's My Ministries panel open the queue for one Ministry by
    GET, so Back and reload work; only this closed value may travel in a
    URL (search stays private POST state). A malformed number is a 400; a
    well-formed number outside the person's scope is refused like any other
    access they do not have.
    """
    if not valid_ministry(value):
        raise ValueError("Invalid Ministry link.")
    if not allows(principal, Capability.MINISTRY_FOLLOWUP, ministry_id=int(value)):
        raise PermissionError("That Ministry is outside this person's follow-up scope.")
    return value


def _error(campaign_id, *, request_id=None, status=400, link=False):
    """No private form/exception values or database-dependent context processors.

    ``link`` marks a refused address (a GET with a malformed or out-of-date
    query), which says so instead of asking to reload a form never sent:
    reloading that address would only repeat the error.
    """
    debug_swallowed("report request refused")
    response = HttpResponse(
        render_to_string(
            "stewardship/ministry-followup-error.html",
            {
                "campaign_id": campaign_id,
                "request_id": request_id,
                "status": status,
                "link": link,
            },
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
    # An audit append takes no row locks, so it need not join the
    # writers' work order; waiting there stalled report pages behind
    # every source promotion and installer.
    with transaction.atomic():
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


# What each correctable refusal says, and the form fields (by name) it
# concerns: the one place a refusal code is tied to fields. Each names the
# one problem, not every rule (#553). The refused page marks those fields in
# error, shows the message beside them, and its summary links to the first.
# outcome_kind's message is built from its details (_refusal_error).
REFUSALS = {
    "outcome_required": (_("Choose an outcome for Resolved."), ("outcome",)),
    "outcome_kind": (None, ("outcome",)),
    "other_needs_notes": (
        _("Add notes: the outcome Other needs them."),
        ("notes",),
    ),
    "contact_incomplete": (
        _("Enter the date and time of the contact attempt."),
        ("contact_date", "contact_time"),
    ),
    "contact_future": (
        # The server checks this because the browser's clock can be wrong.
        _(
            "The contact attempt's date and time can't be in the future. "
            "If they aren't, check your computer's clock."
        ),
        ("contact_date", "contact_time"),
    ),
    # No usable browser zone came with the time (#558): a tab opened before
    # local-time entry, or a browser that reports none. The re-rendered page
    # fills the zone again, so saving again normally works.
    "contact_zone": (
        _(
            "The contact time came without your computer's time zone. Save "
            "again; if this repeats, check your computer's time zone setting."
        ),
        ("contact_date", "contact_time"),
    ),
}
# Each form field's element id on the request page.
FIELD_IDS = {
    "outcome": "followup-outcome",
    "notes": "followup-notes",
    "contact_date": "contact-date",
    "contact_time": "contact-time",
}
ACTIONS = {"join": _("join"), "leave": _("leave")}
# The submitted fields a refused page shows again.
FORM_FIELDS = (
    "expected_version",
    "state",
    "outcome",
    "notes",
    "contact_channel",
    "contact_date",
    "contact_time",
    "contact_zone",
    "contact_notes",
)


def refusal_fields(refusal, submitted):
    """The form fields, by name, that a FollowupRefusal concerns.

    An incomplete contact attempt names only the date or time that is
    missing; when neither is blank, one is malformed, so it names both.
    """
    names = REFUSALS[refusal.code][1]
    if refusal.code == "contact_incomplete":
        missing = tuple(name for name in names if not submitted.get(name, "").strip())
        return missing or names
    return names


def _refusal_error(refusal, submitted):
    """The error for one FollowupRefusal: its message, the fields it
    concerns (marked in error beside the message) and the field the
    summary links to, the first of them."""
    message = REFUSALS[refusal.code][0]
    if refusal.code == "outcome_kind":
        message = _("%(outcome)s doesn't apply to a request to %(action)s.") % {
            "outcome": OUTCOMES.get(refusal.details["outcome"], ""),
            "action": ACTIONS[refusal.details["action"]],
        }
    fields = refusal_fields(refusal, submitted)
    return {
        "code": refusal.code,
        "message": message,
        "fields": fields,
        "field_id": FIELD_IDS[fields[0]],
    }


def _page_response(request, campaign_id, *, request_id=None, refusal=None):
    """Guard every data query, template render and byte; audit response completion.

    With a refusal, the request page is shown again in place (status 400)
    with an error summary and the submitted values, instead of an error page.
    """
    finish, handed_off = None, False
    try:
        service = runtime()
        principal = _principal(request, service.store)
        if request_id is None:
            if request.GET:
                # A query string carries either the token of a remembered
                # view ("Return to Ministry follow-up", #534) or, from Home's
                # My Ministries panel, one Ministry (#533). Search and every
                # other filter stay private POST state.
                if request.method != "GET":
                    raise ValueError("Search and filters require a POST body.")
                values = filters(request.GET, allowed={"queue", "ministry"})
                if len(values) != 1:
                    raise ValueError("Use one query value.")
                if "ministry" in values:
                    # Remembered below like any view, so its token keeps
                    # the Ministry on each request page, Save and next and
                    # the way back to the queue.
                    query = FollowupQuery(
                        ministry=_linked_ministry(values["ministry"], principal)
                    )
                else:
                    query = _recalled(
                        request, campaign_id, queue_token(values["queue"])
                    )
            else:
                parameters = request.POST.copy()
                parameters.pop("csrfmiddlewaretoken", None)
                query = FollowupQuery.parse(parameters)
            history_page = 1
            # Remembered before the response streams: the session is saved
            # once the view returns.
            token = remember_queue(request, QUEUE, campaign_id, _view_values(query))
            queue_view, history_only = query, False
        else:
            values = filters(request.GET, allowed={"page", "queue"})
            history_page = parse_page(values.get("page", "1"))
            # A refused save shows this page again from its own POST body.
            token = queue_token(
                request.POST.get("queue", "")
                if refusal is not None
                else values.get("queue", "")
            )
            # A token the session doesn't hold (another sign-in, an older
            # view) is not echoed into this page's links and form.
            if token and recall_queue(request, QUEUE, campaign_id, token) is None:
                token = ""
            queue_view = _recalled(request, campaign_id, token)
            # An in-place history page swaps only the history (its links
            # are data-in-place-only), so it skips the scan; a reload of
            # that address is an ordinary load and scans as usual.
            history_only = (
                "page" in values and request.headers.get("X-Requested-With") == "fetch"
            )
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
            nonlocal query
            result = followup_page(campaign_id, query, principal, request_id=request_id)
            # A page past the end of the queue shows its last page, as on
            # every other Admin table; one open request is not paged.
            moved = (
                None
                if request_id
                else clamp_query(query, result["total"], query.page_size)
            )
            if moved is not None:
                query = moved
                result = followup_page(campaign_id, query, principal)
            mutable = True
            try:
                admit_campaign(campaign_id, mutating=True)
            except PermissionError:
                mutable = False
            item = result["rows"][0] if request_id else None
            # Save and next opens the next open request after this one in
            # the remembered queue, found now under this guarded read (#534).
            following = (
                next_open(
                    lambda number: followup_page(
                        campaign_id,
                        replace(queue_view, page=number, size=str(SCAN_SIZE)),
                        principal,
                    )["rows"],
                    SCAN_SIZE,
                    request_id,
                    lambda row: row["open"],
                )
                if item and item["open"] and mutable and not history_only
                else None
            )
            # The form's values: the request's own, or what was just submitted.
            form = (
                dict(
                    {key: "" for key in FORM_FIELDS},
                    expected_version=str(item["version"]),
                    state=item["state"],
                    notes=item["notes"] or "",
                )
                if item
                else {}
            )
            if item and refusal is not None:
                form.update(
                    (key, request.POST.get(key, ""))
                    for key in FORM_FIELDS
                    if key in request.POST
                )
            error = (
                _refusal_error(refusal, request.POST)
                if item and refusal is not None
                else {}
            )
            history, more_history = (
                followup_history(request_id, history_page) if item else ([], False)
            )
            # Past edits keep showing an assignment they recorded before
            # follow-up assignment was removed (#552); new edits record none.
            labels = _labels(
                [row.actor_id for row in history] + [row.assignee_id for row in history]
            )
            for row in history:
                row.actor_label = labels.get(str(row.actor_id), FORMER)
                row.assignee_label = (
                    labels.get(str(row.assignee_id), FORMER) if row.assignee_id else ""
                )
                row.state_label = HISTORY_STATES[row.state]
                row.outcome_label = OUTCOMES.get(row.outcome, "")
                row.channel_label = CHANNELS.get(row.contact_channel, "")
            context = dict(
                result,
                campaign_id=campaign_id,
                query=query,
                # The queue's shared navigator and sortable headings, as
                # private POST forms back to the queue (web/tables.py).
                table=report_table(
                    result["rows"],
                    number=query.page,
                    size=query.page_size,
                    total=result["total"],
                    carry=[
                        (key, value)
                        for key, value in query.form_values().items()
                        if key != "sort"
                    ],
                    sorting=SORTING,
                    sort=query.sort,
                    action=reverse("admin:ministry_followup"),
                ),
                mutable=mutable,
                item=item,
                queue_token=token,
                next_id=following,
                history=history,
                previous_history=history_page - 1 if history_page > 1 else None,
                next_history=history_page + 1 if more_history else None,
                request_key=uuid4(),
                states=STATES,
                staff_states=[(key, STATES[key]) for key in STAFF_STATES],
                # Only Resolved takes a chosen outcome (change_values), and
                # only those this kind of request can record (outcomes_for).
                resolved_outcomes=[
                    (key, OUTCOMES[key]) for key in outcomes_for(item["action"])
                ]
                if item
                else [],
                form=form,
                errors=[error] if error else [],
                # The refused fields, marked in error with the message beside
                # them (an empty dict when nothing was refused).
                field_error=error,
                # The page checks a contact time as it is typed, with the
                # server's own words for a time in the future (#592).
                future_message=REFUSALS["contact_future"][0],
                outcomes=OUTCOMES,
                channels=CHANNELS,
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
        if handed_off and refusal is not None:
            # A typed validation answer from this view: the request page the
            # person may already read, with their own submitted values, so the
            # security middleware keeps it rather than a generic 400 text.
            response.status_code = 400
            response.stewardship_safe_error = True
        return response
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return _error(campaign_id, request_id=request_id, status=503)
    except ValueError:
        return _error(campaign_id, request_id=request_id, link=request.method == "GET")
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


def _text(parameters, key):
    """One optional textarea, canonicalized; absent (a hidden field) is empty.

    Native forms encode textarea line breaks as CRLF, while maxlength counts
    the browser's LF representation. Keep storage/replay canonical.
    """
    return parameters.get(key, "").replace("\r\n", "\n").replace("\r", "\n")


def change_values(parameters):
    """Strict closed form grammar; fields that do not apply are ignored.

    The page hides, and so does not send, the outcome unless the status is
    Resolved and the contact details unless a channel is chosen. A stale or
    crafted form may still send them, so the server ignores whatever does
    not apply rather than refusing it: the outcome for any other status (Closed:
    no response always records No response, and open statuses have none),
    and the date, time and notes of a contact attempt that has no channel.
    Notes stay optional except for the outcome Other, which the database
    requires (ministry_revision_outcome).

    A contact date and time are typed in the browser's time zone, which
    ui-v1.js sends in ``contact_zone`` (#558); a contact attempt without a
    known zone is refused rather than guessed.
    """
    required = {"expected_version", "request_key", "state", "notes", "contact_channel"}
    optional = {
        "outcome",
        "contact_date",
        "contact_time",
        "contact_zone",
        "contact_notes",
    }
    keys = set(parameters) - {"csrfmiddlewaretoken"}
    if not required <= keys <= required | optional or any(
        len(parameters.getlist(key)) != 1 for key in keys
    ):
        raise ValueError("Invalid follow-up form.")
    state = parameters["state"]
    outcome = {
        "resolved": parameters.get("outcome") or None,
        "closed_no_response": "no_response",
    }.get(state)
    channel = parameters["contact_channel"] or None
    moment = None
    if channel is not None:
        try:
            moment = browser_instant(
                parameters.get("contact_date", ""),
                parameters.get("contact_time", ""),
                parameters.get("contact_zone", ""),
            )
        except UnknownZone:
            raise FollowupRefusal("contact_zone") from None
        except ValueError:
            raise FollowupRefusal("contact_incomplete") from None
    return dict(
        expected_version=expected_version(parameters["expected_version"]),
        request_key=UUID(parameters["request_key"]),
        change=WorkflowChange(
            state=state,
            outcome=outcome,
            notes=_text(parameters, "notes"),
            contact_channel=channel,
            contact_at=moment,
            contact_notes=_text(parameters, "contact_notes") if channel else "",
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
    """Denial, stale, outage and validation mapping for a follow-up edit.

    A correctable refusal re-renders the request page in place (#553); a
    malformed form still gets the plain 400 page.
    """
    try:
        service = runtime()
        principal = _principal(request, service.store)
        if request.GET:
            raise ValueError("Follow-up edits require a POST body.")
        response = redirect(work(service.store, principal.identity))
        response["Cache-Control"] = "no-store"
        return response
    except StaleRecordError:
        return _error(campaign_id, request_id=request_id, status=409)
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return _error(campaign_id, request_id=request_id, status=503)
    except FollowupRefusal as refusal:
        # Only work() raises one, so the service and principal are set.
        return _refused(
            request, campaign_id, request_id, refusal, service.store, principal
        )
    except ValueError:
        return _error(campaign_id, request_id=request_id)


def _refused(request, campaign_id, request_id, refusal, store, principal):
    """Show a correctable refusal on the request page, unless it went stale.

    Some refusals (a missing outcome, Other without notes, an incomplete
    contact) are found before the version check. If the request changed or
    closed since the form was loaded, re-rendering would pair the person's
    stale values with the current version, and resubmitting them would
    silently overwrite the other edit; so that answers as a conflict (409),
    exactly as a stale save does. Otherwise the page carries the submitted
    version, so a later change still reads as a conflict.

    These refusals can come before the edit's own scope check, so the
    caller's current authority for the request's Ministry is checked first:
    a request outside it gets the same denial as an unknown one, never a
    conflict that would reveal it exists or what version it is at.
    """
    try:
        current = (
            MinistryRequest.objects.filter(
                pk=request_id,
                submission__campaign_id=campaign_id,
                submission__mode="live",
            )
            .values_list("version", "state", "ministry_duid")
            .first()
        )
        if current is None:
            return denial()
        version, state, ministry = current
        authorize_ministry(store, principal.identity, ministry)
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (DatabaseError, *SAFE_FAILURES, StorageInvariantError):
        return _error(campaign_id, request_id=request_id, status=503)
    if request.POST.get("expected_version") != str(version) or state not in OPEN_STATES:
        return _error(campaign_id, request_id=request_id, status=409)
    return _page_response(request, campaign_id, request_id=request_id, refusal=refusal)


@require_POST
def update(request, campaign_id, request_id):
    """Apply one optimistic edit, then redirect to the request's fresh detail.

    Save and next redirects instead to the next open request the page found,
    or, after the last one, to the remembered queue view (#534). A refused or
    stale save never moves on.
    """

    def work(store, actor):
        parameters = request.POST.copy()
        token, advance, following = pop_navigation(parameters)
        values = change_values(parameters)
        _in_campaign(campaign_id, [request_id])
        update_request(store, actor, request_id, **values)
        if advance and following is None:
            url = reverse("admin:ministry_followup")
        else:
            url = reverse(
                "admin:ministry_followup_item",
                args=[following if advance else request_id],
            )
        return with_queue(url, token)

    return _mutation(request, campaign_id, request_id, work)

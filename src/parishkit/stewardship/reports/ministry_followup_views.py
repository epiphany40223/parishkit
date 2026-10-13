"""Private native Ministry follow-up queue, history and optimistic editing."""

from dataclasses import replace
from urllib.parse import urlencode
from uuid import UUID, uuid4

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from parishkit.stewardship.accounts.admin_navigation import PAGES
from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.observability import Event, debug_swallowed, emit_failure
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError
from parishkit.stewardship.time_entry import (
    TimeEntryError,
    canonical,
    is_blank,
    parse_time,
)
from parishkit.stewardship.web.contracts import (
    ErrorCode,
    FieldError,
    expected_version,
    filters,
    validation_response,
)
from parishkit.stewardship.web.dates import UnknownZone, browser_instant
from parishkit.stewardship.web.refusals import Refusal
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.tables import (
    PAGE_SIZES,
    paginate,
    report_table,
    table_parameters,
)
from parishkit.stewardship.workflows.followup import (
    BULK_ACTIONS,
    MAX_BULK,
    BulkStale,
    FollowupRefusal,
    WorkflowChange,
    apply_bulk_outcome,
    authorize_ministry,
    outcomes_for,
    update_request,
)
from parishkit.stewardship.workflows.models import (
    OPEN_STATES,
    STAFF_STATES,
    MinistryRequest,
)
from parishkit.stewardship.workflows.roster import (
    can_tick,
    mark,
    roster_marks,
    set_roster_entered,
)

from .export_services import admit_campaign
from .export_views import SAFE_FAILURES
from .information import parse_page
from .ministries import outcome_label, unscoped
from .ministry_followup import (
    CHANNELS,
    FILTER_OUTCOMES,
    HISTORY_STATES,
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
# The filters a link may carry (#533, #1133): one Ministry, and with it the
# queue's own closed Request and Status values. Never the search.
LINK_FILTERS = frozenset({"ministry", "action", "state"})
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
        raise unscoped(principal, "Ministry follow-up is unavailable.")
    return principal


def _linked_ministry(value, principal):
    """A Ministry number from a link (#533), checked against this person's scope.

    Links from Home's My Ministries panel and the counts on Ministry
    requests (#1133) open the queue for one Ministry by GET, so Back and
    reload work; only closed values may travel in a URL (search stays
    private POST state). A malformed number is a 400; a
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
    # A typed time the shared time-of-day parser cannot read (#398); its
    # message is the parser's own, from the refusal's details.
    "contact_time": (None, ("contact_time",)),
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
# What each bulk outcome is called on its button (#1131), by its posted
# "kind:outcome" value (BULK_ACTIONS).
BULK_NAMES = {
    "join:joined": _("Joined"),
    "join:declined": _("Declined"),
    "leave:leave_confirmed": _("Left"),
    "leave:declined": _("Stayed"),
}
# Why the bulk buttons of one kind are unavailable (ui-v1.js
# wireSelection's data-kind-hint-<kind>, shown in the bulk bar's reserved
# reason line): only requests of one kind are ticked, or both kinds are.
BULK_KIND_HINTS = {
    "join": _("Left and Stayed are for requests to leave."),
    "leave": _("Joined and Declined are for requests to join."),
    "mixed": _(
        "Tick only requests to join, or only requests to leave, to record an outcome."
    ),
}


def bulk_actions():
    """The queue's bulk outcome buttons and the dialog text each one sets.

    Each button opens the one shared confirmation dialog (#879), which takes
    its title, body and announcement from the button (ui-v1.js). ``kind``
    makes a button available only while every ticked request is of that
    kind (Administrator, 2026-10-13), so the dialog always acts on every
    ticked request; the server checks every request's kind itself
    (apply_bulk_outcome).
    """
    actions = []
    for kind, outcome in BULK_ACTIONS:
        value = f"{kind}:{outcome}"
        values = {"name": BULK_NAMES[value], "label": outcome_label(outcome, kind)}
        actions.append(
            {
                "value": value,
                "kind": kind,
                "label": _("Mark as %(name)s") % values,
                "title_one": _("Mark {name} as %(label)s?") % values,
                "title_many": _("Mark {count} requests as %(label)s?") % values,
                "done_one": _("Marked {name} as %(label)s.") % values,
                "done_many": _("Marked {count} requests as %(label)s.") % values,
                "body": _(
                    "Each request is resolved as %(label)s, with its own entry "
                    "in its follow-up history."
                )
                % values,
            }
        )
    return actions


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
    if refusal.code == "contact_time":
        message = refusal.details["message"]
    elif refusal.code == "outcome_kind":
        message = _("%(outcome)s doesn't apply to a request to %(action)s.") % {
            "outcome": outcome_label(refusal.details["outcome"], "any"),
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
            if campaign_id is None:
                # No current campaign: the "no campaign" page, as Ministry
                # requests shows (NAV-11 review).
                response = render(
                    request,
                    "stewardship/report-empty.html",
                    {"page_name": PAGES["ministry_followup"].label},
                )
                response["Cache-Control"] = "no-store"
                return response
            if request.GET:
                # A query string carries either the token of a remembered
                # view ("Return to Ministry follow-up", #534) or one
                # Ministry: from Home's My Ministries panel (#533), or with
                # a request type and status from a count on Ministry
                # requests (#1133). Search and every other filter stay
                # private POST state.
                if request.method != "GET":
                    raise ValueError("Search and filters require a POST body.")
                values = filters(request.GET, allowed={"queue", *LINK_FILTERS})
                if "ministry" in values and "queue" not in values:
                    # Remembered below like any view, so its token keeps
                    # these filters on each request page, Save and next and
                    # the way back to the queue. The parse checks the closed
                    # values; the Ministry is then checked against scope.
                    query = FollowupQuery.parse(values)
                    _linked_ministry(query.ministry, principal)
                elif values.keys() == {"queue"}:
                    query = _recalled(
                        request, campaign_id, queue_token(values["queue"])
                    )
                else:
                    raise ValueError("Use a queue token or a Ministry.")
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
            # The roster tick (#528): read for the shown rows only, so the
            # frozen queue selection is unchanged.
            marks = roster_marks(row["id"] for row in result["rows"])
            for row in result["rows"]:
                mark(row, marks)
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
                row.outcome_label = outcome_label(row.outcome, item["action"])
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
                roster_key=uuid4(),
                # Admin and Staff set the tick; a Ministry leader sees it only.
                can_tick=can_tick(principal),
                # The help about Ministries removed from the campaign is for
                # Staff and Administrators (#1135). A leader never meets one:
                # a leader leads only Ministries the campaign asks about (#922).
                campaign_marks=allows(principal, Capability.MINISTRY_FOLLOWUP),
                states=STATES,
                staff_states=[(key, STATES[key]) for key in STAFF_STATES],
                # Only Resolved takes a chosen outcome (change_values), and
                # only those this kind of request can record (outcomes_for).
                resolved_outcomes=[
                    (key, outcome_label(key, item["action"]))
                    for key in outcomes_for(item["action"])
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
                # The Outcome filter always uses the combined labels (both
                # meanings of declined, #1132): the filter form is kept in
                # place on Apply (data-table-sync), so per-kind option text
                # would go stale when the Request filter changes.
                outcomes=FILTER_OUTCOMES,
                channels=CHANNELS,
                # Bulk outcomes (#1131): one replay key per rendering of the
                # queue, sent with the selection, and the remembered view's
                # address, which the dialog redraws the table from once the
                # outcome is recorded, keeping its filters, sort and page.
                bulk_key=uuid4(),
                refresh_url=with_queue(reverse("admin:ministry_followup"), token),
                bulk_actions=bulk_actions(),
                bulk_kind_hints=BULK_KIND_HINTS,
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


def _contact_clock(text):
    """A typed contact time as ``HH:MM``; blank stays blank.

    The page bounds the entry at 32 characters, so a longer one is a
    malformed form, not a correctable refusal. An entry the parser refuses
    is refused in place with the parser's own message; a blank one is left
    for ``browser_instant`` to refuse as an incomplete contact attempt.
    """
    if len(text) > 32:
        raise ValueError("Invalid follow-up form.")
    if is_blank(text):
        return ""
    try:
        return canonical(parse_time(text))
    except TimeEntryError as error:
        raise FollowupRefusal("contact_time", message=error.message) from None


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
    known zone is refused rather than guessed. The time is typed in any
    common form (2:30 PM, 1430, noon) and read by the shared time-of-day
    parser, as every Admin time is (#398).
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
    return dict(
        expected_version=expected_version(parameters["expected_version"]),
        request_key=UUID(parameters["request_key"]),
        change=WorkflowChange(
            state=state,
            outcome=outcome,
            notes=_text(parameters, "notes"),
            **_contact_values(parameters),
        ),
    )


def _contact_values(parameters):
    """The optional contact attempt of a follow-up form, as change fields.

    Shared by a request's own form and the queue's bulk outcome dialog
    (#1131). Without a channel the attempt's date, time and notes are
    ignored, since the page does not send them; with one, the date and time
    are read in the browser's zone (``contact_zone``, #558) and the time in
    any common form (#398).
    """
    channel = parameters["contact_channel"] or None
    moment = None
    if channel is not None:
        # Read before the try below: its refusal (a ValueError) must not be
        # turned into an incomplete contact attempt.
        clock = _contact_clock(parameters.get("contact_time", ""))
        try:
            moment = browser_instant(
                parameters.get("contact_date", ""),
                clock,
                parameters.get("contact_zone", ""),
            )
        except UnknownZone:
            raise FollowupRefusal("contact_zone") from None
        except ValueError:
            raise FollowupRefusal("contact_incomplete") from None
    return dict(
        contact_channel=channel,
        contact_at=moment,
        contact_notes=_text(parameters, "contact_notes") if channel else "",
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


@require_POST
def roster(request, campaign_id, request_id):
    """Set or clear one resolved request's Entered in ParishSoft tick (#528).

    The answer is the page the tick was drawn on, Post/Redirect/Get, so it
    refreshes in place: the request's own page (keeping its queue token), or
    Roster changes to enter (``return_to=roster``), which keeps the list's
    own Show, sort, page and page size from the tick form, so the reader
    stays where they were. A tick someone else changed first is a conflict:
    on the request page a 409, as a stale follow-up save is; on the list,
    the list again with a notice (``changed=1``), so the reader never leaves
    it.
    """
    # Lazy: the list's module reads the follow-up selection from this one.
    from .ministry_roster_views import SHOW
    from .ministry_roster_views import SORTING as ROSTER_SORTING

    view_fields = {"show", "ministry", *table_parameters()}

    def work(store, actor):
        """Validate the closed form, record the tick and name the answer."""
        values = filters(
            request.POST,
            allowed={
                "sequence",
                "entered",
                "request_key",
                "return_to",
                "queue",
                "csrfmiddlewaretoken",
                *view_fields,
            },
        )
        back = values.get("return_to", "item")
        if values.get("entered") not in {"yes", "no"} or back not in {"item", "roster"}:
            raise ValueError("Invalid roster tick.")
        view = {key: values[key] for key in sorted(view_fields) if key in values}
        if view.get("show", "todo") not in SHOW or (
            view.get("ministry") and not valid_ministry(view["ministry"])
        ):
            raise ValueError("Invalid roster list choice.")
        # Refuse a bad sort, page or size now, as the list itself would.
        paging = {
            key: value for key, value in view.items() if key not in {"show", "ministry"}
        }
        paginate([], paging, sorting=ROSTER_SORTING)
        token = queue_token(values.get("queue", ""))
        _in_campaign(campaign_id, [request_id])
        listed = reverse("admin:ministry_roster")
        try:
            set_roster_entered(
                store,
                actor,
                request_id,
                sequence=expected_version(values.get("sequence", "")),
                entered=values["entered"] == "yes",
                request_key=UUID(values.get("request_key", "")),
            )
        except StaleRecordError:
            if back != "roster":
                raise
            return f"{listed}?{urlencode(view | {'changed': '1'})}"
        if back == "roster":
            return f"{listed}?{urlencode(view)}" if view else listed
        return with_queue(
            reverse("admin:ministry_followup_item", args=[request_id]), token
        )

    return _mutation(request, campaign_id, request_id, work)


# What a refused bulk outcome says in its dialog (#1131). Each names the one
# request it is about, by the names the queue row shows, read from the
# scoped selection; nothing submitted is echoed.
BULK_STALE = _(
    "%(row)s changed after this list was shown (someone else may have "
    "recorded follow-up on it), so nothing was saved."
)
BULK_KIND = _(
    "%(row)s is a request to %(action)s, so %(outcome)s doesn't apply to it. "
    "Nothing was saved."
)
BULK_FIX = _("Cancel, then reload the list and try again.")


def _bulk_selection(parameters):
    """The chosen requests: ``request`` values ``<uuid>:<version>``, as a dict.

    Each is a request id with the version its row showed, so every request
    is checked against its own version. At most one page (MAX_BULK), no
    request twice; anything else is a malformed form.
    """
    values = parameters.getlist("request")
    if not 1 <= len(values) <= MAX_BULK:
        raise ValueError("Choose between one and a page of requests.")
    chosen = {}
    for value in values:
        identity, separator, version = value.partition(":")
        if not separator or len(value) > 64:
            raise ValueError("Invalid follow-up selection.")
        chosen[UUID(identity)] = expected_version(version)
    if len(chosen) != len(values):
        raise ValueError("A request was chosen twice.")
    return chosen


def bulk_values(parameters):
    """Strict closed grammar of the bulk outcome dialog's POST.

    The button's "kind:outcome" (one of BULK_ACTIONS), the page's bulk
    key, the chosen
    requests and the optional contact attempt the dialog applies to each
    (as on a request's own form, _contact_values).
    """
    single = {"outcome", "bulk_key", "contact_channel"}
    optional = {"contact_date", "contact_time", "contact_zone", "contact_notes"}
    keys = set(parameters) - {"csrfmiddlewaretoken", "request"}
    if not single <= keys <= single | optional or any(
        len(parameters.getlist(key)) != 1 for key in keys
    ):
        raise ValueError("Invalid follow-up form.")
    kind, _separator, outcome = parameters["outcome"].partition(":")
    if (kind, outcome) not in BULK_ACTIONS:
        raise ValueError("Invalid bulk outcome.")
    contact = _contact_values(parameters)
    return dict(
        versions=_bulk_selection(parameters),
        action=kind,
        outcome=outcome,
        bulk_key=UUID(parameters["bulk_key"]),
        # The state, outcome and notes are set per request by the service.
        change=WorkflowChange(state="new", outcome=None, notes="", **contact),
    )


def _row_name(campaign_id, principal, request_id):
    """A request as its queue row names it ("Member — Ministry"), or None.

    Read through the scoped selection, so only a request the person may see
    is named; any failure falls back to a refusal that names no row.
    """
    try:
        with transaction.atomic():
            row = followup_page(
                campaign_id,
                FollowupQuery(state="any", history="all"),
                principal,
                request_id=request_id,
            )["rows"][0]
    except (DatabaseError, *SAFE_FAILURES, StorageInvariantError, IndexError):
        return None
    return f"{row['member_name']} — {row['ministry_name']}"


def _bulk_refusal(status, message):
    """A refusal the dialog shows below its buttons (ui-v1.js refusalText)."""
    return validation_response(
        [FieldError(ErrorCode.STALE if status == 409 else ErrorCode.INVALID)],
        status=status,
        refusal=Refusal(message, BULK_FIX if status == 409 else None),
    )


@require_POST
def bulk(request, campaign_id):
    """Record one outcome for the requests chosen on the queue (#1131).

    The shared confirmation dialog posts the chosen rows (each with its
    version), the outcome and an optional contact attempt, without a page
    load. All or none: a request outside the person's Ministries is denied
    (403); one that changed since the page was read (409) or is of the
    other kind (400) is named in the refusal the dialog
    shows; a contact attempt is checked as on a request's own form. Success
    answers 204, and the dialog redraws the table from the remembered view.
    """
    try:
        service = runtime()
        principal = _principal(request, service.store)
        if request.GET:
            raise ValueError("Follow-up edits require a POST body.")
        parameters = request.POST.copy()
        values = bulk_values(parameters)
        _in_campaign(campaign_id, list(values["versions"]))
        apply_bulk_outcome(service.store, principal.identity, **values)
        response = HttpResponse(status=204)
        response["Cache-Control"] = "no-store"
        return response
    except BulkStale as error:
        row = _row_name(campaign_id, principal, error.request_id)
        if row is None:
            return _error(campaign_id, status=409)
        return _bulk_refusal(409, BULK_STALE % {"row": row})
    except StaleRecordError:
        return _error(campaign_id, status=409)
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return _error(campaign_id, status=503)
    except FollowupRefusal as refusal:
        if refusal.code == "outcome_kind" and "request_id" in refusal.details:
            row = _row_name(campaign_id, principal, refusal.details["request_id"])
            if row is None:
                return _error(campaign_id)
            return _bulk_refusal(
                400,
                BULK_KIND
                % {
                    "row": row,
                    "action": ACTIONS[refusal.details["action"]],
                    # The batch's own label (a leave batch's declined
                    # reads Stayed), not the refused request's.
                    "outcome": outcome_label(
                        refusal.details["outcome"], refusal.details["kind"]
                    ),
                },
            )
        if refusal.code not in REFUSALS or refusal.code == "outcome_kind":
            return _error(campaign_id)
        return _bulk_refusal(400, _refusal_error(refusal, request.POST)["message"])
    except ValueError:
        return _error(campaign_id)

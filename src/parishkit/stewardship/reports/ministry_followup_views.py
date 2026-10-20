"""Ministry requests: the review worklist, request pages and their edits.

The queue page is the Ministry stream's review worklist (#1684,
``ministry_worklist``), whose decisions post to the page itself and are
recorded by ``workflows.review``; each request's own page keeps its
follow-up form, contact attempts and history.
"""

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
from parishkit.stewardship.web.filter_errors import error_context, refused
from parishkit.stewardship.web.refusals import Refusal, with_codes, with_status_code
from parishkit.stewardship.web.report_errors import report_unavailable
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.review_worklist import read_decision, record
from parishkit.stewardship.web.tables import (
    PAGE_SIZES,
)
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
from parishkit.stewardship.workflows.review import DecisionRefusal, MinistryDecisions
from parishkit.stewardship.workflows.roster import (
    can_tick,
    mark,
    roster_marks,
    set_roster_entered,
)

from .export_services import admit_campaign
from .export_views import SAFE_FAILURES
from .information import parse_page
from .ministries import (
    MinistryQuery,
    ministry_page,
    outcome_label,
    reads_every_ministry,
    unscoped,
)
from .ministry_contacts import current_ministry, request_contacts
from .ministry_exports import MAX_PACKET_MINISTRIES
from .ministry_followup import (
    CHANNELS,
    EXPORT_PDF_LIMIT,
    HISTORY_STATES,
    STATES,
    FollowupQuery,
    can_follow_up,
    followup_history,
    followup_page,
    valid_ministry,
)
from .ministry_worklist import (
    WorklistQuery,
    for_reader,
    read_rows,
    row_name,
    worklist_context,
)
from .read_admission import admit_report_read
from .report_paging import (
    next_open,
    pop_navigation,
    queue_token,
    recall_queue,
    remember_queue,
    with_queue,
)

TEMPLATE = "stewardship/ministry-followup.html"
# The queue page, the review worklist (#1684).
QUEUE_TEMPLATE = "stewardship/ministry-requests.html"
FORMER = "Former portal user"
# The name this queue's views are remembered under (report_paging.py).
QUEUE = "ministry_followup"
# The filters a link may carry (#533, #1133): one Ministry, and with it the
# worklist's own closed Request and Stage values (#1684). Never the search.
LINK_FILTERS = frozenset({"ministry", "kind", "stage"})
# Rows per read while finding the next open request (the selection takes any
# LIMIT, so the largest page size keeps the scan to a few reads).
SCAN_SIZE = max(PAGE_SIZES)


def _recalled(request, campaign_id, token):
    """The remembered worklist view, or the default one.

    A token that is unknown here (another sign-in, an older view, or values a
    later release no longer accepts, such as the follow-up queue's Status
    and Outcome before #1684) shows the default worklist, not an error.
    """
    values = recall_queue(request, QUEUE, campaign_id, token)
    try:
        return WorklistQuery.parse(values) if values is not None else WorklistQuery()
    except ValueError:
        return WorklistQuery()


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

    Links from a leader's My review work (#1690) and the counts on Ministry
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
    # The refusal log's codes for this page of its own (#1403).
    return with_status_code(response, *(["bad_link"] if link else []))


def _audit(principal, campaign_id, outcome, ministry=None):
    """Retain parish-owned access evidence, not private search or displayed values.

    A request page names its Ministry (``ministry``): the page shows that
    Member's contact details, so its view is audited with Ministry scope, as
    the retired Members joining and leaving lists were (#1157).
    """
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
            context={"outcome": outcome}
            | ({} if ministry is None else {"ministry_duid": ministry}),
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


# The Follow-up packet's limit refusal (#1332) is described by the message
# line in the packet's hint row (ministry-packet.html).
PACKET_LINES = {"ministries": "ministry-packet"}


def packet_context(error, choices):
    """Ministry requests' context for a packet refused at its boxes (#1332).

    ``error`` is the ``FilterError`` whose query is the submitted selection;
    ``choices`` the packet's boxes. The boxes keep the ticks sent among
    them (a value no box offers is never echoed), every box is marked, so
    the first edit of any clears the mark, and the error summary links to
    the first box (the packet's heading, when the reader may include no
    Ministry). The hint row is sized to the message, so its clearing moves
    nothing.
    """
    first = (
        f"ministry-packet-{choices[0]['duid']}"
        if choices
        else "ministry-packet-heading"
    )
    return error_context(error, {"ministries": first}, PACKET_LINES) | {
        "packet_ticked": set(error.query),
        "packet_sizes": {PACKET_LINES["ministries"]: error.message},
    }


def queue_response(request, campaign_id, *, packet):
    """Ministry requests drawn again for a refused Follow-up packet (#1332).

    ``packet`` is the ``FilterError`` for the packet's boxes. The worklist
    is the default view (the packet's form carries no worklist filters), and
    the page answers 400 with the refusal's code.
    """
    return _page_response(request, campaign_id, packet=packet)


def _queue_query(request, principal, parameters):
    """The worklist view a queue request asks for (#534, #1133, #1684).

    A query string carries either the token of a remembered view ("Return
    to Ministry requests", #534) or closed link filters: one Ministry, from
    a leader's My review work (#1690) or with a request type and stage from
    a count on By Ministry (#1133), or a Stage alone, from Home's Ministry
    requests to enter by hand card (R39 finding 2). Search and every other
    filter stay private POST state (``parameters``, the POST body less any
    decision's fields). A Ministry leader's view of Staff's update phase
    reads as their Accepted (``for_reader``).
    """
    if request.GET:
        if request.method != "GET":
            raise ValueError("Search and filters require a POST body.")
        values = filters(request.GET, allowed={"queue", *LINK_FILTERS})
        if "queue" not in values and ("ministry" in values or set(values) == {"stage"}):
            # Remembered below like any view, so its token keeps these
            # filters on each request page, Save and next and the way back
            # to the worklist. The parse checks the closed values; a
            # Ministry is then checked against scope.
            query = WorklistQuery.parse(values)
            if query.ministry:
                _linked_ministry(query.ministry, principal)
            return for_reader(query, principal)
        if values.keys() == {"queue"}:
            return _recalled(request, None, queue_token(values["queue"]))
        raise ValueError("Use a queue token or a Ministry.")
    if parameters is None:
        return WorklistQuery()
    return for_reader(WorklistQuery.parse(parameters), principal)


def _page_response(
    request,
    campaign_id,
    *,
    request_id=None,
    refusal=None,
    packet=None,
    parameters=None,
    notice=None,
):
    """Guard every data query, template render and byte; audit response completion.

    The queue page is the review worklist; ``parameters`` are its POST
    body's view fields and ``notice`` the answer to a decision just
    recorded (``queue``). With a refusal, the request page is shown again in
    place (status 400) with an error summary and the submitted values,
    instead of an error page. With a refused Follow-up packet (``packet``),
    the worklist's default view is shown again (status 400) with the error
    at the packet's boxes.
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
                    {"page_name": PAGES["ministry_report"].label},
                )
                response["Cache-Control"] = "no-store"
                return response
            if packet is not None:
                # The packet's POST body is not a worklist view: the default.
                query = WorklistQuery()
            elif request.GET and set(request.GET) == {"queue"}:
                query = _recalled(
                    request,
                    campaign_id,
                    queue_token(filters(request.GET, allowed={"queue"})["queue"]),
                )
            else:
                query = _queue_query(request, principal, parameters)
            history_page = 1
            # Remembered before the response streams: the session is saved
            # once the view returns.
            token = remember_queue(request, QUEUE, campaign_id, query.view_values())
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
        # The request's Ministry, once read, for the completion event.
        finalized, shown_ministry = False, None

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
                    shown_ministry,
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

        def mutable_now():
            """Whether other campaign work leaves edits open (the work gate)."""
            try:
                admit_campaign(campaign_id, mutating=True)
            except PermissionError:
                return False
            return True

        def queue_content():
            """The worklist page, its By Ministry counts and its packet."""
            mutable = mutable_now()
            # The worklist reads an empty campaign as empty for Staff and
            # Administrators; its By Ministry read cross-checks that (#1133).
            result = read_rows(campaign_id, query, principal, empty=True)
            context = dict(
                worklist_context(
                    campaign_id,
                    query,
                    principal,
                    result,
                    token=token,
                    mutable=mutable,
                    action=reverse("admin:ministry_report"),
                ),
                **_overview(campaign_id, principal),
                campaign_id=campaign_id,
                mutable=mutable,
                notice=notice,
                queue_token=token,
                # Admin and Staff see the roster link; a leader never does.
                can_tick=can_tick(principal),
                # The help about Ministries removed from the campaign is for
                # Staff and Administrators (#1135). A leader never meets one:
                # a leader leads only Ministries the campaign asks about (#922).
                campaign_marks=allows(principal, Capability.MINISTRY_FOLLOWUP),
                # Export these requests (#1157): the applied view's filters,
                # search and sort.
                export_fields=query.form_values(),
                export_pdf_limit=EXPORT_PDF_LIMIT,
            )
            if packet is not None:
                context |= packet_context(packet, context["packet_choices"])
            return iter(
                (render_to_string(QUEUE_TEMPLATE, context, request=request).encode(),)
            )

        def content():
            """All lazy SQL and rendering stay within the response-owned barrier."""
            nonlocal shown_ministry
            if request_id is None:
                return queue_content()
            result = followup_page(campaign_id, query, principal, request_id=request_id)
            # The roster tick (#528), for the request shown.
            marks = roster_marks(row["id"] for row in result["rows"])
            for row in result["rows"]:
                mark(row, marks)
            mutable = mutable_now()

            def actionable(row):
                """An open request whose Member ParishSoft still lists (#1217)."""
                return row["open"] and not row["source_mark"]

            item = result["rows"][0]
            # Named before the contacts are read, so a failed read's audit
            # event still carries the request's Ministry: the one its
            # contacts are read under (#1718).
            shown_ministry = current_ministry(item)
            # The Member's contact details (#1157), read for this one request
            # through the same scoped SQL the Ministry lists used. An in-place
            # history page swaps only the history, so it skips them.
            contact = (
                request_contacts(campaign_id, principal, [item]).get(item["id"])
                if not history_only
                else None
            )
            # Save and next opens the next open request after this one in
            # the remembered worklist view, found now under this guarded
            # read (#534); its open requests are the view's To review.
            following = (
                next_open(
                    lambda number: followup_page(
                        campaign_id,
                        replace(
                            queue_view.open_query(), page=number, size=str(SCAN_SIZE)
                        ),
                        principal,
                    )["rows"],
                    SCAN_SIZE,
                    request_id,
                    actionable,
                )
                if actionable(item) and mutable and not history_only
                else None
            )
            # The form's values: the request's own, or what was just submitted.
            form = dict(
                {key: "" for key in FORM_FIELDS},
                expected_version=str(item["version"]),
                state=item["state"],
                notes=item["notes"] or "",
            )
            if refusal is not None:
                form.update(
                    (key, request.POST.get(key, ""))
                    for key in FORM_FIELDS
                    if key in request.POST
                )
            error = _refusal_error(refusal, request.POST) if refusal is not None else {}
            history, more_history = followup_history(request_id, history_page)
            # Past edits keep showing an assignment they recorded before
            # follow-up assignment was removed (#552); new edits record none.
            labels = _labels(
                [row.actor_id for row in history] + [row.assignee_id for row in history]
            )
            names = {
                ministry["duid"]: ministry["name"] for ministry in result["ministries"]
            }
            for row in history:
                row.actor_label = labels.get(str(row.actor_id), FORMER)
                row.assignee_label = (
                    labels.get(str(row.assignee_id), FORMER) if row.assignee_id else ""
                )
                row.state_label = HISTORY_STATES[row.state]
                row.outcome_label = outcome_label(row.outcome, item["action"])
                row.channel_label = CHANNELS.get(row.contact_channel, "")
                # An Edit's Ministry (#1684), by the name the reader's scope
                # gives it, else the request's own reviewed name.
                row.reviewed_label = (
                    names.get(row.reviewed_ministry_duid)
                    or item.get("reviewed_ministry_name")
                    or ""
                    if row.reviewed_ministry_duid
                    else ""
                )
            context = dict(
                result,
                campaign_id=campaign_id,
                query=query,
                mutable=mutable,
                item=item,
                contact=contact,
                queue_token=token,
                next_id=following,
                history=history,
                previous_history=history_page - 1 if history_page > 1 else None,
                next_history=history_page + 1 if more_history else None,
                request_key=uuid4(),
                roster_key=uuid4(),
                # Admin and Staff see and set the tick; a leader never sees it.
                can_tick=can_tick(principal),
                campaign_marks=allows(principal, Capability.MINISTRY_FOLLOWUP),
                states=STATES,
                staff_states=[(key, STATES[key]) for key in STAFF_STATES],
                # Only Resolved takes a chosen outcome (change_values), and
                # only those this kind of request can record (outcomes_for).
                resolved_outcomes=[
                    (key, outcome_label(key, item["action"]))
                    for key in outcomes_for(item["action"])
                ],
                form=form,
                errors=[error] if error else [],
                # The refused fields, marked in error with the message beside
                # them (an empty dict when nothing was refused).
                field_error=error,
                # The page checks a contact time as it is typed, with the
                # server's own words for a time in the future (#592).
                future_message=REFUSALS["contact_future"][0],
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
            # The refusal log's codes for this refusal (#1444).
            with_status_code(response, refusal.code)
        elif handed_off and packet is not None:
            # Known before the response was made, so its status and code
            # are set before any byte is sent.
            refused(response)
        return response
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        # Ministry requests itself answers an outage with the Admin report
        # 503 page, as the summary page it replaced did (#1133); a request
        # page keeps its own, which offers to reload that request.
        if request_id is None:
            return report_unavailable()
        return _error(campaign_id, request_id=request_id, status=503)
    except ValueError:
        return _error(campaign_id, request_id=request_id, link=request.method == "GET")
    finally:
        if finish is not None and not handed_off:
            finish(False)


# What each By Ministry count applies to the worklist (#1133, #1684): its
# own link filters (LINK_FILTERS) with closed values, so the worklist shows
# exactly the requests counted. Join requests and Leave requests count every
# current request of that kind, whatever its stage, so they choose Every
# stage; Unresolved is To review, the worklist's default, with either
# request: the Home panel's address.
COUNT_FILTERS = {
    "joining": {"kind": "join", "stage": "all"},
    "leaving": {"kind": "leave", "stage": "all"},
    "unresolved": {},
}


def count_links(principal, summary):
    """The addresses one By Ministry row's counts apply to the worklist (#1133).

    A count links only when it is not zero and the reader may follow up
    that Ministry, the check the worklist itself makes on the link: a
    Ministry leader gets links only for the Ministries they lead. The
    address carries only the Ministry number and closed filter values, never
    a search; the template adds the table's fragment, the region the page
    redraws.
    """
    if not allows(principal, Capability.MINISTRY_FOLLOWUP, ministry_id=summary["duid"]):
        return {}
    base = reverse("admin:ministry_report")
    return {
        count: f"{base}?{urlencode({'ministry': summary['duid']} | values)}"
        for count, values in COUNT_FILTERS.items()
        if summary[count]
    }


def _overview(campaign_id, principal):
    """Ministry requests' parts below the worklist (#1133).

    By Ministry and the packet's choices are every Ministry in the reader's
    scope, read once through the Ministry report's own scoped selection and
    cross-check (``ministry_page``), whatever the filters show: By Ministry
    is how a reader picks one. Each By Ministry count is a link that applies
    its Ministry and request to the worklist. The totals and the Follow-up
    progress chart are gone (#1676): a review worklist has none (#1684).
    """
    summaries = ministry_page(campaign_id, MinistryQuery(), principal, every=True)[
        "summaries"
    ]
    for summary in summaries:
        summary["count_links"] = count_links(principal, summary)
    return {
        "by_ministry": summaries,
        # The "Not in ParishSoft data" and campaign marks, for Staff and
        # Administrators only (#1135).
        "show_marks": reads_every_ministry(principal),
        # Every Ministry the packet may include, by name (#533).
        "packet_choices": sorted(
            ({"duid": row["duid"], "name": row["name"]} for row in summaries),
            key=lambda row: (row["name"].lower(), row["duid"]),
        ),
        "packet_limit": MAX_PACKET_MINISTRIES,
        "packet_key": uuid4(),
    }


@require_http_methods(["GET", "POST"])
def queue(request, campaign_id):
    """Ministry requests (#1133): the review worklist (#1684) and its overview.

    Default GET is safe; private filtering and pagination use native POST.
    A POST carrying a decision (a row icon, the bulk bar or Add) records it
    first and answers with the page redrawn from the same view, its notice
    saying what was recorded (``web.review_worklist``).
    """
    if request.method != "POST":
        return _page_response(request, campaign_id)
    parameters = request.POST.copy()
    parameters.pop("csrfmiddlewaretoken", None)
    try:
        decision = read_decision(parameters)
    except ValueError:
        return _error(campaign_id)
    notice = None
    if decision is not None:
        notice = _decide(request, campaign_id, decision)
        if not isinstance(notice, str):
            return notice
    return _page_response(request, campaign_id, parameters=parameters, notice=notice)


def _decide(request, campaign_id, decision):
    """Record one worklist decision; its notice, or the refusal response.

    A race is the notice's to say (``record``). A request outside the
    reader's scope is denied like any unknown one; an Add the reader can
    correct is refused in its dialog, in plain words with its code; a
    malformed decision is the plain 400 page; an outage is 503.
    """
    try:
        if campaign_id is None:
            raise ValueError("No current campaign.")
        service = runtime()
        principal = _principal(request, service.store)
        if request.GET:
            raise ValueError("Ministry decisions require a POST body.")
        decisions = MinistryDecisions(
            service.store,
            campaign_id,
            namer=lambda request_id: _row_name(campaign_id, principal, request_id),
        )
        return record(decisions, principal, decision)
    except DecisionRefusal as refusal:
        return with_codes(
            validation_response(
                [FieldError(ErrorCode.INVALID)],
                status=400,
                refusal=Refusal(refusal.message),
            ),
            refusal.code,
        )
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return _error(campaign_id, status=503)
    except ValueError:
        return _error(campaign_id)


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
            url = reverse("admin:ministry_report")
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

    The answer is the request's own page, Post/Redirect/Get, keeping its
    queue token, so it refreshes in place. A tick someone else changed first
    is a conflict (409), as a stale follow-up save is. Roster changes to
    enter records its marks through its own worklist since #1687; clearing
    a mistaken mark is this page's.
    """

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
            },
        )
        if (
            values.get("entered") not in {"yes", "no"}
            or values.get("return_to", "item") != "item"
        ):
            raise ValueError("Invalid roster tick.")
        token = queue_token(values.get("queue", ""))
        _in_campaign(campaign_id, [request_id])
        set_roster_entered(
            store,
            actor,
            request_id,
            sequence=expected_version(values.get("sequence", "")),
            entered=values["entered"] == "yes",
            request_key=UUID(values.get("request_key", "")),
        )
        return with_queue(
            reverse("admin:ministry_followup_item", args=[request_id]), token
        )

    return _mutation(request, campaign_id, request_id, work)


def _row_name(campaign_id, principal, request_id):
    """A request as its worklist row names it (``row_name``), or None.

    Read through the scoped selection, so only a request the person may see
    is named; any failure falls back to a notice that names no row.
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
    return row_name(row)

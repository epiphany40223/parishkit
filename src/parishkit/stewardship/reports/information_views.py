"""Additional information: the review worklist and each request's page (#1685).

The list is the stream's review worklist (the shared
``components/review-worklist.html``): its filters, sorting and paging
travel in CSRF-protected POST bodies, so search text never reaches an
address, and a row's Accept, Ignore, Back to review or Handled, or the bulk
bar's, posts to the page itself with the page's view
(``review_worklist.read_decision``), so the answer is the same page,
refreshed in place, with a notice naming the request.
``information_worklist`` places the requests in their stages and records
the decisions. A request's own page shows its text, its Family's contact
details and its history; decisions are made on the list. Every view is
audited (``information_viewed``) without the search or any shown value.

When the request page could not read its Contact details because the
ParishSoft data was replaced while it was read, the section's notice polls
the passive status ``contacts_status`` (#1863, ``web.part_status``), which
is never audited and never extends the sign-in; once newer data is in use,
the page re-reads its own address once, passively, and that view is audited
marked automatic, as a report's re-read for newer figures is (#1620).
"""

from uuid import uuid4

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_GET, require_http_methods

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.models import PortalUser
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin, passive_read
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.observability import Event, debug_swallowed, emit_failure
from parishkit.stewardship.pipeline import Stage
from parishkit.stewardship.responses.census_review import KeyRebound
from parishkit.stewardship.responses.information import (
    information_stage,
    latest_revision,
)
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.contracts import (
    ErrorCode,
    FieldError,
    filters,
    validation_response,
)
from parishkit.stewardship.web.part_status import (
    Part,
    is_ready,
    parse_status,
    part_status,
)
from parishkit.stewardship.web.refusals import with_status_code
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.review_worklist import (
    REBOUND,
    RETIRED_DATES,
    drop_retired_dates,
    page_fields,
    read_decision,
    record,
)
from parishkit.stewardship.web.security import private_response
from parishkit.stewardship.web.tables import paginate, table_parameters

from .export_services import admit_campaign
from .export_views import SAFE_FAILURES
from .family_links import link_families
from .information import (
    InformationQuery,
    information_history,
    information_page,
    parse_page,
)
from .information_contacts import family_contacts
from .information_worklist import (
    SORTING,
    InformationDecisions,
    RowItem,
    WorklistQuery,
    requests,
    worklist_page,
)
from .read_admission import admit_report_read

# The Contact details, when the ParishSoft data was replaced while the page
# read them (#1863): the notice that stands in for them until newer data is
# in use, then the page re-reads itself once.
CONTACTS = Part(
    name="information-contacts",
    route="information_contacts_status",
    region="information-item",
    notice=gettext_lazy(
        "The ParishSoft data was replaced while this page was read, so contact "
        "details are unavailable just now. They appear here by themselves once "
        "the newer ParishSoft data can be read."
    ),
    notice_id="information-contacts-unavailable",
)


def _principal(request, store, *, read_only=False):
    """Every list, request page and decision reloads the current Staff capability."""
    principal = authenticated_admin(
        request, store=store, activity=not read_only, read_only=read_only
    )
    if not allows(principal, Capability.ADDITIONAL_FOLLOWUP):
        raise PermissionError("Staff follow-up is unavailable.")
    return principal


def _error(campaign_id, *, item_id=None, status=400, bound=False):
    """No private form/exception values or database-dependent context processors.

    ``bound`` marks a reused export form, whose 409 differs from a decision's.
    """
    debug_swallowed("report request refused")
    response = HttpResponse(
        render_to_string(
            "stewardship/information-error.html",
            {
                "campaign_id": campaign_id,
                "item_id": item_id,
                "status": status,
                "bound": bound,
            },
        ),
        status=status,
        headers={"Cache-Control": "no-store"},
    )
    response.stewardship_safe_error = True
    if status == 503:
        response["Retry-After"] = "5"
    # The refusal log's codes for this page of its own (#1403).
    return with_status_code(response, *(["form_reused"] if bound else []))


def _audit(principal, campaign_id, outcome, *, automatic=False):
    """Retain parish-owned access evidence, not private search or displayed values.

    ``automatic`` marks a request page's own re-read that brought in the
    Contact details it could not read (#1863), as a report's automatic view
    is marked (#1620); a view the reader asked for carries no such key.
    """
    context = {"outcome": outcome}
    if automatic:
        context["automatic"] = True
    # An audit append takes no row locks, so it need not join the
    # writers' work order; waiting there stalled report pages behind
    # every source promotion and installer.
    with transaction.atomic():
        system = SystemConfiguration.objects.select_related(
            "active_configuration__parish"
        ).get()
        record_action(
            Action.INFORMATION_VIEWED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=principal.identity,
            subject_id=campaign_id,
            parish_id=system.active_configuration.parish.pk,
            campaign_id=campaign_id,
            context=context,
        )


def paging_values(parameters):
    """Remove and return the table's page/size/sort values from the form.

    ``parameters`` is a mutable QueryDict. The values only choose what the
    screen shows, so they never reach the filters; a bad choice is refused
    now, as a 400, not later while the page is drawn.
    """
    values = {}
    for name in table_parameters():
        found = parameters.pop(name, None)
        if found is None:
            continue
        if len(found) != 1:
            raise ValueError("Table choices must be single values.")
        values[name] = found[0]
    paginate([], values, sorting=SORTING)
    return values


def _mutable(campaign_id):
    """Whether the campaign admits decisions now (no other campaign work holds it)."""
    try:
        admit_campaign(campaign_id, mutating=True)
    except PermissionError:
        return False
    return True


def decision_label(revision):
    """One history entry's decision, in the pipeline's words.

    A revision saved before migration 0126 has no decision; the page words
    it by its follow-up choices instead (None here).
    """
    if revision.review_decision == "accepted":
        return "Handled" if revision.followed_up else "Accepted"
    return {"ignored": "Ignored", "none": "Back to review"}.get(
        revision.review_decision
    )


def _list_context(campaign_id, query, paging, principal):
    """The worklist page's context: the placed requests, one page of them."""
    action = reverse("admin:information_queue")
    metadata, rows, counts = requests(campaign_id, query)
    table = paginate(
        rows,
        paging,
        carry=list(page_fields(query.form_values()).items()),
        sorting=SORTING,
    )
    # Each shown Family name opens its Timeline (#1570): one read of the
    # page's Family record ids.
    link_families(campaign_id, table.rows)
    mutable = _mutable(campaign_id)
    worklist, table = worklist_page(counts, query, table, action, mutable=mutable)
    return {
        "metadata": metadata,
        "worklist": worklist,
        "table": table,
        "query": query,
        "query_fields": page_fields(query.form_values()),
        "export_fields": page_fields(query.export_values(paging.get("sort", ""))),
        "mutable": mutable,
    }


def _item_context(campaign_id, item_id, history_page, principal, history_only):
    """One request's page: its text, Stage, Family contact details and history."""
    result = information_page(
        campaign_id, InformationQuery(disposition="all"), item_id=item_id
    )
    item = result["rows"][0]
    history, more_history = information_history(
        item_id, history_page, version=item["version"]
    )
    # The Family's contact details (#1595); an in-place history page swaps
    # only the history, so it skips the read.
    contacts = (
        None
        if history_only
        else family_contacts(
            principal, result["metadata"]["source_id"], item["family_duid"]
        )
    )
    if contacts and contacts["state"] == "unavailable":
        # Brought in by themselves once newer data is in use (#1863).
        contacts["status"] = part_status(
            CONTACTS, shown=result["metadata"]["source_generation"]
        )
    ids = {row.actor_id for row in history} | {row.followed_up_by_id for row in history}
    ids.add(item["followed_up_by_id"])
    labels = {
        str(key): email
        for key, email in PortalUser.objects.filter(pk__in=ids - {None}).values_list(
            "id", "email"
        )
    }
    item["completed_by"] = labels.get(
        str(item["followed_up_by_id"]), "Former portal user"
    )
    item["placement"] = information_stage(RowItem(item), latest_revision(item_id))
    # The request page's Family name opens its Timeline (#1570).
    link_families(campaign_id, result["rows"])
    for row in history:
        row.actor_label = labels.get(str(row.actor_id), "Former portal user")
        row.completer_label = labels.get(
            str(row.followed_up_by_id), "Former portal user"
        )
        row.decision_label = decision_label(row)
    return {
        "metadata": result["metadata"],
        "item": item,
        "contacts": contacts,
        "history": history,
        "history_page": history_page,
        "previous_history": history_page - 1 if history_page > 1 else None,
        "next_history": history_page + 1 if more_history else None,
        "mutable": _mutable(campaign_id),
    }


def _respond(request, campaign_id, *, item_id=None):
    """Guard every data query, template render and byte; audit response completion.

    The list reads its filters, table choices and any decision from its own
    POST body, or a Stage alone from a GET link (``?stage=``): a decision
    is recorded first, in its own work transaction, so the page that
    answers shows its result. A request someone changed first
    is the page's notice, not an error; a page sent again with other
    choices is a 409 that asks for a reload.
    """
    finish, handed_off = None, False
    try:
        service = runtime()
        # A request page's own re-read once its Contact details can be read
        # (#1863) is passive: it never extends the sign-in, though it is
        # audited, and marked automatic. The header can only add that mark:
        # every view is audited whatever the request sends. This page's
        # admission has no activity-only form, so the read-only check is
        # how it leaves the sign-in alone (Users uses ``passive``).
        automatic = item_id is not None and passive_read(request)
        principal = _principal(request, service.store, read_only=automatic)
        notice, history_page, history_only, decision = None, 1, False, None
        if item_id is None:
            # A link may carry a Stage alone, a My review work card's
            # ``?stage=`` (#1818); WorklistQuery.parse checks its value.
            # Search and table choices stay private POST state. The retired
            # submitted dates (#1888) are dropped unread, never refused.
            if request.GET and (
                request.method != "GET"
                or not set(request.GET) - RETIRED_DATES <= {"stage"}
            ):
                raise ValueError("Search and filters require a POST body.")
            parameters = drop_retired_dates(
                (request.GET if request.GET else request.POST).copy()
            )
            parameters.pop("csrfmiddlewaretoken", None)
            decision = read_decision(parameters)
            paging = paging_values(parameters)
            if "stage" not in parameters:
                # The worklist opens on To review (review pipeline spec,
                # "Review worklist").
                parameters["stage"] = Stage.TO_REVIEW.value
            query = WorklistQuery.parse(parameters)
        admit_report_read(campaign_id)
        if decision is not None:
            notice = record(
                InformationDecisions(service.store, campaign_id), principal, decision
            )
        if item_id is not None:
            values = filters(request.GET, allowed={"page"})
            history_page = parse_page(values.get("page", "1"))
            # An in-place history page swaps only the history (its links are
            # data-in-place-only); a reload of that address reads it all.
            # The automatic re-read redraws the whole request, its Contact
            # details included, whichever history page the address names.
            history_only = (
                not automatic
                and "page" in values
                and request.headers.get("X-Requested-With") == "fetch"
            )
        _audit(principal, campaign_id, Outcome.STARTED, automatic=automatic)
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
                    automatic=automatic,
                )
            except (DatabaseError, StorageInvariantError) as error:
                emit_failure(error, event=Event.REPORT_AUDIT_FAILED)

        def authorize(guard):
            """Do not trust the pre-audit principal or a previously visible item."""
            current = _principal(request, service.store, read_only=True)
            if current.identity != principal.identity:
                raise PermissionError("Staff follow-up access changed.")
            admit_report_read(campaign_id)

        def content():
            """All lazy SQL and rendering stay within the response-owned barrier."""
            context = (
                _list_context(campaign_id, query, paging, principal)
                if item_id is None
                else _item_context(
                    campaign_id, item_id, history_page, principal, history_only
                )
            )
            context |= {"campaign_id": campaign_id, "notice": notice}
            if item_id is None:
                # The export form's own key, so a repeated submit is a replay.
                context["request_key"] = uuid4()
            html = render_to_string(
                "stewardship/information.html", context, request=request
            )
            return iter((html.encode(),))

        response = campaign_response(
            request,
            [campaign_id],
            authorize=authorize,
            open_content=content,
            on_close=finish,
        )
        if response.status_code == 503 and not response.streaming:
            return _error(campaign_id, item_id=item_id, status=503)
        handed_off = response.status_code == 200 and response.streaming
        return response
    except KeyRebound:
        # The same drawn form was sent again with other choices (its first
        # answer was lost): nothing was recorded, and a reload draws fresh
        # forms with fresh keys.
        return validation_response(
            [FieldError(ErrorCode.STALE)], status=409, refusal=REBOUND
        )
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return _error(campaign_id, item_id=item_id, status=503)
    except ValueError:
        # A malformed form or filter, or a decision the request does not
        # take (a reopening with no note): a 400, nothing recorded.
        return _error(campaign_id, item_id=item_id)
    finally:
        if finish is not None and not handed_off:
            finish(False)


@require_http_methods(["GET", "POST"])
def queue(request, campaign_id):
    """The default GET lists the requests To review; filters and decisions POST."""
    return _respond(request, campaign_id)


@require_GET
def detail(request, campaign_id, item_id):
    """One request of this campaign: its text, contact details and history."""
    return _respond(request, campaign_id, item_id=item_id)


@require_GET
def contacts_status(request, campaign_id):
    """The Contact details' passive status a request page polls (#1863).

    Says only whether ParishSoft data other than the page's own (``shown``)
    is now in use, so a re-read would read the details from it; never a
    contact value. It admits the page's readers, is never audited and never
    extends the sign-in; its answer is the notice's region, settled with the
    follow-up link once ready.
    """
    try:
        service = runtime()
        _principal(request, service.store, read_only=True)
        shown, _retry = parse_status(request.GET)
        admit_report_read(campaign_id)
        ready = is_ready(shown, False)
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return _error(campaign_id, status=503)
    except ValueError:
        # A poll, not a form: a short private answer, as the figures status's.
        return private_response("Invalid status options.\n", status=400)
    html = render_to_string(
        "stewardship/components/part-status.html",
        {"part_status": part_status(CONTACTS, shown=shown, ready=ready)},
    )
    return HttpResponse(html, headers={"Cache-Control": "no-store"})

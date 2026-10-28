"""Admin-only mail metadata and evidence forms; private payloads never serialize."""

from contextlib import contextmanager
from dataclasses import replace
from urllib.parse import urlencode
from uuid import UUID, uuid4

from django.conf import settings
from django.core import signing
from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, IntegrityError, transaction
from django.http import HttpResponse, QueryDict
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import (
    require_http_methods,
    require_POST,
    require_safe,
)

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.admin_caller import AdminCaller
from parishkit.stewardship.accounts.authentication import runtime
from parishkit.stewardship.accounts.cryptography import CryptographicError
from parishkit.stewardship.accounts.limiting import LimiterUnavailable
from parishkit.stewardship.accounts.role_refusal import SHARED_REFUSALS
from parishkit.stewardship.accounts.role_refusal import response as role_response
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.reports.family_links import (
    family_record_ids,
    link_families,
)
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import (
    MESSAGES,
    ErrorCode,
    expected_version,
)
from parishkit.stewardship.web.filter_errors import (
    FilterError,
    error_context,
    refused,
)
from parishkit.stewardship.web.refusals import (
    annotate,
    refusal_text,
    refused_step,
    unexpected_fields,
    with_codes,
    with_default_code,
)
from parishkit.stewardship.web.tables import (
    window_table,
)

from . import delivery_bulk
from .delivery_admin import clear_recipient_refusal, report_recipient_bounce
from .delivery_metadata import (
    DELIVERY_SORTING,
    PURPOSE_CHOICES,
    RECIPIENT_PATTERN,
    RECIPIENT_REFUSAL,
    STATES,
    recipient_scope,
)
from .delivery_reads import (
    LISTING_FILTERS,
    REFUSAL_SORTING,
    UNAVAILABLE_REASONS,
    bounce_panel,
    current_snapshot,
    email_recipients,
    parse_window,
    read_detail,
    read_listing,
    read_refusal,
    read_refusals,
    read_sending_holds,
    unresolved_refusals,
    waiting_emails,
    with_family_names,
    with_recipients,
    with_refusal_counts,
    with_user_links,
)
from .delivery_resolution import resolve_delivery
from .models import TaskRun
from .outbox_models import OutboxMessage
from .recipient_models import RecipientRefusal
from .storage import TaskRetryConflict
from .task_retries import (
    FAMILY_PREPARATION,
    REPORT_EMAIL,
    background_principal,
    command_scope,
    retry_preparation_task,
)

UNAVAILABLE = (
    ConfigError,
    CryptographicError,
    LimiterUnavailable,
    ObjectDoesNotExist,
)
MISSING_TARGET = (
    OutboxMessage.DoesNotExist,
    RecipientRefusal.DoesNotExist,
    TaskRun.DoesNotExist,
)


def _database_error(error):
    """Distinguish rejected state from outages without disclosing SQL or values."""
    if isinstance(error, IntegrityError) and getattr(
        error.__cause__, "sqlstate", None
    ) in {"23514", "23505"}:
        return _error(ErrorCode.STALE, 409)
    return _error(ErrorCode.UNAVAILABLE, 503)


def _error(code, status, error=None, back=None):
    """Render fixed, accessible recovery without submitted evidence or DB chrome.

    In particular, an unavailable database must not be queried a second time
    by the Admin navigation context processor while rendering the error. A
    user-facing refusal (``error``, such as an unexpected-fields refusal)
    shows its own reviewed full-page text instead of the closed message
    (#1401). ``back`` is the server-owned path of the page the refused form
    came from, which the page links to so that it can be opened again. The
    refusal log records ``code`` and the refusal's reason (#1444).
    """
    message = refusal_text(error, page=True) or MESSAGES[code]
    response = HttpResponse(
        render_to_string(
            "stewardship/delivery-error.html", {"message": message, "back": back}
        ),
        status=status,
    )
    response.stewardship_safe_error = True
    response["Cache-Control"] = "no-store"
    if status == 503:
        response["Retry-After"] = "5"
    with_codes(response, code.value)
    return annotate(response, error) if error is not None else response


def _principal(request, store, *, final=False, activity=False):
    """Polling/read views never extend idle expiry; forms require current Admin."""
    return background_principal(request, store, final=final, activity=activity)


@contextmanager
def _command_scope(request, service, actor):
    """The shared command scope, repeating this module's own admission.

    ``_principal`` is looked up when the scope runs, so a test that replaces
    it observes every admission the scope makes.
    """
    with command_scope(request, service, actor, admit=_principal):
        yield


def _retry_inputs(purpose):
    """Only invitations require Family keys; report and receipt retries are keyless.

    The command line's ``admin_operations.FAMILY_KEYED_PURPOSES`` makes the
    same split (naming the keyed purposes); change both together.
    """
    from parishkit.stewardship.accounts.family_authentication import (
        runtime as family_runtime,
    )

    origin = getattr(settings, "STEWARDSHIP_PUBLIC_ORIGIN", None)
    if origin is None:
        raise ConfigError("Mail preparation origin is unavailable.")
    if purpose in {
        "receipt",
        "daily_digest",
        "weekly_digest",
        "admin_report",
        "admin_report_test",
    }:
        return dict(general=None, public=None, public_origin=origin)
    keys = family_runtime()
    return dict(general=keys.general, public=keys.public, public_origin=origin)


def _page(request, template, load, *, subject=None, status=200):
    """Capture bounded metadata, render outside locks, then recheck disclosure.

    ``load(actor)`` reads the page's context inside the transaction; it gets
    the admitted Administrator, whom a definer read names as its caller
    (the Mail message page's recipients, #1024).

    ``status`` is the answer's status: an in-place command refused with an
    error summary answers with this page and a 4xx status (#562).
    """
    try:
        service = runtime()
        actor = _principal(request, service.store)
        with transaction.atomic():
            configuration = SystemConfiguration.objects.first()
            if configuration is None or configuration.restore_review_required:
                return _error(ErrorCode.UNAVAILABLE, 503)
            context, count = load(actor)
        response = render(request, template, context, status=status)
        with transaction.atomic():
            current = _principal(request, service.store, final=True)
            if current.identity != actor.identity:
                raise PermissionError("Delivery reader changed.")
            if SystemConfiguration.objects.filter(
                restore_review_required=True
            ).exists():
                return _error(ErrorCode.UNAVAILABLE, 503)
            record_action(
                Action.DELIVERY_VIEWED,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=current.identity,
                subject_id=subject,
                context={"outcome": Outcome.SUCCEEDED, "count": count},
            )
        if status != 200:
            # This page itself, with the refusal in its review region, not a
            # bare error: the in-place handler swaps it in (#562). The
            # refusal log records its status's code (#1444).
            response.stewardship_safe_error = True
            with_default_code(response)
        response["Cache-Control"] = "no-store"
        return response
    except SHARED_REFUSALS as error:
        # A role that lacks the page, or an ended session: the shared
        # refusal (#1924, #2062), not this page's own error.
        return role_response(error)
    except PermissionError:
        return _error(ErrorCode.DENIED, 403)
    except MISSING_TARGET:
        return _error(ErrorCode.INVALID, 404)
    except DatabaseError as error:
        return _database_error(error)
    except UNAVAILABLE:
        return _error(ErrorCode.UNAVAILABLE, 503)
    except ValueError as error:
        return _error(ErrorCode.INVALID, 400, error, back=request.path)


def _next(request, window, has_next):
    """Preserve filters without carrying any form evidence into navigation URLs."""
    values = request.GET.copy()
    values["page"] = str(window.page + 1)
    return values.urlencode() if has_next else None


def _table(request, window, rows, following, *, total, sorting, sort):
    """Shared navigator model for a list page, carrying its validated filters."""
    return window_table(
        window,
        rows,
        following,
        carry=[
            (name, value)
            for name, value in request.GET.items()
            if name not in {"page", "size", "sort"}
        ],
        total=total,
        sorting=sorting,
        sort=sort,
    )


def _previous(request, window):
    """History and notes share a page; always make earlier evidence reachable."""
    if window.page == 1:
        return None
    values = request.GET.copy()
    values["page"] = str(window.page - 1)
    return values.urlencode()


# Sent mail's Recipient is private filter state (#1562): an email address
# or a Family DUID names a person, and ui-conventions keeps such a value out
# of every URL. It travels only in POST bodies: the filter form, the table's
# navigator and headings, the Production / Testing choice, the row Recipient
# links and the bulk actions. Its other filters may be linked.
PRIVATE_FILTERS = {"recipient"}
TABLE_FIELDS = {"page", "size", "sort"}
RECIPIENT_IN_LINK = _(
    "Recipient is never put in a link. Enter it in the Recipient filter instead."
)


def _listing_parameters(request):
    """Where Sent mail's filters come from for this request (#1562).

    A GET (a bookmark, or a link from another page) carries only the
    filters a link may carry; one naming a Recipient is refused, without
    echoing it. Every POST carries every filter in its body and never comes
    with a query string: the page's own filter form or table control, or a
    bulk step (#2296), whose own fields (``BULK_ONLY``) are left out here.
    Raises ``ValueError``.
    """
    if request.method != "POST":
        if PRIVATE_FILTERS & set(request.GET):
            raise ValueError(RECIPIENT_IN_LINK)
        return request.GET
    if request.GET:
        raise ValueError("Invalid delivery filter.")
    parameters = request.POST.copy()
    for name in BULK_ONLY if "action" in parameters else {"csrfmiddlewaretoken"}:
        parameters.pop(name, None)
    return parameters


# Where Sent mail shows a Recipient refused at its field (#1562): the
# field's element id, and the gate's hint row that shows the message.
FIELD_IDS = {"recipient": "delivery-recipient"}
FIELD_LINES = {"recipient": "delivery-filters"}
# What a page refused at the Recipient still reads: the view choices (the
# mode, the browser zone) and the table's sort and size, not the filters.
REFUSED_KEEPS = ("mode", "zone", "sort", "size")


def _recipient_refusal(parameters):
    """A ``FilterError`` when Sent mail's Recipient is unusable, else None.

    A malformed Recipient (digits and commas that are no whole Family
    DUID, or an "@" that is no one address; any other text is a name,
    #1849) is a rule the reader fixes at that one field
    (UX conventions, Field errors and live checks), so the page is drawn
    again with the error there, not replaced by an error page. Every
    structural rule (an unknown or repeated field, a sort or size the table
    does not take) is checked first and still raises a plain ``ValueError``.
    """
    values, _window = parse_window(parameters, LISTING_FILTERS, DELIVERY_SORTING)
    try:
        recipient_scope(values.get("recipient", ""))
    except ValueError:
        return FilterError("recipient", RECIPIENT_REFUSAL, parameters)
    return None


def _carried(parameters):
    """The applied filters every table control and mode choice posts again."""
    return [
        (name, value)
        for name, value in parameters.items()
        if name not in TABLE_FIELDS and value
    ]


def _page_address(request, parameters, table):
    """This view's address with only the filters a link may carry (#536).

    Never the Recipient and never the page number: ui-v1.js puts it in the
    address bar after each in-place answer (data-page-address), so a reload
    or bookmark keeps the linkable filters, the sort and the size.
    """
    fields = [
        (name, value)
        for name, value in _carried(parameters)
        if name not in PRIVATE_FILTERS
    ] + table.view_fields
    return f"{request.path}?{urlencode(fields)}"


def _mode_fields(parameters, table):
    """The fields each Production / Testing segment posts, by name.

    Sent mail's mode choice is the shared segmented pair (#1575), each
    segment a small POST form: these keep the applied filters (the
    Recipient too, private filter state that never reaches a URL), size and
    sort, and start again at page 1, as a new filter does. The component
    leaves out the applied ``mode``; each segment's button sends its own.
    """
    return dict(_carried(parameters) + table.view_fields)


def _recipient_fields(row, mode):
    """The fields that list everything a row's recipient was sent (#1562).

    A Family email's recipient is its Family (by DUID), any other email's
    its one address (an Administrator's, or a Ministry leader's); the form
    keeps the mode shown and posts them, so neither reaches a URL. None
    when the address is unknown. A Production Family email the current
    campaign has a record of links its Family's Timeline instead
    (``_with_timelines``), and an email to a current user's address that
    user's Timeline (``recipient_user_id``, #1885); the template then
    leaves these fields unused.
    """
    duid = row["family__family_duid"]
    recipient = str(duid) if duid else row.get("recipient_address")
    if not recipient:
        return None
    return [("recipient", recipient), ("mode", mode)]


def _with_timelines(rows):
    """Each row with ``timeline_family_id``, for its Recipient's Timeline link.

    A Production Family email gets its Family's record in the current
    campaign, the one the Timeline address serves (#1570), or None when
    that campaign has no record of the Family; every other email gets None
    and keeps the in-place recipient filter (#1562): a Testing email, since
    the Timeline shows only Production records, and an email to a person
    (who may have a user's Timeline instead, ``recipient_user_id``). One
    read of the page's record ids, none when the page lists no Production
    Family email. The email's own
    ``family_id`` is its own campaign's record, so it is left as it is.
    """
    ids = family_record_ids(
        None,
        (row["family__family_duid"] for row in rows if row["mode"] == "production"),
    )
    return [
        row
        | {
            "timeline_family_id": ids.get(row["family__family_duid"])
            if row["mode"] == "production"
            else None
        }
        for row in rows
    ]


@require_http_methods(["GET", "HEAD", "POST"])
@sensitive_post_parameters("note", "recipient")
def delivery_list(request):
    """Sent mail: every email, filtered by exact or closed choices (#1562).

    Never fetches message content: the address of an email to a person (an
    Administrator's report or notice, a Ministry leader's sign-in email)
    comes from a definer read that returns only addresses. A POST naming an
    ``action`` is one of the page's bulk actions (#382 M4, ``_bulk``): it is
    answered with this page, whose bulk review region shows the preview,
    the result or the refusal in place. Any other POST reads the page with
    the filters in its body (``_listing_parameters``).
    """
    bulk = {}
    if request.method == "POST" and "action" in request.POST:
        bulk = _bulk(request)
    if isinstance(bulk, HttpResponse):
        return bulk
    # A Recipient refused at its field, which load notes (#1562).
    field_refusal = []

    def load(actor):
        """Capture one filtered page without reading any private message payload.

        The page shows the system's current operating mode's email (Testing
        before Production is activated) unless its filters name a mode (or
        a send, whose mode it shows). A Recipient refused at its field
        (``_recipient_refusal``) lists the emails for no filters, in the
        mode, sort and size sent, while the filter form shows what was sent.
        """
        sent = _listing_parameters(request)
        parameters = sent
        # A bulk action's form carries the Recipient already applied, so
        # only a hand-built one can bring a malformed one; it is refused by
        # read_listing's error page as before, never as a refused filter
        # beside an action that ran.
        problem = None if "action" in request.POST else _recipient_refusal(sent)
        if problem is not None:
            field_refusal.append(problem)
            parameters = QueryDict(mutable=True)
            for name in REFUSED_KEEPS:
                if sent.get(name):
                    parameters[name] = sent[name]
        data = read_listing(
            parameters,
            actor=actor.identity,
            default_mode=SystemConfiguration.objects.values_list(
                "mode", flat=True
            ).first(),
        )
        values = data["values"]
        # The filter form shows what was sent on a refused page, as typed.
        form = (
            values
            if problem is None
            else {"state": "all", "purpose": "all"}
            | {name: value for name, value in sent.items() if value}
        )
        rows = _with_timelines(
            with_recipients(
                with_refusal_counts(with_family_names(data["rows"], data["snapshot"])),
                actor.identity,
            )
        )
        # The bulk actions' rows (#2296): each row a bulk action covers gets
        # a selection box of its kind, and Select all N matching counts them
        # on every page of this view.
        available = delivery_bulk.selectable()
        rows = [
            row | {"bulk_kind": available.get(row["id"], {}).get("kind")}
            for row in rows
        ]
        matching = _matching(
            available,
            delivery_bulk.matching_ids(
                values,
                snapshot=data["snapshot"],
                actor=actor.identity,
                among=available,
            )
            if problem is None
            else [],
            sum(1 for row in rows if row["bulk_kind"]),
        )
        table = replace(
            window_table(
                data["window"],
                [
                    row | {"recipient_fields": _recipient_fields(row, values["mode"])}
                    for row in rows
                ],
                data["has_next"],
                carry=_carried(parameters),
                total=data["total"],
                sorting=DELIVERY_SORTING,
                sort=values["sort"],
            ),
            method="post",
            action=request.path,
        )
        context = dict(
            table=table,
            page_address=_page_address(request, parameters, table),
            states=STATES,
            selected_state=form["state"],
            purposes=PURPOSE_CHOICES,
            selected_purpose=form["purpose"],
            recipient=form.get("recipient", ""),
            recipient_pattern=RECIPIENT_PATTERN,
            recipient_hint=RECIPIENT_REFUSAL,
            start=form.get("start", ""),
            end=form.get("end", ""),
            zone=values.get("zone", ""),
            mode=values["mode"],
            mode_fields=_mode_fields(parameters, table),
            send=data["send"],
            holds=read_sending_holds(database_now()),
            # The emails a report send waits on (#1872), drawn once per page
            # load beside the sending limit note.
            report_waiting=waiting_emails(actor.identity),
            # The link line's count (#935), drawn once per page load.
            refusal_count=unresolved_refusals().count(),
            # The view's fields every bulk step posts again, so its answer
            # draws this same view (#2296).
            bulk_view=table.page_fields(table.number),
            matching=matching,
            # The selection column and bulk bar, only while there is
            # something to select (#2296): a box on this page, or Select all
            # N matching.
            bulk_shown=bool(matching) or any(row["bulk_kind"] for row in rows),
            **bulk,
        )
        if problem is not None:
            # The error at the field and in the summary; the hint row keeps
            # the message's height once it clears on the first edit.
            context |= error_context(problem, FIELD_IDS, FIELD_LINES) | {
                "filter_sizes": {FIELD_LINES[problem.field]: problem.message}
            }
        return context, len(data["rows"])

    response = _page(request, "stewardship/deliveries.html", load)
    if field_refusal and response.status_code == 200:
        # This page drawn again for a Recipient refused at its field: a
        # 400 refusal the in-place Apply swaps in, keeping the form.
        refused(response)
    return response


def _matching(available, ids, shown):
    """Select all N matching's toggle for this view, or None (#2296).

    ``ids`` are the selectable emails the view's filters match on every
    page, ``shown`` how many of them this page gives a box. The toggle is
    offered only when the filters match more than the page shows. Its
    ``kind`` is the one kind of all of them, or ``mixed``, by which the
    bulk buttons fit while it is pressed.
    """
    if len(ids) <= shown:
        return None
    kinds = {available[message_id]["kind"] for message_id in ids}
    return dict(count=len(ids), kind=kinds.pop() if len(kinds) == 1 else "mixed")


# What a refused bulk action says in its review dialog (#2296).
BULK_REFUSALS = {
    "changed": _(
        "Some of the selected emails changed, or this review expired, so "
        "nothing was changed. Close this, then select the emails again to "
        "see what qualifies now."
    ),
    "empty": _(
        "No selected email qualifies for this action any more, so nothing "
        "was changed. Close this to see the current list."
    ),
    "mixed": _(
        "The emails matching these filters are both Failed and Not sure it "
        "arrived, which need different actions, so nothing was changed. "
        "Close this, choose one of them under Delivery, then select again."
    ),
    "selection": _(
        "The selected emails could not be read, or more than one page of "
        "them was selected, so nothing was changed. Close this, then select "
        "the emails again in the list."
    ),
    "invalid": _(
        "Enter the evidence or reason and, to record emails as not sent, "
        "confirm that you checked the mail service's records. Nothing was "
        "changed."
    ),
}
# The fields each bulk step posts besides the CSRF token and the view's
# filters, sort, size and page (``VIEW_FIELDS``), as (required, optional):
# the checks below refuse any other, and the refusal log counts them
# (#1444). The selection form posts ``preview`` with the ticked rows
# (``delivery``, the only field that repeats) or Select all N matching's
# ``scope``; the review dialog's Apply posts ``confirm``; the notice's
# Continue posts ``continue``, whose review's Apply posts ``confirm``.
_BULK_FIELDS = {
    "preview": ({"action", "kind", "scope"}, {"delivery"}),
    "continue": ({"action", "preview", "note"}, set()),
    "confirm": ({"action", "preview", "note"}, {"checked"}),
}
BULK_ONLY = {"csrfmiddlewaretoken", "kind", "scope", "delivery", "note", "checked"}
BULK_ONLY |= {"action", "preview"}
VIEW_FIELDS = LISTING_FILTERS | TABLE_FIELDS


def _bulk(request):
    """One bulk step (#2296): a review, or what applying one did.

    ``preview`` (the bulk bar's Send again or Record as not sent) and
    ``continue`` (the notice's Continue) are answered with the review
    dialog's fragment, ``delivery-bulk-review.html``: the count to review,
    the note and records check it asks for, and the signed preview and the
    view's fields that its Apply posts back. ``confirm`` (that Apply)
    resolves each message with the ordinary per-message command
    (``delivery_bulk.apply_preview``) and returns the page's context,
    ``bulk_result`` and ``bulk_note``: Sent mail is drawn again with them,
    and the dialog redraws the page's regions from that answer. A refusal
    is the fragment with its reason (``data-review-refusal``) and a 4xx
    status, shown in the dialog with nothing changed; a denial or an
    outage is the fixed error page. Returns an ``HttpResponse`` or the
    context.
    """
    try:
        service = runtime()
        actor = _principal(request, service.store, activity=True)
        supplied = set(request.POST) - {"csrfmiddlewaretoken"}
        action = request.POST.get("action")
        if action not in _BULK_FIELDS:
            # No sources: an unknown action value, not an unexpected field.
            raise unexpected_fields()
        required, optional = _BULK_FIELDS[action]
        if (
            any(
                len(request.POST.getlist(key)) != 1
                for key in request.POST
                if key != "delivery"
            )
            or not required <= supplied
            or supplied - required - optional - VIEW_FIELDS
        ):
            raise unexpected_fields(
                request.POST,
                allowed=required | optional | VIEW_FIELDS | {"csrfmiddlewaretoken"},
                multiple=("delivery",),
            )
        view = [
            (name, value)
            for name, value in _listing_parameters(request).items()
            if value
        ]
        if action == "preview":
            with transaction.atomic():
                _available()
                review = _bulk_preview(request, actor)
            return _bulk_answer(request, dict(review=review, view=view))
        if action == "continue":
            binding = delivery_bulk.load_preview(
                request.POST["preview"], actor.identity, request.POST["note"]
            )
            with transaction.atomic():
                _available()
                count = delivery_bulk.left(binding)
            if not count:
                raise delivery_bulk.NothingToResolve("Nothing is left.")
            review = dict(
                kind=binding["kind"],
                count=count,
                token=request.POST["preview"],
                note=binding["note"],
                continuing=True,
            )
            return _bulk_answer(request, dict(review=review, view=view))
        binding = delivery_bulk.load_preview(
            request.POST["preview"],
            actor.identity,
            request.POST["note"],
            checked=request.POST.get("checked") == "yes",
        )
        with transaction.atomic():
            _available()
        result = delivery_bulk.apply_preview(
            service.store,
            actor.identity,
            binding,
            scope=lambda: _command_scope(request, service, actor),
            admit=lambda: _principal(request, service.store, final=True),
            preparation_inputs=_retry_inputs,
        )
        # Continue applies the rest of the same preview, past what it skipped.
        return dict(bulk_result=result, bulk_note=binding["note"])
    except delivery_bulk.NothingToResolve as error:
        return _bulk_answer(request, dict(refused="empty"), 409, error)
    except _MixedSelection as error:
        return _bulk_answer(request, dict(refused="mixed"), 409, error)
    except _BadSelection as error:
        return _bulk_answer(request, dict(refused="selection"), 400, error)
    except (StaleRecordError, TaskRetryConflict, signing.BadSignature) as error:
        return _bulk_answer(request, dict(refused="changed"), 409, error)
    except SHARED_REFUSALS as error:
        # A role that lacks the page, or an ended session: the shared
        # refusal (#1924, #2062), not this page's own error.
        return role_response(error)
    except PermissionError:
        return _error(ErrorCode.DENIED, 403)
    except DatabaseError as error:
        return _database_error(error)
    except UNAVAILABLE:
        return _error(ErrorCode.UNAVAILABLE, 503)
    except ValueError as error:
        # A stale form's unexpected fields say so; other invalid input (a
        # missing note or records check) keeps the "Enter…" guidance (#1401).
        return _bulk_answer(
            request, dict(refused="invalid", text=refusal_text(error)), 400, error
        )


class _MixedSelection(ValueError):
    """Select all N matching covers both kinds of email (#2296)."""


class _BadSelection(ValueError):
    """A hand-built selection: a malformed id, more than one page of ids,
    or no rows and no Select all N matching (#2296)."""


def _bulk_preview(request, actor):
    """The review of the bulk bar's action for the posted selection.

    A page selection posts its ticked rows' ids (at most one page,
    ``delivery_bulk.SELECTION_LIMIT``); Select all N matching posts
    ``scope=matching`` with the view's filters, and the emails are read
    again from them, refused when they mix the two kinds. Runs inside the
    caller's transaction; returns ``delivery_bulk.preview``'s answer.
    """
    available = delivery_bulk.selectable()
    kind, scope = request.POST["kind"], request.POST["scope"]
    if scope == "matching":
        data = read_listing(
            _listing_parameters(request),
            actor=actor.identity,
            default_mode=SystemConfiguration.objects.values_list(
                "mode", flat=True
            ).first(),
        )
        ids = delivery_bulk.matching_ids(
            data["values"],
            snapshot=data["snapshot"],
            actor=actor.identity,
            among=available,
        )
        if len({available[message_id]["kind"] for message_id in ids}) > 1:
            raise _MixedSelection("The matching emails mix both kinds.")
    elif scope == "page" and "delivery" in request.POST:
        posted = request.POST.getlist("delivery")
        if len(posted) > delivery_bulk.SELECTION_LIMIT:
            raise _BadSelection("Select at most one page of emails.")
        try:
            ids = [UUID(text) for text in posted]
        except ValueError as error:
            raise _BadSelection("Invalid email in the bulk selection.") from error
    else:
        raise _BadSelection("Invalid bulk selection.")
    return delivery_bulk.preview(
        actor.identity, kind=kind, ids=ids, available=available
    )


def _bulk_answer(request, context, status=200, error=None):
    """The review dialog's fragment for a bulk step, or its refusal.

    ``context`` holds ``review`` (and the ``view`` fields its Apply posts
    back) or ``refused``, a ``BULK_REFUSALS`` key whose words the fragment
    shows (``text`` replaces them for a stale form's unexpected fields).
    A refusal keeps its own explanation and records its reason and code.
    """
    if "refused" in context and not context.get("text"):
        context["text"] = BULK_REFUSALS[context["refused"]]
    response = render(
        request, "stewardship/delivery-bulk-review.html", context, status=status
    )
    response["Cache-Control"] = "no-store"
    if status != 200:
        response.stewardship_safe_error = True
        refused_step(
            response,
            context["refused"],
            error if context["refused"] == "invalid" else None,
        )
    return response


def _available():
    """Refuse while a restore review withholds delivery pages, as ``_page`` does."""
    configuration = SystemConfiguration.objects.first()
    if configuration is None or configuration.restore_review_required:
        raise ObjectDoesNotExist("Delivery pages are withheld.")


def outcome_context(data):
    """The outcome panel's part of the Mail message page's context (#1563).

    ``data`` is ``read_detail``'s answer (its ``outcome``, ``choices``,
    ``actions``, ``paused`` and ``last_attempt_at``). ``choices`` maps each
    answer the question offers by its choice, with a fresh command id (used
    only by an available answer's form) and its unavailable reason's words;
    ``choice_rows`` lists the same answers in the page's order, whose reasons
    the question says once each. ``note_command`` is the closed "Add a note"
    form's command id while the note is offered but no answer offers it,
    else None. ``report_waiting`` says a later send of this report email's
    report waits on it (#1872).
    """
    choices = {
        row["choice"]: row
        | {"id": uuid4(), "reason": UNAVAILABLE_REASONS.get(row["unavailable"])}
        for row in data["choices"]
    }
    asks_note = any(row["action"] == "note" for row in data["choices"])
    return dict(
        outcome=data["outcome"],
        choices=choices,
        choice_rows=list(choices.values()),
        paused=data["paused"],
        last_attempt_at=data["last_attempt_at"],
        note_command=uuid4() if "note" in data["actions"] and not asks_note else None,
        report_waiting=bool(data.get("report_waiting")),
        # Whether any answer can be given now, for the waiting notice.
        answerable=any(row["unavailable"] is None for row in data["choices"]),
    )


@require_safe
def delivery_detail(request, message_id):
    """Immutable attempt metadata is distinct from the latest Task execution."""

    def load(actor):
        """Pin history's upper version to the selected message observation.

        The page names the email by its purpose and its Family's name (from
        the latest ParishSoft data, as Sent mail reads it), its
        recipient addresses (read for ``actor`` through the definer function,
        #1024) and the refused addresses its delivery recorded (#1015).
        ``outcome`` and ``choices`` are its outcome panel and the answers
        its question offers (#1563); ``note_command`` is the closed "Add a
        note" form's command id while the note is offered but no question
        offers it. ``bounce`` is the "Report a bounced address" panel (#806
        slice 4), or None where it does not apply.
        """
        data = read_detail(message_id, request.GET, actor=actor.identity)
        window = data["window"]
        (delivery,) = with_family_names([data["delivery"]], current_snapshot())
        # Each intended address that is a current user's links that
        # user's Timeline (#1885): one query of the users, however many.
        recipients = with_user_links(
            email_recipients(message_id, actor.identity, delivery["state"])
        )
        return dict(
            delivery=delivery,
            recipients=recipients,
            bounce=bounce_panel(
                delivery, recipients, can_change="note" in data["actions"]
            ),
            refused=data["refused"],
            events=data["events"],
            task=data["task"],
            notes=data["notes"],
            **outcome_context(data),
            next_query=_next(request, window, data["has_next"]),
            previous_query=_previous(request, window),
        ), len(data["events"]) + len(data["notes"])

    return _page(request, "stewardship/delivery.html", load, subject=message_id)


@require_safe
def refusal_list(request):
    """Show unresolved Family-scoped addresses; no hidden cross-Family clearance."""

    def load(actor):
        """Read only a bounded current unresolved-address page."""
        data = read_refusals(request.GET)
        return dict(
            table=_table(
                request,
                data["window"],
                # Each Family name opens its Timeline in the current
                # campaign (#1570): one read of the page's record ids.
                link_families(
                    None,
                    with_family_names(
                        data["rows"], data["snapshot"], key="family_duid"
                    ),
                ),
                data["has_next"],
                total=data["total"],
                sorting=REFUSAL_SORTING,
                sort=data["values"]["sort"],
            ),
            query=data["values"].get("duid", ""),
        ), len(data["rows"])

    return _page(request, "stewardship/delivery-refusals.html", load)


@require_safe
def refusal_detail(request, refusal_id):
    """Pin the source generation in a CSRF-protected explicit verification form."""

    def load(actor):
        """Capture retained evidence and the generation the Admin must verify,
        with the Family's name from the latest ParishSoft data (#935)."""
        data = read_refusal(refusal_id, request.GET)
        duid = data["refusal"].family_duid
        named = link_families(
            None,
            with_family_names(
                [dict(family_duid=duid)], current_snapshot(), key="family_duid"
            ),
        )
        # The name opens the Family's Timeline in the current campaign (#1570).
        return data | {
            "family_name": named[0]["family_name"],
            "family_id": named[0]["family_id"],
        }, 1

    return _page(request, "stewardship/delivery-refusal.html", load, subject=refusal_id)


@require_POST
@sensitive_post_parameters("note")
def clear_refusal(request, refusal_id):
    """An explicit current-Admin command never runs in a GET or passive poll."""
    try:
        service = runtime()
        actor = _principal(request, service.store, activity=True)
        names = {
            "command_id",
            "source_snapshot_id",
            "source_generation",
            "note",
            "verified",
        }
        supplied = set(request.POST) - {"csrfmiddlewaretoken"}
        if (
            request.GET
            or supplied != names
            or any(len(request.POST.getlist(key)) != 1 for key in request.POST)
        ):
            raise unexpected_fields(
                request.GET, request.POST, allowed=names | {"csrfmiddlewaretoken"}
            )
        with _command_scope(request, service, actor):
            clear_recipient_refusal(
                service.store,
                actor.identity,
                refusal_id=refusal_id,
                command_id=UUID(request.POST["command_id"]),
                source_snapshot_id=UUID(request.POST["source_snapshot_id"]),
                source_generation=expected_version(request.POST["source_generation"]),
                note=request.POST["note"],
                verified=request.POST["verified"] == "yes",
            )
        response = redirect("admin:delivery_refusal", refusal_id=refusal_id)
        response["Cache-Control"] = "no-store"
        return response
    except (StaleRecordError, TaskRetryConflict):
        return _error(ErrorCode.STALE, 409)
    except SHARED_REFUSALS as error:
        # A role that lacks the page, or an ended session: the shared
        # refusal (#1924, #2062), not this page's own error.
        return role_response(error)
    except PermissionError:
        return _error(ErrorCode.DENIED, 403)
    except MISSING_TARGET:
        return _error(ErrorCode.INVALID, 404)
    except DatabaseError as error:
        return _database_error(error)
    except UNAVAILABLE:
        return _error(ErrorCode.UNAVAILABLE, 503)
    except ValueError as error:
        return _error(
            ErrorCode.INVALID,
            400,
            error,
            back=reverse("admin:delivery_refusal", kwargs={"refusal_id": refusal_id}),
        )


@require_POST
@sensitive_post_parameters("note")
def bounce_report(request, message_id):
    """Report that a delivered email bounced at one address (#806 slice 4).

    The Mail message page's in-place form; an explicit current-Admin command
    that never runs in a GET. It needs the address, a note and the tick, as
    the page's gate does, and answers with the page again (Post/Redirect/
    Get), whose bounce panel lists the report. As for Clear verified
    refusal, the report only sets an address aside, touches no credential,
    and clearing undoes it.
    """
    try:
        service = runtime()
        actor = _principal(request, service.store, activity=True)
        names = {"command_id", "address", "note", "observed"}
        supplied = set(request.POST) - {"csrfmiddlewaretoken"}
        if (
            request.GET
            or supplied != names
            or any(len(request.POST.getlist(key)) != 1 for key in request.POST)
        ):
            raise unexpected_fields(
                request.GET, request.POST, allowed=names | {"csrfmiddlewaretoken"}
            )
        with _command_scope(request, service, actor):
            report_recipient_bounce(
                service.store,
                actor.identity,
                message_id=message_id,
                address=request.POST["address"],
                command_id=UUID(request.POST["command_id"]),
                note=request.POST["note"],
                observed=request.POST["observed"] == "yes",
            )
        response = redirect("admin:delivery", message_id=message_id)
        response["Cache-Control"] = "no-store"
        return response
    except (StaleRecordError, TaskRetryConflict):
        return _error(ErrorCode.STALE, 409)
    except SHARED_REFUSALS as error:
        # A role that lacks the page, or an ended session: the shared
        # refusal (#1924, #2062), not this page's own error.
        return role_response(error)
    except PermissionError:
        return _error(ErrorCode.DENIED, 403)
    except MISSING_TARGET:
        return _error(ErrorCode.INVALID, 404)
    except DatabaseError as error:
        return _database_error(error)
    except UNAVAILABLE:
        return _error(ErrorCode.UNAVAILABLE, 503)
    except ValueError as error:
        return _error(
            ErrorCode.INVALID,
            400,
            error,
            back=reverse("admin:delivery", kwargs={"message_id": message_id}),
        )


@require_POST
@sensitive_post_parameters("note")
def resolution_command(request, message_id):
    """Validate a closed evidence form; no status query or SMTP runs in web."""
    try:
        service = runtime()
        actor = _principal(request, service.store, activity=True)
        required = {"command_id", "expected_version", "action", "note"}
        supplied = set(request.POST) - {"csrfmiddlewaretoken"}
        if (
            request.GET
            or not required <= supplied
            or supplied - required - {"duplicate_acknowledged"}
            or any(len(request.POST.getlist(key)) != 1 for key in request.POST)
        ):
            raise unexpected_fields(
                request.GET,
                request.POST,
                allowed=required | {"duplicate_acknowledged", "csrfmiddlewaretoken"},
            )
        with _command_scope(request, service, actor):
            resolve_delivery(
                service.store,
                actor.identity,
                message_id=message_id,
                command_id=UUID(request.POST["command_id"]),
                expected_version=expected_version(request.POST["expected_version"]),
                action=request.POST["action"],
                note=request.POST["note"],
                duplicate_acknowledged=request.POST.get("duplicate_acknowledged")
                == "yes",
                preparation_inputs=_retry_inputs,
            )
        response = redirect("admin:delivery", message_id=message_id)
        response["Cache-Control"] = "no-store"
        return response
    except (StaleRecordError, TaskRetryConflict):
        return _error(ErrorCode.STALE, 409)
    except SHARED_REFUSALS as error:
        # A role that lacks the page, or an ended session: the shared
        # refusal (#1924, #2062), not this page's own error.
        return role_response(error)
    except PermissionError:
        return _error(ErrorCode.DENIED, 403)
    except MISSING_TARGET:
        return _error(ErrorCode.INVALID, 404)
    except DatabaseError as error:
        return _database_error(error)
    except UNAVAILABLE:
        return _error(ErrorCode.UNAVAILABLE, 503)
    except ValueError as error:
        return _error(
            ErrorCode.INVALID,
            400,
            error,
            back=reverse("admin:delivery", kwargs={"message_id": message_id}),
        )


@require_POST
def preparation_retry(request, task_id, *, report=False):
    """Retry one explicitly selected local preparation after its cause is fixed."""
    kind = REPORT_EMAIL if report else FAMILY_PREPARATION
    try:
        service = runtime()
        caller = AdminCaller.from_request(request)
        actor = _principal(caller, service.store, activity=True)
        supplied = set(request.POST) - {"csrfmiddlewaretoken"}
        if (
            request.GET
            or supplied != {"command_id"}
            or any(len(request.POST.getlist(key)) != 1 for key in request.POST)
        ):
            raise unexpected_fields(
                request.GET,
                request.POST,
                allowed={"command_id", "csrfmiddlewaretoken"},
            )
        command_id = UUID(request.POST["command_id"])
        with _command_scope(caller, service, actor):
            result = retry_preparation_task(
                service.store, actor, task_id, command_id=command_id, kind=kind
            )
        response = redirect("admin:background_task_page", task_id=result.run_id)
        response["Cache-Control"] = "no-store"
        return response
    except (StaleRecordError, TaskRetryConflict):
        return _error(ErrorCode.STALE, 409)
    except SHARED_REFUSALS as error:
        # A role that lacks the page, or an ended session: the shared
        # refusal (#1924, #2062), not this page's own error.
        return role_response(error)
    except PermissionError:
        return _error(ErrorCode.DENIED, 403)
    except MISSING_TARGET:
        return _error(ErrorCode.INVALID, 404)
    except DatabaseError as error:
        return _database_error(error)
    except UNAVAILABLE:
        return _error(ErrorCode.UNAVAILABLE, 503)
    except ValueError as error:
        return _error(
            ErrorCode.INVALID,
            400,
            error,
            back=reverse("admin:background_task_page", kwargs={"task_id": task_id}),
        )

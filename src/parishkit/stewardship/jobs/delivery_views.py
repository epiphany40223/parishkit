"""Admin-only mail metadata and evidence forms; private payloads never serialize."""

from contextlib import contextmanager
from uuid import UUID, uuid4

from django.conf import settings
from django.core import signing
from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, IntegrityError, transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
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
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import (
    MESSAGES,
    ErrorCode,
    expected_version,
)
from parishkit.stewardship.web.tables import (
    window_table,
)

from . import delivery_bulk
from .delivery_admin import clear_recipient_refusal
from .delivery_metadata import DELIVERY_SORTING, STATES
from .delivery_reads import (
    ACTION_LABELS,
    REFUSAL_SORTING,
    read_detail,
    read_listing,
    read_refusal,
    read_refusals,
    read_sending_holds,
)
from .delivery_resolution import resolve_delivery
from .models import TaskRun
from .outbox_models import OutboxMessage
from .recipient_models import RecipientRefusal
from .storage import TaskRetryConflict
from .task_retries import (
    DAILY_DIGEST,
    FAMILY_PREPARATION,
    WEEKLY_DIGEST,
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


def _error(code, status):
    """Render fixed, accessible recovery without submitted evidence or DB chrome.

    In particular, an unavailable database must not be queried a second time
    by the Admin navigation context processor while rendering the error.
    """
    response = HttpResponse(
        render_to_string(
            "stewardship/delivery-error.html", {"message": MESSAGES[code]}
        ),
        status=status,
    )
    response.stewardship_safe_error = True
    response["Cache-Control"] = "no-store"
    if status == 503:
        response["Retry-After"] = "5"
    return response


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
    if purpose in {"receipt", "daily_digest", "weekly_digest"}:
        return dict(general=None, public=None, public_origin=origin)
    keys = family_runtime()
    return dict(general=keys.general, public=keys.public, public_origin=origin)


def _page(request, template, load, *, subject=None, status=200):
    """Capture bounded metadata, render outside locks, then recheck disclosure.

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
            context, count = load()
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
            # bare error: the in-place handler swaps it in (#562).
            response.stewardship_safe_error = True
        response["Cache-Control"] = "no-store"
        return response
    except PermissionError:
        return _error(ErrorCode.DENIED, 403)
    except MISSING_TARGET:
        return _error(ErrorCode.INVALID, 404)
    except DatabaseError as error:
        return _database_error(error)
    except UNAVAILABLE:
        return _error(ErrorCode.UNAVAILABLE, 503)
    except ValueError:
        return _error(ErrorCode.INVALID, 400)


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


@require_http_methods(["GET", "HEAD", "POST"])
@sensitive_post_parameters("note")
def delivery_list(request):
    """Search exact operational identifiers without fetching message content.

    A POST is one of the page's bulk actions (#382 M4, ``_bulk``): it is
    answered with this page, whose bulk review region shows the preview,
    the result or the refusal in place.
    """
    bulk, status = _bulk(request) if request.method == "POST" else ({}, 200)
    if isinstance(bulk, HttpResponse):
        return bulk

    def load():
        """Capture one filtered page without reading any private message payload."""
        data = read_listing(request.GET)
        values = data["values"]
        return dict(
            table=_table(
                request,
                data["window"],
                data["rows"],
                data["has_next"],
                total=data["total"],
                sorting=DELIVERY_SORTING,
                sort=values["sort"],
            ),
            states=STATES,
            selected_state=values["state"],
            query=values["q"],
            send=data["send"],
            holds=read_sending_holds(database_now()),
            bulk_overview=delivery_bulk.overview(),
            **bulk,
        ), len(data["rows"])

    return _page(request, "stewardship/deliveries.html", load, status=status)


# What a refused bulk action says in the page's error summary.
BULK_REFUSALS = {
    "stale": _(
        "These emails changed, or the preview expired, before you confirmed. "
        "Choose Preview again to see what qualifies now."
    ),
    "empty": _("No email qualifies for this action any more."),
    "invalid": _(
        "Enter a note, choose a type of email and, to record emails as not "
        "sent, confirm that you checked the mail service's records."
    ),
}


def _bulk(request):
    """Preview or apply one bulk action; returns ``(context, status)``.

    ``context`` fills the page's bulk review region: ``bulk_review`` (a
    preview to confirm), ``bulk_result`` (what applying it did) or
    ``bulk_errors`` (a refusal, with a 4xx status). A denial or an outage
    is answered by the fixed error page instead, returned in place of the
    context. Previewing changes nothing; applying resolves each message with
    the ordinary per-message command (``delivery_bulk.apply_preview``).
    """
    try:
        service = runtime()
        actor = _principal(request, service.store, activity=True)
        supplied = set(request.POST) - {"csrfmiddlewaretoken"}
        if any(len(request.POST.getlist(key)) != 1 for key in request.POST):
            raise ValueError("Repeated bulk fields.")
        action = request.POST.get("action")
        if action == "preview":
            fields = {"action", "kind", "purpose", "note"}
            if not fields <= supplied or supplied - fields - {"checked"}:
                raise ValueError("Invalid bulk preview fields.")
            with transaction.atomic():
                _available()
                review = delivery_bulk.preview(
                    actor.identity,
                    kind=request.POST["kind"],
                    purpose=request.POST["purpose"],
                    note=request.POST["note"],
                    checked=request.POST.get("checked") == "yes",
                )
            return dict(bulk_review=review | {"form": f"bulk-{review['kind']}"}), 200
        if action != "confirm" or supplied != {"action", "preview", "note"}:
            raise ValueError("Invalid bulk confirmation fields.")
        # The signed preview carries only the note's digest; the note is
        # posted again beside it and must match.
        binding = delivery_bulk.load_preview(
            request.POST["preview"], actor.identity, request.POST["note"]
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
        return dict(bulk_result=result, bulk_note=binding["note"]), 200
    except delivery_bulk.NothingToResolve:
        return _bulk_refused("empty", 409)
    except (StaleRecordError, TaskRetryConflict, signing.BadSignature):
        return _bulk_refused("stale", 409)
    except PermissionError:
        return _error(ErrorCode.DENIED, 403), 403
    except DatabaseError as error:
        return _database_error(error), 503
    except UNAVAILABLE:
        return _error(ErrorCode.UNAVAILABLE, 503), 503
    except ValueError:
        return _bulk_refused("invalid", 400)


def _bulk_refused(reason, status):
    """A refused bulk action: the page again, with its reason in the region."""
    return dict(bulk_errors=[dict(message=BULK_REFUSALS[reason])]), status


def _available():
    """Refuse while a restore review withholds delivery pages, as ``_page`` does."""
    configuration = SystemConfiguration.objects.first()
    if configuration is None or configuration.restore_review_required:
        raise ObjectDoesNotExist("Delivery pages are withheld.")


@require_safe
def delivery_detail(request, message_id):
    """Immutable attempt metadata is distinct from the latest Task execution."""

    def load():
        """Pin history's upper version to the selected message observation."""
        data = read_detail(message_id, request.GET)
        window = data["window"]
        return dict(
            delivery=data["delivery"],
            events=data["events"],
            task=data["task"],
            notes=data["notes"],
            retry_unavailable=data["retry_unavailable"],
            commands=[
                dict(action=action, label=ACTION_LABELS[action], id=uuid4())
                for action in data["actions"]
            ],
            next_query=_next(request, window, data["has_next"]),
            previous_query=_previous(request, window),
        ), len(data["events"]) + len(data["notes"])

    return _page(request, "stewardship/delivery.html", load, subject=message_id)


@require_safe
def refusal_list(request):
    """Show unresolved Family-scoped addresses; no hidden cross-Family clearance."""

    def load():
        """Read only a bounded current unresolved-address page."""
        data = read_refusals(request.GET)
        return dict(
            table=_table(
                request,
                data["window"],
                data["rows"],
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

    def load():
        """Capture retained evidence and the generation the Admin must verify."""
        return read_refusal(refusal_id, request.GET), 1

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
            raise ValueError("Invalid verification fields.")
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
    except PermissionError:
        return _error(ErrorCode.DENIED, 403)
    except MISSING_TARGET:
        return _error(ErrorCode.INVALID, 404)
    except DatabaseError as error:
        return _database_error(error)
    except UNAVAILABLE:
        return _error(ErrorCode.UNAVAILABLE, 503)
    except ValueError:
        return _error(ErrorCode.INVALID, 400)


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
            raise ValueError("Invalid resolution fields.")
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
    except PermissionError:
        return _error(ErrorCode.DENIED, 403)
    except MISSING_TARGET:
        return _error(ErrorCode.INVALID, 404)
    except DatabaseError as error:
        return _database_error(error)
    except UNAVAILABLE:
        return _error(ErrorCode.UNAVAILABLE, 503)
    except ValueError:
        return _error(ErrorCode.INVALID, 400)


@require_POST
def preparation_retry(request, task_id, *, daily=False, weekly=False):
    """Retry one explicitly selected local preparation after its cause is fixed."""
    kind = WEEKLY_DIGEST if weekly else DAILY_DIGEST if daily else FAMILY_PREPARATION
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
            raise ValueError("Invalid preparation retry fields.")
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
    except PermissionError:
        return _error(ErrorCode.DENIED, 403)
    except MISSING_TARGET:
        return _error(ErrorCode.INVALID, 404)
    except DatabaseError as error:
        return _database_error(error)
    except UNAVAILABLE:
        return _error(ErrorCode.UNAVAILABLE, 503)
    except ValueError:
        return _error(ErrorCode.INVALID, 400)

"""Admin-only mail metadata and evidence forms; private payloads never serialize."""

from contextlib import contextmanager
from uuid import UUID, uuid4

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST, require_safe

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.authentication import runtime
from parishkit.stewardship.accounts.cryptography import CryptographicError
from parishkit.stewardship.accounts.limiting import LimiterUnavailable
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import (
    MESSAGES,
    ErrorCode,
    PageWindow,
    expected_version,
    filters,
)

from .delivery_admin import clear_recipient_refusal
from .delivery_metadata import FIELDS, STATES, family_duid, listing, messages
from .delivery_resolution import resolve_delivery
from .delivery_resolution_models import DeliveryResolution
from .models import TaskRun
from .outbox_models import OutboxEvent, OutboxMessage
from .recipient_models import RecipientRefusal, RecipientRefusalResolution
from .storage import TaskRetryConflict

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
    actor = authenticated_admin(
        request, store=store, activity=activity, read_only=final
    )
    if not allows(actor, Capability.BACKGROUND_WORK):
        raise PermissionError("Delivery administration is unavailable.")
    return actor


@contextmanager
def _command_scope(request, service, actor):
    """Lock the live session after the work boundary and retain it through commit.

    Initial form admission is not authority for a later effect. Logout or
    revocation that wins this lock is observed before the command; expiry during
    processing is checked again and rolls back every command-side effect.
    """
    try:
        with work_transaction():
            # Lock without rotating cookies or writing session maintenance in a
            # transaction that the domain command may subsequently roll back.
            PortalSession.objects.select_for_update().filter(
                session_id=request.session.session_key
            ).first()
            current = _principal(request, service.store, final=True)
            if current.identity != actor.identity:
                raise PermissionError("Delivery command identity changed.")
            yield
            current = _principal(request, service.store, final=True)
            if current.identity != actor.identity:
                raise PermissionError("Delivery command identity changed.")
    except PermissionError:
        # Persist timeout/revocation audit or authority rotation only after the
        # effect rollback. A replacement cookie must name a committed session.
        # If maintenance itself is unavailable, deliberately report 503: no
        # effect committed and current session authority could not be established.
        _principal(request, service.store)
        raise


def _retry_inputs(purpose):
    """Receipt retries load no Family keys; invitations retain scoped preparation."""
    from parishkit.stewardship.accounts.family_authentication import (
        runtime as family_runtime,
    )

    origin = getattr(settings, "STEWARDSHIP_PUBLIC_ORIGIN", None)
    if origin is None:
        raise ConfigError("Mail preparation origin is unavailable.")
    if purpose == "receipt":
        return dict(general=None, public=None, public_origin=origin)
    keys = family_runtime()
    return dict(general=keys.general, public=keys.public, public_origin=origin)


def _window(request, allowed):
    """Bound all lists and reject repeated, unknown or malformed query options."""
    values = filters(request.GET, allowed={"page", "size", *allowed})
    return values, PageWindow(
        expected_version(values.get("page", "1")),
        expected_version(values.get("size", "25")),
    )


def _page(request, template, load, *, subject=None):
    """Capture bounded metadata, render outside locks, then recheck disclosure."""
    try:
        service = runtime()
        actor = _principal(request, service.store)
        with transaction.atomic():
            configuration = SystemConfiguration.objects.first()
            if configuration is None or configuration.restore_review_required:
                return _error(ErrorCode.UNAVAILABLE, 503)
            context, count = load()
        response = render(request, template, context)
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


def _previous(request, window):
    """History and notes share a page; always make earlier evidence reachable."""
    if window.page == 1:
        return None
    values = request.GET.copy()
    values["page"] = str(window.page - 1)
    return values.urlencode()


@require_safe
def delivery_list(request):
    """Search exact operational identifiers without fetching message content."""

    def load():
        """Capture one filtered page without reading any private message payload."""
        values, window = _window(request, {"state", "q"})
        state, query = values.get("state", "all"), values.get("q", "")
        rows, following = listing(window, state=state, query=query)
        return dict(
            deliveries=rows,
            states=STATES,
            selected_state=state,
            query=query,
            next_query=_next(request, window, following),
            previous_query=_previous(request, window),
        ), len(rows)

    return _page(request, "stewardship/deliveries.html", load)


@require_safe
def delivery_detail(request, message_id):
    """Immutable attempt metadata is distinct from the latest Task execution."""

    def load():
        """Pin history's upper version to the selected message observation."""
        window = _window(request, set())[1]
        message = messages().values(*FIELDS).get(pk=message_id)
        events, following = window.rows(
            OutboxEvent.objects.filter(
                message_id=message_id, version__lte=message["version"]
            )
            .order_by("-version")
            .values("created_at", "version", "state", "action", "attempt", "reason")
        )
        task = (
            TaskRun.objects.filter(root_id=message["task_id"])
            .order_by("-retry_sequence")
            .values("id", "state", "version", "retry_sequence")
            .first()
        )
        campaign = Campaign.objects.only("state").get(pk=message["campaign_id"])
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT stewardship_export_admitted_v1(%s,true), "
                "stewardship_delivery_retry_admitted_v1(%s)",
                (message["campaign_id"], message_id),
            )
            can_resolve, can_retry = cursor.fetchone()
        actions = ["note"] if campaign.state != "archived" and can_resolve else []
        if task and task["state"] == "failed" and actions:
            if message["state"] == "delivery_unknown":
                actions.append("accept")
                if can_retry:
                    actions.append("resend")
            elif can_retry:
                if message["state"] == "permanent_failure":
                    actions.append("retry_failed")
                elif message["state"] in {"pending", "retry_wait"}:
                    actions.append("retry_unsent")
        labels = {
            "note": _("Save evidence note"),
            "accept": _("Confirm delivery using external evidence"),
            "resend": _("Authorize potentially duplicate resend"),
            "retry_failed": _("Retry failed delivery"),
            "retry_unsent": _("Retry delivery not accepted by the provider"),
        }
        notes, notes_following = window.rows(
            DeliveryResolution.objects.filter(message_id=message_id)
            .order_by("-created_at", "-id")
            .values("created_at", "action", "evidence_note")
        )
        return dict(
            delivery=message,
            events=events,
            task=task,
            notes=notes,
            retry_unavailable=bool(
                task
                and task["state"] == "failed"
                and not can_retry
                and message["state"]
                in {"delivery_unknown", "permanent_failure", "pending", "retry_wait"}
            ),
            commands=[
                dict(action=action, label=labels[action], id=uuid4())
                for action in actions
            ],
            next_query=_next(request, window, following or notes_following),
            previous_query=_previous(request, window),
        ), len(events) + len(notes)

    return _page(request, "stewardship/delivery.html", load, subject=message_id)


@require_safe
def refusal_list(request):
    """Show unresolved Family-scoped addresses; no hidden cross-Family clearance."""

    def load():
        """Read only a bounded current unresolved-address page."""
        values, window = _window(request, {"duid"})
        query = RecipientRefusal.objects.exclude(
            pk__in=RecipientRefusalResolution.objects.values("refusal_id")
        )
        if values.get("duid"):
            query = query.filter(family_duid=family_duid(values["duid"]))
        rows, following = window.rows(query.order_by("family_duid", "address", "id"))
        return dict(
            refusals=rows,
            query=values.get("duid", ""),
            next_query=_next(request, window, following),
            previous_query=_previous(request, window),
        ), len(rows)

    return _page(request, "stewardship/delivery-refusals.html", load)


@require_safe
def refusal_detail(request, refusal_id):
    """Pin the source generation in a CSRF-protected explicit verification form."""

    def load():
        """Capture retained evidence and the generation the Admin must verify."""
        filters(request.GET, allowed=set())
        refusal = RecipientRefusal.objects.get(pk=refusal_id)
        resolved = RecipientRefusalResolution.objects.filter(
            refusal_id=refusal_id
        ).first()
        source = SourceCurrent.objects.first()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM stewardship_source_current cur "
                "JOIN stewardship_system_configuration r ON true "
                "JOIN stewardship_campaign c ON c.id=r.current_campaign_id "
                "JOIN stewardship_campaign_credentials k ON k.campaign_id=c.id "
                "WHERE cur.organization_id=%s AND cur.snapshot_id=k.source_snapshot_id "
                "AND cur.generation=k.source_generation AND NOT k.population_dirty "
                "AND c.state<>'archived' "
                "AND stewardship_export_admitted_v1(c.id,true))",
                (refusal.organization_id,),
            )
            can_clear = cursor.fetchone()[0]
        return dict(
            refusal=refusal,
            resolved=resolved,
            source=source,
            can_clear=can_clear,
            command_id=uuid4(),
        ), 1

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
def preparation_retry(request, task_id):
    """Retry one explicitly selected local preparation after its cause is fixed."""
    from .family_mail_tasks import TASK_TYPE, retry_preparation

    try:
        service = runtime()
        actor = _principal(request, service.store, activity=True)
        supplied = set(request.POST) - {"csrfmiddlewaretoken"}
        if (
            request.GET
            or supplied != {"command_id"}
            or any(len(request.POST.getlist(key)) != 1 for key in request.POST)
        ):
            raise ValueError("Invalid preparation retry fields.")
        command_id = UUID(request.POST["command_id"])
        with _command_scope(request, service, actor):
            task = TaskRun.objects.get(pk=task_id, task_type=TASK_TYPE)
            runs = TaskRun.objects.filter(root_id=task.root_id)
            previous = runs.filter(retry_command_id=command_id).first()
            if (previous and previous.parent_id != task_id) or (
                previous is None
                and runs.order_by("-retry_sequence").first().pk != task_id
            ):
                raise StaleRecordError("The selected preparation is no longer current.")
            result = retry_preparation(
                service.store,
                actor.identity,
                task.domain_request_id,
                command_id=command_id,
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

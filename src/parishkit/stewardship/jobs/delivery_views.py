"""Admin-only mail metadata and evidence forms; private payloads never serialize."""

from uuid import UUID, uuid4

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST, require_safe

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.authentication import runtime
from parishkit.stewardship.accounts.cryptography import CryptographicError
from parishkit.stewardship.accounts.limiting import LimiterUnavailable
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
from .delivery_metadata import FIELDS, STATES, listing, messages
from .delivery_resolution import resolve_delivery
from .delivery_resolution_models import DeliveryResolution
from .models import TaskRun
from .outbox_models import OutboxEvent
from .recipient_models import RecipientRefusal, RecipientRefusalResolution
from .storage import TaskRetryConflict

UNAVAILABLE = (
    ConfigError,
    CryptographicError,
    DatabaseError,
    LimiterUnavailable,
    ObjectDoesNotExist,
)


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
    except UNAVAILABLE:
        return _error(ErrorCode.UNAVAILABLE, 503)
    except ValueError:
        return _error(ErrorCode.INVALID, 400)


def _next(request, window, has_next):
    """Preserve filters without carrying any form evidence into navigation URLs."""
    values = request.GET.copy()
    values["page"] = str(window.page + 1)
    return values.urlencode() if has_next else None


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
            states=[
                (state, _(state.replace("_", " ").capitalize())) for state in STATES
            ],
            selected_state=state,
            query=query,
            next_query=_next(request, window, following),
        ), len(rows)

    return _page(request, "stewardship/deliveries.html", load)


@require_safe
def delivery_detail(request, message_id):
    """Immutable attempt metadata is distinct from the latest Task execution."""

    def load():
        """Pin history's upper version to the selected message observation."""
        _, window = _window(request, set())
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
        actions = [] if campaign.state == "archived" else ["note"]
        if task and task["state"] == "failed" and campaign.state != "archived":
            if message["state"] == "delivery_unknown":
                actions.append("accept")
                if campaign.state != "closed":
                    actions.append("resend")
            elif campaign.state != "closed":
                if message["state"] == "permanent_failure":
                    actions.append("retry_failed")
                elif message["state"] in {"pending", "retry_wait"}:
                    actions.append("retry_unsent")
        labels = {
            "note": "Save evidence note",
            "accept": "Confirm delivery using external evidence",
            "resend": "Authorize potentially duplicate resend",
            "retry_failed": "Retry failed delivery",
            "retry_unsent": "Retry failed unsent preparation",
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
            commands=[
                dict(action=action, label=labels[action], id=uuid4())
                for action in actions
            ],
            next_query=_next(request, window, following or notes_following),
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
            duid = values["duid"]
            if not duid.isascii() or not duid.isdecimal() or not 0 < int(duid) < 2**63:
                raise ValueError("Use an exact Family DUID.")
            query = query.filter(family_duid=int(duid))
        rows, following = window.rows(query.order_by("family_duid", "address", "id"))
        return dict(
            refusals=rows,
            query=values.get("duid", ""),
            next_query=_next(request, window, following),
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
        return dict(
            refusal=refusal,
            resolved=resolved,
            source=source,
            command_id=uuid4(),
        ), 1

    return _page(request, "stewardship/delivery-refusal.html", load, subject=refusal_id)


@require_POST
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
    except UNAVAILABLE:
        return _error(ErrorCode.UNAVAILABLE, 503)
    except ValueError:
        return _error(ErrorCode.INVALID, 400)


@require_POST
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
        preparation = {}
        if request.POST["action"] in {"resend", "retry_failed", "retry_unsent"}:
            from parishkit.stewardship.accounts.family_authentication import (
                runtime as family_runtime,
            )

            keys = family_runtime()
            origin = getattr(settings, "STEWARDSHIP_PUBLIC_ORIGIN", None)
            if origin is None:
                raise ConfigError("Mail preparation origin is unavailable.")
            preparation = dict(
                general=keys.general, public=keys.public, public_origin=origin
            )
        resolve_delivery(
            service.store,
            actor.identity,
            message_id=message_id,
            command_id=UUID(request.POST["command_id"]),
            expected_version=expected_version(request.POST["expected_version"]),
            action=request.POST["action"],
            note=request.POST["note"],
            duplicate_acknowledged=request.POST.get("duplicate_acknowledged") == "yes",
            **preparation,
        )
        response = redirect("admin:delivery", message_id=message_id)
        response["Cache-Control"] = "no-store"
        return response
    except (StaleRecordError, TaskRetryConflict):
        return _error(ErrorCode.STALE, 409)
    except PermissionError:
        return _error(ErrorCode.DENIED, 403)
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
        with work_transaction():
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
    except UNAVAILABLE:
        return _error(ErrorCode.UNAVAILABLE, 503)
    except ValueError:
        return _error(ErrorCode.INVALID, 400)

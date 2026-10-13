"""The Administrator-only System health page and its polled status (ADM-13, #530).

The page renders ``system_health.read_health``: the problems first, then the
six panels. live-status-v1.js re-reads the status fragment every 10 seconds
while the page is open (pausing while the tab is hidden), so a problem that
starts or ends appears by itself. Only opening the page is audited
(``system_health_viewed``); the fragment records nothing and never renews
the Administrator's idle time. Neither read takes the work-order lock or
any data lock; the only lock is the brief one the first sign-in check
takes on the reader's own session row.
"""

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_safe

from parishkit.config import ConfigError
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.work_locks import read_transaction
from parishkit.stewardship.jobs.delivery_views import (
    UNAVAILABLE,
    _database_error,
    _error,
)
from parishkit.stewardship.observability import debug_swallowed
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.system_health import read_health
from parishkit.stewardship.web.contracts import ErrorCode, filters

from .admin_editing import editable_configuration
from .authentication import runtime
from .content_views import resave_recommended
from .policy import Capability, allows
from .runtime_models import SystemConfiguration
from .sessions import authenticated_admin
from .source_form_views import source_form_summary

# How often the open page re-reads the status, in ms (the specification's
# 10 seconds). Watching stops after live-status-v1.js's usual hour.
POLL_MILLISECONDS = 10_000


def _principal(request, store, *, final=False, activity=False):
    """Only an Administrator reads System health.

    Viewing uses the Administrator-only ``SYSTEM_LOGS`` capability that
    System logs uses. Opening the page counts as activity, as any page
    does; the polled fragment and the final recheck renew nothing.
    """
    actor = authenticated_admin(
        request, store=store, activity=activity, read_only=final
    )
    if not allows(actor, Capability.SYSTEM_LOGS):
        raise PermissionError("System health requires an Administrator.")
    return actor


def resave_summary(service, campaign_id):
    """For System health: the current campaign's content to re-save (#838).

    The same rows the Pages and emails list marks, read in their own
    read-only snapshot on page load only. ``"no_campaign"`` without a
    current campaign; None when the read cannot run right now (no coherent
    configuration, a restore, a database fault), so System health says it
    could not check instead of failing the whole page.
    """
    if campaign_id is None:
        return "no_campaign"
    try:
        with read_transaction():
            configuration = editable_configuration(service)
            campaign = Campaign.objects.select_related("active_configuration").get(
                pk=campaign_id
            )
            return resave_recommended(configuration, campaign)
    except (ConfigError, DatabaseError, ObjectDoesNotExist, StorageInvariantError):
        debug_swallowed("re-save summary unavailable")
        return None


def _read(request, template, *, page):
    """Admit an Administrator, read, render, then recheck before release.

    Rendering happens outside the read snapshot. Before the HTML is released
    the session and the restore review are checked again, so an
    Administrator signed out or demoted, or a restore begun, while the page
    rendered withholds it. Only the page is audited, with the number of
    problems it showed.
    """
    try:
        filters(request.GET, allowed=set())
        service = runtime()
        actor = _principal(request, service.store, activity=page)
        found = read_health(service.store)
        if found is None:
            return _error(ErrorCode.UNAVAILABLE, 503)
        health, extra = found
        context = {
            "health": health,
            "campaign_id": extra["campaign_id"],
            "pause": extra["pause"],
            "workgroup_missing": extra["workgroup_missing"],
            "debug_actor": extra["debug_actor"],
            "accept_actor": extra["accept_actor"],
            "poll_interval": POLL_MILLISECONDS,
        }
        if page:
            context["status_url"] = reverse("admin:system_health_status")
            # Families the form cannot open (#774): on page load only, never
            # in the polled status, so the poll never pays for the scan.
            context["source_form_count"] = source_form_summary(extra["campaign_id"])
            # Content to re-save (#838): likewise on page load only.
            context["resave"] = resave_summary(service, extra["campaign_id"])
            # The Admin chrome reuses this instant instead of reading the clock.
            request._stewardship_display_now = health.checked_at
            response = render(request, template, context)
        else:
            # The fragment needs no Admin chrome, so it skips the context
            # processors (and their queries) a full page render runs.
            response = HttpResponse(render_to_string(template, context))
        with transaction.atomic():
            current = _principal(request, service.store, final=True)
            if current.identity != actor.identity:
                raise PermissionError("System health reader changed.")
            if SystemConfiguration.objects.filter(
                restore_review_required=True
            ).exists():
                return _error(ErrorCode.UNAVAILABLE, 503)
            if page:
                record_action(
                    Action.SYSTEM_HEALTH_VIEWED,
                    actor_kind=ActorKind.PORTAL_USER,
                    actor_id=current.identity,
                    context={
                        "outcome": Outcome.SUCCEEDED,
                        "count": len(health.problems),
                    },
                )
        response["Cache-Control"] = "no-store"
        return response
    except PermissionError:
        return _error(ErrorCode.DENIED, 403)
    except DatabaseError as error:
        return _database_error(error)
    except UNAVAILABLE:
        return _error(ErrorCode.UNAVAILABLE, 503)
    except ValueError:
        return _error(ErrorCode.INVALID, 400)


@require_safe
def system(request):
    """``/admin/system/`` opens System health, the group's first entry."""
    return redirect("admin:system_health")


@require_http_methods(["GET", "HEAD", "POST"])
def system_health(request):
    """The System health page: one audited view, then passive polls.

    Take a backup now's and Turn off debug logging's steps post to this
    same address (``_act``).
    """
    if request.method == "POST":
        return _act(request)
    return _read(request, "stewardship/system-health.html", page=True)


@require_safe
def system_health_status(request):
    """Passive status fragment the open page polls; never audited."""
    return _read(request, "stewardship/system-health-status.html", page=False)


# The form fields each action step accepts: Take a backup now's (ADM-13 PR
# 3b), Accept this change once's (PR 5b, named accept_*; ``checked`` is the
# "I checked this in ParishSoft" tick box) and Turn off debug logging's (PR
# 6b, named debug_off_*).
_ACTION_FIELDS = {
    "accept_preview": {"action"},
    "accept_confirm": {"action", "token", "checked"},
    "accept_cancel": {"action"},
    "preview": {"action"},
    "confirm": {"action", "key"},
    "cancel": {"action"},
    "debug_off_preview": {"action"},
    "debug_off_confirm": {"action", "key"},
    "debug_off_cancel": {"action"},
}
# What an in-place refusal is answered with (HTTP status, the region's words).
_REFUSALS = {"reauthenticate": 403, "busy": 409, "invalid": 400, "denied": 403}
# Each action's region (its template's context name) by its steps' prefix;
# Take a backup now's steps have none.
_REGIONS = (("accept_", "accept"), ("debug_off_", "debug_off"))


def _step(action, request, service):
    """Run one action step; return what its region shows.

    Previews and confirmations go to ``system_health``'s service functions,
    which check the Administrator and the fresh sign-in themselves. Cancel
    only closes the preview, so it only rechecks the Administrator.
    """
    from uuid import UUID

    from parishkit.stewardship import system_health as health_actions

    if action == "preview":
        return {"preview": health_actions.preview_backup(request, service.store)}
    if action == "debug_off_preview":
        return {"preview": health_actions.preview_debug_off(request, service.store)}
    if action == "accept_preview":
        return {"preview": health_actions.preview_acceptance(request, service.store)}
    if action == "accept_confirm":
        # The tick box is the confirmation; the page's gate keeps Confirm
        # unavailable until it is ticked, and this refuses a request without.
        if request.POST.get("checked") != "yes":
            raise ValueError("Accepting a change needs the tick box.")
        row, created, refresh = health_actions.accept_change(
            request, service, token=request.POST.get("token", "")
        )
        return {"requested": row, "created": created, "refresh": refresh}
    if action in {"confirm", "debug_off_confirm"}:
        record = (
            health_actions.request_backup
            if action == "confirm"
            else health_actions.turn_off_debug
        )
        row, created = record(
            request, service.store, key=UUID(request.POST.get("key", ""))
        )
        return {"requested": row, "created": created}
    # Cancel only closes the preview; nothing was recorded.
    _principal(request, service.store, activity=True)
    return None


def _act(request):
    """An action's step, posted to the System health page itself.

    The answer is the whole page at its own address, so ui-v1.js swaps the
    action's region (``#backup-now``, ``#accept-change`` or ``#debug-off``)
    in place; the step's result goes to the template as ``backup_now``,
    ``accept`` or ``debug_off``. A step
    the server refuses for a reason the reader can act on (a stale sign-in,
    a request already live or a switch already set, a malformed form) is
    answered the same way, the region saying why, with a status ui-v1.js
    treats as refused; the stale sign-in offers the "Confirm with Google"
    step-up, which returns to this page. Anything else (not an
    Administrator, the system unavailable) is the usual closed refusal. No
    step records a page view; confirming records the action's audit event
    (``backup_requested``, ``source_drop_accepted`` or
    ``debug_logging_turned_off``). For Accept this change once, ``busy``
    means the refusal reviewed changed (another refusal, a promoted full
    refresh, an acceptance already given), which its region words as "This
    changed since you looked".
    """
    from parishkit.config import ConfigError
    from parishkit.stewardship import system_health as health_actions
    from parishkit.stewardship.storage import StaleRecordError

    from .admin_editing import error_response
    from .integration_views import ERRORS
    from .sessions import FreshAuthenticationRequired
    from .setup_views import _closed

    action = request.POST.get("action", "")
    region = next(
        (name for prefix, name in _REGIONS if action.startswith(prefix)), "backup_now"
    )
    result, status = None, 200
    try:
        service = runtime()
        try:
            if action not in _ACTION_FIELDS:
                raise ValueError("Invalid System health action step.")
            _closed(request, _ACTION_FIELDS[action])
            result = _step(action, request, service)
        except FreshAuthenticationRequired:
            result = {"refused": "reauthenticate"}
        except health_actions.ActionRefused:
            # The database refused it for a reason a new sign-in would not
            # fix; the service function logged why.
            result = {"refused": "denied"}
        except StaleRecordError:
            result = {"refused": "busy"}
        except ConfigError:
            # A ValueError too, but the system's configuration, not the
            # form: the outer handler answers it as unavailable (503).
            raise
        except ValueError:
            result = {"refused": "invalid"}
        if result and "refused" in result:
            status = _REFUSALS[result["refused"]]
            # The page itself is still only for an Administrator.
            _principal(request, service.store)
        found = read_health(service.store)
        if found is None:
            return _error(ErrorCode.UNAVAILABLE, 503)
        health, extra = found
        response = render(
            request,
            "stewardship/system-health.html",
            {
                "health": health,
                "campaign_id": extra["campaign_id"],
                "pause": extra["pause"],
                "debug_actor": extra["debug_actor"],
                "accept_actor": extra["accept_actor"],
                "poll_interval": POLL_MILLISECONDS,
                "status_url": reverse("admin:system_health_status"),
                region: result,
                # The debug logging and acceptance regions stay once the page
                # has drawn them, so answering any of their steps keeps them
                # in place.
                "debug_off_shown": region == "debug_off",
                "accept_shown": region == "accept",
            },
            status=status,
        )
        if status != 200:
            # The answer carries its own explanation; keep it as rendered.
            response.stewardship_safe_error = True
        response["Cache-Control"] = "no-store"
        return response
    except ERRORS as error:
        return error_response(error)

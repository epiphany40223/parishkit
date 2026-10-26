"""The Administrator-only System health page and its polled status (ADM-13, #530).

The page renders ``system_health.read_health`` as a dashboard (#1214): the
overall status line, then one tile per area, each with its state, its
problems and its panel content. live-status-v1.js re-reads the status
fragment every 10 seconds while the page is open (pausing while the tab is
hidden), so a problem that starts or ends appears by itself; the two
page-load tiles are checked again by the fragment about every 5 minutes
(#1790). Only opening the page is audited (``system_health_viewed``); the
fragment records nothing and never renews the Administrator's idle time.
Neither read takes the work-order lock or any data lock; the only lock is
the brief one the first sign-in check takes on the reader's own session
row.
"""

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.http import urlencode
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
from parishkit.stewardship.system_health import (
    dashboard,
    loaded_query,
    loaded_states,
    parse_loaded,
    read_health,
    resave_tile,
    source_form_tile,
)
from parishkit.stewardship.web.contracts import ErrorCode, filters
from parishkit.stewardship.web.refusals import (
    refusal_text,
    refused_step,
    unexpected_fields,
)

from .admin_editing import editable_configuration
from .authentication import runtime
from .content_views import resave_recommended
from .policy import Capability
from .role_refusal import SHARED_REFUSALS, require
from .role_refusal import response as role_response
from .runtime_models import SystemConfiguration
from .sessions import authenticated_admin
from .source_form_views import source_form_summary

# How often the open page re-reads the status, in ms (the specification's
# 10 seconds). Watching stops after live-status-v1.js's usual hour.
POLL_MILLISECONDS = 10_000
# How often the open page asks the fragment to check the two page-load tiles
# again (``?slow=1``), in ms: each check reads every Family or every saved
# page and email, so it runs at most every 5 minutes, not on every poll
# (#1790).
RECHECK_MILLISECONDS = 300_000


def _principal(request, store, *, final=False, activity=False):
    """Only an Administrator reads System health.

    Viewing uses the Administrator-only ``SYSTEM_LOGS`` capability that
    System logs uses. Opening the page counts as activity, as any page
    does; the polled fragment and the final recheck renew nothing.
    """
    actor = authenticated_admin(
        request, store=store, activity=activity, read_only=final
    )
    return require(actor, Capability.SYSTEM_LOGS)


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


def _context(health, extra, *, loaded):
    """The template context shared by the page, its fragment and an action.

    ``loaded`` holds the page-load tiles' states that are not Working (the
    page's own reads, or the fragment's ``?loaded=``); an action's answer
    passes none, since only its region is swapped in.
    """
    return {
        "health": health,
        "dashboard": dashboard(health, loaded=loaded),
        "campaign_id": extra["campaign_id"],
        "pause": extra["pause"],
        "debug_actor": extra["debug_actor"],
        "accept_actor": extra["accept_actor"],
        "refused_attempt": extra["refused_attempt"],
        # Clear the halt (ADM-13 PR 4b): None unless a mail sender is halted.
        "clear_halt": extra.get("clear_halt"),
        # The ParishSoft panel's line for a key that reaches several
        # organizations (#1554); None otherwise.
        "organizations": extra.get("organizations"),
        "poll_interval": POLL_MILLISECONDS,
        "recheck_interval": RECHECK_MILLISECONDS,
    }


def _status_url(loaded):
    """The fragment's address, carrying the page-load tiles' states."""
    url = reverse("admin:system_health_status")
    query = loaded_query(loaded)
    return f"{url}?{urlencode({'loaded': query})}" if query else url


def _loaded_reads(service, extra):
    """Run the two page-load checks: ``(states, context)``.

    Families the form cannot open (#774) and the content to re-save (#838)
    each read every Family or every saved page and email, so only opening
    the page and the fragment's slow recheck (``?slow=1``, #1790) run them,
    never an ordinary poll. ``states`` are the tiles' states for the
    dashboard, ``context`` the answers and summaries the templates draw.
    """
    source_form = source_form_summary(extra["campaign_id"])
    resave = resave_summary(service, extra["campaign_id"])
    context = {
        "source_form_count": source_form,
        "resave": resave,
        # Their summaries and states, chosen in one place (#1214).
        "source_form_tile": source_form_tile(source_form),
        "resave_tile": resave_tile(resave),
    }
    return loaded_states(source_form, resave), context


def _read(request, template, *, page):
    """Admit an Administrator, read, render, then recheck before release.

    Rendering happens outside the read snapshot. Before the HTML is released
    the session and the restore review are checked again, so an
    Administrator signed out or demoted, or a restore begun, while the page
    rendered withholds it. Only the page is audited, with the number of
    problems it showed.

    The fragment accepts two query keys. ``loaded`` carries the page-load
    tiles' states, which an ordinary poll counts into the overall line
    (``parse_loaded`` ignores anything else in it). ``slow=1`` asks it to
    check those tiles again instead (#1790): the answer carries their
    copies and the address with their new states. Either way the fragment
    stays passive: no audit row, no renewed idle time. The page accepts
    no key.
    """
    try:
        query = filters(request.GET, allowed=set() if page else {"loaded", "slow"})
        if query.get("slow", "1") != "1":
            raise ValueError("slow takes only 1.")
        service = runtime()
        actor = _principal(request, service.store, activity=page)
        found = read_health(service.store)
        if found is None:
            return _error(ErrorCode.UNAVAILABLE, 503)
        health, extra = found
        if page or "slow" in query:
            loaded, reads = _loaded_reads(service, extra)
            context = _context(health, extra, loaded=loaded) | reads
            # Later polls echo these states (live-status-v1.js follows a
            # fresh data-live-url).
            context["status_url"] = _status_url(loaded)
        else:
            context = _context(health, extra, loaded=parse_loaded(query.get("loaded")))
            # An ordinary poll has no fresh page-load reads, so it draws no
            # copies of those tiles; the page keeps the last ones.
            context["loaded_echoed"] = True
        if page:
            # The Admin chrome reuses this instant instead of reading the clock.
            request._stewardship_display_now = health.checked_at
            response = render(request, template, context)
        else:
            # The fragment needs no Admin chrome, so it skips the context
            # processors (and their queries) a full page render runs. It
            # still carries the reader, whose role decides the actor links
            # (#2259), so a poll draws them as the page did.
            response = HttpResponse(
                render_to_string(template, context | {"request": request})
            )
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
# "I checked this in ParishSoft" tick box), Turn off debug logging's (PR
# 6b, named debug_off_*) and Allow debug logging again's (PR 6c, named
# debug_allow_*, whose answer redraws Turn off debug logging's region).
_ACTION_FIELDS = {
    "accept_preview": {"action"},
    "accept_confirm": {"action", "token", "checked"},
    "accept_cancel": {"action"},
    "preview": {"action"},
    "confirm": {"action", "key"},
    "debug_off_preview": {"action"},
    "debug_off_confirm": {"action", "key"},
    "debug_allow_preview": {"action"},
    "debug_allow_confirm": {"action", "key"},
    # Clear the halt (PR 4b, named clear_*): the preview, Check again and
    # Confirm, which carries the preview's signed binding.
    "clear_preview": {"action"},
    "clear_check": {"action", "token"},
    "clear_confirm": {"action", "token"},
}
# What a refusal is answered with (HTTP status; the words are the region's,
# or the review dialog fragment's).
_REFUSALS = {
    "signed_out": 403,
    "busy": 409,
    "invalid": 400,
    "denied": 403,
    "production": 403,
}
# Each action's region (its template's context name) by its steps' prefix;
# Take a backup now's steps have none.
_REGIONS = (
    ("accept_", "accept"),
    ("debug_off_", "debug_off"),
    ("debug_allow_", "debug_off"),
    ("clear_", "clear"),
)
# The actions confirmed in the shared review dialog (UX conventions, "A
# confirmation when Review would only repeat the page", #1356), by their
# result's context name (and whether it is Allow debug logging again's):
# each step answers with this fragment, which holds the confirmation, the
# refusal (data-review-refusal) or the result, never the whole page.
_DIALOG_ANSWERS = {
    ("backup_now", False): "stewardship/system-health-backup-review.html",
    ("debug_off", False): "stewardship/system-health-debug-off-review.html",
    ("debug_off", True): "stewardship/system-health-debug-allow-review.html",
}


def _step(action, request, service):
    """Run one action step; return what its region shows.

    Previews and confirmations go to ``system_health``'s service functions,
    which check the Administrator and the live session themselves. Cancel
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
    if action in {"clear_preview", "clear_check", "clear_confirm"}:
        return _clear_step(action, request, service)
    if action == "debug_allow_preview":
        preview = health_actions.preview_debug_allow(request, service.store)
        return {"allow": True, "preview": preview}
    if action == "debug_allow_confirm":
        row, created = health_actions.allow_debug(
            request, service.store, key=UUID(request.POST.get("key", ""))
        )
        return {"allow": True, "allowed": row, "created": created}
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
    # Cancel only closes the preview; nothing was recorded. Only Accept
    # this change once has a Cancel step: the review dialog's Cancel sends
    # nothing.
    _principal(request, service.store, activity=True)
    return None


def _clear_step(action, request, service):
    """One of Clear the halt's steps (ADM-13 PR 4b), through the service
    functions ``pk-admin system mail-clear-preview`` and ``system
    mail-clear`` share (``mail_halt_clear``). None needs a recent sign-in
    (#1547). The preview asks mail dispatch for a mailbox check when it
    needs one; Check again asks for a new one; Confirm records the clear
    signal for the halt the preview's binding names."""
    from parishkit.stewardship import mail_halt_clear

    if action == "clear_preview":
        _, preview = mail_halt_clear.preview_clear(request, service.store)
        return {"preview": preview}
    if action == "clear_check":
        return {"preview": mail_halt_clear.check_again(request, service.store)}
    row, created = mail_halt_clear.clear_halt(
        request, service.store, token=request.POST.get("token", "")
    )
    return {"cleared": row, "created": created}


def _act(request):
    """An action's step, posted to the System health page itself.

    Accept this change once's answer is the whole page at its own address,
    so ui-v1.js swaps its region (``#accept-change``) in place. Take a backup
    now, Turn off debug logging and Allow debug logging again confirm in the
    shared review dialog (#1356): each of their steps is
    answered with its fragment (``_DIALOG_ANSWERS``), the preview's
    confirmation, the request made, or a refusal the dialog shows as drawn.
    The step's result goes to the template as ``backup_now``, ``accept`` or
    ``debug_off`` (where an Allow debug logging again step's result is
    marked ``allow``). A step
    the server refuses for a reason the reader can act on (a session that
    ended, a request already live or a switch already set or already cleared, Allow
    debug logging again in Production, a malformed form) is
    answered the same way, the region saying why, with a status ui-v1.js
    treats as refused (an ended session is ``signed_out``). Anything else (not an
    Administrator, the system unavailable) is the usual closed refusal. No
    step records a page view; confirming records the action's audit event
    (``backup_requested``, ``source_drop_accepted``,
    ``debug_logging_turned_off`` or ``debug_logging_allowed``). For Accept
    this change once, ``busy`` means the refusal reviewed changed (another
    refusal, a promoted full refresh, an acceptance already given), which its
    region words as "This changed since you looked".
    """
    from parishkit.config import ConfigError
    from parishkit.stewardship import system_health as health_actions
    from parishkit.stewardship.mail_halt_clear import ClearRefused
    from parishkit.stewardship.storage import StaleRecordError

    from .admin_editing import error_response
    from .integration_views import ERRORS
    from .sessions import SignInEnded
    from .setup_views import _closed

    action = request.POST.get("action", "")
    region = next(
        (name for prefix, name in _REGIONS if action.startswith(prefix)), "backup_now"
    )
    allow = action.startswith("debug_allow_")
    result, status, refused_error = None, 200, None
    try:
        service = runtime()
        try:
            if action not in _ACTION_FIELDS:
                # No sources: an unknown action value, not an unexpected field.
                raise unexpected_fields()
            _closed(request, _ACTION_FIELDS[action])
            result = _step(action, request, service)
        except SignInEnded:
            # The session ended between admission and the guard's write.
            result = {"refused": "signed_out"}
        except health_actions.DebugAllowProduction:
            # Only the server operator clears the switch in Production.
            result = {"refused": "production"}
        except (health_actions.ActionRefused, ClearRefused):
            # The database refused it for a reason signing in again would
            # not fix; the service function logged why.
            result = {"refused": "denied"}
        except StaleRecordError as error:
            result = {"refused": "busy"}
            if region == "clear":
                # Clear the halt says which check no longer holds, in the
                # service function's own plain words (mail_halt_clear.REASONS).
                result["refusal_text"] = str(error)
        except ConfigError:
            # A ValueError too, but the system's configuration, not the
            # form: the outer handler answers it as unavailable (503).
            raise
        except ValueError as error:
            # A stale form's unexpected fields say so in the region (#1401).
            result = {"refused": "invalid", "refusal_text": refusal_text(error)}
            refused_error = error
        if result and "refused" in result:
            result["allow"] = allow
            status = _REFUSALS[result["refused"]]
            # The page itself is still only for an Administrator.
            _principal(request, service.store)
        found = read_health(service.store)
        if found is None:
            return _error(ErrorCode.UNAVAILABLE, 503)
        health, extra = found
        # Only the action's region is used, so the answer skips the
        # page-load tiles' reads; they show as Working in the discarded rest,
        # which draws no copies of them (loaded_echoed) that a status region
        # could take. A review dialog's action answers with its fragment
        # instead, whose result draws the region again.
        response = render(
            request,
            _DIALOG_ANSWERS.get((region, allow), "stewardship/system-health.html"),
            _context(health, extra, loaded={})
            | {"status_url": _status_url({}), "loaded_echoed": True, region: result},
            status=status,
        )
        if status != 200:
            # The answer carries its own explanation; keep it as rendered.
            response.stewardship_safe_error = True
        if result and "refused" in result:
            # The refusal log records why the step was refused (#1403).
            refused_step(response, result["refused"], refused_error)
        response["Cache-Control"] = "no-store"
        return response
    except ERRORS as error:
        return error_response(error)

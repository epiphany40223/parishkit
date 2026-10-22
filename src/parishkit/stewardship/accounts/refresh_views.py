"""Manual ParishSoft refresh: one confirmed, coalesced, durable request.

An Administrator asks for an immediate full refresh from a confirmation page.
The confirmation records one durable refresh command bound to a key the page
chose, so a repeated submission returns the same run, and the domain owner
coalesces the request behind any refresh already waiting for the same window:
no concurrent poll starts, and one manual full load may follow a running one.
Either way the Administrator stays on Refresh from ParishSoft (navigation rule
5, #521): the run is remembered in the signed-in session, never in the URL,
and the page shows its live status below the Refresh now button. Nothing here
reads ParishSoft or holds a lock while waiting; the background service does
the work and the status region reports its phases.
"""

from uuid import uuid4

from django import forms
from django.db import DatabaseError
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_safe

from parishkit.config import ConfigError
from parishkit.stewardship.jobs.models import NONTERMINAL_STATES, TaskRun
from parishkit.stewardship.jobs.operational_sources import configured_policy
from parishkit.stewardship.jobs.views import REFRESH_RUN, _task_read
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.source.errors import (
    SourceOrganizationChanged,
    SourceScopeChanged,
)
from parishkit.stewardship.source.refresh_models import SourceRefreshRequest
from parishkit.stewardship.source.refresh_status import (
    full_refresh_status,
    refresh_schedule,
)
from parishkit.stewardship.source.requests import TASK_TYPE, request_refresh
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError
from parishkit.stewardship.web.admin_forms import AdminForm
from parishkit.stewardship.web.contracts import filters

from .admin_editing import editable_configuration, error_response, principal
from .authentication import runtime
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .sessions import authenticated_admin, automatic_read

# The session key that remembers the run this sign-in last requested, so the
# page can show it after the Post/Redirect/Get (navigation rule 5).
RUN_KEY = "stewardship_refresh_run"
# The page's region that shows the form and that run; Refresh now posts in
# place to it, and its id is the redirect's fragment.
RUN_REGION = REFRESH_RUN


class RefreshForm(AdminForm):
    """Only the page's own request key comes in; the window is decided at admission.

    ``return_step`` is the Campaign setup step whose Load the campaign's
    ParishSoft data notice posted it (#1404), to return to instead of this
    page.
    """

    request_key = forms.UUIDField()
    return_step = forms.CharField(required=False, max_length=16)


def _return_url(step):
    """The Campaign setup step page to return to after a load, or None.

    Only a step of the stepper over the current campaign, while it applies,
    from its own list, so a posted value can never name another address.
    """
    if not step:
        return None
    from . import campaign_wizard

    wizard = campaign_wizard.current()
    found = wizard.step(step) if wizard else None
    return found.url if found else None


def _pending():
    """Whether a refresh is running or a full one waits, for the page's wording.

    The domain owner decides coalescing under its own lock, from the window,
    the lease and the promotion state as well; this read only tells the
    Administrator what to expect, so it counts what the owner could coalesce
    into, a full refresh whose run is nonterminal and not running, and the
    page promises no more than that the request may join it.
    """
    nonterminal = TaskRun.objects.filter(
        task_type=TASK_TYPE, state__in=NONTERMINAL_STATES
    )
    running = nonterminal.filter(state="running").values("root_id")
    waiting = SourceRefreshRequest.objects.filter(
        kind="full", task_root_id__in=nonterminal.values("root_id")
    ).exclude(task_root_id__in=running)
    return {"running": running.exists(), "waiting": waiting.exists()}


def read_refresh_page(service):
    """What the confirmation page shows: what is pending and the latest refresh.

    The page and ``pk-admin refresh status`` (ADM-11 PR 6a) both read this.
    It returns the editable configuration too, which the page displays.
    """
    configuration = editable_configuration(service)
    return configuration, {
        "pending": _pending(),
        # The same full/incremental status the dashboard shows; this page is
        # itself the "run a full refresh now" action, so no link.
        "full_refresh": full_refresh_status(
            refresh_schedule(configuration), timezone.now()
        ),
        # The lateness margin the about text names (#510).
        "lateness_minutes": configured_policy().source_stale_seconds // 60,
    }


def _run(request):
    """The live status context of the run this sign-in last requested, or {}.

    It is read as the Background task page reads it, unaudited, with the
    page's own Background work rechecks, and points the status region's
    poller at the task's passive status fragment, which never renews idle
    time. A run that can no longer be read (removed by retention, or the
    reader no longer allowed) is forgotten rather than shown as an error.
    """
    task_id = request.session.get(RUN_KEY)
    if not isinstance(task_id, str):
        return {}
    result, context = _task_read(request, task_id)
    if context is None:
        if result.status_code in {403, 404}:
            del request.session[RUN_KEY]
        return {}
    return context | {
        "run_task_id": task_id,
        # The poll asks for this page's wording of the region (#521).
        "status_url": reverse("admin:background_task_status", args=[task_id])
        + "?view=refresh",
    }


def _page(request, service):
    """The confirmation page: the latest refresh, what is pending, a fresh key,
    and the status of the run this sign-in last requested."""
    configuration, values = read_refresh_page(service)
    request._stewardship_display_configuration = configuration
    return render(
        request,
        "stewardship/source-refresh.html",
        {**values, **_run(request), "request_key": uuid4()},
    )


def _request(request, service, actor):
    """Record the command under the page's key and show its run on this page."""
    form = RefreshForm(request.POST)
    if not form.is_valid():
        raise ValueError("Invalid refresh form.")
    key = form.cleaned_data["request_key"]
    if key.version != 4:
        raise ValueError("Invalid refresh key.")
    receipt = request_manual_refresh(request, service, actor, key)
    request.session[RUN_KEY] = str(receipt.task_root_id)
    # From a Campaign setup step's notice, back to that step, which then
    # says the data is loading (#1404); the run's progress stays on this page.
    back = _return_url(form.cleaned_data["return_step"])
    if back:
        return HttpResponseRedirect(back)
    return HttpResponseRedirect(reverse("admin:source_refresh") + f"#{RUN_REGION}")


def request_manual_refresh(caller, service, actor, key):
    """Record one manual full refresh command under ``key``; return its receipt.

    The page's POST and ``pk-admin refresh start`` (ADM-11 PR 6a) both call
    this. ``caller`` is the request or an ``AdminCaller``. The Administrator
    is rechecked under the domain's work-order lock, even on a repeat.
    """
    denied = []

    def authorize(scope):
        """Admission is the current Administrator's, rechecked under the lock."""
        fresh = authenticated_admin(caller, store=service.store, read_only=True)
        allowed = (
            allows(fresh, Capability.CONFIGURE) and fresh.identity == actor.identity
        )
        if not allowed:
            denied.append(True)
        return allowed

    try:
        receipt = request_refresh(
            command_id=key,
            cause="manual",
            actor_id=actor.identity,
            correlation_id=current_correlation(),
            authorize=authorize,
        )
    except (SourceOrganizationChanged, SourceScopeChanged):
        # The typed source refusals are permission errors by class; they keep
        # their own meaning and reach the route's handler for them.
        raise
    except PermissionError:
        if denied:
            raise
        # A plain permission error the domain raised for want of a configured
        # organization, not a denial of this Administrator: an outage to the
        # page, as the page itself says.
        raise ConfigError(
            "Source refresh requires its configured organization."
        ) from None
    return receipt


@require_safe
def campaign_load_status(request):
    """Load ParishSoft data's passive status while it awaits the system's load.

    Creating a campaign starts its load by itself (#1951): the refresh
    scheduler requests a full refresh for the new campaign's scope within
    seconds (``campaign_wizard.LOAD_START_WAIT``). Until that refresh
    exists the step has no run to poll, so its region polls this instead,
    which draws the same region from the step's state
    (``campaign_wizard.awaited_load_status``): still waiting, then the
    run's stages, then settled with its follow-up link, which re-reads the
    page in place. Administrators only, as the page; never audited and
    never extends the sign-in, so a page left open does not keep it alive.

    It refuses exactly when the page does: before setup or during a restore
    review (``editable_configuration``), and with no current campaign, an
    unusable configuration rather than a permission refusal. As the page
    and the other passive statuses do, the sign-in is checked again after
    the read, so HTML read for a reader signed out or replaced meanwhile is
    never released.
    """
    from . import campaign_wizard

    try:
        service = runtime()
        actor = principal(
            request, service, read_only=True, capability=Capability.CONFIGURE
        )
        filters(request.GET, allowed=set())
        editable_configuration(service)
        values = campaign_wizard.awaited_load_status()
        if values is None:
            raise ConfigError("No current campaign to load.")
        html = render_to_string("stewardship/campaign-load-status.html", values)
        fresh = principal(
            request, service, read_only=True, capability=Capability.CONFIGURE
        )
        if fresh.identity != actor.identity:
            raise PermissionError("Load status reader changed.")
    except (ConfigError, DatabaseError, PermissionError, ValueError) as error:
        return error_response(error)
    return HttpResponse(html, headers={"Cache-Control": "no-store"})


@require_http_methods(["GET", "HEAD", "POST"])
def source_refresh(request):
    """Confirm and request a manual full refresh; repeats lead to the same run."""
    try:
        service = runtime()
        # The page's own quiet re-read once a load's status settles (#1985)
        # is passive: the reader did not act, so it never extends the
        # sign-in. Every other check is the same as for an ordinary read.
        actor = principal(
            request,
            service,
            passive=automatic_read(request),
            capability=Capability.CONFIGURE,
        )
        filters(request.GET, allowed=set())
        if request.method == "POST":
            filters(
                request.POST,
                allowed={"request_key", "return_step", "csrfmiddlewaretoken"},
            )
            if request.FILES:
                raise ValueError("Unexpected upload.")
            response = _request(request, service, actor)
        else:
            response = _page(request, service)
        fresh = principal(
            request, service, read_only=True, capability=Capability.CONFIGURE
        )
        if fresh.identity != actor.identity:
            raise PermissionError("Refresh requester changed.")
        response["Cache-Control"] = "no-store"
        return response
    except (SourceOrganizationChanged, SourceScopeChanged) as error:
        # The source is not the configured one: an outage to the page, never
        # a refresh of another organization.
        return error_response(ConfigError(str(error)))
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        PermissionError,
        ValueError,
        StaleRecordError,
        StorageInvariantError,
    ) as error:
        return error_response(error)

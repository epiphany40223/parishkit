"""Manual ParishSoft refresh: one confirmed, coalesced, durable request.

An Administrator asks for an immediate full refresh from a confirmation page.
The confirmation records one durable refresh command bound to a key the page
chose, so a repeated submission returns the same run, and the domain owner
coalesces the request behind any refresh already waiting for the same window:
no concurrent poll starts, one manual full load may follow a running one, and
the Administrator is taken to the run's own status page either way. Nothing
here reads ParishSoft or holds a lock while waiting; the background service
does the work and the status page reports its phases.
"""

from uuid import uuid4

from django import forms
from django.db import DatabaseError
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.jobs.models import NONTERMINAL_STATES, TaskRun
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.source.errors import (
    SourceOrganizationChanged,
    SourceScopeChanged,
)
from parishkit.stewardship.source.requests import TASK_TYPE, request_refresh
from parishkit.stewardship.source.snapshot_models import SourceCurrent, SourceSnapshot
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError
from parishkit.stewardship.web.contracts import filters

from .admin_editing import editable_configuration, error_response, principal
from .authentication import runtime
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .sessions import authenticated_admin


class RefreshForm(forms.Form):
    """Only the page's own request key comes in; the window is decided at admission."""

    request_key = forms.UUIDField()


def _pending():
    """Whether a refresh is running or waiting, for the page's wording only.

    The domain owner decides coalescing under its own lock; this read only
    tells the Administrator what to expect.
    """
    states = set(
        TaskRun.objects.filter(task_type=TASK_TYPE, state__in=NONTERMINAL_STATES)
        .values_list("state", flat=True)
        .distinct()
    )
    return {
        "running": "running" in states,
        "waiting": bool(states - {"running"}),
    }


def _page(request, service):
    """The confirmation page: the latest refresh, what is pending, a fresh key."""
    configuration = editable_configuration(service)
    refreshed_at = (
        SourceSnapshot.objects.filter(
            pk__in=SourceCurrent.objects.exclude(snapshot_id=None).values("snapshot_id")
        )
        .values_list("promoted_at", flat=True)
        .first()
    )
    request._stewardship_display_configuration = configuration
    return render(
        request,
        "stewardship/source-refresh.html",
        {
            "refreshed_at": refreshed_at,
            "pending": _pending(),
            "request_key": uuid4(),
        },
    )


def _request(request, service, actor):
    """Record the command under the page's key and go to its run."""
    form = RefreshForm(request.POST)
    if not form.is_valid():
        raise ValueError("Invalid refresh form.")
    key = form.cleaned_data["request_key"]
    if key.version != 4:
        raise ValueError("Invalid refresh key.")

    def authorize(scope):
        """Admission is the current Administrator's, rechecked under the lock."""
        fresh = authenticated_admin(request, store=service.store, read_only=True)
        return allows(fresh, Capability.CONFIGURE) and fresh.identity == actor.identity

    receipt = request_refresh(
        command_id=key,
        cause="manual",
        actor_id=actor.identity,
        correlation_id=current_correlation(),
        authorize=authorize,
    )
    return HttpResponseRedirect(f"/admin/background/task/{receipt.task_root_id}")


@require_http_methods(["GET", "HEAD", "POST"])
def source_refresh(request):
    """Confirm and request a manual full refresh; repeats lead to the same run."""
    try:
        service = runtime()
        actor = principal(request, service, capability=Capability.CONFIGURE)
        filters(request.GET, allowed=set())
        if request.method == "POST":
            filters(request.POST, allowed={"request_key", "csrfmiddlewaretoken"})
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

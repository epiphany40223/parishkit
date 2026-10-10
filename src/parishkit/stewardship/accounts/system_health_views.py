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
from django.views.decorators.http import require_safe

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


@require_safe
def system_health(request):
    """The System health page: one audited view, then passive polls."""
    return _read(request, "stewardship/system-health.html", page=True)


@require_safe
def system_health_status(request):
    """Passive status fragment the open page polls; never audited."""
    return _read(request, "stewardship/system-health-status.html", page=False)

"""Atomic Admin preview of mail schedules and their draft campaign date window."""

from django.core import signing
from django.db import DatabaseError
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.work_locks import (
    read_transaction,
    work_transaction,
)
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters

from . import admin_navigation
from .admin_editing import confirm, error_response, principal
from .authentication import runtime
from .content_views import _records
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .schedule_changes import build_preview, confirm_scope, preview_salt
from .schedule_forms import Schedules, ScheduleWindow, schedule_action
from .schedule_reads import campaign_schedules, schedule_state
from .sessions import authenticated_admin


def _page(request, campaign, window, schedules, digest, *, editable, status=200):
    """Show civil dates/timezone separately from browser-local audit timestamps."""
    # Schedules stay editable when the dates are locked: step 1 of 3 (#196).
    admin_navigation.place(request, flow="change", step="edit")
    response = render(
        request,
        "stewardship/schedule-settings.html",
        {
            "campaign": campaign,
            "window": window,
            "schedules": schedules,
            "base_digest": digest,
            "editable": editable,
            "templates_url": reverse("admin:content_catalog"),
            # Proposed dates arrive in the query string from campaign
            # settings, but POSTs with a query string are refused, so the
            # form posts to the clean path and carries the dates as fields.
            "post_url": request.path,
        },
        status=status,
    )
    if status == 400:
        response.stewardship_safe_error = True
    return response


def _preview(
    request, service, actor, state, campaign, window, schedules, editable, salt
):
    """Review the posted change (``schedule_changes.build_preview``), or show errors."""
    context = build_preview(
        service,
        actor,
        state,
        campaign,
        window,
        schedules,
        base_digest=request.POST.get("base_digest"),
        salt=salt,
    )
    if context is None:
        return _page(
            request,
            campaign,
            window,
            schedules,
            state[0].active_configuration.digest,
            editable=editable,
            status=400,
        )
    admin_navigation.place(request, flow="change", step="review")
    return render(
        request,
        "stewardship/schedule-preview.html",
        context | {"post_url": request.path},
    )


@require_http_methods(["GET", "HEAD", "POST"])
def schedule_settings(request, campaign_id):
    """Current Admins reconcile schedules; dates stay structurally guarded."""
    try:
        service = runtime()
        actor = principal(request, service)
        salt = preview_salt(campaign_id)
        if request.FILES or (request.method == "POST" and request.GET):
            raise ValueError("Invalid schedule parameters.")
        proposed = filters(request.GET, allowed={"start_date", "end_date", "timezone"})
        if any(len(value) > 64 for value in proposed.values()):
            raise ValueError("Invalid proposed campaign window.")
        if request.method == "POST" and request.POST.get("action") == "confirm":
            schedule_action(request.POST, window_fields=set())
            return confirm(
                request,
                service,
                actor,
                salt=salt,
                current_scope=lambda service: confirm_scope(service, campaign_id),
            )
        with (
            read_transaction()
            if request.method in {"GET", "HEAD"}
            else work_transaction()
        ):
            state, campaign, editable = schedule_state(service, campaign_id)
            if proposed and not editable:
                raise StaleRecordError("Campaign dates are structurally locked.")
            previous = campaign.active_configuration.values
            window = ScheduleWindow(
                request.POST if request.method == "POST" else None,
                prefix="window",
                previous=previous,
                editable=editable,
                proposed=proposed,
            )
            if request.method == "POST":
                schedule_action(
                    request.POST,
                    window_fields=set(window.fields) if editable else set(),
                )
            schedules = Schedules(
                request.POST if request.method == "POST" else None,
                prefix="schedules",
                templates=_records(state[0], campaign_id),
                campaign_id=campaign_id,
                campaign=previous,
                previous=campaign_schedules(state[0], campaign_id),
            )
            response = (
                _preview(
                    request,
                    service,
                    actor,
                    state,
                    campaign,
                    window,
                    schedules,
                    editable,
                    salt,
                )
                if request.method == "POST"
                else _page(
                    request,
                    campaign,
                    window,
                    schedules,
                    state[0].active_configuration.digest,
                    editable=editable,
                )
            )
        # Recheck access after the observation ends, so a GET's read-only
        # snapshot cannot hide a revocation committed while it rendered.
        if not allows(
            authenticated_admin(request, store=service.store, read_only=True),
            Capability.CONFIGURE,
        ):
            raise PermissionError("Schedule access was revoked.")
        response["Cache-Control"] = "no-store"
        return response
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        PermissionError,
        ValueError,
        LookupError,
        StaleRecordError,
        signing.BadSignature,
    ) as error:
        return error_response(error)

"""Atomic Admin preview of mail schedules and their draft campaign date window."""

from uuid import uuid4

from django.core import signing
from django.db import DatabaseError
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.configuration import schedule_window_changed
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters

from .admin_editing import confirm, error_response, principal, sign_preview
from .authentication import runtime
from .campaign_views import _state, _target
from .content_forms import EMAIL_LABELS
from .content_views import _campaign, _records
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .request_patch import build_candidate
from .schedule_forms import WEEKDAYS, Schedules, ScheduleWindow, schedule_action
from .schedule_preview import fingerprint, work_summary
from .sessions import authenticated_admin


def _scope(service, campaign_id):
    """Confirmation rechecks the same schedule generation inside intake's work lock."""
    state = _state(service)
    return state[0], fingerprint(state[-1], work_summary(campaign_id))


def _describe(values):
    """Name the weekday instead of exposing a storage index to the Administrator."""
    if values is None:
        return None
    return values | {
        "weekday_label": _(WEEKDAYS[values["weekday"]])
        if values["weekday"] is not None
        else None
    }


def _page(request, campaign, window, schedules, digest, *, editable, status=200):
    """Show civil dates/timezone separately from browser-local audit timestamps."""
    response = render(
        request,
        "stewardship/schedule-settings.html",
        {
            "campaign": campaign,
            "window": window,
            "schedules": schedules,
            "base_digest": digest,
            "editable": editable,
        },
        status=status,
    )
    if status == 400:
        response.stewardship_safe_error = True
    return response


def _preview(
    request, service, actor, state, campaign, window, schedules, editable, salt
):
    """Validate the complete candidate, including each stranded or reordered mailing."""
    configuration = state[0]
    digest = configuration.active_configuration.digest
    if request.POST.get("base_digest") != digest:
        raise StaleRecordError("Reload campaign schedules before saving.")
    window_valid = window.is_valid()
    if window_valid:
        schedules.campaign = window.values()
    schedules_valid = schedules.is_valid()
    if not window_valid or not schedules_valid:
        return _page(
            request, campaign, window, schedules, digest, editable=editable, status=400
        )
    changed = {
        key: value
        for key, value in window.values().items()
        if value != campaign.active_configuration.values[key]
    }
    patch = schedules.patch()
    if schedule_window_changed(campaign.active_configuration.values, window.values()):
        explicit = {row["id"] for row in patch}
        # Identical civil mail fields still acquire a new cadence/window when
        # the campaign moves. Include them in the reviewed work inventory too.
        patch.extend(
            {"operation": "update", "section": "schedules", **row}
            for row in schedules.previous
            if row["id"] not in explicit
        )
    schedule_changes = list(patch)
    if changed:
        patch.append(
            {
                "operation": "update",
                "section": "campaigns",
                "id": str(campaign.pk),
                "values": changed,
            }
        )
    base = service.store.active()
    if base is None or base.digest != digest:
        raise StaleRecordError("The applied configuration changed.")
    try:
        if not patch:
            raise ValueError("No changes.")
        build_candidate(base, patch, candidate_id=uuid4())
    except (ConfigError, ValueError):
        window.add_error(
            None,
            "Every Family mailing must fit the campaign; reminders must follow "
            "exactly one initial mailing and use distinct times. Resolve all "
            "affected schedules, or change at most 100 records per request.",
        )
        return _page(
            request, campaign, window, schedules, digest, editable=editable, status=400
        )
    summary = work_summary(campaign.pk)
    prior = {row["id"]: row["values"] for row in schedules.previous}
    changes = [
        {
            "operation": row["operation"],
            "before": _describe(prior.get(row["id"])),
            "after": _describe(row.get("values")),
            "impact": summary.get(row["id"], {}),
            "label": EMAIL_LABELS[(row.get("values") or prior[row["id"]])["kind"]],
        }
        for row in schedule_changes
    ]
    blocking = sum(change["impact"].get("blocking", 0) for change in changes)
    return render(
        request,
        "stewardship/schedule-preview.html",
        {
            "campaign": campaign,
            "changes": changes,
            "window_changes": changed,
            "before_window": campaign.active_configuration.values,
            "after_window": window.values(),
            "blocking": blocking,
            "preview": None
            if blocking
            else sign_preview(
                actor=actor,
                configuration=configuration,
                patch=patch,
                salt=salt,
                snapshot=fingerprint(state[-1], summary),
            ),
        },
    )


@require_http_methods(["GET", "HEAD", "POST"])
def schedule_settings(request, campaign_id):
    """Current Admins reconcile schedules; dates stay structurally guarded."""
    try:
        service = runtime()
        actor = principal(request, service)
        salt = f"stewardship-schedules-preview-v1:{campaign_id}"
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
                current_scope=lambda service: _scope(service, campaign_id),
            )
        with work_transaction():
            state = _state(service)
            campaign = _campaign(state, campaign_id)
            _, editable = _target(state[0], state[1], state[3], campaign_id)
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
                previous=[
                    row
                    for row in state[0]
                    .active_configuration.canonical_document["sections"]
                    .get("schedules", [])
                    if row["values"]["campaign_id"] == str(campaign_id)
                ],
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

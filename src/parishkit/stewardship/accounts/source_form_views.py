"""Families the form cannot open: the Administrator's list (#774).

``reports.source_form.blocked_families`` finds them with the same check as
``pk-stewardship source-form-check``. This page lists them, sorted and paged
in place as an ordinary GET table (nothing on it is private search text), for
Administrators only, as System health is. Opening it is audited with the
number of Families listed, never their DUIDs. System health shows the count
on page load (``source_form_summary``), outside its polled status, so the
10-second poll never pays for the scan.
"""

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.views.decorators.http import require_safe

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.read_guards import (
    CampaignReadGuard,
    ReadUnavailable,
)
from parishkit.stewardship.observability import Event, debug_swallowed, emit_failure
from parishkit.stewardship.reports.export_views import SAFE_FAILURES
from parishkit.stewardship.reports.source_form import blocked_families
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.tables import Sorting, paginate, table_parameters

TEMPLATE = "stewardship/source-form.html"


def _text(value):
    """Case-insensitive text sort key."""
    return (value or "").casefold()


SORTING = Sorting.by_column(
    {
        "family": lambda row: (_text(row["family_name"]), row["family_duid"]),
        "duid": lambda row: row["family_duid"],
        "member": lambda row: row["member_duid"] or 0,
        "field": lambda row: _text(row["field"]),
    },
    default="family",
)


def _principal(request, store, *, read_only=False):
    """Administrators only, with System health's capability."""
    principal = authenticated_admin(
        request, store=store, activity=not read_only, read_only=read_only
    )
    if not allows(principal, Capability.SYSTEM_LOGS):
        raise PermissionError("This list requires an Administrator.")
    return principal


def _current_campaign():
    """The current campaign's id, or None."""
    return SystemConfiguration.objects.values_list(
        "current_campaign_id", flat=True
    ).get()


def _no_abort():
    """A plain page render has no transport to terminate on a deadline."""


def source_form_summary(campaign_id):
    """For System health: how many Families are blocked, or None if unknown.

    With no current campaign it answers ``"no_campaign"``, so System health
    says what the list says rather than that the check failed.

    Read in its own guard. A scan that cannot run now (no promoted source, a
    restore review, a deadline) answers None, and System health says it
    could not check rather than failing the whole page.
    """
    if campaign_id is None:
        return "no_campaign"
    try:
        with CampaignReadGuard(
            [campaign_id], authorize=lambda guard: None, abort=_no_abort
        ):
            return blocked_families(campaign_id)["families"]
    except (ConfigError, ReadUnavailable, DatabaseError, StorageInvariantError):
        debug_swallowed("source form summary unavailable")
        return None


def _error(status):
    """No private values, and a way back to System health."""
    response = HttpResponse(
        render_to_string("stewardship/source-form-error.html", {"status": status}),
        status=status,
        headers={"Cache-Control": "no-store"},
    )
    response.stewardship_safe_error = True
    if status == 503:
        response["Retry-After"] = "5"
    return response


def _audit(principal, outcome, count):
    """Retain access evidence as a count only."""
    with transaction.atomic():
        record_action(
            Action.SOURCE_FORM_VIEWED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=principal.identity,
            context={"outcome": outcome, "count": count},
        )


@require_safe
def source_form(request):
    """The list of Families whose ParishSoft data blocks the form."""
    finish, handed_off = None, False
    try:
        service = runtime()
        principal = _principal(request, service.store)
        try:
            paging = filters(request.GET, allowed=table_parameters())
            paginate([], paging, sorting=SORTING)
        except ValueError:
            return _error(400)
        campaign_id = _current_campaign()
        if campaign_id is None:
            response = HttpResponse(
                render_to_string(TEMPLATE, {"no_campaign": True}, request=request),
                headers={"Cache-Control": "no-store"},
            )
            _audit(principal, Outcome.SUCCEEDED, 0)
            return response
        finalized, count = False, 0

        def finish(completed):
            """Audit completion once, after the read guard is released."""
            nonlocal finalized
            if finalized:
                return
            finalized = True
            try:
                _audit(
                    principal,
                    Outcome.SUCCEEDED if completed else Outcome.FAILED,
                    count,
                )
            except (DatabaseError, StorageInvariantError) as error:
                emit_failure(error, event=Event.REPORT_AUDIT_FAILED)

        def authorize(guard):
            """A role change while the list is produced ends it."""
            nonlocal principal
            fresh = _principal(request, service.store, read_only=True)
            if fresh.identity != principal.identity:
                raise PermissionError("Source form list access changed.")
            principal = fresh

        def content():
            """Scan (or reuse the cached scan), then render the page."""
            nonlocal count
            result = blocked_families(campaign_id)
            count = result["families"]
            table = paginate(result["rows"], paging, sorting=SORTING)
            return iter(
                (
                    render_to_string(
                        TEMPLATE, result | {"table": table}, request=request
                    ).encode(),
                )
            )

        response = campaign_response(
            request,
            [campaign_id],
            authorize=authorize,
            open_content=content,
            on_close=finish,
        )
        if response.status_code == 503 and not response.streaming:
            return _error(503)
        handed_off = response.status_code == 200 and response.streaming
        return response
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError, ConfigError):
        return _error(503)
    finally:
        if finish is not None and not handed_off:
            finish(False)

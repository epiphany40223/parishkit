"""Native private selection; no client-provided document or authorization scope."""

from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.urls import reverse
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.contracts import (
    ErrorCode,
    FieldError,
    validation_response,
)
from parishkit.stewardship.web.dates import UnknownZone, refuse_zoneless_dates
from parishkit.stewardship.web.refusals import Refusal
from parishkit.stewardship.web.report_errors import report_unavailable
from parishkit.stewardship.web.security import private_response

from .export_services import ExportRequestBound
from .export_ui import _redirect
from .export_views import SAFE_FAILURES
from .ministries import ZONE_MESSAGE, MinistryQuery, refuse_leader_activity
from .ministry_exports import MAX_PACKET_MINISTRIES, create_ministry_export
from .ministry_views import _principal

# Same meaning as the shared export-request-bound.html notice on the HTML pages.
BOUND = (
    "This export form was already used for a different export. Nothing new was"
    " queued. Reload the page and try again.\n"
)
# The packet form's help states the same limit (ministry-report.html).
TOO_MANY = format_lazy(
    _(
        "A follow-up packet can include at most {limit} ticked Ministries, so "
        "nothing was queued."
    ),
    limit=MAX_PACKET_MINISTRIES,
)
TOO_MANY_FIX = _(
    "Tick fewer Ministries, or leave every box unticked to include all the "
    "Ministries you are authorized for."
)


class TooManyMinistries(ValueError):
    """A packet selection ticks more Ministries than one packet may hold."""


@require_POST
def create(request, campaign_id):
    """Accept complete filtered summary/join/leave work through a CSRF form."""
    try:
        service = runtime()
        actor = _principal(request, service.store)
        parameters = request.POST.copy()
        parameters.pop("csrfmiddlewaretoken", None)
        fields = {"format", "browser_timezone", "request_key", "action", "ministry"}
        expected = fields | (set(MinistryQuery.__dataclass_fields__) - {"page", "size"})
        # A query string is refused first: only the POSTed form's own
        # fields may earn the reload-the-page zone refusal.
        if request.GET:
            raise ValueError("Invalid Ministry export fields.")
        refuse_zoneless_dates(parameters, expected)
        if set(parameters) != expected or any(
            len(parameters.getlist(key)) != 1 for key in parameters
        ):
            raise ValueError("Invalid Ministry export fields.")
        values = {key: parameters.pop(key)[0] for key in fields}
        ministry = values["ministry"]
        if ministry and (
            len(ministry) > 10
            or not ministry.isascii()
            or not ministry.isdecimal()
            or str(int(ministry)) != ministry
            or not 0 < int(ministry) < 2**31
        ):
            raise ValueError("Invalid Ministry selection.")
        result = create_ministry_export(
            service.store,
            actor.identity,
            campaign_id=campaign_id,
            query=refuse_leader_activity(
                actor, MinistryQuery.parse(parameters, detail=bool(ministry))
            ),
            ministry_id=int(ministry) if ministry else None,
            action=values["action"],
            format=values["format"],
            browser_timezone=values["browser_timezone"],
            request_key=UUID(values["request_key"]),
        )
        return _redirect(result.pk)
    except ExportRequestBound:
        return private_response(BOUND, status=409)
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return report_unavailable()
    except UnknownZone:
        return private_response(ZONE_MESSAGE, status=400)
    except ValueError:
        return private_response("Invalid Ministry export selection.\n", status=400)


def packet_selection(parameters):
    """Closed form grammar: ticked Ministries, or none for every authorized one.

    One control has one meaning, so the form cannot reach an ambiguous state
    such as "all" selected beside individually ticked boxes.
    """
    fields = {"format", "browser_timezone", "request_key"}
    keys = set(parameters)
    if (
        not fields <= keys
        or keys - fields - {"ministries", "history"}
        or any(len(parameters.getlist(key)) != 1 for key in keys - {"ministries"})
        or parameters.get("history", "yes") != "yes"
    ):
        raise ValueError("Invalid Ministry packet fields.")
    chosen = parameters.getlist("ministries")
    if not chosen:
        return None
    if len(chosen) > MAX_PACKET_MINISTRIES:
        raise TooManyMinistries("Too many Ministries for one packet.")
    if any(
        len(value) > 10
        or not value.isascii()
        or not value.isdecimal()
        or str(int(value)) != value
        or not 0 < int(value) < 2**31
        for value in chosen
    ):
        raise ValueError("Invalid Ministry packet selection.")
    ministries = tuple(sorted({int(value) for value in chosen}))
    if len(ministries) != len(chosen):
        raise ValueError("A Ministry was selected more than once.")
    return ministries


@require_POST
def create_packet(request, campaign_id):
    """Queue one multi-Ministry follow-up packet through a CSRF form."""
    try:
        service = runtime()
        actor = _principal(request, service.store)
        parameters = request.POST.copy()
        parameters.pop("csrfmiddlewaretoken", None)
        if request.GET:
            raise ValueError("Ministry packets require a POST body.")
        ministries = packet_selection(parameters)
        result = create_ministry_export(
            service.store,
            actor.identity,
            campaign_id=campaign_id,
            action="packet",
            ministries=ministries,
            history="history" in parameters,
            format=parameters["format"],
            browser_timezone=parameters["browser_timezone"],
            request_key=UUID(parameters["request_key"]),
        )
        return _redirect(result.pk)
    except ExportRequestBound:
        return private_response(BOUND, status=409)
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return report_unavailable()
    except TooManyMinistries:
        # Ticking past the limit is an ordinary choice, not a malformed
        # form, so the refusal says what to do instead: a typed refusal,
        # shown to a person as the Admin error page with a way back.
        return validation_response(
            [FieldError(ErrorCode.INVALID, "ministries")],
            refusal=Refusal(
                TOO_MANY,
                fix=TOO_MANY_FIX,
                link=reverse("admin:ministry_report"),
                link_label=_("Return to Ministry requests"),
            ),
        )
    except ValueError:
        return private_response("Invalid Ministry packet selection.\n", status=400)

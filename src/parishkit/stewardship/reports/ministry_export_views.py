"""Native private selection; no client-provided document or authorization scope."""

from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.views.decorators.http import require_POST

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.security import private_response

from .export_ui import _redirect
from .export_views import SAFE_FAILURES
from .ministries import MinistryQuery
from .ministry_exports import MAX_PACKET_MINISTRIES, create_ministry_export
from .ministry_views import _principal


@require_POST
def create(request, campaign_id):
    """Accept complete filtered summary/join/leave work through a CSRF form."""
    try:
        service = runtime()
        actor = _principal(request, service.store)
        parameters = request.POST.copy()
        parameters.pop("csrfmiddlewaretoken", None)
        fields = {"format", "browser_timezone", "request_key", "action", "ministry"}
        if (
            request.GET
            or set(parameters)
            != fields | (set(MinistryQuery.__dataclass_fields__) - {"page"})
            or any(len(parameters.getlist(key)) != 1 for key in parameters)
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
            query=MinistryQuery.parse(parameters, detail=bool(ministry)),
            ministry_id=int(ministry) if ministry else None,
            action=values["action"],
            format=values["format"],
            browser_timezone=values["browser_timezone"],
            request_key=UUID(values["request_key"]),
        )
        return _redirect(result.pk)
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return denial(status=503, retry=5)
    except ValueError:
        return private_response("Invalid Ministry export selection.\n", status=400)


def packet_selection(parameters):
    """Closed form grammar: every authorized Ministry, or an explicit chosen set."""
    fields = {"format", "browser_timezone", "request_key", "selection"}
    keys = set(parameters)
    if (
        not fields <= keys
        or keys - fields - {"ministries", "history"}
        or any(len(parameters.getlist(key)) != 1 for key in keys - {"ministries"})
        or parameters.get("history", "yes") != "yes"
        or parameters["selection"] not in {"all", "chosen"}
    ):
        raise ValueError("Invalid Ministry packet fields.")
    chosen = parameters.getlist("ministries")
    if parameters["selection"] == "all":
        # Ticked boxes are ignored only when the form says so explicitly; a
        # mixed request is ambiguous, so refuse it rather than guess.
        if chosen:
            raise ValueError("Choose all Ministries or a selection, not both.")
        return None
    if not 1 <= len(chosen) <= MAX_PACKET_MINISTRIES or any(
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
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return denial(status=503, retry=5)
    except ValueError:
        return private_response("Invalid Ministry packet selection.\n", status=400)

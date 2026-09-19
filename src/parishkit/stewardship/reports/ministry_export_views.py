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
from .ministry_exports import create_ministry_export
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

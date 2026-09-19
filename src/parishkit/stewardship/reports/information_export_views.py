"""Native complete-result export commands with private CSRF POST filter state."""

from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.views.decorators.http import require_POST

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.storage import StorageInvariantError

from .export_ui import _redirect
from .export_views import SAFE_FAILURES
from .information import InformationQuery
from .information_exports import create_information_export
from .information_views import _error, _principal


@require_POST
def create(request, campaign_id):
    """The displayed all-result estimate is not a page selection or a data pin."""
    try:
        service = runtime()
        principal = _principal(request, service.store)
        parameters = request.POST.copy()
        parameters.pop("csrfmiddlewaretoken", None)
        fields = {"format", "browser_timezone", "request_key"}
        query_fields = set(InformationQuery.__dataclass_fields__) - {"page"}
        if (
            request.GET
            or set(parameters) - {"history"} != fields | query_fields
            or any(len(parameters.getlist(key)) != 1 for key in parameters)
        ):
            raise ValueError("Invalid information export fields.")
        history = parameters.pop("history", [None])[0]
        if history not in {None, "yes"}:
            raise ValueError("Invalid workflow history option.")
        values = {field: parameters.pop(field)[0] for field in fields}
        result = create_information_export(
            service.store,
            principal.identity,
            campaign_id=campaign_id,
            query=InformationQuery.parse(parameters),
            history=history == "yes",
            format=values["format"],
            browser_timezone=values["browser_timezone"],
            request_key=UUID(values["request_key"]),
        )
        return _redirect(result.pk)
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return _error(campaign_id, status=503)
    except ValueError:
        return _error(campaign_id, status=400)

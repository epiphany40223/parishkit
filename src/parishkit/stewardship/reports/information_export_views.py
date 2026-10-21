"""Native complete-result export commands with private CSRF POST filter state."""

from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.views.decorators.http import require_POST

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.dates import (
    parish_zone,
    unasked_zone,
)
from parishkit.stewardship.web.review_worklist import RETIRED_DATES, drop_retired_dates

from .export_services import ExportRequestBound
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
        # The page has no submitted-date filter (#1888): a pane drawn before
        # then still posts its empty dates and zone, which are dropped
        # unread, so the export captures no date range.
        parameters = drop_retired_dates(request.POST.copy())
        parameters.pop("csrfmiddlewaretoken", None)
        # The browser's zone is posted unasked, so it is optional and
        # never refused: a missing or unknown one is the parish's (#1679).
        fields = {"format", "request_key"}
        query_fields = set(InformationQuery.__dataclass_fields__) - {
            "page",
            *RETIRED_DATES,
        }
        if request.GET:
            raise ValueError("Invalid information export fields.")
        if set(parameters) - {
            "history",
            "browser_timezone",
        } != fields | query_fields or any(
            len(parameters.getlist(key)) != 1 for key in parameters
        ):
            raise ValueError("Invalid information export fields.")
        history = parameters.pop("history", [None])[0]
        if history not in {None, "yes"}:
            raise ValueError("Invalid workflow history option.")
        values = {field: parameters.pop(field)[0] for field in fields}
        browser = parameters.pop("browser_timezone", [None])[0]
        query = InformationQuery.parse(parameters)
        zone = unasked_zone(browser, None) or parish_zone()
        result = create_information_export(
            service.store,
            principal.identity,
            campaign_id=campaign_id,
            query=query,
            history=history == "yes",
            format=values["format"],
            browser_timezone=zone,
            request_key=UUID(values["request_key"]),
        )
        return _redirect(result.pk)
    except ExportRequestBound:
        return _error(campaign_id, status=409, bound=True)
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return _error(campaign_id, status=503)
    except ValueError:
        return _error(campaign_id, status=400)

"""Native complete-result financial export command with private CSRF POST state."""

from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.views.decorators.http import require_POST

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.storage import StorageInvariantError

from .export_ui import _redirect
from .export_views import SAFE_FAILURES
from .financial import FinancialQuery
from .financial_exports import create_financial_export
from .financial_views import _error, _principal
from .read_admission import admit_report_read


@require_POST
def create(request, campaign_id):
    """Queue one complete capture of the applied filters; the page is not a pin."""
    try:
        service = runtime()
        principal = _principal(request, service.store)
        # An unknown campaign is a denial, never a filter problem with a link
        # back to a page that would only deny again.
        admit_report_read(campaign_id)
        parameters = request.POST.copy()
        parameters.pop("csrfmiddlewaretoken", None)
        fields = {"format", "browser_timezone", "request_key"}
        query_fields = set(FinancialQuery.__dataclass_fields__) - {"page"}
        if (
            request.GET
            or set(parameters) != fields | query_fields
            or any(len(parameters.getlist(key)) != 1 for key in parameters)
        ):
            raise ValueError("Invalid financial export fields.")
        values = {field: parameters.pop(field)[0] for field in fields}
        result = create_financial_export(
            service.store,
            principal.identity,
            campaign_id=campaign_id,
            query=FinancialQuery.parse(parameters),
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

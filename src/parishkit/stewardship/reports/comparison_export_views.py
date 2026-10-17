"""The Campaign comparison page's download form (#1018, slice 6).

Administrators and Staff (``CAMPAIGN_REPORT``, as on the page) queue a CSV,
XLSX or PDF summary of the comparison on the day the page shows. The view
reads the page's comparison under the campaign's read guard and reduces it
at once to the protected capture (``comparison_exports.read_capture``),
then stores it and queues the file (``create_comparison_export``) and
answers with the export's status page, where the shared lifecycle
publishes, downloads, cancels and retries it.

The form posts exactly ``day`` (the page's comparison day, blank before day
1 has ended), ``format`` and ``request_key``, each once, and no query, plus
at most one ``browser_timezone``. The files hold campaign days and dates, no
exact action times, so the form asks for no time zone (#1161, ui-conventions
"Downloaded files"): the browser's, posted unasked, sets the file's request
time, and a missing or unknown one falls back to the parish's. A day that
is not a whole number from 1 to the current comparison day is refused (400)
with a way back to the page; a reused form is the shared 409.
"""

from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.observability import debug_swallowed
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.dates import parish_zone, unasked_zone
from parishkit.stewardship.web.refusals import with_status_code

from .comparison_exports import create_comparison_export, read_capture
from .comparison_reads import DayOutOfRange
from .comparison_views import DAY
from .export_services import ExportRequestBound
from .export_ui import _redirect
from .export_views import SAFE_FAILURES, _principal
from .facts import FactUnavailable
from .read_admission import admit_report_read

FIELDS = frozenset({"day", "format", "request_key"})


def _error(*, status):
    """No submitted value or internal failure, and a way back to the page."""
    debug_swallowed("comparison download refused")
    response = HttpResponse(
        render_to_string(
            "stewardship/campaign-comparison-error.html", {"status": status}
        ),
        status=status,
        headers={"Cache-Control": "no-store"},
    )
    response.stewardship_safe_error = True
    if status == 503:
        response["Retry-After"] = "5"
    # The refusal log's codes for this page of its own (#1403).
    return with_status_code(response)


def form_day(value):
    """The posted day: None when blank (before day 1 has ended), else 1 or more."""
    if value == "":
        return None
    if not DAY.fullmatch(value):
        raise ValueError("Invalid comparison day.")
    return int(value)


@require_POST
def create(request, campaign_id):
    """Capture the page's comparison on the posted day and queue its file."""
    try:
        service = runtime()
        principal = _principal(request, service.store)
        admit_report_read(campaign_id)
        parameters = request.POST.copy()
        parameters.pop("csrfmiddlewaretoken", None)
        if (
            request.GET
            or set(parameters) - {"browser_timezone"} != FIELDS
            or any(len(parameters.getlist(key)) != 1 for key in parameters)
        ):
            raise ValueError("Invalid comparison download fields.")
        values = {field: parameters[field] for field in FIELDS}
        day = form_day(values["day"])
        request_key = UUID(values["request_key"])
        shown, document = read_capture(
            service.store, principal.identity, campaign_id, day=day
        )
        result = create_comparison_export(
            service.store,
            principal.identity,
            campaign_id=campaign_id,
            day=shown,
            format=values["format"],
            browser_timezone=unasked_zone(
                parameters.get("browser_timezone"), parish_zone()
            ),
            request_key=request_key,
            document=document,
        )
        return _redirect(result.pk)
    except ExportRequestBound:
        return _error(status=409)
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except DayOutOfRange:
        return _error(status=400)
    except (*SAFE_FAILURES, FactUnavailable, StorageInvariantError):
        return _error(status=503)
    except ValueError:
        return _error(status=400)

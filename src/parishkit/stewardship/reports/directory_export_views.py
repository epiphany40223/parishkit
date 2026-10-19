"""Private native export commands over the closed complete-directory capture."""

from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.views.decorators.http import require_POST

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.cryptography import CryptographicError
from parishkit.stewardship.accounts.family_authentication import (
    runtime as family_runtime,
)
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.dates import parish_zone, unasked_zone

from .directories import DirectoryQuery, NoRehearsal
from .directory_exports import create_directory_export
from .directory_views import _error, _principal, mailing_option
from .export_services import ExportRequestBound
from .export_ui import _redirect
from .export_views import SAFE_FAILURES


@require_POST
def create(request, campaign_id):
    """Forms supply filters, never a result document, Family ID or retained input.

    The page's hidden ``mailing`` field chooses the mail-merge export.
    ``browser_timezone`` is the browser's zone, sent unasked for every
    file (#1679); a missing or unknown one is the parish's.
    """
    try:
        service = runtime()
        principal = _principal(request, service.store)
        parameters = request.POST.copy()
        parameters.pop("csrfmiddlewaretoken", None)
        postal = mailing_option(parameters)
        fields = {"format", "request_key"}
        # A Testing page's form names the rehearsal it showed (#933), so the
        # export reads that one or is refused (409), never a later one.
        if parameters.get("mode") == "testing":
            fields.add("rehearsal")
        query_fields = set(DirectoryQuery.__dataclass_fields__) - {"page"}
        if (
            request.GET
            or set(parameters) - {"browser_timezone"} != fields | query_fields
            or any(len(parameters.getlist(key)) != 1 for key in parameters)
        ):
            raise ValueError("Invalid directory export fields.")
        values = {field: parameters.pop(field)[0] for field in fields}
        rehearsal = UUID(values["rehearsal"]) if "rehearsal" in values else None
        zone = parameters.pop("browser_timezone", [None])[0]
        query = DirectoryQuery.parse(parameters)
        # No file asks for a zone, the code list with response columns
        # included (#1679): the browser's, posted silently, else the parish's.
        zone = unasked_zone(zone, parish_zone())
        result = create_directory_export(
            service.store,
            principal.identity,
            campaign_id=campaign_id,
            query=query,
            postal=postal,
            format=values["format"],
            browser_timezone=zone,
            request_key=UUID(values["request_key"]),
            mac=family_runtime().mac,
            rehearsal=rehearsal,
        )
        return _redirect(result.pk)
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError, CryptographicError):
        return _error(campaign_id, status=503)
    except ExportRequestBound:
        return _error(campaign_id, status=409)
    except NoRehearsal:
        return _error(campaign_id, status=409, no_rehearsal=True)
    except ValueError:
        return _error(campaign_id, status=400)

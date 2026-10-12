"""Private native export commands over the closed complete-directory capture."""

from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.urls import reverse
from django.views.decorators.http import require_POST

from parishkit.stewardship.accounts import admin_navigation
from parishkit.stewardship.accounts.admin_editing import step_up_response
from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.cryptography import CryptographicError
from parishkit.stewardship.accounts.family_authentication import (
    runtime as family_runtime,
)
from parishkit.stewardship.accounts.sessions import (
    FreshAuthenticationRequired,
    require_fresh,
)
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.dates import parish_zone, unasked_zone

from .directories import DirectoryQuery, NoRehearsal
from .directory_exports import create_directory_export
from .directory_views import _error, _principal, export_asks_zone, mailing_option
from .export_services import ExportRequestBound
from .export_ui import _redirect
from .export_views import SAFE_FAILURES


@require_POST
def create(request, campaign_id):
    """Forms supply filters, never a result document, Family ID or retained input.

    The page's hidden ``mailing`` field chooses the mail-merge export.
    ``browser_timezone`` is the asked zone when the file has response
    columns (``export_asks_zone``), else the browser's, sent unasked.

    The file holds every listed Family's live code, so creating it needs a
    Google sign-in within the last five minutes (#547). A stale one gets the
    step-up page, which returns to the directory (filters are private POST
    state, so only the mailing-columns preset comes back) and creates nothing.
    """
    try:
        service = runtime()
        principal = _principal(request, service.store)
        parameters = request.POST.copy()
        parameters.pop("csrfmiddlewaretoken", None)
        postal = mailing_option(parameters)
        try:
            require_fresh(request)
        except FreshAuthenticationRequired:
            page = reverse("admin:family_directory")
            return step_up_response(
                page + ("?mailing=yes" if postal else ""),
                admin_navigation.PAGES["family_directory"].label,
            )
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
        # Only a code list with response columns has times in its rows, so
        # only that form asks for a zone, and the service checks the answer;
        # any other file uses the browser's zone, posted silently, else the
        # parish's (#1127).
        if not export_asks_zone(query, postal):
            zone = unasked_zone(zone, parish_zone())
        elif zone is None:
            raise ValueError("Invalid directory export fields.")
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

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

from .directories import DirectoryQuery
from .directory_exports import create_directory_export
from .directory_views import _error, _principal, mailing_option
from .export_services import ExportRequestBound
from .export_ui import _redirect
from .export_views import SAFE_FAILURES


@require_POST
def create(request, campaign_id):
    """Forms supply filters, never a result document, Family ID or retained input.

    The page's hidden ``mailing`` field chooses the mail-merge export.

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
        fields = {"format", "browser_timezone", "request_key"}
        query_fields = set(DirectoryQuery.__dataclass_fields__) - {"page"}
        if (
            request.GET
            or set(parameters) != fields | query_fields
            or any(len(parameters.getlist(key)) != 1 for key in parameters)
        ):
            raise ValueError("Invalid directory export fields.")
        values = {field: parameters.pop(field)[0] for field in fields}
        result = create_directory_export(
            service.store,
            principal.identity,
            campaign_id=campaign_id,
            query=DirectoryQuery.parse(parameters),
            postal=postal,
            format=values["format"],
            browser_timezone=values["browser_timezone"],
            request_key=UUID(values["request_key"]),
            mac=family_runtime().mac,
        )
        return _redirect(result.pk)
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError, CryptographicError):
        return _error(campaign_id, status=503)
    except ExportRequestBound:
        return _error(campaign_id, status=409)
    except ValueError:
        return _error(campaign_id, status=400)

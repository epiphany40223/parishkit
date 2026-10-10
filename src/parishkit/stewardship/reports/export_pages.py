"""The shared export page: one address for every report export (decision 8).

A report export and a latest-data participation export are separate records
with separate views, but both are "an export" to the reader, so both live at
``/admin/reports/exports/<request>/`` with the same actions under it
(admin-portal spec, "Navigation decisions", decision 8). The request id
names exactly one record, so each route hands it to the view that owns it.
Each view still authenticates the reader and checks the record's owner and
campaign itself; the lookup here only chooses which view answers.

The two views word their refusals differently, so whatever this module
refuses before choosing one (a query string, an outage during the lookup)
it answers with one shared page, and an id's kind is never revealed before
the owning view has checked the reader (NAV-12 review).
"""

from django.db import DatabaseError

from . import exact_ui, export_ui
from .exact_models import ExactExportRequest


def _owner(request, request_id, exact_view, export_view):
    """The owning view for ``request_id``, or the shared refusal response.

    Returns ``(view, None)`` or ``(None, response)``. A query string is
    refused for both kinds (neither page nor action takes one), and a failed
    lookup is the same "temporarily unavailable" page for both.
    """
    if request.GET:
        return None, export_ui._error(request, request_id=request_id, status=400)
    try:
        exact = ExactExportRequest.objects.filter(pk=request_id).exists()
    except DatabaseError:
        return None, export_ui._error(request, request_id=request_id, status=503)
    return (exact_view if exact else export_view), None


def detail(request, request_id):
    """The export's status page, from whichever view owns the record."""
    view, refused = _owner(request, request_id, exact_ui.detail, export_ui.detail)
    return refused or view(request, request_id)


def command(request, request_id, *, action):
    """Cancel or retry the export, through the view that owns the record.

    Only a report export has a file, so downloading and regenerating one
    route straight to its view.
    """
    view, refused = _owner(request, request_id, exact_ui.command, export_ui.command)
    return refused or view(request, request_id, action=action)

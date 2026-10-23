"""Send a test to me on the Report emails page and the editor (#2036).

The report-emails spec's "Send a test to me". The Report emails row action
posts the saved report's id here (``report_email_test``); the editor's
button posts the unsaved form to the editor itself with ``test=yes``, which
calls ``test_answer`` once the form's own checks pass (or answers its
refusals with ``form_errors``). Both are page-script posts that answer
JSON, so the result is shown in place, in the status line after the button
(UX conventions, "No layout shift"): ``{"queued": true}`` with whether the
figures were ``updating``; a field refusal is ``400`` with ``{"errors":
[{"field", "message"}]}``; any other refusal is the shared refusal body
(``web.refusals``), which the line shows in words.

Only Administrators send tests. Each records ``report_email_test_sent``
with the request (``report_email_test.request_test``).
"""

from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from parishkit.stewardship.accounts.admin_editing import error_response
from parishkit.stewardship.accounts.authentication import runtime
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.work_locks import read_transaction
from parishkit.stewardship.jobs.delivery_views import _command_scope
from parishkit.stewardship.jobs.delivery_views import _principal as admin_principal
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.refusals import (
    unexpected_fields,
    with_codes,
    with_default_code,
)

from .report_email_changes import refuse_archived
from .report_email_list import report_records
from .report_email_preview_views import ERRORS
from .report_email_test import request_test

FIELDS = frozenset({"report", "csrfmiddlewaretoken"})


def _answer(body, status=200):
    """A JSON answer the page script reads, kept as is by the error pages."""
    response = JsonResponse(body, status=status)
    response.stewardship_safe_error = True
    # The refusal log's code for this page's own refusal (#1444).
    with_default_code(response)
    response["Cache-Control"] = "no-store"
    return response


def form_errors(form):
    """The editor form's refusals as the test's ``400`` answer, by field label."""
    errors = [
        {
            "field": None if name == "__all__" else str(form.fields[name].label),
            "message": str(message),
        }
        for name, messages in form.errors.items()
        for message in messages
    ]
    return with_codes(_answer({"errors": errors}, status=400), "invalid")


def test_answer(request, service, actor, values, *, definition_id):
    """Draw ``values`` now and queue the test, inside the page's command scope.

    ``definition_id`` names the saved report (None for an unsaved New
    form). The compile runs first, outside the work lock; the request is
    then recorded under it, with the session rechecked around it.
    """
    _row, preview, _created = request_test(
        actor.identity,
        values,
        definition_id=definition_id,
        scope=_command_scope(request, service, actor),
    )
    return _answer({"queued": True, "updating": preview.updating})


@require_POST
def report_email_test(request, campaign_id):
    """Send a test to me of a saved report email: the row action (spec).

    The post names the report; one that is not the current campaign's is
    refused as an unknown request, and an archived campaign's is refused.
    """
    try:
        service = runtime()
        actor = admin_principal(request, service.store, activity=True)
        filters(request.GET, allowed=set())
        if request.FILES:
            raise unexpected_fields(request.FILES, allowed=())
        posted = request.POST
        if (
            "report" not in posted
            or set(posted) - FIELDS
            or any(len(values) != 1 for _name, values in posted.lists())
        ):
            raise unexpected_fields(posted, allowed=FIELDS)
        if campaign_id is None:
            raise LookupError("There is no current campaign.")
        report = str(UUID(posted["report"]))
        with read_transaction():
            system = SystemConfiguration.objects.select_related(
                "active_configuration", "current_campaign"
            ).get()
            if system.current_campaign_id != campaign_id:
                raise LookupError("There is no current campaign.")
            refuse_archived(system.current_campaign)
            records = {
                row["id"]: row["values"] for row in report_records(system, campaign_id)
            }
        if report not in records:
            # No sources: the field was allowed; the id is no report's.
            raise unexpected_fields()
        return test_answer(
            request, service, actor, records[report], definition_id=UUID(report)
        )
    except ObjectDoesNotExist:
        return error_response(PermissionError("No configuration."))
    except ERRORS as error:
        return error_response(error)

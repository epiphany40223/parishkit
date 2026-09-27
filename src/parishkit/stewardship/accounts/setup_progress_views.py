"""Exact source-load progress; GET is passive and visible-page POST is bounded."""

from datetime import datetime

from django.http import JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.web.contracts import filters

from .authentication import runtime
from .setup_drafts import view_draft
from .setup_progress import source_progress
from .setup_views import ERRORS, _checked, error_response, page_error
from .setup_wizard import wizard_for

# Plain-language status for each observable load situation. The page renders
# all of them (hidden) so the polling script can switch text without inventing
# its own wording; ``summary`` picks the same key on the server.
SUMMARIES = {
    "queued": _("Waiting for the background loading service to pick up the job."),
    "retry_wait": _(
        "ParishSoft did not answer in time. The load waits briefly and then "
        "retries automatically; nothing needs to be done."
    ),
    "fetching": _(
        "Downloading Families, Members, Ministries and funds from ParishSoft. "
        "The totals are not known until the download finishes, so the count stays "
        "at zero for now. This is normal and usually takes one to three minutes."
    ),
    "staging": _(
        "Saving the downloaded records for this setup. The count shows how many "
        "records have been saved so far."
    ),
    "validating": _("Checking that the saved records are complete and consistent."),
    "working": _("The load is running."),
    "done": _("Parish data is loaded. Continue to the next setup step."),
    "failed": _("The load did not finish."),
}


def summary(progress):
    """Choose the plain-language status key for one progress observation."""
    if progress["setup_state"] == "expired" or progress["task_state"] in {
        "failed",
        "cancelled",
        "abandoned",
    }:
        return "failed"
    if progress["task_state"] == "succeeded" or progress["setup_state"] == "collecting":
        return "done"
    if progress["task_state"] in {"queued", "retry_wait"}:
        return progress["task_state"]
    return progress["phase"] if progress["phase"] in SUMMARIES else "working"


def _seconds_since(now, instant):
    """Whole seconds from an ISO instant to the server's observation time."""
    if not instant:
        return None
    delta = datetime.fromisoformat(now) - datetime.fromisoformat(instant)
    return max(0, int(delta.total_seconds()))


@require_http_methods(["GET", "HEAD", "POST"])
def setup_source_progress(request, task_id):
    """No posted heartbeat, deadline, worker or claimed version is trusted."""
    try:
        query = filters(request.GET, allowed={"format"})
        if query and query != {"format": "json"}:
            raise ValueError("Invalid progress format.")
        if (
            request.FILES
            or set(request.POST) - {"csrfmiddlewaretoken"}
            or any(len(values) != 1 for _, values in request.POST.lists())
        ):
            raise ValueError("Invalid source progress fields.")
        service = runtime()
        progress = source_progress(
            request, service, task_id, renew=request.method == "POST"
        )
        if query:
            response = JsonResponse(progress)
        else:
            response = render(
                request,
                "stewardship/setup-source-progress.html",
                {
                    "progress": progress,
                    "wizard": _wizard(request, service),
                    "status_key": summary(progress),
                    "summaries": SUMMARIES,
                    "elapsed": _seconds_since(
                        progress["server_now"], progress["started_at"]
                    ),
                    "quiet": _seconds_since(
                        progress["server_now"], progress["heartbeat_at"]
                    ),
                },
            )
        return _checked(request, service, response)
    except ERRORS as error:
        if request.GET:
            return error_response(error)
        return page_error(request, error, "source")


def _wizard(request, service):
    """Show the stepper when this sign-in's draft is still readable.

    The progress page must keep rendering for failed or expired loads, whose
    draft may no longer be viewable, so an unavailable draft omits the stepper.
    """
    try:
        return wizard_for(view_draft(request, service), "source")
    except ERRORS:
        return None

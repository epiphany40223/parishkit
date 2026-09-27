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
        "Step 1 of 3: downloading your parish's records from ParishSoft. Each "
        "item below is marked done as it finishes. The ministry rosters take "
        "the longest because ParishSoft answers one request per ministry: "
        "about ten minutes for a few thousand Families and 200 ministries, and "
        "longer for a larger parish."
    ),
    "staging": _(
        "Step 2 of 3: saving the downloaded records for this setup. The bar "
        "shows how many have been saved so far."
    ),
    "validating": _(
        "Step 3 of 3: checking that the saved records are complete and "
        "consistent. This is the last part of the load."
    ),
    "working": _("The load is running."),
    "done": _("Parish data is loaded. Continue to the next setup step."),
    "failed": _("The load did not finish."),
}

# The three parts of a load, in order, as the page names them.
PHASES = (
    ("fetching", _("Download from ParishSoft")),
    ("staging", _("Save the records for this setup")),
    ("validating", _("Check the saved records")),
)
PHASE_STATUS = {
    "done": _("Done"),
    "active": _("In progress"),
    "waiting": _("Not started"),
}

# What each downloaded collection is, in the order the loader reports them.
COLLECTIONS = {
    "families": _("Families"),
    "family_groups": _("Family group names"),
    "members": _("Members"),
    "member_contactinfos": _("Member contact details"),
    "ministry_types": _("List of Ministries"),
    "ministry_roster": _("Ministry rosters (one request per Ministry)"),
    "funds": _("Giving funds"),
}
# Status wording with {placeholders}; the polling script fills in the same
# templates from the page's data attributes, so both stay in one language.
COLLECTION_TEXT = {
    "done": _("{count} loaded"),
    "rosters": _("{finished} of {expected} loaded"),
    "active": _("Downloading…"),
    "waiting": _("Waiting"),
}


def phases(progress, status_key):
    """Name each load phase's state; the polling script mirrors this."""
    order = [key for key, _label in PHASES]
    # Before the download starts (queued, starting) no phase has begun.
    index = order.index(progress["phase"]) if progress["phase"] in order else -1
    rows = []
    for position, (key, label) in enumerate(PHASES):
        if status_key == "done" or position < index:
            state = "done"
        elif position == index and progress["active"]:
            state = "active"
        else:
            state = "waiting"
        rows.append(
            {"key": key, "label": label, "state": state, "status": PHASE_STATUS[state]}
        )
    return rows


def collections(progress):
    """Label each downloaded collection and word its state for the first render.

    Exactly one unfinished collection is "active" while the download runs.
    The polling script mirrors this so the list updates without a reload.
    """
    fetching = progress["phase"] == "fetching" and progress["active"]
    rows, waiting = [], False
    for item in progress["collections"]:
        if item["done"]:
            state = "done"
        elif fetching and not waiting:
            state, waiting = "active", True
        else:
            state = "waiting"
        known = item["key"] == "ministry_roster" and item["expected"] is not None
        if known and state != "waiting":
            text = COLLECTION_TEXT["rosters"].format(
                finished=f"{item['finished']:,}", expected=f"{item['expected']:,}"
            )
        elif state == "done":
            text = COLLECTION_TEXT["done"].format(count=f"{item['count']:,}")
        else:
            text = COLLECTION_TEXT[state]
        rows.append(
            item | {"label": COLLECTIONS[item["key"]], "state": state, "text": text}
        )
    return rows


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
                    "phases": phases(progress, summary(progress)),
                    "phase_status": PHASE_STATUS,
                    "collections": collections(progress),
                    "collection_text": COLLECTION_TEXT,
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

"""Exact source-load progress; GET is passive and visible-page POST is bounded."""

from datetime import datetime, timedelta

from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.jobs.queue_wait import waited_words
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.refusals import unexpected_fields

from . import stages
from .authentication import runtime
from .setup_drafts import view_draft
from .setup_progress import source_progress
from .setup_views import ERRORS, _checked, error_response, page_error
from .setup_wizard import wizard_for

# Plain-language status for each observable load situation; ``summary``
# picks the key for one observation.
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
    "failed": _(
        "The load did not finish. Check the ParishSoft connection, then load "
        "the parish data again."
    ),
    "ended": _("The load did not finish."),
    "voided": _(
        "This load is no longer used. Load the parish data again from the "
        "Load parish data step."
    ),
}

# The status keys the page can show while it still updates itself.
RUNNING_SUMMARIES = (
    "queued",
    "retry_wait",
    "fetching",
    "staging",
    "validating",
    "working",
)

# The three parts of a load, in order, as the page names them.
PHASES = (
    ("fetching", _("Download from ParishSoft")),
    ("staging", _("Save the records for this setup")),
    ("validating", _("Check the saved records")),
)

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
# Status wording with {placeholders}, filled in by ``collections``.
COLLECTION_TEXT = {
    "done": _("{count} loaded"),
    "rosters": _("{finished} of {expected} loaded"),
    "active": _("Downloading…"),
    "waiting": _("Waiting"),
}


def collection_reserve(key):
    """Every wording a collection's row can take while the load runs (#1903).

    The row draws them invisibly in its status cell, so a row never wraps
    to another line part-way through the load (a narrow screen can fit
    "Waiting" beside a label but not "1,234 loaded"). The counts are the
    size of a large parish's; a larger one grows the row once.
    """
    texts = [
        COLLECTION_TEXT["waiting"],
        COLLECTION_TEXT["active"],
        COLLECTION_TEXT["done"].format(count="99,999"),
    ]
    if key == "ministry_roster":
        texts.append(COLLECTION_TEXT["rosters"].format(finished="999", expected="999"))
    return texts


def phases(progress, status_key):
    """Name each load phase's state for the live status region (stage boxes)."""
    order = [key for key, _label in PHASES]
    # Before the download starts (queued, starting) no phase has begun.
    index = order.index(progress["phase"]) if progress["phase"] in order else -1
    if status_key == "done":
        index = len(order)
    return stages.rows(PHASES, index, "active" if progress["active"] else "waiting")


def collections(progress):
    """Label each downloaded collection and word its state for the first render.

    Exactly one unfinished collection is "active" while the download runs.
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
            item
            | {
                "label": COLLECTIONS[item["key"]],
                "state": state,
                "text": text,
                "reserve": collection_reserve(item["key"]),
            }
        )
    return rows


def summary(progress):
    """Choose the plain-language status key for one progress observation.

    Since #1353 a load that fails for good returns setup to collecting, so
    "failed" offers the way back (fix the ParishSoft connection, Load again).
    "ended" is a load that ended with setup itself (expired), or one that
    stopped while setup still says loading: only a new setup helps. "voided"
    is a load that is no longer the attempt's valid one: discarded by a
    changed connection or Load again (no longer bound), or succeeded but
    stale. ``bound`` and ``loaded`` default to true for observations that
    predate them.
    """
    if progress["setup_state"] == "expired":
        return "ended"
    if not progress.get("bound", True):
        return "voided"
    if progress["task_state"] in {"failed", "cancelled", "abandoned"}:
        return "failed" if progress["setup_state"] == "collecting" else "ended"
    if progress["task_state"] == "succeeded":
        return "done" if progress.get("loaded", True) else "voided"
    if progress["setup_state"] == "collecting":
        return "done"
    if progress["task_state"] in {"queued", "retry_wait"}:
        return progress["task_state"]
    return progress["phase"] if progress["phase"] in SUMMARIES else "working"


def _ago(now, instant):
    """How long before the observation ``instant`` was, in waited_words' words.

    live-status-v1.js ticks the same words forward between polls.
    """
    if not instant:
        return None
    delta = datetime.fromisoformat(now) - datetime.fromisoformat(instant)
    return waited_words(max(delta, timedelta()))


def _status_context(progress):
    """The live status region's context for one progress observation."""
    status_key = summary(progress)
    return {
        "progress": progress,
        "status_key": status_key,
        "summary_text": SUMMARIES[status_key],
        # Every other wording the page can show while it still updates,
        # drawn invisibly in the summary's grid cell so the stage boxes below
        # never move when the wording changes (#1903). The settled wordings
        # (done, failed, ended, voided) are not reserved: the page stops
        # updating on them and a failure comes with its own notice.
        "summary_reserve": [
            SUMMARIES[key] for key in RUNNING_SUMMARIES if key != status_key
        ],
        # The saved-record bar shows only while records are saved and
        # checked; before then it keeps its space, invisible (#1903).
        "records_shown": progress["phase"] in {"staging", "validating"},
        "phases": phases(progress, status_key),
        "collections": collections(progress),
        "started_ago": _ago(progress["server_now"], progress["started_at"]),
        "quiet_ago": _ago(progress["server_now"], progress["heartbeat_at"]),
    }


@require_http_methods(["GET", "HEAD", "POST"])
def setup_source_progress(request, task_id):
    """No posted heartbeat, deadline, worker or claimed version is trusted.

    A GET is passive: the page, its JSON, or (``?format=status``) the live
    status region the open page polls (#1354). Only a POST, the visible
    page's renewal (answered as JSON), may renew the setup's idle deadline.
    Every form is admitted by the same exact-task ``source_progress`` check.
    """
    try:
        query = filters(request.GET, allowed={"format"})
        shape = query.get("format")
        if shape not in {None, "json", "status"} or (
            shape == "status" and request.method == "POST"
        ):
            raise ValueError("Invalid progress format.")
        if (
            request.FILES
            or set(request.POST) - {"csrfmiddlewaretoken"}
            or any(len(values) != 1 for _, values in request.POST.lists())
        ):
            raise unexpected_fields(
                request.POST, request.FILES, allowed={"csrfmiddlewaretoken"}
            )
        service = runtime()
        progress = source_progress(
            request, service, task_id, renew=request.method == "POST"
        )
        wizard = None if shape == "json" else _wizard(request, service)
        if shape == "json":
            response = JsonResponse(progress)
        elif shape == "status":
            response = render(
                request,
                "stewardship/setup-source-progress-status.html",
                _status_context(progress) | {"wizard": wizard},
            )
        else:
            path = reverse("admin:setup_source_progress", args=[task_id])
            response = render(
                request,
                "stewardship/setup-source-progress.html",
                _status_context(progress)
                | {"wizard": wizard, "status_url": path + "?format=status"},
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

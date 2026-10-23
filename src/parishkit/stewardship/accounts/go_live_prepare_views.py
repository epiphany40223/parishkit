"""The Go live page's Prepare step (#1585 slice 3): passive GET, POSTs in place.

The step's state comes from ``go_live_prepare.read``; this module draws it
and runs its controls. Every POST answers a redirect (to this step, or to
Check once Stop go-live applied) or a fragment of the Stop go-live dialog,
never a page, so a browser reload never sends a form again.
"""

from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.campaigns.activation_models import (
    ProductionTokenCancellation,
    ProductionTokenPreparation,
)
from parishkit.stewardship.campaigns.go_live_sequencing import (
    begin_attempt,
    live_preparations,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.refusals import refused_step, unexpected_fields

from . import activation_progress, go_live_check, go_live_prepare, go_live_progress
from .admin_editing import principal
from .authentication import runtime
from .integration_views import ERRORS, _checked
from .setup_views import _closed, error_response

# Every Prepare action, by its form's ``action``, with its other fields.
_FIELDS = {
    "retry": {"control"},
    "again": {"control"},
    "review_stop": set(),
    "stop": {"control"},
}
# How often the step re-reads itself while it waits for the Administrator
# (a failure, Refresh and prepare again, ready): only another tab or
# Administrator changes it then.
_IDLE_INTERVAL_MS = 30000
# The longest the shared watcher follows a page (live-status-v1.js).
_WATCH_MS = 3 * 60 * 60 * 1000


@require_http_methods(["GET", "HEAD", "POST"])
def prepare(request, campaign_id):
    """The Go live page's Prepare step (#1585): passive GET, every POST in place.

    GET draws the step from durable state, so a reload returns to it with
    its current status; it changes nothing and does not extend the
    sign-in's idle time. Once the links are ready, a browser's own
    navigation to this address (a reload, the step bar, Check's Continue)
    opens Confirm Production, since the step has completed (wizard rule 8);
    the page's own in-place reads (``Sec-Fetch-Mode`` other than
    "navigate") are drawn, so a page watching the step shows Ready to
    confirm and its Continue. Each POST:

    - ``retry`` retries the failed task its control names (cleanup, a
      preparation, or the discard of earlier links) and changes no data;
    - ``again`` (Refresh and prepare again) begins the next attempt;
    - ``review_stop`` answers the Stop go-live dialog's review;
    - ``stop`` (the dialog's Apply) discards the Family links and stops
      Testing cleanup through their owners, then opens Check.
    """
    try:
        filters(request.GET, allowed=set())
        service = runtime()
        if request.method != "POST":
            _closed(request, set())
            shown = go_live_prepare.read(request, service, campaign_id)
            if shown.state == "ready" and _navigation(request):
                return _checked(
                    request,
                    service,
                    HttpResponseRedirect(shown.confirm_url, status=303),
                )
            response = render(
                request, "stewardship/go-live-prepare.html", _context(shown)
            )
            return _checked(request, service, response)
        action = request.POST.get("action")
        if action not in _FIELDS:
            # No sources: an unknown action value, not an unexpected field.
            raise unexpected_fields()
        _closed(request, {"action", *_FIELDS[action]})
        if action == "review_stop":
            return _stop_answer(request, service, campaign_id)
        # A post without its control is a forged one: _closed rejects extra
        # fields, not missing ones, so the empty token is refused as a bad
        # signature (a 400), as go_live_views does, never a KeyError (500).
        token = request.POST.get("control", "")
        target = _act(request, service, campaign_id, action, token)
        if target is None:
            # Stop go-live found the step moved under the work lock: said
            # inside its dialog, never a silent landing on the step.
            return _stop_answer(request, service, campaign_id, changed=True)
        return _checked(request, service, HttpResponseRedirect(target, status=303))
    except ObjectDoesNotExist:
        return error_response(LookupError("Go-live preparation is unavailable."))
    except ERRORS as error:
        return error_response(error)


def _navigation(request):
    """Whether this GET is the browser's own page load, not a page's fetch.

    The Fetch Metadata header every current browser sends; without it (an
    older browser, a test client) the step is drawn, which is always safe.
    """
    return request.headers.get("Sec-Fetch-Mode") == "navigate"


def _context(shown):
    """The Prepare step's template context."""
    waiting = shown.state in {"failed", "again", "ready", "closed"}
    return {
        "stage": shown,
        "campaign": shown.request.campaign if shown.request else None,
        "wizard": go_live_check.bar(
            go_live_check.bar_state(shown),
            current="prepare",
            prepared=shown.state == "ready",
        ),
        # Work that moves by itself is followed closely; a step that waits
        # for the Administrator only re-reads now and then, for another tab.
        "live_pending": shown.started,
        "live_interval": _IDLE_INTERVAL_MS if waiting else None,
        "live_limit": _WATCH_MS,
        # "ParishSoft data changed, so new links are being prepared (2 of
        # 4)": read now, so the words follow go_live_sequencing's cap.
        "max_preparations": go_live_prepare.MAX_PREPARATIONS,
    }


def _act(request, service, campaign_id, action, token):
    """Run one Prepare control; return the step address to open next.

    The control must belong to this Administrator (else a 403) and name
    exactly what the step shows now; otherwise it was already acted on (a
    resend after a lost answer) or the step moved on, and nothing is done:
    the step then shows how it stands now. The step is read and the control
    acted on in one work transaction, so the state the control was checked
    against is the state it acts on: no other writer can move it between
    the two. The owners still recheck everything under their own locks.

    Returns None when Stop go-live found the state moved under an owner's
    lock (``StaleRecordError``): nothing was stopped (the transaction rolled
    back), and the caller says so inside the dialog.
    """
    value = go_live_prepare.unsign(token)
    here = reverse("admin:go_live_prepare")
    away = here if action != "stop" else reverse("admin:go_live")
    try:
        with work_transaction():
            actor = principal(request, service)
            if value["actor"] != str(actor.identity):
                raise PermissionError(
                    "This go-live control belongs to another sign-in."
                )
            shown = go_live_prepare.read(request, service, campaign_id)
            if not shown.started or str(shown.request.pk) != value["request"]:
                return away
            if shown.controls.get(action) != token:
                return away
            row = shown.request
            if action == "retry":
                _retry(request, service, campaign_id, actor, row, value)
            elif action == "again":
                _again(request, service, actor, row, int(value["number"]))
            else:
                _stop(request, service, campaign_id, actor, row)
                return away
    except StaleRecordError:
        # The owner found the state moved under its own lock: the step shows
        # how it stands now; a stop says so in its dialog.
        return None if action == "stop" else here
    return here


def _retry(request, service, campaign_id, actor, row, value):
    """Retry the failed task ``value`` names through its owner's own control.

    The owner's control is minted now, so no page age reaches the
    Administrator: cleanup's retry (``go_live_progress``), a preparation's
    or its discard's retry (``activation_progress``). Called inside
    ``_act``'s transaction, after the control matched the step read there;
    the task version the control names is checked again here so the
    owner's control is minted for exactly that version. Returns whether a
    retry was asked for (``pk-admin go-live retry`` reports a no-op as
    nothing to retry; the page ignores it).
    """
    if value["action"] == "retry_task":
        controls = go_live_progress.progress(request, service, campaign_id, row.pk)
        token = controls["controls"].get("retry")
        if not token:
            return False
        go_live_progress.control(request, service, campaign_id, row.pk, token=token)
        return True
    with work_transaction():
        preparation = ProductionTokenPreparation.objects.get(
            pk=value["preparation"], transition=row
        )
        task = TaskRun.objects.get(pk=value["task"])
        if str(task.version) != value["version"]:
            raise StaleRecordError("The failed go-live task changed.")
        token = activation_progress._sign(
            actor, row, value["action"], preparation=preparation, task=task
        )
    activation_progress.control(request, service, campaign_id, row.pk, token=token)
    return True


def _again(request, service, actor, row, number):
    """Refresh and prepare again: begin attempt ``number`` as this Administrator.

    Its refresh is an ordinary manual refresh, admitted for the current
    Administrator under the refresh's own lock; the producer then prepares
    new links once it promotes, discarding the old ones first. A second
    click after the first committed is a replay and begins nothing more.
    """

    def authorize(scope):
        """The Administrator who clicked, still signed in, rechecked under the lock."""
        return principal(request, service, read_only=True).identity == actor.identity

    with work_transaction():
        begin_attempt(
            row,
            number=number,
            actor_id=actor.identity,
            correlation_id=current_correlation(),
            authorize=authorize,
        )


def _stop(request, service, campaign_id, actor, row):
    """Stop go-live: discard the Family links, then stop Testing cleanup.

    One transaction through the existing owners (the links page's discard
    and the cleanup page's cancellation, each control minted now), so a
    stop is all or nothing. Testing data already deleted is not restored.
    Cancelling releases the go-live gate and, with it, the refresh hold
    once cleanup has stopped.
    """
    with work_transaction():
        for preparation in live_preparations(row):
            if ProductionTokenCancellation.objects.filter(
                preparation=preparation
            ).exists():
                continue
            activation_progress.control(
                request,
                service,
                campaign_id,
                row.pk,
                token=activation_progress._sign(
                    actor, row, "cancel", preparation=preparation
                ),
            )
        page = go_live_progress.progress(request, service, campaign_id, row.pk)
        token = page["controls"].get("cancel")
        if token:
            go_live_progress.control(request, service, campaign_id, row.pk, token=token)


def _stop_answer(request, service, campaign_id, *, changed=False):
    """The Stop go-live dialog's review: the sentence for the state, or a refusal.

    A go-live no longer in progress is refused inside the dialog, saying
    who moved it on: another Administrator stopped it, or confirmed
    Production (a 409 with its closed code). ``changed`` is an Apply whose
    stop found the state moved under an owner's lock and stopped nothing
    (``_act``); it is refused in the dialog too, saying to try again.
    """
    principal(request, service)
    shown = go_live_prepare.read(request, service, campaign_id)
    refused = None
    if not shown.started:
        refused = "confirmed" if shown.state == "confirmed" else "stopped"
    elif changed:
        refused = "changed"
    response = render(
        request,
        "stewardship/go-live-stop-review.html",
        {"stage": shown, "refused": refused, "check_url": reverse("admin:go_live")},
        status=409 if refused else 200,
    )
    if refused:
        # The answer carries its own explanation; keep it as rendered.
        response.stewardship_safe_error = True
        refused_step(response, refused)
    return _checked(request, service, response)

"""The Go live page's Prepare step: its state, stage boxes and controls (#1585).

Between Start go-live and Confirm Production the system does three things
by itself (background-processing spec, "Go-live sequencing"): Testing
cleanup deletes the Testing data, the attempt's full ParishSoft refresh
runs, and the scheduler's go-live producer prepares the Family links once
both are done (preparing them again when ParishSoft data changes). The
Prepare step (admin-portal spec, "Go live page") shows them as three stage
boxes and is drawn from durable state on every read, so a browser reload
returns to the same step with its current status. Its state is one of:

- ``running``: something is still moving by itself; there is nothing to do.
- ``failed``: a stage failed after its automatic retries. Retry retries the
  same task (cleanup, a preparation or the discard of earlier links) and
  changes no data.
- ``again``: the system stopped and needs the Administrator to begin the
  next attempt with Refresh and prepare again: the refresh hold ended, the
  links went stale too often ("ParishSoft data keeps changing"), the
  attempt's refresh failed (a refresh has no task retry: a new one is the
  retry), or the request has no attempt at all (started before #1585 slice
  3).
- ``ready``: all three stages are done; Confirm Production can open.
- ``closed``: the campaign ended while waiting; only Stop go-live is left.
- ``stopped``, ``not_started`` and ``confirmed``: Prepare is not the step
  in progress; the page says so and leads to Check or Production
  activation.

Presentation only: it grants nothing. Every control is signed without a
time (so every poll of the same state draws the same markup and no page
age ever refuses it) and names exactly what it acts on; the POST re-reads
the state, acts only when the control still names it, and leaves every
check to the owners, which recheck authority under their own locks.
"""

from dataclasses import dataclass, field

from django.core import signing
from django.core.exceptions import ObjectDoesNotExist
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.campaigns.activation_inputs import TokenPreparationInputs
from parishkit.stewardship.campaigns.activation_models import (
    ProductionTokenCancellation,
    ProductionTokenPreparation,
)
from parishkit.stewardship.campaigns.activation_tokens import (
    current_inputs,
    prepared_generation,
)
from parishkit.stewardship.campaigns.go_live_sequencing import (
    MAX_PREPARATIONS,
    OPEN_STATES,
    attempts,
    preparations,
    refresh_command_id,
)
from parishkit.stewardship.campaigns.production_models import (
    ProductionCleanupCancellation,
    ProductionTransitionRequest,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.models import NONTERMINAL_STATES, TaskRun
from parishkit.stewardship.source.refresh_models import SourceRefreshCommand
from parishkit.stewardship.storage import StaleRecordError

from . import stages
from .admin_editing import editable_configuration, principal
from .sessions import database_now

# Prepare's controls (``control``), signed without a time.
SALT = "stewardship-go-live-prepare-control-v1"

# The three stage boxes, left to right.
STAGES = (
    ("cleanup", _("Delete Testing data")),
    ("refresh", _("Refresh ParishSoft data")),
    ("links", _("Prepare Family links")),
)


@dataclass(frozen=True)
class Prepare:
    """The Prepare step as drawn now (see the module docstring for ``state``).

    ``reason`` says which failure or which ``again`` cause it is; ``boxes``
    are the stage boxes (``stages.rows``' shape); ``done`` and ``total`` the
    detail line's counts and ``preparation`` the number of the attempt's
    preparation it names (of ``MAX_PREPARATIONS``). ``confirm_url`` opens
    Confirm Production once the links are ready. ``controls`` holds each
    offered control by action. ``stop_kind`` words the Stop go-live
    dialog: "cleanup" while Testing data is still being deleted, "links"
    once Family links are being prepared or are prepared, else None.
    ``stopped_by_other`` is set when another Administrator stopped it.
    """

    state: str
    request: ProductionTransitionRequest | None = None
    reason: str | None = None
    boxes: tuple = ()
    done: int = 0
    total: int = 0
    preparation: int = 0
    confirm_url: str | None = None
    controls: dict = field(default_factory=dict)
    stop_kind: str | None = None
    stopped_by_other: bool = False

    @property
    def started(self):
        """Whether a go-live is in progress (Prepare is the step shown)."""
        return self.state not in {"stopped", "not_started", "confirmed"}


def sign(actor, row, action, **values):
    """A Prepare control: ``action`` on request ``row``, for this Administrator.

    Signed without a time: the POST re-reads the state and acts only while
    the control still names what the step shows (``control_for``).
    """
    return signing.Signer(salt=SALT).sign_object(
        {"actor": str(actor.identity), "request": str(row.pk), "action": action}
        | {key: str(value) for key, value in values.items()}
    )


def unsign(token):
    """A Prepare control's contents; a forged one is ``BadSignature`` (a 400)."""
    if type(token) is not str or len(token) > 4096:
        raise signing.BadSignature("Invalid go-live control.")
    value = signing.Signer(salt=SALT).unsign_object(token)
    if type(value) is not dict or not {"actor", "request", "action"} <= set(value):
        raise signing.BadSignature("Invalid go-live control.")
    return value


def _latest(root_id):
    """The newest execution of a task chain (a retry is a child run), or None."""
    if root_id is None:
        return None
    return TaskRun.objects.filter(root_id=root_id).order_by("-retry_sequence").first()


def _refresh_box(request_id, attempt):
    """The Refresh ParishSoft data box's state for ``attempt`` (or None).

    Done once a full refresh that started after the attempt began has
    promoted (go_live_sequencing's rule); failed when the attempt's own
    refresh failed with nothing promoted; else in progress. A refresh that
    was coalesced into, or superseded by, another stays in progress: the
    other one promotes, or the hold reaches its cap.
    """
    if attempt is None:
        return "waiting"
    if attempt.promoted_at is not None:
        return "done"
    command = (
        SourceRefreshCommand.objects.select_related("request")
        .filter(pk=refresh_command_id(request_id, attempt.number))
        .first()
    )
    task = _latest(command.request.task_root_id) if command else None
    return "failed" if task is not None and task.state == "failed" else "active"


def _links(row, ready_inputs):
    """How the request's Family links stand: a dict the boxes and detail read.

    ``state`` is "done" (the newest preparation is ready on current data),
    "failed" (its task failed on current data, or discarding earlier links
    failed, with ``task`` and ``kind`` naming the retry), "stale" (the
    newest is discarded or no longer current), "active" (being prepared or
    discarded) or "none" (none yet). ``preparation`` is the newest row.
    Preparations of one request are few, so checking each is cheap.
    """
    rows = list(
        ProductionTokenPreparation.objects.filter(transition=row).order_by(
            "created_at", "id"
        )
    )
    for preparation in rows:
        cancellation = ProductionTokenCancellation.objects.filter(
            preparation=preparation
        ).first()
        task = _latest(cancellation.task_id) if cancellation else None
        if task is not None and task.state == "failed":
            return {
                "state": "failed",
                "kind": "retry_cleanup",
                "preparation": preparation,
                "task": task,
            }
    if not rows:
        return {"state": "none", "preparation": None, "task": None}
    newest = rows[-1]
    task = _latest(newest.task_id)
    discarded = ProductionTokenCancellation.objects.filter(preparation=newest).exists()
    current = (
        ready_inputs is not None
        and not discarded
        and TokenPreparationInputs.retained(newest) == ready_inputs
    )
    result = {"preparation": newest, "task": task}
    if not current:
        return result | {"state": "stale"}
    if task.state == "failed":
        return result | {"state": "failed", "kind": "retry"}
    generation = prepared_generation(newest)
    if task.state == "succeeded" and generation and generation.state == "ready":
        return result | {"state": "done"}
    return result | {"state": "active"}


def read(request, service, campaign_id):
    """The Prepare step of the current Testing draft, read now.

    Under the common work lock with a passive principal, so a page read
    never extends the sign-in's idle time. The campaign's latest go-live
    request decides which step is in progress.
    """
    with work_transaction():
        actor = principal(request, service, passive=True)
        configuration = editable_configuration(service)
        if campaign_id is None or configuration.current_campaign_id != campaign_id:
            # No current campaign: refused as an unknown campaign is.
            raise ObjectDoesNotExist("Go-live preparation needs the current campaign.")
        row = (
            ProductionTransitionRequest.objects.select_related("campaign")
            .filter(campaign_id=campaign_id)
            .order_by("-created_at", "-id")
            .first()
        )
        if row is None:
            return Prepare("not_started")
        if row.state == "activated":
            return Prepare("confirmed", row)
        stop = ProductionCleanupCancellation.objects.filter(request=row).first()
        if stop is not None or row.state not in OPEN_STATES:
            return Prepare(
                "stopped",
                row,
                stopped_by_other=stop is not None and stop.actor_id != actor.identity,
            )
        return _started(actor, row)


def _started(actor, row):
    """The Prepare step of an open go-live request ``row`` (see ``read``)."""
    instant = database_now()
    found = attempts(row)
    attempt = found[-1] if found else None
    cleanup = {
        "cleanup_complete": "done",
        "cleanup_failed": "failed",
    }.get(row.state, "active")
    refresh = _refresh_box(row.pk, attempt)
    ready_inputs = None
    if cleanup == "done":
        try:
            ready_inputs = current_inputs(row)
        except StaleRecordError:
            ready_inputs = None
    links = _links(row, ready_inputs)
    made = len(preparations(row, attempt)) if attempt else 0
    # The boxes: each stage's state, the links only once the two before it
    # are done (until then they wait).
    states = {"cleanup": cleanup, "refresh": refresh, "links": "waiting"}
    if links["state"] == "failed":
        states["links"] = "failed"
    elif cleanup == "done" and refresh == "done":
        states["links"] = "done" if links["state"] == "done" else "active"
    boxes = tuple(
        {
            "key": key,
            "label": label,
            "state": states[key],
            "status": stages.STATUS[states[key]],
        }
        for key, label in STAGES
    )
    common = {
        "request": row,
        "boxes": boxes,
        "stop_kind": "cleanup"
        if cleanup != "done"
        else ("links" if links["preparation"] is not None else None),
        "controls": {"stop": sign(actor, row, "stop")},
    }
    again = sign(actor, row, "again", number=len(found) + 1)
    if instant >= row.campaign.active_configuration.ends_at:
        return Prepare("closed", **common)
    if cleanup == "failed":
        task = _latest(row.task_id)
        common["controls"]["retry"] = sign(
            actor, row, "retry_task", task=task.pk, version=task.version
        )
        return Prepare("failed", reason="cleanup", **common)
    reason = None
    if attempt is None:
        reason = "no_attempt"
    elif refresh == "failed":
        reason = "refresh_failed"
    elif instant >= attempt.hold_end:
        reason = "hold_ended"
    elif links["state"] in {"stale", "none"} and made >= MAX_PREPARATIONS:
        reason = "exhausted"
    if reason is not None:
        common["controls"]["again"] = again
        return Prepare("again", reason=reason, **common)
    if links["state"] == "failed":
        task = links["task"]
        common["controls"]["retry"] = sign(
            actor,
            row,
            links["kind"],
            preparation=links["preparation"].pk,
            task=task.pk,
            version=task.version,
        )
        return Prepare("failed", reason=links["kind"], **common)
    if states["links"] == "done":
        return Prepare(
            "ready",
            confirm_url=reverse(
                "admin:production_confirmation",
                args=[row.pk, links["preparation"].pk],
            ),
            **common,
        )
    if cleanup != "done":
        return Prepare(
            "running",
            reason="cleanup",
            done=row.processed_count,
            total=row.inventory_total,
            **common,
        )
    if refresh != "done":
        return Prepare("running", reason="refresh", **common)
    task = links["task"]
    if links["state"] == "active" and task.state in NONTERMINAL_STATES:
        return Prepare(
            "running",
            reason="preparing" if made <= 1 else "repreparing",
            done=task.progress_current,
            total=links["preparation"].eligible_count,
            preparation=made,
            **common,
        )
    # Waiting for the producer: the first preparation, or the next one
    # once the stale links are discarded.
    return Prepare(
        "running",
        reason="waiting" if made == 0 else "repreparing",
        preparation=min(made + 1, MAX_PREPARATIONS),
        **common,
    )

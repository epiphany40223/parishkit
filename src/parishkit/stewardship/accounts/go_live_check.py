"""The Go live page's Check step: its state, step bar and kept check (#1585).

The Check step (admin-portal spec, "Go live page") is drawn from durable
state on every read, so a browser reload returns to the same step with its
current status. Its state is one of:

- ``started``: the campaign's latest go-live request still owns the gate
  and nobody asked to stop it; the step says when and by whom it started
  and its one primary opens the step in progress, Prepare (which opens
  Confirm Production itself once the Family links are ready).
- ``stopping``: a stop was asked and is still running, either Testing data
  deletion stopping at its next safe point (``stop_kind`` "cleanup") or
  the stopped request's prepared Family links being discarded ("links").
- ``stop_failed``: discarding those links failed after its automatic
  retries; the step says when its last attempt failed, and the primary
  retries the same disposal and changes nothing else.
- ``closed``, ``not_ready``, ``unchecked`` and ``passed``: no go-live is
  in progress, and the step shows the readiness checks; ``passed`` needs a
  passing public web address check kept in the signed-in session for the
  exact inputs digest the page shows now (``kept_check``).

Presentation only: it grants nothing. Every action re-checks its own
authority and inputs on the server.
"""

from dataclasses import dataclass
from datetime import datetime

from django.core import signing
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.activation_models import (
    ProductionTokenCancellation,
    ProductionTokenPreparation,
)
from parishkit.stewardship.campaigns.go_live_sequencing import OPEN_STATES
from parishkit.stewardship.campaigns.production_models import (
    ProductionCleanupCancellation,
    ProductionTransitionRequest,
)
from parishkit.stewardship.campaigns.withdrawal_models import ProductionWithdrawal
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.models import NONTERMINAL_STATES, TaskRun

from .actor_names import actor_names
from .admin_editing import editable_configuration, principal
from .setup_wizard import Step, Wizard

# The signed-in session's last public web address check (admin-portal spec,
# "Go live page", Updates and reload): what it checked, never a permit.
CHECK_KEY = "pk_admin_go_live_check"
# Retry stopping go-live's control (``retry_control``).
RETRY_SALT = "stewardship-go-live-check-retry-stop-v1"

# The Go live page's four steps, by key, in order.
STEPS = (
    ("check", _("Check")),
    ("prepare", _("Prepare")),
    ("confirm", _("Confirm Production")),
    ("activate", _("Activation")),
)


@dataclass(frozen=True)
class Progress:
    """The go-live in progress or being stopped, as the Check step shows it.

    ``request`` is the campaign's latest go-live request; ``started_by``
    the Administrator who started it (an ``actor_names.Actor``, #2259);
    ``stop_kind`` what a stop is still doing ("cleanup" or "links");
    ``done``/``total`` its progress; ``retry`` the signed Retry stopping
    go-live control, and ``failed`` the failed disposal it retries as
    ``(preparation, task)``. ``stopped_by_other`` is set when another
    Administrator than the viewer stopped it, for the line that says so
    (#1585). ``cancelled_at`` is when Cancel go-live returned the campaign
    to a Testing draft after Production was confirmed, for Check's
    "Go-live cancelled at" line (#1585 slice 5).
    """

    state: str
    request: ProductionTransitionRequest | None = None
    started_by: object | None = None
    stop_kind: str | None = None
    done: int = 0
    total: int = 0
    retry: str | None = None
    failed: tuple | None = None
    stopped_at: datetime | None = None
    stopped_by_other: bool = False
    cancelled_at: datetime | None = None

    @property
    def in_progress_url(self):
        """The step in progress: always Prepare, which leads on by itself."""
        if self.request is None:
            return None
        return reverse("admin:go_live_prepare")


def _latest_task(root_id):
    """The newest execution of a task chain (a retry is a child run)."""
    return TaskRun.objects.filter(root_id=root_id).order_by("-retry_sequence").first()


def retry_control(actor, row, preparation, task):
    """Retry stopping go-live's control for one failed disposal.

    Signed without a time, so every read of the same failure draws the same
    text: the step's live region then stays unchanged between polls and
    its watcher stops at its limit (a timestamped control would make each
    poll look new). It binds the failure's exact task version; the POST
    mints the links page's own control then (``go_live_views.check``), so
    no page-age limit reaches the Administrator either.
    """
    return signing.Signer(salt=RETRY_SALT).sign_object(
        {
            "actor": str(actor.identity),
            "request": str(row.pk),
            "preparation": str(preparation.pk),
            "task": str(task.pk),
            "version": task.version,
        }
    )


def _links_stop(actor, row):
    """How the stopped request's Family links stand: (state, done, total, failed).

    ``state`` is "stopping" while a discard (or a stale preparation's own
    scrub) still runs, "stop_failed" when a discard failed after its
    retries (with ``failed``, its ``(preparation, task)``), else None.
    Preparations of one request are few, so checking each is cheap.
    """
    failed, running = None, None
    for preparation in ProductionTokenPreparation.objects.filter(transition=row):
        cancellation = ProductionTokenCancellation.objects.filter(
            preparation=preparation
        ).first()
        task = _latest_task(
            cancellation.task_id if cancellation else preparation.task_id
        )
        if task is None:
            continue
        if cancellation is not None and task.state == "failed":
            failed = (preparation, task)
        elif task.state in NONTERMINAL_STATES:
            running = task
    if failed is not None:
        task = failed[1]
        return "stop_failed", task.progress_current, task.progress_total, failed
    if running is not None:
        return "stopping", running.progress_current, running.progress_total, None
    return None, 0, 0, None


def progress(request, service, campaign_id):
    """The go-live state of the current Testing draft, or ``Progress("idle")``.

    Read under the common work lock with a passive principal, so a page read
    never extends the sign-in's idle time. A stopped request whose stop has
    finished is "idle" with ``stopped_at``, for the "Go-live stopped at"
    line above the readiness checks; a confirmed one that Cancel go-live
    withdrew is "idle" with ``cancelled_at``, for "Go-live cancelled at".
    """
    with work_transaction():
        actor = principal(request, service, passive=True)
        editable_configuration(service)
        row = (
            ProductionTransitionRequest.objects.filter(campaign_id=campaign_id)
            .order_by("-created_at", "-id")
            .first()
        )
        if row is None:
            return Progress("idle")
        if row.state == "activated":
            # A go-live that was cancelled after Production was confirmed
            # (Check is open only while the campaign is a draft again):
            # Check starts again from readiness and says when.
            withdrawal = ProductionWithdrawal.objects.filter(
                confirmation__request=row
            ).first()
            return Progress(
                "idle", cancelled_at=withdrawal.created_at if withdrawal else None
            )
        stop = ProductionCleanupCancellation.objects.filter(request=row).first()
        stopping = stop is not None
        # The line "Another Administrator stopped go-live." (#1585).
        other = stopping and stop.actor_id != actor.identity
        if row.state in OPEN_STATES and not stopping:
            # By name, linked to the user's Timeline (#2259).
            who = actor_names([row.initiated_by_id]).get(str(row.initiated_by_id))
            return Progress("started", row, started_by=who)
        if row.state != "cancelled":
            return Progress(
                "stopping",
                row,
                stop_kind="cleanup",
                done=row.processed_count,
                total=row.inventory_total,
                stopped_by_other=other,
            )
        state, done, total, failed = _links_stop(actor, row)
        if state is not None:
            return Progress(
                state,
                row,
                stop_kind="links",
                done=done,
                total=total,
                retry=retry_control(actor, row, *failed) if failed else None,
                failed=failed,
                stopped_by_other=other,
            )
        return Progress("idle", row, stopped_at=row.updated_at, stopped_by_other=other)


def remember_check(request, campaign_id, actor_id, inputs, verified, origin, profile):
    """Keep this sign-in's last public web address check for a reload.

    Session data, never a form field: the digest is the readiness inputs'
    the check ran against, so any later change to them makes it out of date.
    """
    request.session[CHECK_KEY] = {
        "campaign": str(campaign_id),
        "actor": str(actor_id),
        "digest": inputs.digest,
        "origin": origin,
        "profile": profile,
        "verified": bool(verified),
    }


def kept_check(request, campaign_id, actor_id, inputs):
    """The kept check's result for these exact inputs: True, False or None.

    None means not checked, or out of date: the campaign, the Administrator,
    the inputs digest or the deployment's public web address changed since.
    """
    from .go_live_commands import configured_origin

    kept = getattr(request, "session", {}).get(CHECK_KEY)
    if not isinstance(kept, dict):
        return None
    try:
        origin, profile = configured_origin()
    except ConfigError:
        return None
    if (
        kept.get("campaign"),
        kept.get("actor"),
        kept.get("digest"),
        kept.get("origin"),
        kept.get("profile"),
    ) != (str(campaign_id), str(actor_id), inputs.digest, origin, profile.value):
        return None
    return kept.get("verified") is True


def check_state(problems, target, verified):
    """The Check step's readiness state when no go-live is in progress."""
    if target == "closed":
        return "closed"
    if problems:
        return "not_ready"
    return "passed" if verified is True else "unchecked"


def bar_state(shown):
    """The step bar's ``state`` for a Prepare or Confirm Production read."""
    if shown.state == "confirmed":
        return "confirmed"
    return "started" if shown.started else "idle"


def bar(state, *, current="check", prepared=False):
    """The Go live step bar, as a ``setup_wizard.Wizard``, on step ``current``.

    Before Start, Check is not done and every later step is unavailable,
    saying why. Once go-live has started (``state`` "started"), Check is
    completed and Prepare opens; once the Family links are ready
    (``prepared``) Prepare is completed too and Confirm Production opens.
    Activation waits for the confirmation. Once Production is confirmed
    (``state`` "confirmed", drawn by Prepare and Confirm Production), the
    first three steps are completed and Activation, in progress, opens
    Production activation; Check, an unavailable menu page then, has no
    link.
    """
    confirmed = state == "confirmed"
    started = state == "started" or confirmed
    prepared = prepared or confirmed
    first = _("Not available yet: Start go-live on the Check step first.")
    reasons = {"prepare": first, "confirm": first, "activate": first}
    if started:
        reasons = {
            "confirm": _("Not available yet: the Family links are not prepared yet."),
            "activate": _("Not available yet: Confirm Production first."),
        }
    done = {"check": started, "prepare": started and prepared, "confirm": confirmed}
    urls = {
        "check": None if confirmed else reverse("admin:go_live"),
        "prepare": reverse("admin:go_live_prepare") if started else None,
        "confirm": reverse("admin:go_live_confirm") if started and prepared else None,
        "activate": reverse("admin:production_progress") if confirmed else None,
    }
    steps = []
    for number, (key, label) in enumerate(STEPS, start=1):
        url = urls.get(key)
        if done.get(key):
            state_name, status = "done", _("Completed")
        elif url and key in {"prepare", "activate"}:
            state_name, status = "todo", _("In progress")
        elif url:
            state_name, status = "todo", _("Not done yet")
        else:
            state_name, status = "blocked", reasons[key]
        steps.append(
            Step(
                number=number,
                key=key,
                label=label,
                href=url,
                url=url,
                state=state_name,
                status=status,
                current=key == current,
            )
        )
    index = [key for key, _label in STEPS].index(current)
    return Wizard(
        steps=tuple(steps),
        previous=None,
        next=steps[index + 1] if index + 1 < len(steps) else None,
        completed=sum(1 for value in done.values() if value),
        total=len(steps),
    )

"""The Go live page's Check step: its state, step bar and kept check (#1585).

The Check step (admin-portal spec, "Go live page") is drawn from durable
state on every read, so a browser reload returns to the same step with its
current status. Its state is one of:

- ``started``: the campaign's latest go-live request still owns the gate
  and nobody asked to stop it; the step says when and by whom it started
  and its one primary opens the step in progress (until #1585 slice 3,
  today's Testing cleanup or Prepare Family links page).
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
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.models import NONTERMINAL_STATES, TaskRun

from .admin_editing import editable_configuration, principal
from .policy_models import PortalUser
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
    the email of the Administrator who started it; ``stop_kind`` what a
    stop is still doing ("cleanup" or "links"); ``done``/``total`` its
    progress; ``retry`` the signed Retry stopping go-live control, and
    ``failed`` the failed disposal it retries as ``(preparation, task)``.
    """

    state: str
    request: ProductionTransitionRequest | None = None
    started_by: str | None = None
    stop_kind: str | None = None
    done: int = 0
    total: int = 0
    retry: str | None = None
    failed: tuple | None = None
    stopped_at: datetime | None = None

    @property
    def in_progress_url(self):
        """The step in progress: Prepare Family links once cleanup is complete."""
        if self.request is None:
            return None
        name = (
            "admin:go_live_links"
            if self.request.state == "cleanup_complete"
            else "admin:go_live_cleanup"
        )
        return reverse(name, args=[self.request.pk])


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
    line above the readiness checks.
    """
    with work_transaction():
        actor = principal(request, service, passive=True)
        editable_configuration(service)
        row = (
            ProductionTransitionRequest.objects.filter(campaign_id=campaign_id)
            .order_by("-created_at", "-id")
            .first()
        )
        if row is None or row.state == "activated":
            # Nothing started, or a go-live that was cancelled after
            # Production was confirmed: Check starts again from readiness.
            return Progress("idle")
        stopping = ProductionCleanupCancellation.objects.filter(request=row).exists()
        if row.state in OPEN_STATES and not stopping:
            email = (
                PortalUser.objects.filter(pk=row.initiated_by_id)
                .values_list("email", flat=True)
                .first()
            )
            return Progress("started", row, started_by=email)
        if row.state != "cancelled":
            return Progress(
                "stopping",
                row,
                stop_kind="cleanup",
                done=row.processed_count,
                total=row.inventory_total,
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
            )
        return Progress("idle", row, stopped_at=row.updated_at)


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


def bar(state, in_progress_url=None):
    """The Go live step bar on the Check step, as a ``setup_wizard.Wizard``.

    Check is the current step. Before Start it is not done and every later
    step is unavailable, saying why; once go-live has started Check is
    completed, Prepare opens the step in progress, and the later steps wait
    for the Family links and the confirmation.
    """
    started = state == "started"
    reasons = {
        "prepare": _("Not available yet: Start go-live on the Check step first."),
        "confirm": _("Not available yet: Start go-live on the Check step first."),
        "activate": _("Not available yet: Start go-live on the Check step first."),
    }
    if started:
        reasons = {
            "confirm": _("Not available yet: the Family links are not prepared yet."),
            "activate": _("Not available yet: Confirm Production first."),
        }
    steps = []
    for number, (key, label) in enumerate(STEPS, start=1):
        url = reverse("admin:go_live") if key == "check" else None
        state_name, status = "todo", _("Not done yet")
        if key == "check" and started:
            state_name, status = "done", _("Completed")
        elif key == "prepare" and started:
            url, status = in_progress_url, _("In progress")
        elif key != "check":
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
                current=key == "check",
            )
        )
    return Wizard(
        steps=tuple(steps),
        previous=None,
        next=steps[1],
        completed=1 if started else 0,
        total=len(steps),
    )

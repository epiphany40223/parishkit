"""The Go live page's actions on the command line (ADM-11 PR 12, #1809).

``go-live preview`` and ``go-live start`` are Check's Check readiness and
public web address and the Start go-live dialog's Apply; ``go-live status``
is the Prepare step's read; ``go-live retry`` the Retry the current step
offers (Production activation's mail preparation, a failed Prepare box, or
Check's Retry stopping go-live); ``go-live refresh-again`` and ``go-live
stop`` are Refresh and prepare again and Stop go-live; ``go-live
confirm-preview`` and ``go-live confirm`` the Confirm Production dialog's
Review and Apply; ``go-live cancel-preview`` and ``go-live cancel`` the
Cancel go-live dialog's Review and Apply; ``go-live families`` the Testing
submissions list as a streamed CSV.

Every action runs the request-free function the page's own view runs, with
the command's caller (``go_live_views.start_reviewed``,
``go_live_confirm_views.review_confirm`` and ``apply_review``,
``confirmation_views.review_cancel`` and ``apply_cancel``, the Prepare
step's ``_retry``, ``_again`` and ``_stop``, ``go_live_views._retry_stop``
and ``confirmation_progress.retry_shown``), so the page's rules, its
owners' checks and the SQL guards apply unchanged. A dialog's refusal
(the step moved, the public web address check failed, the counts changed,
another Administrator acted) is ``stale_version``; the operator reads
``go-live status`` or ``go-live progress`` and acts again. Each
state-changing command records its own ``admin_cmd_go_live_<verb>`` event
in the action's own transaction, through the owner's ``record`` hook or
inside the work transaction that acts, so nothing is recorded when the
action is not.

A preview's token is the page's own signed binding (Start go-live's
preview, Confirm Production's review), or for Cancel go-live the page's
review binding with the reason beside it, signed (not encrypted) under
``CANCEL_SALT``: Cancel go-live's dialog takes its reason at Apply, the
command at its preview. Nothing here prints a Family name except the
streamed Testing submissions file.
"""

import csv
import hashlib
import io
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from .admin_reads import NotAvailable, ReadModel, _admit, _held, _recheck

# ``go-live cancel-preview``'s token: the page's review binding and the
# reason, signed without a time, as the page's review binding is.
CANCEL_SALT = "stewardship-admin-go-live-cancel-v1"
# The Cancel go-live counts a document prints; the inventory's fingerprint,
# which only the owner's intent binds, never prints.
CANCEL_COUNTS = (
    "occurrences",
    "cancellable",
    "messages",
    "failed",
    "delivered",
    "blocking",
)
# The Testing submissions file: one page of the list at a time, as the page
# reads it (``PageWindow``'s largest size).
FAMILY_PAGE = 100
FAMILY_HEADINGS = ("duid", "name")
# What each Prepare control's action retries, as ``go-live retry`` names it.
RETRIED = {
    "retry_task": "cleanup",
    "retry": "links",
    "retry_cleanup": "links_discard",
}


def _stale(key):
    """The ``StaleRecordError`` a dialog refusal ``key`` is reported as."""
    from .storage import StaleRecordError

    return StaleRecordError(f"The go-live action was refused: {key}.")


def _campaign(service):
    """The current campaign, which the Go live page's addresses name.

    No current campaign is ``not_available``.
    """
    from .admin_reads import _current_campaign
    from .campaigns.work_locks import read_transaction

    def read():
        """The current campaign, in one snapshot."""
        with read_transaction():
            return _current_campaign(service, None)

    return _held(read)


def _page_refusals(function):
    """Run one of the page's functions, its refusals as the command's codes.

    As the pages' views do: another installer finishing is a stale refusal
    (retry), and a record that vanished is not available. A tampered or
    foreign token is ``invalid``; Testing submissions for a campaign that
    is not the Testing draft any more is ``stale_version``.
    """
    from django.core import signing
    from django.core.exceptions import ObjectDoesNotExist

    from .accounts.installation_lock import ConfigurationBusy
    from .storage import StaleRecordError
    from .web.refusals import UserFacingGone

    try:
        return function()
    except ConfigurationBusy:
        raise StaleRecordError("Another operation is finishing; retry.") from None
    except UserFacingGone:
        raise StaleRecordError("The campaign is not the Testing draft.") from None
    except ObjectDoesNotExist:
        raise NotAvailable("Go-live is not available for this campaign.") from None
    except signing.BadSignature:
        raise ValueError("The token is not valid.") from None


def _acting(caller, service, actor, function, context, recorded):
    """Run a state-changing step; report its refusals as the command's codes.

    A refusal that may come from a session that ended meanwhile is exit 5
    after the recheck. A database failure once the command's own event was
    recorded may have committed (exit 6). ``recorded`` is the list the
    step's ``record`` hook appends to.
    """
    from django.db import DatabaseError

    from .admin_changes import _ended_or_raise
    from .observability import _guard_refusal

    try:
        result = _page_refusals(function)
    except (PermissionError, NotAvailable) as error:
        _ended_or_raise(caller, service, actor, error)
    except DatabaseError as error:
        if recorded and not _guard_refusal(error):
            context["committed"] = True
        raise
    context["committed"] = True
    return result


def _own(caller, service, binding):
    """Refuse another Administrator's token (``denied``) before any prompt."""
    actor = _admit(caller, service.store)
    if binding.get("actor") != str(actor.identity):
        raise PermissionError("This token belongs to another Administrator.")


def _event(verb, caller, actor):
    """Record the command's own ``admin_cmd_go_live_<verb>`` event."""
    from .admin_integration_actions import _command_event
    from .audit.schemas import Action

    _command_event(Action(f"admin_cmd_go_live_{verb}"), caller, actor)


# --------------------------------------------------------------- the status


@dataclass(frozen=True)
class GoLiveStatus(ReadModel):
    """The Prepare step as ``go-live status`` prints it.

    ``state`` and ``reason`` are ``go_live_prepare.Prepare``'s; ``boxes``
    each stage box's ``key`` and ``state``; ``done``, ``total``,
    ``preparation`` and ``max_preparations`` the detail line's counts;
    ``prepared_id`` the ready preparation Confirm Production confirms. The
    ``*_available`` members say which controls the page offers, never the
    signed controls themselves. ``stopping`` is Check's stop still running
    or failed (``go_live_check.Progress``), else None. A watch stops once
    nothing moves by itself: not ``running``, and no stop still running.
    """

    campaign_id: UUID
    state: str
    reason: str | None
    request_id: UUID | None
    boxes: list
    done: int
    total: int
    preparation: int
    max_preparations: int
    prepared_id: UUID | None
    failed_at: datetime | None
    stop_kind: str | None
    stopped_by_other: bool
    retry_available: bool
    again_available: bool
    stop_available: bool
    confirm_available: bool
    stopping: dict | None

    @property
    def terminal(self):
        """Nothing moves by itself any more."""
        return self.state != "running" and (
            self.stopping is None or self.stopping["state"] != "stopping"
        )

    @classmethod
    def build(cls, campaign_id, shown, stage):
        """Project the Prepare read ``shown`` and Check's stage ``stage``."""
        from .accounts.go_live_prepare import MAX_PREPARATIONS

        stopping = None
        if stage.state in {"stopping", "stop_failed"}:
            stopping = {
                "state": stage.state,
                "kind": stage.stop_kind,
                "done": stage.done,
                "total": stage.total,
                "retry_available": stage.retry is not None,
            }
        return cls(
            campaign_id=campaign_id,
            state=shown.state,
            reason=shown.reason,
            request_id=shown.request.pk if shown.request else None,
            boxes=[{"key": box["key"], "state": box["state"]} for box in shown.boxes],
            done=shown.done,
            total=shown.total,
            preparation=shown.preparation,
            max_preparations=MAX_PREPARATIONS,
            prepared_id=shown.prepared.pk if shown.prepared else None,
            failed_at=shown.failed_at,
            stop_kind=shown.stop_kind,
            stopped_by_other=shown.stopped_by_other,
            retry_available="retry" in shown.controls,
            again_available="again" in shown.controls,
            stop_available="stop" in shown.controls,
            confirm_available=shown.state == "ready",
            stopping=stopping,
        )


def read_status(caller, service):
    """``go-live status``: the Prepare step's read, with Check's stop.

    Passive, for any session, recording nothing, as the page's GET. Read
    through the page's own ``go_live_prepare.read`` and
    ``go_live_check.progress``; a refusal is reported after the session
    recheck, as the other go-live reads do.
    """
    from .accounts import go_live_check, go_live_prepare
    from .accounts.policy import Capability
    from .admin_reads import _current_campaign, _page_read

    def step():
        """Admit, read as the page does, then recheck."""
        actor = _admit(caller, service.store, Capability.CONFIGURE)
        campaign_id = _current_campaign(service, None)
        shown, stage = _page_read(
            caller,
            service,
            actor,
            campaign_id,
            lambda: (
                go_live_prepare.read(caller, service, campaign_id),
                go_live_check.progress(caller, service, campaign_id),
            ),
        )
        model = GoLiveStatus.build(campaign_id, shown, stage)
        _recheck(caller, service.store, actor, Capability.CONFIGURE)
        return model

    return _held(step)


def _status_document(caller, service):
    """``go-live status``'s document, read after a commit."""
    return read_status(caller, service).to_document()


# --------------------------------------------- Check: preview and Start go-live


@dataclass(frozen=True)
class StartPreview(ReadModel):
    """``go-live preview``: Check readiness and public web address.

    ``readiness`` is ``go-live readiness``'s document; ``origin_verified``
    the public web address check's result; ``startable`` both together;
    ``preview.token`` the Start go-live dialog's own signed preview (five
    minutes for the page's Review; ``go-live start`` checks only its
    signature), or null when not ``startable``.
    """

    campaign_id: UUID
    origin_verified: bool
    startable: bool
    readiness: dict
    preview: dict


def preview_start(caller, service):
    """``go-live preview``: the DNS and origin check, then readiness.

    Admits as Check's post does (recording activity), runs the page's own
    ``go_live_commands.verify_preview`` (the check outside any transaction,
    then the inputs), and reads the recent cleanup requests as ``go-live
    readiness`` does. Changes nothing and records no event.
    """
    from .accounts.go_live_commands import verify_preview
    from .accounts.go_live_inputs import recent_cleanup_requests
    from .accounts.policy import Capability
    from .admin_changes import _admit_change, _ended_or_raise
    from .admin_reads import GoLiveReadiness

    actor = _admit_change(caller, service)
    campaign_id = _campaign(service)
    try:
        inputs, verified, token = _page_refusals(
            lambda: verify_preview(caller, service, campaign_id)
        )
    except (PermissionError, NotAvailable) as error:
        _ended_or_raise(caller, service, actor, error)
    readiness = GoLiveReadiness.build(inputs, recent_cleanup_requests(campaign_id))
    _recheck(caller, service.store, actor, Capability.CONFIGURE)
    return StartPreview(
        campaign_id=campaign_id,
        origin_verified=bool(verified),
        startable=token is not None,
        readiness=readiness.to_document(),
        preview={"token": token},
    )


def _start_binding(token):
    """The Start go-live preview's binding: its signature checked, not its age."""
    from django.core import signing

    from .accounts.go_live_commands import SALT

    if type(token) is not str or len(token) > 4096:
        raise ValueError("The preview token is not valid.")
    binding = _page_refusals(lambda: signing.loads(token, salt=SALT))
    if type(binding) is not dict or type(binding.get("digest")) is not str:
        raise ValueError("The preview token is not valid.")
    return binding


def start_prompt_lines(caller, service, token):
    """The Start go-live prompt: the dialog's sentences and cleanup counts
    (go-live-start-review.html).

    Checked before the prompt: the token's signature, that it is this
    Administrator's, and that readiness still matches it (else
    ``stale_version``, with nothing asked). None, with nothing asked, when
    the same token already started go-live or this Administrator's go-live
    is already started: ``start`` then starts nothing (and, since it is
    told nothing was asked, refuses rather than start anything new). Any
    other go-live in progress is refused here, before the prompt.
    """
    from .accounts import go_live_check
    from .accounts.go_live_inputs import collect_inputs
    from .accounts.go_live_views import _in_progress_refusal, _sent_before

    binding = _start_binding(token)
    actor = _admit(caller, service.store)
    if binding.get("actor") != str(actor.identity):
        raise PermissionError("This preview belongs to another Administrator.")
    campaign_id = _campaign(service)
    if _sent_before(campaign_id, binding):
        return None
    stage = _held(
        lambda: _page_refusals(
            lambda: go_live_check.progress(caller, service, campaign_id)
        )
    )
    if stage.state != "idle":
        refusal = _in_progress_refusal(actor, stage)
        if refusal is not None:
            raise _stale(refusal[0])
        return None
    inputs = _held(
        lambda: _page_refusals(lambda: collect_inputs(caller, service, campaign_id))
    )
    if inputs.problems or inputs.digest != binding["digest"]:
        raise _stale("changed")
    cleanup = inputs.cleanup
    return (
        "Start go-live for the current Testing draft. Testing data is deleted; "
        "the system refreshes ParishSoft data and prepares new Family links by "
        "itself. Nothing reaches Families until you confirm.",
        f"Testing submissions: {cleanup.submissions}. Families with Testing "
        f"submissions: {cleanup.families}. Testing messages: {cleanup.messages}. "
        f"Testing records to delete: {cleanup.inventory.total}.",
        "Deleted Testing data cannot be restored, even if you stop going live "
        "later. Testing links and codes stop working now.",
    )


@dataclass(frozen=True)
class StartResult(ReadModel):
    """``go-live start``: the go-live request and the Prepare step after it.

    ``created`` is false when nothing new started (the same token again, or
    go-live this Administrator had already started).
    """

    created: bool
    request_id: UUID | None
    status: dict


def start(caller, service, *, token, context, asked=True):
    """``go-live start``: the Start go-live dialog's Apply.

    ``asked`` is whether the prompt (the acknowledgement) was asked: a run
    that skipped it (a replay, or go-live this Administrator already
    started) must start nothing new, so a new request then is refused and
    rolls back, whatever changed since.

    Admits as the page does, then runs the page's own
    ``go_live_views.start_reviewed`` (without the browser session's kept
    check: the preview ran the check itself, and Apply runs it again). Its
    ``start_cleanup`` passes ``sign_in_instant`` (``automation_fresh_gate``
    and the ``fresh_gated`` notice); the ``record`` hook adds the
    irreversible notice and incident and ``admin_cmd_go_live_start`` in the
    same transaction. A refusal is ``stale_version``.
    """
    from .accounts.go_live_views import start_reviewed
    from .accounts.sessions import sign_in_instant
    from .admin_changes import _admit_change

    actor = _admit_change(caller, service)
    campaign_id = _campaign(service)
    caller.campaign_id = campaign_id
    recorded = []

    def record(current, status):
        """Testing cleanup is irreversible: its notice, and the command's event.

        Never without the acknowledgement: raising here rolls the new
        request back.
        """
        if not asked:
            raise _stale("not_acknowledged")
        sign_in_instant(caller, irreversible=True)
        _event("start", caller, current)
        recorded.append(status.request_id)

    refusal = _acting(
        caller,
        service,
        actor,
        lambda: start_reviewed(caller, service, campaign_id, token, record=record),
        context,
        recorded,
    )
    if refusal is not None:
        context["committed"] = False
        raise _stale(refusal[0])
    status = _status_document(caller, service)
    return StartResult(
        created=bool(recorded),
        request_id=status["request_id"],
        status=status,
    )


# ----------------------------------------------------- the Prepare controls


def prepare_control(caller, service, action):
    """The Prepare control ``action`` the step offers now: ``(token, shown)``.

    ``action`` is ``again`` (Refresh and prepare again) or ``stop`` (Stop
    go-live). Read passively before the prompt; a control the step does not
    offer now is ``stale_version``.
    """
    from .accounts import go_live_prepare

    _admit(caller, service.store)
    campaign_id = _campaign(service)
    shown = _held(
        lambda: _page_refusals(
            lambda: go_live_prepare.read(caller, service, campaign_id)
        )
    )
    token = shown.controls.get(action) if shown.started else None
    if token is None:
        raise _stale("not_offered")
    return token, shown


def again_prompt_lines(shown):
    """Refresh and prepare again's prompt: the page's own sentence
    (go-live-prepare.html's note for ``again``)."""
    return (
        "Refresh and prepare again: request a new ParishSoft refresh, then "
        "prepare new Family links from it. It does not delete Testing data "
        "again.",
    )


def stop_prompt_lines(shown):
    """Stop go-live's prompt: the Stop go-live dialog's sentence
    (go-live-stop-review.html)."""
    first = {
        "cleanup": "Testing data deletion stops at the next safe point. ",
        "links": "The prepared Family links are discarded. ",
    }.get(shown.stop_kind, "")
    return (
        "Stop go-live. "
        + first
        + "Testing data already deleted is not restored. The campaign stays a "
        "Testing draft and Testing work can start again.",
    )


@dataclass(frozen=True)
class PrepareResult(ReadModel):
    """``go-live refresh-again`` or ``go-live stop``: the step after it.

    ``attempt`` is the attempt Refresh and prepare again began (None for a
    stop).
    """

    attempt: int | None
    status: dict


def apply_prepare(caller, service, action, *, token, context):
    """Refresh and prepare again or Stop go-live, as the page's ``_act``.

    In one work transaction, as the page: admit with activity, read the
    step, and act only while it still offers the same control (controls are
    signed without a time, so the same state draws the same one); else
    ``stale_version``. Then the page's own ``_again`` or ``_stop`` through
    the owners, which recheck everything under their own locks, and
    ``admin_cmd_go_live_refresh_again`` or ``_stop`` in that transaction.
    """
    from .accounts import go_live_prepare, go_live_prepare_views
    from .accounts.admin_editing import principal
    from .admin_changes import _admit_change
    from .campaigns.work_locks import work_transaction

    actor = _admit_change(caller, service)
    campaign_id = _campaign(service)
    value = _page_refusals(lambda: go_live_prepare.unsign(token))
    recorded = []

    def act():
        """Re-read, compare the control, act and record, in one transaction."""
        with work_transaction():
            current = principal(caller, service)
            shown = go_live_prepare.read(caller, service, campaign_id)
            if (
                not shown.started
                or str(shown.request.pk) != value["request"]
                or shown.controls.get(action) != token
            ):
                raise _stale("changed")
            if action == "again":
                go_live_prepare_views._again(
                    caller, service, current, shown.request, int(value["number"])
                )
            else:
                go_live_prepare_views._stop(
                    caller, service, campaign_id, current, shown.request
                )
            _event("refresh_again" if action == "again" else "stop", caller, current)
            recorded.append(action)

    _acting(caller, service, actor, act, context, recorded)
    return PrepareResult(
        attempt=int(value["number"]) if action == "again" else None,
        status=_status_document(caller, service),
    )


# ----------------------------------------------------------------- the retry


@dataclass(frozen=True)
class RetryResult(ReadModel):
    """``go-live retry``: what was retried, and the step after it.

    ``step`` is ``activation``, ``prepare`` or ``check``; ``retried`` is
    ``mail_preparation``, ``cleanup``, ``links``, ``links_discard`` or
    ``stop``; ``status`` is ``go-live progress``'s document for
    ``activation``, else ``go-live status``'s.
    """

    step: str
    retried: str
    status: dict


def retry(caller, service, *, context):
    """``go-live retry``: the one Retry the current step offers.

    In one work transaction: in Production, mail preparation's Retry
    (``confirmation_progress.retry_shown`` with the page's control read
    now); in Testing, the Prepare step's Retry while go-live is in progress
    (its ``_retry`` with the step's control), else Check's Retry stopping
    go-live (``go_live_views._retry_stop``). Each owner rechecks the
    Administrator and the failed task's version, and
    ``admin_cmd_go_live_retry`` is recorded in that transaction. Nothing to
    retry is ``not_available``; it changes no data, so it does not prompt.
    """
    from .accounts import (
        confirmation_progress,
        go_live_check,
        go_live_prepare,
        go_live_prepare_views,
        go_live_views,
    )
    from .accounts.admin_editing import editable_configuration, principal
    from .admin_changes import _admit_change
    from .admin_reads import read_go_live_progress
    from .campaigns.work_locks import work_transaction

    actor = _admit_change(caller, service)
    campaign_id = _campaign(service)
    recorded = []

    def act():
        """Find the failed task, retry it through its owner, and record."""
        with work_transaction():
            current = principal(caller, service)
            if editable_configuration(service).mode == "production":
                shown = confirmation_progress.progress(caller, service, campaign_id)
                control = shown["control"]
                if control is None or not confirmation_progress.retry_shown(
                    caller, service, campaign_id, control=control
                ):
                    raise NotAvailable("There is no failed go-live task to retry.")
                done = ("activation", "mail_preparation")
            else:
                shown = go_live_prepare.read(caller, service, campaign_id)
                if shown.started:
                    token = shown.controls.get("retry")
                    if token is None:
                        raise NotAvailable("There is no failed go-live task to retry.")
                    value = go_live_prepare.unsign(token)
                    if not go_live_prepare_views._retry(
                        caller, service, campaign_id, current, shown.request, value
                    ):
                        raise NotAvailable("There is no failed go-live task to retry.")
                    done = ("prepare", RETRIED[value["action"]])
                else:
                    stopped = go_live_check.progress(caller, service, campaign_id)
                    if stopped.state != "stop_failed":
                        raise NotAvailable("There is no failed go-live task to retry.")
                    if not go_live_views._retry_stop(
                        caller, service, campaign_id, stopped.retry
                    ):
                        raise NotAvailable("There is no failed go-live task to retry.")
                    done = ("check", "stop")
            _event("retry", caller, current)
            recorded.append(done)
            return done

    step, retried = _acting(caller, service, actor, act, context, recorded)
    status = (
        read_go_live_progress(caller, service, None).to_document()
        if step == "activation"
        else _status_document(caller, service)
    )
    return RetryResult(step=step, retried=retried, status=status)


# ------------------------------------------------------ Confirm Production


@dataclass(frozen=True)
class ConfirmPreview(ReadModel):
    """``go-live confirm-preview``: the Confirm Production dialog's Review.

    ``starts_at`` is the campaign's start (a scheduled campaign waits for
    it); ``counts`` are the counts the confirmation binds
    (``confirmation_commands.preview_counts``); ``preview.token`` is the
    dialog's own review binding, signed without a time.
    """

    campaign_id: UUID
    request_id: UUID
    preparation_id: UUID
    target_state: str
    starts_at: datetime
    start_passed: bool
    reprepared: bool
    counts: dict
    preview: dict


def preview_confirm(caller, service):
    """``go-live confirm-preview``: the dialog's Review, or ``stale_version``.

    Admits as the page's post does (recording activity) and runs the page's
    own ``review_confirm``: a step that is not ready (or Production already
    confirmed) is ``stale_version``. Changes nothing and records no event.
    """
    from .accounts import confirmation_commands
    from .accounts.go_live_confirm_views import review_confirm
    from .accounts.policy import Capability
    from .admin_changes import _admit_change, _ended_or_raise

    actor = _admit_change(caller, service)
    campaign_id = _campaign(service)
    try:
        shown, refusal, review = _held(
            lambda: _page_refusals(lambda: review_confirm(caller, service, campaign_id))
        )
    except (PermissionError, NotAvailable) as error:
        _ended_or_raise(caller, service, actor, error)
    if review is None:
        raise _stale(refusal[0] if refusal else "confirmed")
    _recheck(caller, service.store, actor, Capability.CONFIGURE)
    return ConfirmPreview(
        campaign_id=campaign_id,
        request_id=shown.request.pk,
        preparation_id=shown.prepare.prepared.pk,
        target_state=shown.target,
        starts_at=shown.preview.readiness.campaign.active_configuration.starts_at,
        start_passed=shown.start_passed,
        reprepared=shown.reprepared,
        counts=confirmation_commands.preview_counts(shown.preview),
        preview={"token": review},
    )


def confirm_prompt_lines(caller, service, token):
    """The Confirm Production prompt, from the review binding's own counts.

    The dialog's question and summary (go-live-confirm-review.html), its two
    headline counts and its caution. Before the prompt, a token that is not
    the page's review binding is ``invalid``, and another Administrator's
    ``denied``.
    """
    from .accounts.go_live_confirm_views import _binding
    from .campaigns.models import Campaign

    binding = _page_refusals(lambda: _binding(token))
    _own(caller, service, binding)
    counts = binding["counts"]
    if binding["target"] == "scheduled":
        starts = (
            Campaign.objects.select_related("active_configuration")
            .get(pk=_campaign(service))
            .active_configuration.starts_at
        )
        question = (
            "Schedule the campaign? The campaign is scheduled to start "
            f"{starts.isoformat()}; nothing is sent until then."
        )
    else:
        question = (
            "Go live now? The campaign becomes active at once, and the emails "
            "due at once are sent."
        )
    return (
        question,
        f"Families with an eligible email address: {counts['eligible_families']} "
        f"of {counts['active_families']}. Family emails due at once: "
        f"{counts['family_messages']}.",
        "Families can open the form and emails start being sent. This cannot "
        "be undone after the campaign starts.",
    )


@dataclass(frozen=True)
class ConfirmResult(ReadModel):
    """``go-live confirm``: the confirmation and Production activation after it.

    ``created`` is false when nothing new was confirmed (the same token
    again, or Production this Administrator had already confirmed).
    """

    created: bool
    confirmation_id: UUID
    target_state: str
    progress: dict


def confirm(caller, service, *, token, context):
    """``go-live confirm``: the Confirm Production dialog's Apply.

    The prompt took the typed word. Runs the page's own ``apply_review``:
    the step still ready on the same links, the public web address check
    again, the preview built again and compared with the review, then
    ``confirmation_commands.confirm``, which passes ``sign_in_instant``
    irreversibly (the ``irreversible`` notice and incident) and records
    ``admin_cmd_go_live_confirm`` through its ``record`` hook. A refusal is
    ``stale_version``.
    """
    from .accounts.go_live_confirm_views import WORD, apply_review
    from .admin_changes import _admit_change
    from .admin_reads import read_go_live_progress
    from .campaigns.confirmation_models import ProductionConfirmation

    actor = _admit_change(caller, service)
    campaign_id = _campaign(service)
    caller.campaign_id = campaign_id
    recorded = []

    def record(current, confirmation):
        """The command's own event, in the confirmation's transaction."""
        _event("confirm", caller, current)
        recorded.append(confirmation.pk)

    refusal = _acting(
        caller,
        service,
        actor,
        lambda: apply_review(
            caller, service, campaign_id, review=token, typed=WORD, record=record
        ),
        context,
        recorded,
    )
    if refusal is not None:
        context["committed"] = False
        raise _stale(refusal[0])
    receipt = ProductionConfirmation.objects.filter(
        request__campaign_id=campaign_id
    ).latest("created_at")
    return ConfirmResult(
        created=bool(recorded),
        confirmation_id=receipt.pk,
        target_state=receipt.target_state,
        progress=read_go_live_progress(caller, service, None).to_document(),
    )


# ------------------------------------------------------------ Cancel go-live


@dataclass(frozen=True)
class CancelPreview(ReadModel):
    """``go-live cancel-preview``: the Cancel go-live dialog's Review.

    ``inventory`` holds the counts the dialog shows and ``blocking``;
    ``cancellable`` is whether nothing blocks it; ``preview.token`` binds
    the dialog's review and the reason (null while mail blocks it).
    """

    campaign_id: UUID
    confirmation_id: UUID
    reason: str
    inventory: dict
    cancellable: bool
    preview: dict


def preview_cancel(caller, service, *, reason):
    """``go-live cancel-preview``: the dialog's Review, with its reason.

    Admits as the page's post does, then checks the reason as the dialog's
    Apply does (more than spaces, at most 2,000 characters; else
    ``invalid``), and runs the page's own ``review_cancel``: Cancel
    go-live not available now (the campaign started, delivery is paused,
    it was cancelled) is ``stale_version``. Changes nothing and records no
    event.
    """
    from django.core import signing

    from .accounts.confirmation_views import REASON_MAX, review_cancel
    from .accounts.policy import Capability
    from .admin_changes import _admit_change, _ended_or_raise

    actor = _admit_change(caller, service)
    if type(reason) is not str or not reason.strip() or len(reason) > REASON_MAX:
        raise ValueError("Enter a reason of at most 2,000 characters.")
    campaign_id = _campaign(service)
    try:
        refused, values = _held(
            lambda: _page_refusals(lambda: review_cancel(caller, service, campaign_id))
        )
    except (PermissionError, NotAvailable) as error:
        _ended_or_raise(caller, service, actor, error)
    if refused not in {None, "blocking"}:
        raise _stale(refused)
    token = None
    if refused is None:
        token = signing.Signer(salt=CANCEL_SALT).sign_object(
            {"review": values["review"], "reason": reason}
        )
    _recheck(caller, service.store, actor, Capability.CONFIGURE)
    inventory = values["inventory"]
    return CancelPreview(
        campaign_id=campaign_id,
        confirmation_id=values["confirmation"].pk,
        reason=reason,
        inventory={name: inventory[name] for name in CANCEL_COUNTS},
        cancellable=token is not None,
        preview={"token": token},
    )


def _cancel_binding(token):
    """``go-live cancel-preview``'s token: ``(review, reason)``, signature checked.

    The page's review binding inside is checked too; either altered is
    ``invalid``.
    """
    from django.core import signing

    from .accounts.confirmation_views import _binding
    from .admin_changes import TOKEN_LIMIT

    if type(token) is not str or len(token) > TOKEN_LIMIT:
        raise ValueError("The preview token is not valid.")
    value = _page_refusals(
        lambda: signing.Signer(salt=CANCEL_SALT).unsign_object(token)
    )
    if (
        type(value) is not dict
        or set(value) != {"review", "reason"}
        or type(value["reason"]) is not str
    ):
        raise ValueError("The preview token is not valid.")
    review = _page_refusals(lambda: _binding(value["review"]))
    return value["review"], value["reason"], review


def cancel_prompt_lines(caller, service, token):
    """The Cancel go-live prompt: the dialog's sentences
    (production-cancel-review.html), the reason and the counts.

    Before the prompt, an altered token is ``invalid`` and another
    Administrator's ``denied``.
    """
    _review, reason, binding = _cancel_binding(token)
    _own(caller, service, binding)
    inventory = binding["inventory"]
    return (
        "Cancelling go-live cancels mail that has not been sent and makes "
        "this campaign a Testing draft again, so its settings can be changed.",
        f"Reason: {reason}",
        f"Scheduled mail runs recorded: {inventory['occurrences']}. Scheduled "
        f"mail runs to cancel: {inventory['cancellable']}. Unsent messages to "
        f"cancel: {inventory['messages']}. Failed mail runs kept in history: "
        f"{inventory['failed']}. Delivered messages kept in history: "
        f"{inventory['delivered']}.",
        "Testing data deleted earlier is not restored; going live again starts "
        "from Check.",
    )


@dataclass(frozen=True)
class CancelResult(ReadModel):
    """``go-live cancel``: the withdrawal; the campaign is a Testing draft again.

    ``created`` is false when nothing new was cancelled (the same token
    again, or go-live this Administrator had already cancelled).
    """

    created: bool
    withdrawal_id: UUID
    campaign_id: UUID


def cancel(caller, service, *, token, context):
    """``go-live cancel``: the Cancel go-live dialog's Apply.

    Runs the page's own ``apply_cancel`` with the token's review and
    reason: the cancellation built again and compared with the review, then
    ``withdrawal_commands.withdraw``, which passes ``sign_in_instant``
    irreversibly and records ``admin_cmd_go_live_cancel`` through its
    ``record`` hook. A refusal is ``stale_version``.
    """
    from .accounts.confirmation_views import apply_cancel
    from .admin_changes import _admit_change
    from .campaigns.withdrawal_models import ProductionWithdrawal

    review, reason, _binding = _cancel_binding(token)
    actor = _admit_change(caller, service)
    campaign_id = _campaign(service)
    caller.campaign_id = campaign_id
    recorded = []

    def record(current, withdrawal):
        """The command's own event, in the withdrawal's transaction."""
        _event("cancel", caller, current)
        recorded.append(withdrawal.pk)

    refusal = _acting(
        caller,
        service,
        actor,
        lambda: apply_cancel(
            caller,
            service,
            campaign_id,
            review=review,
            reason=reason,
            record=record,
        ),
        context,
        recorded,
    )
    if refusal is not None:
        context["committed"] = False
        raise _stale(refusal[0])
    withdrawal = ProductionWithdrawal.objects.filter(
        confirmation__request__campaign_id=campaign_id
    ).latest("created_at")
    return CancelResult(
        created=bool(recorded), withdrawal_id=withdrawal.pk, campaign_id=campaign_id
    )


# ------------------------------------------------------- Testing submissions


@dataclass(frozen=True)
class FamiliesExport(ReadModel):
    """The Testing submissions file ``go-live families`` wrote, described.

    ``count`` is how many Families it lists; ``size`` and ``sha256`` are of
    exactly the bytes written.
    """

    campaign_id: UUID
    count: int
    file_name: str
    content_type: str
    size: int
    sha256: str


def families_csv(rows):
    """The list as CSV: each Family's DUID and name, formulas neutralized."""
    from .web.exports import csv_cell

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(FAMILY_HEADINGS)
    writer.writerows([csv_cell(row["duid"]), csv_cell(row["name"])] for row in rows)
    return buffer.getvalue().encode("utf-8")


def export_families(caller, service, *, context):
    """``go-live families``: the Testing submissions list, as CSV bytes.

    Passive, for any session, recording nothing, as the page. Every page of
    the list is read in one work transaction through the page's own
    ``testing_scope`` and ``cleanup_families``, by DUID; a campaign that is
    not the current Testing draft is ``stale_version``. The bytes go to
    ``context["stream"]``, which the command line writes alone to standard
    output, and the document to standard error.
    """
    from .accounts.go_live_views import testing_scope
    from .accounts.policy import Capability
    from .admin_reads import _current_campaign, _page_read
    from .campaigns.cleanup_preview import cleanup_families
    from .campaigns.work_locks import work_transaction
    from .web.contracts import PageWindow

    def read(campaign_id):
        """Every page of the list, in one transaction."""
        rows = []
        with work_transaction():
            _scope, source_id = testing_scope(caller, service, campaign_id)
            page, more = 1, True
            while more:
                _window, found, more, _total = cleanup_families(
                    campaign_id,
                    source_id=source_id,
                    window=PageWindow(page, FAMILY_PAGE),
                    sort="duid",
                )
                rows.extend(found)
                page += 1
        return rows

    def step():
        """Admit, read as the page does, then recheck."""
        actor = _admit(caller, service.store, Capability.CONFIGURE)
        campaign_id = _current_campaign(service, None)
        rows = _page_read(
            caller,
            service,
            actor,
            campaign_id,
            lambda: _page_refusals(lambda: read(campaign_id)),
        )
        _recheck(caller, service.store, actor, Capability.CONFIGURE)
        return campaign_id, rows

    campaign_id, rows = _held(step)
    body = families_csv(rows)
    context["stream"] = body
    return FamiliesExport(
        campaign_id=campaign_id,
        count=len(rows),
        file_name="testing-submissions.csv",
        content_type="text/csv",
        size=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
    )

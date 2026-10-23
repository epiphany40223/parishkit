"""Send a test to me: one copy of a report email to its Administrator (#2036).

The report-emails spec's "Send a test to me". The Report emails row action,
the editor's button (``report_email_test_views``) and ``pk-admin
report-email send-test`` call ``request_test``: the report (or the editor's
unsaved form) is drawn as Preview draws it (``report_email_preview_views
.draw``), and one ``ReportEmailTest`` keeps that email with its
``report_email_test`` task, under the work lock. Its database guard (frozen
file 0173) admits it only for a current Administrator, fills nothing from
the form it can check (the address is their own sign-in address), and its
partial unique index allows one queued test per Administrator and report.

The worker's task (``report_email_test_handler``) makes the test's one
outbox message, purpose ``admin_report_test``: no Family, no occurrence,
fulfillment, preparation, snapshot or pause hold, so it never counts as a
send of the report. Mail dispatch sends exactly the kept email
(``jobs.report_test_dispatch``). Once the message is settled the database
settles the test and scrubs its email.
"""

import base64
from uuid import UUID, uuid4

from django.db import IntegrityError
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.accounts.configuration_models import AppliedIntegration
from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.runtime_models import CampaignWorkGate
from parishkit.stewardship.campaigns.work_locks import (
    read_transaction,
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.jobs.dispatch import Handler, RecoveryPlan
from parishkit.stewardship.jobs.family_mail_delivery_tasks import (
    preparation_attempts,
)
from parishkit.stewardship.jobs.family_mail_dispatch import FamilyDeliveryHeld
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.outbox_storage import create_message
from parishkit.stewardship.jobs.outbox_validation import (
    DeliveryIdentity,
    ReportRenderInput,
)
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.phases import TaskPhase
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status, enqueue
from parishkit.stewardship.web.content import text_html
from parishkit.stewardship.web.refusals import UserFacingStale, UserFacingUnavailable

from .report_email_cohort import still_recipient
from .report_email_models import ReportEmailTest

TASK_TYPE = "report_email_test"
PURPOSE = "admin_report_test"
# A test's task is retried this many times before it fails (as an
# Administrator notice's preparation is); a held test does not spend them.
MAX_ATTEMPTS = 5
HELD_RETRY_SECONDS = 30
# The campaign states in which each mode sends report emails, and so a
# test: as report_email_ownership.report_held and the database's
# stewardship_report_email_test_live_v1 (frozen file 0173) decide. An
# archived campaign offers no Send a test to me.
SENDING = {
    "production": frozenset({"scheduled", "active", "closed"}),
    "testing": frozenset({"draft"}),
}
PENDING = _("A test of this report is already on its way to you.")
PENDING_FIX = _(
    "Wait for it to arrive, then send another. If Sent mail shows its "
    "delivery as unknown, record what happened there first."
)
PAUSED = _("Mail is paused, so the test was not sent.")
PAUSED_FIX = _("Send it after Resume.")
NOT_SENDING = _("This campaign is not sending report emails now, so no test was sent.")


def label_line(mode, address, testing_recipient):
    """The test's first line, which says it is a test (spec, "What it sends")."""
    if mode == "testing":
        return (
            f"TEST — sent to {testing_recipient} instead of you ({address}). "
            "It is a test of this report email, not a send of the report."
        )
    return (
        "TEST — a test of this report email, sent only to you. "
        "It is not a send of the report."
    )


def label_render(row, *, configuration_id, email):
    """The test's email to its Administrator, routed as its mode requires.

    "[TEST] " before the kept subject and the first line before the kept
    email; in Testing it goes to the Testing recipient instead. The
    database's ``stewardship_report_email_test_render_v1`` checks the same.
    """
    line = label_line(row.mode, row.address, row.testing_recipient)
    return ReportRenderInput(
        configuration_id=configuration_id,
        template_id=None,
        sender=email.settings["sender"],
        reply_to=email.settings["reply_to"],
        intended_recipients=(row.address,),
        routed_recipients=(row.testing_recipient or row.address,),
        subject="[TEST] " + row.subject,
        # text_html matches sanitizer output, so the delivery check passes.
        html="<h2>TEST</h2><p>" + text_html(line) + "</p>" + row.html,
        text=line + "\n\n" + row.text,
    )


def images_of(row):
    """The test's kept images, ``(content id, PNG bytes)``, as compiled."""
    return tuple(
        (name, base64.b64decode(data, validate=True)) for name, data in row.images
    )


def sends(mode, state):
    """Whether a campaign in ``state`` sends report emails (and tests) in ``mode``."""
    return state in SENDING.get(mode, ())


def pending(actor_id, definition_id):
    """Whether this Administrator has a queued test of this report (or form)."""
    return ReportEmailTest.objects.filter(
        requested_by_id=actor_id, definition_id=definition_id, state="queued"
    ).exists()


def _replayed(request_id, actor_id, definition_id):
    """The test an earlier call with this request key made, or None.

    Its bindings only: web may not read a test's address or email again.
    The key names one Administrator's test of one report.
    """
    previous = (
        ReportEmailTest.objects.only("id", "requested_by_id", "definition_id", "mode")
        .filter(pk=request_id)
        .first()
    )
    if previous is not None and (previous.requested_by_id, previous.definition_id) != (
        actor_id,
        definition_id,
    ):
        raise ValueError("The report email test key is already bound.")
    return previous


def _refused(error):
    """Whether the database refused the test (its guard or one-pending index)."""
    return isinstance(error, IntegrityError) and getattr(
        error.__cause__, "sqlstate", None
    ) in {"23514", "23505"}


def request_test(actor_id, values, *, definition_id, request_id=None, scope=None):
    """Draw ``values`` now and queue one test of it to its Administrator.

    ``definition_id`` is the saved report's id (None for an unsaved New
    form); ``request_id`` the request's id (the command line's request key;
    a new one otherwise). ``scope`` is the caller's command scope (a context
    manager that rechecks the session around the write), or None. Returns
    ``(row, preview, created)``; a replayed ``request_id`` returns its
    test with ``preview`` None. Raises the Preview refusals
    (``UserFacingMissing``), ``UserFacingStale`` while a test of the report
    is pending and ``UserFacingUnavailable`` while Production mail is paused
    or the campaign has ended. Nothing is written before the compile.
    """
    from contextlib import nullcontext

    from .export_services import admit_campaign
    from .report_email_preview_views import draw

    if not isinstance(actor_id, UUID) or not (
        definition_id is None or isinstance(definition_id, UUID)
    ):
        raise ValueError("A report email test requires canonical identities.")
    request_id = request_id or uuid4()
    previous = _replayed(request_id, actor_id, definition_id)
    if previous is not None:
        return previous, None, False
    with read_transaction():
        system = SystemConfiguration.objects.select_related(
            "active_configuration__parish", "current_campaign__active_configuration"
        ).get()
        campaign = system.current_campaign
        if campaign is None:
            raise LookupError("There is no current campaign.")
        admit_campaign(campaign.pk, mutating=False)
        preview = draw(values, system, campaign)
    try:
        with scope or nullcontext(), work_transaction():
            row = _queue(actor_id, definition_id, request_id, preview, campaign.pk)
    except (IntegrityError, UserFacingStale) as error:
        # A concurrent call with the same request key made it first (its
        # test is pending under the lock, or its key is taken): this call is
        # that test's replay, not a second test.
        previous = _replayed(request_id, actor_id, definition_id)
        if previous is not None:
            return previous, None, False
        if isinstance(error, UserFacingStale):
            raise
        if _refused(error) and pending(actor_id, definition_id):
            raise UserFacingStale(
                PENDING, fix=PENDING_FIX, reason="test_pending"
            ) from None
        raise
    return row, preview, True


def _queue(actor_id, definition_id, request_id, preview, campaign_id):
    """Record the drawn test and its task, audited, under the work lock.

    Rechecks what the compile ran outside the lock: the same campaign and
    mode, a campaign that sends in that mode (``SENDING``), Production mail
    not paused, and no pending test of the report. The guard and the unique
    index repeat it.
    """
    require_work_order()
    system = SystemConfiguration.objects.get()
    campaign = Campaign.objects.get(pk=campaign_id)
    if system.current_campaign_id != campaign_id or system.mode != preview.mode:
        raise UserFacingStale(
            _("The campaign or mode changed while the test was drawn."),
            fix=_("Nothing was sent. Try again."),
        )
    if not sends(system.mode, campaign.state):
        raise UserFacingUnavailable(NOT_SENDING)
    if system.mode == "production" and campaign.delivery_paused:
        raise UserFacingUnavailable(PAUSED, fix=PAUSED_FIX)
    if pending(actor_id, definition_id):
        raise UserFacingStale(PENDING, fix=PENDING_FIX, reason="test_pending")
    address = PortalUser.objects.values_list("email", flat=True).get(pk=actor_id)
    task = enqueue(
        task_type=TASK_TYPE,
        domain_request_id=request_id,
        actor_id=actor_id,
        correlation_id=request_id,
        idempotency_key=request_id,
        admit=lambda *args: True,
    )
    row = ReportEmailTest.objects.create(
        id=request_id,
        campaign_id=campaign_id,
        configuration_id=system.active_configuration_id,
        definition_id=definition_id,
        requested_by_id=actor_id,
        mode=system.mode,
        testing_recipient=system.testing_recipient
        if system.mode == "testing"
        else None,
        address=address.lower(),
        subject=preview.subject,
        html=preview.html,
        text=preview.text,
        images=[
            [image.content_id, base64.b64encode(image.data).decode("ascii")]
            for image in preview.images
        ],
        task_id=task.run_id,
        actor_id=actor_id,
        correlation_id=request_id,
    )
    record_action(
        Action.REPORT_EMAIL_TEST_SENT,
        actor_kind=ActorKind.PORTAL_USER,
        actor_id=actor_id,
        subject_id=definition_id or campaign_id,
        parish_id=system.active_configuration.parish.pk,
        campaign_id=campaign_id,
        # The mode says where it went: the Administrator, or (Testing) the
        # Testing recipient; never the address or the email.
        context={"outcome": Outcome.SUCCEEDED, "report_mode": system.mode},
    )
    return row


class TestHeld(FamilyDeliveryHeld):
    """A test that waits for a temporary gate (a restore, go-live or work gate)."""

    __test__ = False  # Not a pytest test class, despite its name.


# The test's columns the scheduler's admission reads (never the address or
# the email, which its login may not read).
BINDING = ("id", "campaign_id", "mode", "testing_recipient", "state", "task_id")


def bound_test(status, *, full=False):
    """The task's test: its bindings only, or (``full``, the worker) all of it."""
    require_work_order()
    if status.task_type != TASK_TYPE:
        raise PermissionError("Report email test task type differs.")
    rows = ReportEmailTest.objects if full else ReportEmailTest.objects.only(*BINDING)
    return rows.get(pk=status.domain_request_id, task_id=status.root_id)


def has_message(row):
    """Whether the task has made the test's message.

    By the message's metadata columns only, which the scheduler may read (a
    test's scope is its campaign).
    """
    return OutboxMessage.objects.filter(
        campaign_id=row.campaign_id, semantic_key=row.pk, purpose=PURPOSE
    ).exists()


def scope_lost(row, *, address=True):
    """Whether the test can never be sent: its campaign, mode or sender changed.

    The current campaign or mode is another, the Testing recipient changed,
    the campaign does not send in the test's mode (``SENDING``), or
    (``address``: for a caller that may read it) the address is no longer
    an Administrator's. Each cancels the unsent test; none comes back.
    """
    runtime = SystemConfiguration.objects.get()
    testing = runtime.testing_recipient if runtime.mode == "testing" else None
    return (
        row.state != "queued"
        or runtime.current_campaign_id != row.campaign_id
        or runtime.mode != row.mode
        or testing != row.testing_recipient
        or not sends(
            row.mode,
            Campaign.objects.values_list("state", flat=True).get(pk=row.campaign_id),
        )
        or (
            address
            and not still_recipient(
                None,
                {"address": row.address, "kind": "group", "ref": "administrators"},
            )
        )
    )


def held(row):
    """Whether a temporary gate holds the test: a restore, go-live or work gate."""
    runtime = SystemConfiguration.objects.get()
    return (
        runtime.restore_review_required
        or CampaignCredentialState.objects.filter(
            campaign_id=row.campaign_id, go_live_gate=True
        ).exists()
        or CampaignWorkGate.objects.filter(campaign_id=row.campaign_id)
        .exclude(state="released")
        .exists()
    )


def disposition(row):
    """``complete`` once its message exists, ``safe_cancel`` once lost, else None.

    The address is rechecked only on a row loaded whole (the worker's); the
    scheduler's admission reads bindings only, and the worker's effect and
    the SQL write gate repeat the check.
    """
    require_work_order()
    if has_message(row):
        return "complete"
    if scope_lost(row, address="address" not in row.get_deferred_fields()):
        return "safe_cancel"
    return None


def make_message(claim):
    """Make the test's one outbox message from its kept email, under its claim."""
    require_work_order()
    row = bound_test(_status(lock_task_claim(claim)), full=True)
    if disposition(row) is not None:
        raise PermissionError("Report email test is not currently admitted.")
    if held(row):
        raise TestHeld("Report email test awaits current campaign authority.")
    runtime = SystemConfiguration.objects.get()
    email = AppliedIntegration.objects.get(
        configuration_id=runtime.active_configuration_id, kind="email"
    )
    identity = DeliveryIdentity(
        scope_id=row.campaign_id,
        campaign_id=row.campaign_id,
        semantic_key=row.pk,
        mode=row.mode,
        routing="testing_override" if row.mode == "testing" else "production",
        purpose=PURPOSE,
    )

    def admit(action, candidate, status, *, expected=identity):
        """Only this current claim may allocate this exact test delivery."""
        lock_task_claim(claim)
        return action in {"create", "create_task"} and candidate == expected

    return create_message(
        identity=identity,
        render=label_render(
            row, configuration_id=runtime.active_configuration_id, email=email
        ),
        actor_id=claim.worker_id,
        correlation_id=claim.run_id,
        command_id=row.pk,
        admit=admit,
    )


def recover_test(status):
    """An abandoned test is complete with its message; otherwise retried or failed."""
    row = bound_test(status)
    if status.state != "abandoned":
        return None
    outcome = disposition(row)
    if outcome is not None:
        return RecoveryPlan(
            "recovery_complete" if outcome == "complete" else "recovery_cancel"
        )
    if preparation_attempts(status) >= MAX_ATTEMPTS:
        return RecoveryPlan("recovery_fail")
    return RecoveryPlan("recovery_retry", HELD_RETRY_SECONDS)


def admit_test(action, status):
    """Every effect rechecks the test; expired ownership may always be fenced.

    Only the worker completes or cancels a test, so only then is the whole
    row (its address) read; the scheduler's hints read its bindings.
    """
    row = bound_test(status, full=action in {"complete", "safe_cancel"})
    if action in {"lease_expired", "heartbeat", "progress", "retryable_failure"}:
        return True
    if action == "recovery_hint":
        return status.state == "running" or recover_test(status) is not None
    if action.startswith("recovery_"):
        plan = recover_test(status)
        return plan is not None and plan.action == action
    outcome = disposition(row)
    if action in {"complete", "safe_cancel"}:
        return outcome == action
    if action in {"hint", "claim", "effect"}:
        return True
    return outcome is None and action == "permanent_failure"


def _execute(execution):
    """Make the message, or cancel a lost test; a held test waits."""
    try:
        with execution.effect():
            row = bound_test(_status(lock_task_claim(execution.claim)), full=True)
            outcome = disposition(row)
            if outcome is None:
                make_message(execution.claim)
                outcome = "complete"
        execution.transition(outcome)
    except TestHeld:
        # A temporary gate is an ordinary held retry, not a failed attempt.
        execution.progress(0, 0, phase=TaskPhase.RECONCILING)
        execution.transition("retryable_failure", retry_seconds=HELD_RETRY_SECONDS)


def report_email_test_handler(*, scheduler=False):
    """Scheduler admission reads bindings only; only the worker makes the message."""
    if type(scheduler) is not bool:
        raise TypeError("Report email tests require a compiled service role.")

    def unavailable(execution):
        """Reading a test's bindings confers no mail preparation authority."""
        raise PermissionError("The scheduler cannot prepare report email tests.")

    return Handler(
        WorkQueue.GENERAL,
        admit_test,
        unavailable if scheduler else _execute,
        recover=recover_test,
        scope=work_transaction,
    )


def task_of(row):
    """The test's task run, for a caller that reports it."""
    return _status(TaskRun.objects.get(pk=row.task_id))

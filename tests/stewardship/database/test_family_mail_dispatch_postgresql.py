"""Restricted real outbox dispatch transactions with synthetic provider outcomes."""

from uuid import uuid4

import pytest

from parishkit.stewardship.campaigns.schedule_models import (
    ScheduleDefinition,
    ScheduleFulfillment,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs.dispatch import Handler, claim_hint
from parishkit.stewardship.jobs.family_mail_dispatch import (
    TASK_TYPE,
    begin_submission,
    bound_dispatch,
    finish_submission,
)
from parishkit.stewardship.jobs.family_mail_tasks import TASK_TYPE as PREPARE
from parishkit.stewardship.jobs.lifetime import maintain_execution
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.queues import WorkQueue

from .campaign_builders import campaign_clock, complete_empty_catchup
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_family_mail_preparation_postgresql import (  # noqa: F401
    allocate,
    family_mail,
    handler,
)

pytestmark = pytest.mark.django_db(transaction=True)


def prepare(harness):
    """Start with the actual restricted preparation producer/consumer receipt."""
    from parishkit.stewardship.campaigns.credential_keys import (
        initialize_key_inventories,
    )

    initialize_key_inventories(harness.rings.private)
    ticket = allocate()
    owner = handler(harness)
    with task_login(ServiceRole.WORKER, exact=True):
        execution = claim_hint(
            ticket.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={PREPARE: owner},
        )
        with maintain_execution(execution):
            owner.execute(execution)
    return OutboxMessage.objects.get()


def claim(message):
    """Exercise storage ownership independently of external Workspace credentials."""

    def admitted(action, status):
        bound_dispatch(status)
        return True

    owner = Handler(
        WorkQueue.MAIL, admitted, lambda execution: None, scope=work_transaction
    )
    return claim_hint(
        message.task_id,
        queue=WorkQueue.MAIL,
        worker_id=uuid4(),
        handlers={TASK_TYPE: owner},
    )


@pytest.mark.parametrize("production", [False, True])
@pytest.mark.parametrize(
    "status", [Status.ACCEPTED, Status.TRANSIENT, Status.PERMANENT, Status.UNKNOWN]
)
def test_restricted_dispatch_preserves_outcome_and_scrubs_only_terminal(
    family_mail,  # noqa: F811
    production,
    status,  # noqa: F811
):
    """Exactly one attempt follows final current-source/credential checks."""
    if production:
        family_mail = activate_response_service(family_mail)
        complete_empty_catchup(family_mail.campaign, uuid4())
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(family_mail)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(message)
            mail, deadline, configuration, attempt = begin_submission(
                message.pk,
                execution.claim,
                private=family_mail.rings.private,
                public_origin="http://localhost:8000",
            )
            assert mail.recipients == (
                ("valid@example.org",) if production else ("test@example.org",)
            )
            assert family_mail.code in mail.text and "/access/" in mail.text
            assert ("/access/test." in mail.text) is not production
            assert attempt == 1
            result = finish_submission(
                message.pk, execution.claim, FamilyDeliveryResult(status, 1)
            )
        message.refresh_from_db()
        occurrence = ScheduleOccurrence.objects.get(pk=message.semantic_key)
        assert result.state.value == message.state
        assert (message.sealed_substitutions is None) is (
            status in {Status.ACCEPTED, Status.PERMANENT}
        )
        assert (
            occurrence.state
            == {
                Status.ACCEPTED: "succeeded",
                Status.TRANSIENT: "pending",
                Status.PERMANENT: "failed",
                Status.UNKNOWN: "delivery_unknown",
            }[status]
        )
        assert ScheduleFulfillment.objects.filter(disposition="delivered").exists() is (
            status is Status.ACCEPTED
        )


def test_abandoned_submission_records_unknown_and_does_not_resend(
    family_mail,  # noqa: F811
    monkeypatch,  # noqa: F811
):
    """Recovery drains expired ownership before classifying a missing receipt."""
    from pathlib import Path
    from threading import Event

    from parishkit.stewardship.jobs.dispatch import recover_hint
    from parishkit.stewardship.jobs.family_mail_delivery_tasks import delivery_handler
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.storage import _status

    from .test_taskrun_postgresql import act, expire

    monkeypatch.setattr(
        "parishkit.stewardship.jobs.family_mail_dispatch.PROVIDER_SECONDS", 1
    )
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(family_mail)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(message)
            begin_submission(
                message.pk,
                execution.claim,
                private=family_mail.rings.private,
                public_origin="http://localhost:8000",
            )
        running = act(
            _status(TaskRun.objects.get(pk=message.task_id)),
            "heartbeat",
            lease_seconds=1,
        )
        expire(running)
        Event().wait(0.05)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            owner = delivery_handler(None, credential_path=Path("/unused"))
            assert recover_hint(
                message.task_id,
                queue=WorkQueue.MAIL,
                worker_id=uuid4(),
                handlers={TASK_TYPE: owner},
            )
        message.refresh_from_db()
        assert (
            message.state == "delivery_unknown"
            and message.sealed_substitutions is not None
        )
        assert TaskRun.objects.get(pk=message.task_id).state == "failed"
        assert (
            ScheduleOccurrence.objects.get(pk=message.semantic_key).state
            == "delivery_unknown"
        )
        assert not ScheduleFulfillment.objects.exists()


@pytest.mark.parametrize("submitted", [False, True])
def test_pause_holds_new_send_but_preserves_inflight_acceptance(family_mail, submitted):  # noqa: F811
    """A pause cannot erase truthful acceptance or silently cancel unsent work."""
    from .test_outbox_boundaries_postgresql import control

    harness = activate_response_service(family_mail)
    complete_empty_catchup(harness.campaign, uuid4())
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(message)
            if submitted:
                begin_submission(
                    message.pk,
                    execution.claim,
                    private=harness.rings.private,
                    public_origin="http://localhost:8000",
                )
        control(harness.campaign, "pause")
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            if submitted:
                finish_submission(
                    message.pk,
                    execution.claim,
                    FamilyDeliveryResult(Status.ACCEPTED, 1),
                )
            else:
                assert (
                    begin_submission(
                        message.pk,
                        execution.claim,
                        private=harness.rings.private,
                        public_origin="http://localhost:8000",
                    )
                    is None
                )
        message.refresh_from_db()
        if submitted:
            assert message.state == "delivered" and message.sealed_substitutions is None
        else:
            assert message.state == "pending" and message.pause_hold_id is not None
            control(harness.campaign, "resume")
            with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
                assert (
                    begin_submission(
                        message.pk,
                        execution.claim,
                        private=harness.rings.private,
                        public_origin="http://localhost:8000",
                    )
                    is not None
                )
            message.refresh_from_db()
            assert message.state == "submitting" and message.pause_hold_id is None


def test_refreshed_source_changes_envelope_before_send(family_mail):  # noqa: F811
    """A prepared stale address is not copied into a later provider attempt."""
    from .response_builders import response_source
    from .test_recipient_suppressions_postgresql import refresh

    harness = activate_response_service(family_mail)
    complete_empty_catchup(harness.campaign, uuid4())
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        original_render = message.render_id
        changed = response_source()
        changed.members[3]["emailAddress"] = "corrected@example.org"
        refresh(harness, changed)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(message)
            mail, *_ = begin_submission(
                message.pk,
                execution.claim,
                private=harness.rings.private,
                public_origin="http://localhost:8000",
            )
            assert mail.recipients == ("corrected@example.org",)
        message.refresh_from_db()
        assert message.render_id != original_render
        assert message.renders.get(pk=original_render).routed_recipients == [
            "valid@example.org"
        ]


def test_scheduler_hints_are_metadata_only(family_mail, monkeypatch):  # noqa: F811
    """The installed scheduler can route work without recipients or ciphertext."""
    from parishkit.stewardship.jobs.family_mail_delivery_tasks import delivery_handler
    from parishkit.stewardship.jobs.scanning import collect_hints

    monkeypatch.setattr(
        "parishkit.stewardship.jobs.family_mail_delivery_tasks.mail_authority",
        lambda store: None,
    )
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(family_mail)
        with task_login(ServiceRole.SCHEDULER, exact=True):
            hints, _ = collect_hints(
                handlers={TASK_TYPE: delivery_handler(None, scheduler=True)}
            )
        assert [hint.run_id for hint in hints] == [message.task_id]


@pytest.mark.parametrize("production", [False, True])
def test_response_after_preparation_cancels_without_decrypting(
    family_mail,  # noqa: F811
    monkeypatch,
    production,  # noqa: F811
):  # noqa: F811
    """Submit racing an already-rendered invitation is checked at actual dispatch."""
    from .test_response_submission_postgresql import form_and_answers, submit

    harness = family_mail
    if production:
        harness = activate_response_service(harness)
        complete_empty_catchup(harness.campaign, uuid4())
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        form, answers = form_and_answers(harness)
        answers["testing_acknowledged"] = not production
        assert submit(harness, form, answers).submission is not None

        def forbidden(*args, **kwargs):
            raise AssertionError("Responded Family credentials were decrypted")

        monkeypatch.setattr(harness.rings.private, "decrypt", forbidden)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(message)
            assert (
                begin_submission(
                    message.pk,
                    execution.claim,
                    private=harness.rings.private,
                    public_origin="http://localhost:8000",
                )
                is None
            )
        message.refresh_from_db()
        assert message.state == "cancelled" and message.sealed_substitutions is None
        assert (
            ScheduleOccurrence.objects.get(pk=message.semantic_key).reason
            == "family_responded"
        )


def test_dispatch_rechecks_newly_overdue_reminders_as_complete_group(family_mail):  # noqa: F811
    """Preparation time cannot freeze missed-work selection until a later send."""
    from uuid import UUID

    from .test_family_schedule_planning_postgresql import add_reminders

    reminders = add_reminders(family_mail.service.store, family_mail.campaign, uuid4())
    with campaign_clock(
        ScheduleDefinition.objects.get(kind="initial").current_revision.due_at
    ):
        message = prepare(family_mail)
    cutoff = ScheduleDefinition.objects.get(
        pk=UUID(reminders[1]["id"])
    ).current_revision.due_at
    with campaign_clock(cutoff), task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message)
        assert (
            begin_submission(
                message.pk,
                execution.claim,
                private=family_mail.rings.private,
                public_origin="http://localhost:8000",
            )
            is not None
        )
    assert ScheduleOccurrence.objects.filter(state="coalesced").count() == 2
    assert ScheduleOccurrence.objects.filter(state="running").count() == 1
    assert not ScheduleOccurrence.objects.filter(
        definition_id=UUID(reminders[2]["id"])
    ).exists()


def test_observed_acceptance_survives_campaign_end(family_mail):  # noqa: F811
    """An admission boundary closing during DATA cannot erase actual delivery."""
    harness = activate_response_service(family_mail)
    complete_empty_catchup(harness.campaign, uuid4())
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(message)
            begin_submission(
                message.pk,
                execution.claim,
                private=harness.rings.private,
                public_origin="http://localhost:8000",
            )
    harness.campaign.refresh_from_db()
    with (
        campaign_clock(harness.campaign.active_configuration.ends_at),
        task_login(ServiceRole.MAIL_DISPATCH, exact=True),
    ):
        finish_submission(
            message.pk, execution.claim, FamilyDeliveryResult(Status.ACCEPTED, 1)
        )
    message.refresh_from_db()
    assert message.state == "delivered"
    assert ScheduleFulfillment.objects.get().disposition == "delivered"

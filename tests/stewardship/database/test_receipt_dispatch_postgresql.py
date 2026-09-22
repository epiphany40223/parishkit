"""Receipt-specific ownership uses the shared commit-before-SMTP journal."""

from uuid import uuid4

import pytest
from django.db import IntegrityError, ProgrammingError, connection, transaction

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs.family_mail_dispatch import (
    begin_submission,
    finish_submission,
)
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.responses.models import SubmissionReceiptOccurrence

from .campaign_builders import campaign_clock, close_campaign
from .test_background_grants_postgresql import task_login
from .test_family_mail_dispatch_postgresql import claim
from .test_outbox_boundaries_postgresql import control
from .test_response_submission_postgresql import form_and_answers, submit

pytestmark = pytest.mark.django_db(transaction=True)


def receipt(harness, *, production=False):
    """Allocate through the actual final Submit owner, never a synthetic outbox."""
    form, answers = form_and_answers(harness)
    answers["testing_acknowledged"] = not production
    row = submit(harness, form, answers).submission
    return OutboxMessage.objects.get(
        pk=SubmissionReceiptOccurrence.objects.get(submission=row).outbox_id
    )


def begin(message, execution):
    """A receipt needs no Family credential key, even in the private MAIL role."""
    return begin_submission(
        message.pk,
        execution.claim,
        private=None,
        public_origin="https://parish.example.org",
    )


@pytest.mark.parametrize(
    "subject,html",
    [
        ("PARISHKIT_PENDING_", "RECEIPT"),
        ("PARISHKIT_REDACTED_", "FAMILY_CODE"),
        ("PARISHKIT PENDING RECEIPT", "Confirmation"),
        ("PARISHKIT-PENDING-RECEIPT", "Confirmation"),
        ("PARISHKIT REDACTED FAMILY CODE", "Confirmation"),
    ],
)
def test_valid_authored_content_passes_exact_per_field_sql_guard(
    live_response_service, subject, html
):
    """Real configuration and MAIL agree; neither joins MIME fields nor uses LIKE."""
    from ..content_factory import content
    from .campaign_builders import change

    harness = live_response_service
    template = content(
        str(harness.campaign.pk),
        kind="email",
        slot="confirmation",
        subject=subject,
        html=html,
        text="Your response was received.",
    )
    store = harness.service.store
    result = change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "add", "section": "content", **template}],
    )
    assert result.state == "applied"
    message = receipt(harness, production=True)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message)
        mail, *_ = begin(message, execution)
        assert mail.subject == subject and mail.html.startswith(html)
        finish_submission(
            message.pk, execution.claim, FamilyDeliveryResult(Status.ACCEPTED, 1)
        )
    message.refresh_from_db()
    assert message.state == "delivered"


@pytest.mark.parametrize("fixture", ["response_service", "live_response_service"])
@pytest.mark.parametrize(
    "status", [Status.ACCEPTED, Status.TRANSIENT, Status.PERMANENT, Status.UNKNOWN]
)
def test_receipt_real_mail_role_settles_exact_attempt_without_answers_or_keys(
    request, fixture, status
):
    """The shared journal receives truthful outcomes without fabricated occurrences."""
    production = fixture == "live_response_service"
    harness = request.getfixturevalue(fixture)
    message = receipt(harness, production=production)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message)
        mail, deadline, configuration, attempt = begin(message, execution)
        assert mail.recipients == (
            ("valid@example.org",) if production else ("test@example.org",)
        )
        assert harness.code not in mail.text and "/access/" not in mail.text
        assert "Submitted:" in mail.text and attempt == 1
        with (
            pytest.raises(ProgrammingError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute("SELECT answers FROM stewardship_submission")
        outcome = finish_submission(
            message.pk, execution.claim, FamilyDeliveryResult(status, 1)
        )
    message.refresh_from_db()
    assert message.state == outcome.state.value
    assert message.sealed_substitutions is None
    assert (
        message.state
        == {
            Status.ACCEPTED: "delivered",
            Status.TRANSIENT: "retry_wait",
            Status.PERMANENT: "permanent_failure",
            Status.UNKNOWN: "delivery_unknown",
        }[status]
    )


@pytest.mark.parametrize("persist_closed", [False, True])
def test_receipt_delivery_remains_admitted_after_end_date(
    live_response_service, persist_closed
):
    """Closing Family access does not cancel an accepted receipt obligation."""
    message = receipt(live_response_service, production=True)
    if persist_closed:
        close_campaign(live_response_service.campaign, uuid4())
        assert live_response_service.campaign.state == "closed"
    with (
        campaign_clock(live_response_service.campaign.active_configuration.ends_at),
        task_login(ServiceRole.MAIL_DISPATCH, exact=True),
    ):
        execution = claim(message)
        assert begin(message, execution) is not None
        finish_submission(
            message.pk, execution.claim, FamilyDeliveryResult(Status.ACCEPTED, 1)
        )
    message.refresh_from_db()
    assert message.state == "delivered"


def test_obsolete_testing_receipt_is_cancelled_without_provider_submission(
    response_service,
):
    """A retained response cannot authorize mail from an invalidated rehearsal."""
    from parishkit.stewardship.campaigns.rehearsals import invalidate_rehearsal

    message = receipt(response_service)
    invalidate_rehearsal(
        campaign_id=response_service.campaign.pk, admit=lambda *args: True
    )
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message)
        assert begin(message, execution) is None
    message.refresh_from_db()
    assert message.state == "cancelled" and message.attempt == 0


def test_held_receipt_resumes_without_coalescing(live_response_service):
    """A pause hold can be created at Submit, observed by MAIL and released later."""
    harness = live_response_service
    control(harness.campaign, "pause")
    message = receipt(harness, production=True)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message)
        assert begin(message, execution) is None
    control(harness.campaign, "resume")
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        assert begin(message, execution) is not None
        finish_submission(
            message.pk, execution.claim, FamilyDeliveryResult(Status.ACCEPTED, 1)
        )
    message.refresh_from_db()
    assert message.pause_hold_id is None and message.state == "delivered"


def test_paused_receipt_resend_is_held_until_resume(live_response_service):
    """An unknown receipt can be resent while paused, so the pause can resume."""
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.storage import _status

    from .test_delivery_resolution_postgresql import resolve
    from .test_policy_postgresql import user
    from .test_taskrun_postgresql import act

    harness = live_response_service
    message = receipt(harness, production=True)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message)
        begin(message, execution)
        finish_submission(
            message.pk, execution.claim, FamilyDeliveryResult(Status.UNKNOWN, 1)
        )
    act(_status(TaskRun.objects.get(pk=message.task_id)), "permanent_failure")
    control(harness.campaign, "pause")
    message.refresh_from_db()
    result = resolve(
        harness,
        user("admin@example.org"),
        message,
        "resend",
        general=None,
        public=None,
    )
    message.refresh_from_db()
    assert message.state == "pending" and message.pause_hold_id is not None
    retry = OutboxMessage(pk=message.pk, task_id=result.retry_task_id)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(retry)
        assert begin(message, execution) is None
    control(harness.campaign, "resume")
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        assert begin(message, execution) is not None


def test_seed_render_cannot_be_submitted_by_the_actual_mail_role(response_service):
    """A compromised caller cannot send the database-owned allocation placeholder."""
    from parishkit.stewardship.campaigns.work_locks import work_transaction
    from parishkit.stewardship.jobs.delivery_states import DeliveryAction
    from parishkit.stewardship.jobs.outbox_storage import change_message

    message = receipt(response_service)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message)
        with (
            pytest.raises(IntegrityError, match="render differs"),
            work_transaction(),
        ):
            change_message(
                message_id=message.pk,
                action=DeliveryAction.SUBMIT,
                command_id=uuid4(),
                expected_version=message.version,
                actor_id=execution.claim.worker_id,
                correlation_id=execution.claim.run_id,
                run_id=execution.claim.run_id,
                task_fence=execution.claim.fence,
                provider_seconds=30,
                admit=lambda *args: True,
            )
    message.refresh_from_db()
    assert message.state == "pending" and message.attempt == 0


def test_pending_receipt_blocks_archive_until_accepted(live_response_service):
    """Archive cannot abandon an accepted response's undelivered obligation."""
    from parishkit.stewardship.campaigns.lifecycle import Action

    from .campaign_builders import command

    harness = live_response_service
    message = receipt(harness, production=True)
    close_campaign(harness.campaign, uuid4())
    with pytest.raises(IntegrityError, match="resolved submission confirmations"):
        command(harness.campaign, uuid4(), Action.ARCHIVE)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message)
        assert begin(message, execution) is not None
        finish_submission(
            message.pk, execution.claim, FamilyDeliveryResult(Status.ACCEPTED, 1)
        )
    command(harness.campaign, uuid4(), Action.ARCHIVE)
    harness.campaign.refresh_from_db()
    assert harness.campaign.state == "archived"


@pytest.mark.parametrize("status", [Status.TRANSIENT, Status.PERMANENT, Status.UNKNOWN])
def test_unresolved_provider_result_still_blocks_archive(live_response_service, status):
    """A provider attempt is not evidence that the receipt obligation was resolved."""
    from parishkit.stewardship.campaigns.lifecycle import Action

    from .campaign_builders import command

    harness = live_response_service
    message = receipt(harness, production=True)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message)
        assert begin(message, execution) is not None
        finish_submission(message.pk, execution.claim, FamilyDeliveryResult(status, 1))
    close_campaign(harness.campaign, uuid4())
    with pytest.raises(IntegrityError, match="resolved submission confirmations"):
        command(harness.campaign, uuid4(), Action.ARCHIVE)


def test_no_recipient_submission_does_not_prevent_archive(live_response_service):
    """The audited Submit-time skip already resolves its confirmation obligation."""
    from parishkit.stewardship.campaigns.lifecycle import Action

    from .campaign_builders import command
    from .response_builders import response_source
    from .test_recipient_suppressions_postgresql import refresh

    harness = live_response_service
    data = response_source()
    data.members[3]["emailAddress"] = ""
    refresh(harness, data)
    form, answers = form_and_answers(harness)
    answers["testing_acknowledged"] = False
    row = submit(harness, form, answers).submission
    occurrence = SubmissionReceiptOccurrence.objects.get(submission=row)
    assert occurrence.disposition == "no_deliverable_recipient"
    assert occurrence.outbox_id is None
    close_campaign(harness.campaign, uuid4())
    command(harness.campaign, uuid4(), Action.ARCHIVE)
    harness.campaign.refresh_from_db()
    assert harness.campaign.state == "archived"


@pytest.mark.parametrize("inactive", [False, True])
def test_source_correction_resumes_the_same_receipt(live_response_service, inactive):
    """Unavailable recipients are recoverable, not permission to discard a receipt."""
    from parishkit.stewardship.jobs.family_mail_dispatch import FamilyDeliveryHeld

    from .response_builders import response_source
    from .test_recipient_suppressions_postgresql import refresh

    harness = live_response_service
    message = receipt(harness, production=True)
    data = response_source()
    if inactive:
        data.members[3]["memberStatus"] = "Inactive"
    else:
        data.members[3]["emailAddress"] = ""
    refresh(harness, data)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message)
        with pytest.raises(FamilyDeliveryHeld, match="source recipients"):
            begin(message, execution)
    message.refresh_from_db()
    assert message.state == "pending" and message.attempt == 0
    refresh(harness, response_source())
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        assert begin(message, execution) is not None
        finish_submission(
            message.pk, execution.claim, FamilyDeliveryResult(Status.ACCEPTED, 1)
        )
    message.refresh_from_db()
    assert message.state == "delivered" and message.attempt == 1


@pytest.mark.parametrize(
    "action,status",
    [
        ("accept", Status.UNKNOWN),
        ("resend", Status.UNKNOWN),
        ("retry_failed", Status.PERMANENT),
        ("retry_unsent", None),
    ],
)
def test_admin_receipt_resolution_is_keyless_and_preserves_attempt_history(
    response_service, action, status
):
    """An Admin explicitly settles uncertainty or queues one linked retry."""
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.storage import _status

    from .test_delivery_resolution_postgresql import resolve
    from .test_policy_postgresql import user
    from .test_taskrun_postgresql import act

    message = receipt(response_service)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message)
        if status is not None:
            begin(message, execution)
            finish_submission(
                message.pk, execution.claim, FamilyDeliveryResult(status, 1)
            )
    act(_status(TaskRun.objects.get(pk=message.task_id)), "permanent_failure")
    message.refresh_from_db()
    previous_version = message.version
    result = resolve(
        response_service,
        user("admin@example.org"),
        message,
        action,
        general=None,
        public=None,
    )
    message.refresh_from_db()
    assert result.preparation is None and message.version > previous_version
    assert message.state == ("delivered" if action == "accept" else "pending")
    assert message.sealed_substitutions is None and message.token_generation_id is None
    assert TaskRun.objects.get(pk=message.task_id).state == "failed"
    if action == "accept":
        assert result.retry_task_id is None
    else:
        assert TaskRun.objects.get(pk=result.retry_task_id).state == "queued"


def test_admin_retry_cannot_inject_private_answer_text(response_service, monkeypatch):
    """The second Web allocation port rejects bodies too, not only final Submit."""
    from parishkit.stewardship.jobs import delivery_resolution
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.storage import _status

    from .test_delivery_resolution_postgresql import resolve
    from .test_policy_postgresql import user
    from .test_taskrun_postgresql import act

    message = receipt(response_service)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        claim(message)
    act(_status(TaskRun.objects.get(pk=message.task_id)), "permanent_failure")
    before = list(TaskRun.objects.values_list("pk", flat=True))
    monkeypatch.setattr(
        delivery_resolution,
        "_prepare_receipt",
        lambda *args: {"receipt": True, "render": {"text": "Private answer text"}},
    )
    with pytest.raises(IntegrityError, match="fresh scoped preparation"):
        resolve(
            response_service,
            user("admin@example.org"),
            message,
            "retry_unsent",
            general=None,
            public=None,
        )
    assert list(TaskRun.objects.values_list("pk", flat=True)) == before
    message.refresh_from_db()
    assert message.state == "pending" and message.version == 1

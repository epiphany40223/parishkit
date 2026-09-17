"""Receipt-specific ownership uses the shared commit-before-SMTP journal."""

from uuid import uuid4

import pytest
from django.db import ProgrammingError, connection, transaction

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

"""Submission receipts use real role grants and commit with the accepted response."""

from dataclasses import replace

import pytest
from django.db import IntegrityError, ProgrammingError, connection, transaction

from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.credential_models import FamilySession
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxEvent, OutboxMessage
from parishkit.stewardship.responses import receipts, submission
from parishkit.stewardship.responses.models import (
    Submission,
    SubmissionReceiptOccurrence,
)

from .response_builders import response_source
from .test_family_auth_postgresql import login
from .test_response_submission_postgresql import form_and_answers, submit
from .test_runtime_auth_grants_postgresql import web_login
from .test_source_families_postgresql import prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)


def test_paused_live_submit_creates_a_held_receipt_atomically(live_response_service):
    """The Family can submit during a delivery pause without minting a send hint."""
    from .test_outbox_boundaries_postgresql import control

    harness = live_response_service
    control(harness.campaign, "pause")
    with web_login():
        form, answers = form_and_answers(harness)
        answers["testing_acknowledged"] = False
        row = submit(harness, form, answers).submission
    receipt = SubmissionReceiptOccurrence.objects.get(submission=row)
    message = OutboxMessage.objects.get(pk=receipt.outbox_id)
    harness.campaign.refresh_from_db()
    assert message.state == "pending" and message.attempt == 0
    assert message.pause_hold.campaign_id == harness.campaign.pk
    assert message.pause_version == harness.campaign.pause_version
    assert message.pause_hold.pause_version == message.pause_version


def test_receipt_parent_requires_journaled_cleanup_even_after_invalidation(
    response_service,
):
    """The old response-only helper cannot orphan a retained delivery identity."""
    from parishkit.stewardship.campaigns.rehearsals import (
        cleanup_rehearsal,
        invalidate_rehearsal,
    )
    from parishkit.stewardship.campaigns.work_locks import work_transaction
    from parishkit.stewardship.storage import StorageInvariantError

    form, answers = form_and_answers(response_service)
    row = submit(response_service, form, answers).submission
    receipt = SubmissionReceiptOccurrence.objects.get(submission=row)
    epoch = invalidate_rehearsal(campaign_id=row.campaign_id, admit=lambda *args: True)
    with pytest.raises(StorageInvariantError, match="journaled"):
        cleanup_rehearsal(epoch)
    with (
        pytest.raises(IntegrityError, match="journaled cleanup"),
        work_transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "DELETE FROM stewardship_submission_receipt WHERE id=%s", [receipt.pk]
        )
    assert Submission.objects.filter(pk=row.pk).exists()
    assert OutboxMessage.objects.filter(pk=receipt.outbox_id).exists()


@pytest.mark.parametrize("fixture", ["response_service", "live_response_service"])
def test_receipt_is_concrete_private_answer_free_and_exactly_bound(request, fixture):
    """Every accepted version has one root/render/history, never a mail stub."""
    harness = request.getfixturevalue(fixture)
    with web_login():
        form, answers = form_and_answers(harness)
        answers["testing_acknowledged"] = fixture == "response_service"
        answers["members"]["3"]["first_name"] = "Private answer only"
        answers["family"]["email_opt_out"] = True
        answers["additional_information"] = "Private response text"
        row = submit(harness, form, answers).submission
        receipt = SubmissionReceiptOccurrence.objects.get(submission=row)
        assert receipt.disposition == "queued" and receipt.preparation is None
        # The command did not grant general access to other messages' secrets.
        with (
            pytest.raises(ProgrammingError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "SELECT sealed_substitutions FROM stewardship_outbox_message"
            )
    message = OutboxMessage.objects.select_related("render", "task").get(
        pk=receipt.outbox_id
    )
    assert message.purpose == "receipt" and message.state == "pending"
    assert message.semantic_key == row.pk and message.family_id == row.family_id
    assert message.campaign_id == row.campaign_id
    assert message.task.domain_request_id == message.pk
    assert message.task.root_id == message.task_id and message.task.state == "queued"
    assert message.sealed_substitutions is message.sealed_key_id is None
    assert message.credential_namespace == "none" and message.rehearsal_epoch_id is None
    assert message.token_generation_id is message.credential_epoch_id is None
    assert message.render.configuration_id == row.configuration_id
    assert message.render.intended_recipients == ["valid@example.org"]
    assert message.render.routed_recipients == (
        ["test@example.org"] if row.mode == "test" else ["valid@example.org"]
    )
    for body in (message.render.html, message.render.text):
        assert "PARISHKIT_PENDING_RECEIPT" in body
        assert "Private answer only" not in body and "Private response text" not in body
        assert harness.code not in body and "PARISHKIT_REDACTED" not in body
    event = OutboxEvent.objects.get(message=message)
    assert event.action == "created" and event.render_id == message.render_id


def test_each_resubmission_keeps_its_distinct_receipt(response_service):
    """A newer accepted response does not coalesce or discard earlier receipts."""
    harness = response_service
    for index in range(2):
        if index:
            client, response = login(harness.code)
            harness = replace(harness, client=client, request=response.wsgi_request)
        form, answers = form_and_answers(harness)
        submit(harness, form, answers)
    assert SubmissionReceiptOccurrence.objects.count() == 2
    assert OutboxMessage.objects.filter(purpose="receipt").count() == 2
    assert set(OutboxMessage.objects.values_list("semantic_key", flat=True)) == set(
        Submission.objects.values_list("pk", flat=True)
    )


def test_no_current_source_address_commits_skip_not_an_empty_outbox(response_service):
    """The decision is based on source heads, not proposed contact corrections."""
    data = response_source()
    data.members[3]["emailAddress"] = ""
    snapshot, claim = prepare(data)
    promote(snapshot, claim, response_service.campaign, response_service.rings)
    with web_login():
        form, answers = form_and_answers(response_service)
        answers["members"]["3"]["email"] = "proposed@example.org"
        row = submit(response_service, form, answers).submission
    receipt = SubmissionReceiptOccurrence.objects.get(submission=row)
    assert receipt.disposition == "no_deliverable_recipient"
    assert receipt.outbox_id is receipt.preparation is None
    assert not OutboxMessage.objects.exists()
    assert (
        AuditEvent.objects.filter(event_type="submission_receipt_skipped").count() == 1
    )
    assert AuditContext.objects.get(
        event__event_type="submission_receipt_skipped"
    ).context == {
        "reason": "no_deliverable_recipient",
        "recipient_count": 0,
    }


def test_failure_after_receipt_allocation_rolls_back_entire_submit(
    response_service, monkeypatch
):
    """No queued root/render survives a later local submission failure."""
    form, answers = form_and_answers(response_service)
    original = submission.create_submission_receipt

    def fail(*args, **kwargs):
        """Fail after real local creation, before session invalidation commits."""
        receipt = original(*args, **kwargs)
        assert receipt.outbox_id is not None
        raise RuntimeError("Synthetic receipt checkpoint failure")

    with monkeypatch.context() as patch:
        patch.setattr(submission, "create_submission_receipt", fail)
        with web_login(), pytest.raises(RuntimeError, match="Synthetic receipt"):
            submit(response_service, form, answers)
    assert not Submission.objects.exists()
    assert not OutboxMessage.objects.exists() and not OutboxEvent.objects.exists()
    assert not TaskRun.objects.filter(task_type="outbox_delivery").exists()
    form.baseline.refresh_from_db()
    assert form.baseline.state == "open"
    assert (
        FamilySession.objects.get(pk=form.baseline.family_session_id).revoked_at is None
    )
    assert submit(response_service, form, answers).submission is not None


@pytest.mark.parametrize(
    "mutation",
    ["intended", "routed", "sender", "configuration", "credential", "private_answer"],
)
def test_receipt_command_rejects_forged_preparation(
    response_service, monkeypatch, mutation
):
    """Even an answer-reading Web caller cannot pass arbitrary prose to MAIL."""
    manager = receipts.SubmissionReceiptOccurrence.objects
    original = manager.create

    def forged(*args, **kwargs):
        """Attempt to append untrusted mail input to the closed allocation command."""
        kwargs["preparation"][mutation] = "Private answer and pledge text"
        return original(*args, **kwargs)

    monkeypatch.setattr(manager, "create", forged)
    with web_login():
        form, answers = form_and_answers(response_service)
        with pytest.raises(IntegrityError, match="exact local preparation"):
            submit(response_service, form, answers)
    assert not Submission.objects.exists() and not OutboxMessage.objects.exists()


def test_submit_does_not_render_templates_or_require_worker_origin(
    response_service, monkeypatch, settings
):
    """Authored mail failure belongs to the worker, not the accepted response."""
    from parishkit.stewardship.jobs import receipt_rendering

    def unavailable(*args, **kwargs):
        """Model any template/rendering failure without suppressing Submit checks."""
        raise ValueError("Synthetic receipt rendering failure")

    monkeypatch.setattr(receipt_rendering, "render_receipt", unavailable)
    del settings.STEWARDSHIP_PUBLIC_ORIGIN
    with web_login():
        form, answers = form_and_answers(response_service)
        result = submit(response_service, form, answers)
    assert result.submission is not None
    assert OutboxMessage.objects.get().state == "pending"

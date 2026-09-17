"""Maintained receipt dispatch and uncertainty recovery with a fake provider."""

from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from django.db import connection

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs.dispatch import recover_hint
from parishkit.stewardship.jobs.family_mail_delivery_tasks import delivery_handler
from parishkit.stewardship.jobs.family_mail_dispatch import TASK_TYPE
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status

from .auth_builders import signed_in
from .response_builders import activate_response_service, response_source
from .test_background_grants_postgresql import task_login
from .test_family_mail_dispatch_postgresql import claim
from .test_family_mail_worker_postgresql import (  # noqa: F401
    deliver,
    dispatch_worker,
    family_mail,
)
from .test_receipt_dispatch_postgresql import begin, receipt
from .test_recipient_suppressions_postgresql import refresh
from .test_taskrun_postgresql import act, expire

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("status", list(Status))
def test_receipt_worker_commits_before_provider_without_opening_family_keys(
    dispatch_worker,  # noqa: F811
    monkeypatch,
    status,
):
    """Same SMTP runner and fenced task lifetime, but no credential substitutions."""
    harness, path = dispatch_worker
    message = receipt(harness)
    calls = []

    def forbidden(*args, **kwargs):
        """A receipt cannot depend on reading/decrypting the Family access token."""
        raise AssertionError("Receipt opened a Family key")

    def provider(value, settings, mail, *, seconds, check):
        """Observe committed submission intent before returning a fake result."""
        assert not connection.in_atomic_block and 0 < seconds <= 30
        check()
        assert OutboxMessage.objects.get(pk=message.pk).state == "submitting"
        assert harness.code not in mail.text and "Submitted:" in mail.text
        calls.append(mail.recipients)
        return FamilyDeliveryResult(status, len(mail.recipients))

    monkeypatch.setattr(harness.rings.private, "decrypt", forbidden)
    monkeypatch.setattr(
        "parishkit.stewardship.jobs.family_mail_delivery_tasks.submit_family", provider
    )
    deliver(harness, path, message)
    assert calls == [("test@example.org",)]
    assert (
        TaskRun.objects.get(pk=message.task_id).state
        == {
            Status.ACCEPTED: "succeeded",
            Status.TRANSIENT: "retry_wait",
            Status.UNAVAILABLE: "retry_wait",
            Status.PERMANENT: "failed",
            Status.UNKNOWN: "failed",
            Status.SYSTEMIC: "failed",
        }[status]
    )


def test_abandoned_receipt_becomes_unknown_not_automatically_resent(
    response_service, monkeypatch
):
    """A missing process acknowledgement is not proof the recipient saw no mail."""
    message = receipt(response_service)
    monkeypatch.setattr(
        "parishkit.stewardship.jobs.family_mail_dispatch.PROVIDER_SECONDS", 1
    )
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message)
        begin(message, execution)
    running = act(
        _status(TaskRun.objects.get(pk=message.task_id)), "heartbeat", lease_seconds=1
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
    assert message.state == "delivery_unknown" and message.attempt == 1
    assert TaskRun.objects.get(pk=message.task_id).state == "failed"


def test_render_failure_preserves_accepted_response_and_never_sends_seed(
    dispatch_worker,  # noqa: F811
    monkeypatch,
):
    """Worker rendering errors stay retryable without undoing the Family's Submit."""
    from parishkit.stewardship.responses.models import Submission

    harness, path = dispatch_worker
    message = receipt(harness)
    calls = []

    def fail(*args, **kwargs):
        """Simulate invalid combined authored content before the provider boundary."""
        raise ValueError("Synthetic template output limit")

    monkeypatch.setattr(
        "parishkit.stewardship.jobs.receipt_rendering.render_receipt", fail
    )
    monkeypatch.setattr(
        "parishkit.stewardship.jobs.family_mail_delivery_tasks.submit_family",
        lambda *args, **kwargs: calls.append(True),
    )
    deliver(harness, path, message)
    message.refresh_from_db()
    assert calls == [] and message.state == "pending" and message.attempt == 0
    assert Submission.objects.filter(pk=message.semantic_key).exists()
    assert TaskRun.objects.get(pk=message.task_id).state == "retry_wait"


def test_receipt_partial_refusal_retries_only_remaining_source_head(
    dispatch_worker,  # noqa: F811
    monkeypatch,
):
    """A known refused head does not erase a distinct accepted submission receipt."""
    harness, path = dispatch_worker
    harness = activate_response_service(harness)
    source = response_source()
    source.members[3]["emailAddress"] = "valid@example.org; zother@example.org"
    refresh(harness, source)
    message = receipt(harness, production=True)
    calls = []

    def provider(value, settings, mail, **kwargs):
        """No acceptance on attempt one; only the still-usable address is retried."""
        calls.append(mail.recipients)
        return (
            FamilyDeliveryResult(Status.TRANSIENT, 2, permanent=(0,), transient=(1,))
            if len(calls) == 1
            else FamilyDeliveryResult(Status.ACCEPTED, 1)
        )

    monkeypatch.setattr(
        "parishkit.stewardship.jobs.family_mail_dispatch.RETRY_BASE_SECONDS", 1
    )
    monkeypatch.setattr(
        "parishkit.stewardship.jobs.family_mail_delivery_tasks.submit_family", provider
    )
    deliver(harness, path, message)
    Event().wait(1.1)
    deliver(harness, path, message)
    message.refresh_from_db()
    assert calls == [
        ("valid@example.org", "zother@example.org"),
        ("zother@example.org",),
    ]
    assert message.state == "delivered" and message.attempt == 2


def test_unknown_receipt_appears_in_admin_warning_and_private_metadata(
    dispatch_worker,  # noqa: F811
    google,
    monkeypatch,
):
    """The existing Admin surface includes receipts without rendering their bodies."""
    harness, path = dispatch_worker
    message = receipt(harness)
    monkeypatch.setattr(
        "parishkit.stewardship.jobs.family_mail_delivery_tasks.submit_family",
        lambda *args, **kwargs: FamilyDeliveryResult(Status.UNKNOWN, 1),
    )
    deliver(harness, path, message)
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB, exact=True):
        response = browser.get(f"/admin/deliveries/{message.pk}")
        assert response.status_code == 200
        assert b"Delivery unknown" in response.content
        assert harness.code.encode() not in response.content
        assert b"Your submission has been received" not in response.content
        assert browser.get("/admin/background/counts").json()["delivery_unknown"] == 1

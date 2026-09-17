"""Maintained daily SMTP dispatch uses only a bounded fake provider in CI."""

from pathlib import Path
from uuid import uuid4

import pytest
from django.db import connection

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.digest_delivery import DigestDeliveryMail
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs.dispatch import recover_hint
from parishkit.stewardship.jobs.family_mail_delivery_tasks import delivery_handler
from parishkit.stewardship.jobs.family_mail_dispatch import TASK_TYPE
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status

from .campaign_builders import campaign_clock
from .test_background_grants_postgresql import task_login
from .test_daily_digest_dispatch_postgresql import allocated, begin
from .test_daily_digest_planning_postgresql import INSTANT
from .test_family_mail_dispatch_postgresql import claim
from .test_family_mail_worker_postgresql import (  # noqa: F401
    deliver,
    dispatch_worker,
    family_mail,
)
from .test_taskrun_postgresql import act, expire

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("status", list(Status))
def test_daily_worker_commits_before_provider_and_never_opens_family_keys(
    dispatch_worker,  # noqa: F811
    monkeypatch,
    status,
):
    harness, path = dispatch_worker
    calls = []

    def forbidden(*args, **kwargs):
        """Daily reports must not use Family token decryption or Family transport."""
        pytest.fail("Daily delivery invoked a Family-only capability.")

    with campaign_clock(INSTANT):
        ready = allocated(harness)
        message = OutboxMessage.objects.get()

        def provider(value, settings, mail, *, seconds, check):
            """The same isolated lifetime owns committed intent, not a DB lock."""
            assert not connection.in_atomic_block and 0 < seconds <= 30
            check()
            assert OutboxMessage.objects.get(pk=message.pk).state == "submitting"
            assert isinstance(mail, DigestDeliveryMail)
            assert mail.chart == bytes(ready.chart)
            calls.append(mail.recipients)
            return FamilyDeliveryResult(status, 1)

        monkeypatch.setattr(harness.rings.private, "decrypt", forbidden)
        monkeypatch.setattr(
            "parishkit.stewardship.jobs.family_mail_delivery_tasks.submit_family",
            forbidden,
        )
        monkeypatch.setattr(
            "parishkit.stewardship.jobs.family_mail_delivery_tasks.submit_digest",
            provider,
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


def test_abandoned_daily_attempt_is_unknown_not_automatically_resent(
    family_mail,  # noqa: F811
    monkeypatch,
):
    with campaign_clock(INSTANT):
        allocated(family_mail)
        message = OutboxMessage.objects.get()
        monkeypatch.setattr(
            "parishkit.stewardship.jobs.family_mail_dispatch.PROVIDER_SECONDS", 1
        )
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(message)
            begin(message, execution)
        running = act(
            _status(TaskRun.objects.get(pk=message.task_id)),
            "heartbeat",
            lease_seconds=1,
        )
        expire(running)
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

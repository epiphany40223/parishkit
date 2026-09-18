"""Maintained weekly MAIL execution commits before a bounded fake provider call."""

from pathlib import Path
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
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.weekly_delivery import WeeklyDeliveryMail

from .campaign_builders import campaign_clock
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_family_mail_dispatch_postgresql import claim
from .test_family_mail_worker_postgresql import (  # noqa: F401
    deliver,
    dispatch_worker,
    family_mail,
)
from .test_taskrun_postgresql import act, expire
from .test_weekly_capture_postgresql import INSTANT
from .test_weekly_dispatch_postgresql import allocated, begin
from .test_weekly_fanout_postgresql import messages

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("status", list(Status))
def test_weekly_worker_commits_before_private_provider_and_uses_no_family_key(
    dispatch_worker,  # noqa: F811
    monkeypatch,
    status,
):
    harness, path = dispatch_worker
    harness = activate_response_service(harness)
    calls = []

    def forbidden(*args, **kwargs):
        """A weekly text report cannot open household keys or use other adapters."""
        pytest.fail("Weekly delivery invoked a foreign mail capability.")

    with campaign_clock(INSTANT):
        allocated(harness)
        message = messages().get()

        def provider(value, settings, mail, *, seconds, check):
            """Observe committed intent with no held database transaction."""
            assert not connection.in_atomic_block and 0 < seconds <= 30
            check()
            assert messages().get(pk=message.pk).state == "submitting"
            assert type(mail) is WeeklyDeliveryMail
            assert "PRIVATE-WEEKLY-DISPATCH-CANARY" in mail.text
            calls.append(mail.recipients)
            return FamilyDeliveryResult(status, 1)

        monkeypatch.setattr(harness.rings.private, "decrypt", forbidden)
        for adapter in ("submit_family", "submit_digest"):
            monkeypatch.setattr(
                "parishkit.stewardship.jobs.family_mail_delivery_tasks." + adapter,
                forbidden,
            )
        monkeypatch.setattr(
            "parishkit.stewardship.jobs.family_mail_delivery_tasks.submit_weekly",
            provider,
        )
        deliver(harness, path, message)
    assert calls == [("admin@example.org",)]
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


def test_abandoned_weekly_attempt_becomes_uncertain_without_resend(
    live_response_service, monkeypatch
):
    with campaign_clock(INSTANT):
        allocated(live_response_service)
        message = messages().get()
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

"""Exact security alert MAIL ownership: recorded recipients, never current grants."""

from pathlib import Path
from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import (
    FamilyDeliveryResult,
    FamilyDeliveryStatus,
)
from parishkit.stewardship.jobs.dispatch import claim_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.operational_dispatch import (
    begin_submission,
    bound_operational,
    cohort_state,
    finish_submission,
)
from parishkit.stewardship.jobs.outbox_dispatch import delivery_handler
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.security_models import SecurityRecipient
from parishkit.stewardship.jobs.security_owner import SECURITY
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.security_delivery import SecurityMail

from ..policy_factory import address
from .campaign_builders import change
from .test_background_grants_postgresql import task_login
from .test_operational_routing_postgresql import routing as routing_fixture
from .test_security_fanout_postgresql import consume, granted, schedule

routing = routing_fixture
pytestmark = pytest.mark.django_db(transaction=True)


def allocated(routing):
    """Use the compiled scheduler and preparation worker before attempting MAIL."""
    store, _, _, _ = routing
    granted(routing)
    (identifier,) = schedule()
    assert consume(identifier, store)
    return list(SecurityRecipient.objects.select_related("outbox").order_by("address"))


def claim(message, store):
    """Claim through the composite handler, without opening a private provider pipe."""
    return claim_hint(
        message.task_id,
        queue=WorkQueue.MAIL,
        worker_id=uuid4(),
        handlers={
            "outbox_delivery": delivery_handler(
                store, credential_path=Path("/synthetic/not-read")
            )
        },
    )


def begin(message, execution, store):
    """Current configuration is pinned at the last local pre-provider boundary."""
    return begin_submission(
        message.pk,
        execution.claim,
        store=store,
        configuration_id=SystemConfiguration.objects.get().active_configuration_id,
        owner=SECURITY,
    )


def test_security_mail_is_owned_and_recorded_under_the_actual_mail_role(routing):
    """The compiled owner binds, prepares, submits and settles one recipient."""
    store, _, _, _ = routing
    first, second = allocated(routing)
    message = first.outbox
    with task_login(ServiceRole.SCHEDULER, exact=True), work_transaction():
        bound = bound_operational(
            _status(TaskRun.objects.get(pk=message.task_id)), SECURITY
        )
        assert bound.pk == message.pk
        # The operational owner cannot bind a security alert, nor the reverse.
        with pytest.raises(OutboxMessage.DoesNotExist):
            bound_operational(_status(TaskRun.objects.get(pk=message.task_id)))
        with CaptureQueriesContext(connection) as queries:
            assert cohort_state(message, SECURITY) == (True, False)
        assert len(queries) == 2
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message, store)
        mail, deadline, attempt = begin(message, execution, store)
        assert isinstance(mail, SecurityMail) and mail.recipients == (first.address,)
        assert attempt == 1 and deadline is not None
        result = finish_submission(
            message.pk,
            execution.claim,
            FamilyDeliveryResult(FamilyDeliveryStatus.ACCEPTED, 1),
            SECURITY,
        )
        assert result.state.value == "delivered"
    message.refresh_from_db()
    assert message.state == "delivered"
    assert message.render.subject.startswith("[TESTING] SECURITY:")
    assert message.sealed_substitutions is None
    second.outbox.refresh_from_db()
    assert second.outbox.state == "pending"


def test_a_recipient_revoked_since_activation_is_still_told(routing):
    """The recipients are the Administrators who existed before the expansion."""
    store, actor, _, records = routing
    first, second = allocated(routing)
    assert second.address == "second@example.org"
    assert (
        change(
            store,
            store.active(),
            actor,
            [
                {
                    "operation": "add",
                    "section": "login_rules",
                    **address("replacement@example.org"),
                },
                {
                    "operation": "remove",
                    "section": "login_rules",
                    "id": records[1]["id"],
                },
            ],
        ).state
        == "applied"
    )
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(second.outbox, store)
        mail, _, attempt = begin(second.outbox, execution, store)
        assert mail.recipients == ("second@example.org",) and attempt == 1
        result = finish_submission(
            second.outbox_id,
            execution.claim,
            FamilyDeliveryResult(FamilyDeliveryStatus.ACCEPTED, 1),
            SECURITY,
        )
        assert result.state.value == "delivered"

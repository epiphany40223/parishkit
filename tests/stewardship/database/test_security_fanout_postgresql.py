"""Durable security alert cohorts and outbox ownership, with no external providers."""

from uuid import uuid4

import pytest
from django.db import IntegrityError

from parishkit.stewardship.accounts.policy_models import PolicySecurityEvent
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs import security_owner
from parishkit.stewardship.jobs.dispatch import claim_hint, execute_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.operational_fanout import (
    fanout_handler,
    prepare_page,
    produce_fanout,
)
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.jobs.security_models import SecurityCohort, SecurityRecipient
from parishkit.stewardship.jobs.security_owner import SECURITY, TASK_TYPE

from ..policy_factory import address
from .campaign_builders import change
from .test_background_grants_postgresql import task_login
from .test_operational_routing_postgresql import routing as routing_fixture

routing = routing_fixture
pytestmark = pytest.mark.django_db(transaction=True)


def schedule():
    """Use only actual scheduler SQL rights for opaque event production."""
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        return produce_fanout(guard, SECURITY)


def consume(identifier, store):
    """Exercise the maintained worker and durable Task completion, not a stub."""
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        return execute_hint(
            identifier,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: fanout_handler(store, owner=SECURITY)},
        )


def granted(routing, email="new@example.org"):
    """Grant Administrator to a new address; the trigger records the expansion."""
    store, actor, _, _ = routing
    assert (
        change(
            store,
            store.active(),
            actor,
            [{"operation": "add", "section": "login_rules", **address(email)}],
        ).state
        == "applied"
    )
    return PolicySecurityEvent.objects.get(target=email)


def test_an_expansion_allocates_one_durable_outbox_per_recorded_recipient(routing):
    """The cohort is the event's recorded recipients; a reload keeps each identity."""
    store, _, _, _ = routing
    # The root activation's own event has nobody to tell and is never scheduled.
    root = PolicySecurityEvent.objects.get(target="admin@example.org")
    assert root.recipients == [] and schedule() == ()
    event = granted(routing)
    assert event.recipients == ["admin@example.org", "second@example.org"]
    (identifier,) = schedule()
    assert schedule() == ()
    assert consume(identifier, store)
    cohort = SecurityCohort.objects.get(event=event)
    assert cohort.addresses == ["admin@example.org", "second@example.org"]
    assert cohort.recipient_count == 2 and cohort.mode == "testing"
    messages = OutboxMessage.objects.filter(purpose="security_event")
    assert SecurityRecipient.objects.count() == messages.count() == 2
    for recipient in SecurityRecipient.objects.select_related("outbox__render"):
        message = recipient.outbox
        assert message.semantic_key == recipient.pk
        assert message.scope_id == cohort.parish_id
        assert message.campaign_id is message.family_id is None
        assert message.credential_namespace == "none"
        assert message.purpose == "security_event"
        assert message.routing == "operational"
        assert message.render.routed_recipients == [recipient.address]
        assert message.render.routed_recipients == message.render.intended_recipients
        assert message.render.subject == (
            "[TESTING] SECURITY: Administrator added to an exact address"
        )
        assert "Target: new@example.org" in message.render.text
        assert "Roles after: Administrator" in message.render.text
        assert message.state == "pending" and message.attempt == 0
    assert not consume(identifier, store)
    assert TaskRun.objects.get(pk=identifier).state == "succeeded"
    assert TaskRun.objects.filter(task_type="outbox_delivery").count() == 2


def test_sql_binds_the_cohort_to_the_recorded_recipients(routing, monkeypatch):
    """A cohort naming anyone but the event's recipients is refused by SQL."""
    store, _, _, _ = routing
    granted(routing)
    (identifier,) = schedule()
    monkeypatch.setattr(security_owner, "_recipients", lambda event: ("x@example.org",))
    with task_login(ServiceRole.WORKER, exact=True):
        execution = claim_hint(
            identifier,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: fanout_handler(store, owner=SECURITY)},
        )
        with (
            pytest.raises(IntegrityError, match="recorded recipients"),
            work_transaction(),
        ):
            prepare_page(execution.claim, store, SECURITY)
    assert not SecurityCohort.objects.exists()
    assert not OutboxMessage.objects.filter(purpose="security_event").exists()


def test_metadata_scheduler_cannot_read_cohorts_or_prepare_them(routing):
    """The scheduler allocates opaque work and sees only metadata columns."""
    from django.db import DatabaseError

    store, _, _, _ = routing
    granted(routing)
    (identifier,) = schedule()
    with task_login(ServiceRole.SCHEDULER, exact=True):
        with pytest.raises(PermissionError):
            fanout_handler(store, scheduler=True, owner=SECURITY).execute(None)
        with pytest.raises(DatabaseError), work_transaction():
            list(SecurityCohort.objects.values_list("addresses", flat=True))
    assert consume(identifier, store)
    with task_login(ServiceRole.SCHEDULER, exact=True), work_transaction():
        assert list(
            SecurityCohort.objects.values_list("recipient_count", flat=True)
        ) == [2]

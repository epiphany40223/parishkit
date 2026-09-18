"""Durable operational cohorts/outbox ownership, with no external providers."""

import json
from dataclasses import asdict
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from parishkit.stewardship.campaigns.domain import SystemMode
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import claim_hint, execute_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.operational_content import (
    IncidentKind,
    IncidentLevel,
    render_alert,
)
from parishkit.stewardship.jobs.operational_fanout import (
    BATCH_SIZE,
    TASK_TYPE,
    fanout_handler,
    prepare_page,
    produce_fanout,
)
from parishkit.stewardship.jobs.operational_models import (
    OperationalCohort,
    OperationalNotice,
    OperationalRecipient,
)
from parishkit.stewardship.jobs.operational_policy import IncidentPolicy
from parishkit.stewardship.jobs.operational_routing import notice_alert
from parishkit.stewardship.jobs.operational_storage import (
    record_observation,
    record_recovery,
)
from parishkit.stewardship.jobs.outbox_models import OutboxMessage, OutboxRender
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scheduler import scheduler_session

from ..policy_factory import address
from .campaign_builders import change
from .test_background_grants_postgresql import task_login
from .test_operational_routing_postgresql import routing as routing_fixture

routing = routing_fixture
pytestmark = pytest.mark.django_db(transaction=True)


def schedule():
    """Use only actual scheduler SQL rights for opaque notice production."""
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        return produce_fanout(guard)


def consume(identifier, store):
    """Exercise the maintained worker and durable Task completion, not a stub."""
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        return execute_hint(
            identifier,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: fanout_handler(store)},
        )


def test_notice_allocates_one_durable_outbox_per_exact_admin(routing):
    """Duplicates and reloads retain the first atomic recipient identity."""
    store, _, notice, _ = routing
    (identifier,) = schedule()
    assert schedule() == ()
    assert consume(identifier, store)
    cohort = OperationalCohort.objects.get(notice_id=notice)
    assert cohort.addresses == ["admin@example.org", "second@example.org"]
    assert cohort.slack_channel == "C123"
    assert OperationalRecipient.objects.count() == OutboxMessage.objects.count() == 2
    for recipient in OperationalRecipient.objects.select_related("outbox__render"):
        message = recipient.outbox
        assert message.semantic_key == recipient.pk
        assert message.scope_id == cohort.parish_id
        assert message.campaign_id is message.family_id is None
        assert message.credential_namespace == "none"
        assert message.routing == message.purpose == "operational"
        assert message.render.routed_recipients == [recipient.address]
        assert message.render.routed_recipients == message.render.intended_recipients
        assert message.render.subject.startswith("[TESTING] CRITICAL:")
        assert message.state == "pending" and message.attempt == 0
    assert not consume(identifier, store)
    assert TaskRun.objects.get(pk=identifier).state == "succeeded"
    assert TaskRun.objects.filter(task_type="outbox_delivery").count() == 2


def test_fanout_page_is_bounded_and_resumes_without_rebinding(routing):
    """Additional pages reuse the captured cohort rather than duplicate children."""
    store, actor, _, _ = routing
    assert (
        change(
            store,
            store.active(),
            actor,
            [
                {
                    "operation": "add",
                    "section": "login_rules",
                    **address(f"admin{i:02}@example.org"),
                }
                for i in range(BATCH_SIZE)
            ],
        ).state
        == "applied"
    )
    (identifier,) = schedule()
    with task_login(ServiceRole.WORKER, exact=True):
        execution = claim_hint(
            identifier,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: fanout_handler(store)},
        )
        with work_transaction():
            assert prepare_page(execution.claim, store) == (BATCH_SIZE, BATCH_SIZE + 2)
        first_ids = set(OperationalRecipient.objects.values_list("pk", flat=True))
        with work_transaction():
            assert prepare_page(execution.claim, store) == (
                BATCH_SIZE + 2,
                BATCH_SIZE + 2,
            )
        with work_transaction():
            assert prepare_page(execution.claim, store) == (
                BATCH_SIZE + 2,
                BATCH_SIZE + 2,
            )
        assert first_ids <= set(
            OperationalRecipient.objects.values_list("pk", flat=True)
        )
    assert OperationalCohort.objects.count() == 1
    assert OutboxMessage.objects.count() == BATCH_SIZE + 2


def test_failed_recipient_write_rolls_back_entire_page(routing, monkeypatch):
    """Neither orphan outboxes nor half-captured cohorts survive interrupted work."""
    store, _, _, _ = routing
    (identifier,) = schedule()
    original = OperationalRecipient.objects.create

    def interrupted(**kwargs):
        """Fail after the child exists, before this page can commit."""
        original(**kwargs)
        raise RuntimeError("synthetic recipient interruption")

    monkeypatch.setattr(OperationalRecipient.objects, "create", interrupted)
    with pytest.raises(RuntimeError):
        consume(identifier, store)
    assert not OperationalCohort.objects.exists()
    assert not OperationalRecipient.objects.exists()
    assert not OutboxMessage.objects.exists()
    assert TaskRun.objects.filter(task_type=TASK_TYPE).count() == 1


def test_sql_rejects_private_text_and_missing_recipient_receipt(routing, monkeypatch):
    """Privileged role grants alone cannot allocate arbitrary operational content."""
    from parishkit.stewardship.jobs import operational_fanout

    store, _, _, _ = routing
    (identifier,) = schedule()
    original = operational_fanout.mail_render

    def private_text(**kwargs):
        """Keep every binding valid while attempting an unauthorized body."""
        from dataclasses import replace

        mail, render = original(**kwargs)
        return mail, replace(render, text="private-family-information")

    monkeypatch.setattr(operational_fanout, "mail_render", private_text)
    with pytest.raises(IntegrityError):
        consume(identifier, store)
    assert not OutboxRender.objects.exists()
    monkeypatch.setattr(operational_fanout, "mail_render", original)
    monkeypatch.setattr(OperationalRecipient.objects, "create", lambda **kwargs: None)
    # Reuse the still-owned claim for a second faulty page, not a fabricated hint.
    from parishkit.stewardship.jobs.ownership import TaskClaim

    row = TaskRun.objects.get(pk=identifier)
    claim = TaskClaim(row.pk, row.fence, row.worker_id)
    with (
        pytest.raises(IntegrityError),
        task_login(ServiceRole.WORKER, exact=True),
        work_transaction(),
    ):
        prepare_page(claim, store)
    assert not OutboxMessage.objects.exists()


def test_closed_content_compilers_agree_for_every_kind_and_recovery(routing):
    """One shared setup compares application content and its SQL admission contract."""
    with work_transaction():
        for kind in IncidentKind:
            incident = record_observation(
                kind, level=IncidentLevel.CRITICAL, policy=IncidentPolicy()
            )
            record_recovery(kind)
            for notice in OperationalNotice.objects.filter(incident=incident):
                for mode in SystemMode:
                    expected = asdict(render_alert(notice_alert(notice.pk, mode)))
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT stewardship_ops_content_v1(%s,%s)",
                            [notice.pk, mode.value],
                        )
                        assert json.loads(cursor.fetchone()[0]) == expected


def test_metadata_scheduler_cannot_read_cohorts_or_prepare_them(routing):
    """The scheduler sees relationships/counts, never operational email addresses."""
    store, _, _, _ = routing
    (identifier,) = schedule()
    consume(identifier, store)
    with task_login(ServiceRole.SCHEDULER, exact=True):
        for statement in (
            "SELECT addresses FROM stewardship_ops_cohort",
            "SELECT address FROM stewardship_ops_recipient",
            "INSERT INTO stewardship_ops_cohort DEFAULT VALUES",
        ):
            with (
                pytest.raises(DatabaseError) as error,
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute(statement)
            assert error.value.__cause__.sqlstate == "42501"
    for table in ("stewardship_ops_cohort", "stewardship_ops_recipient"):
        with (
            pytest.raises(IntegrityError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(f"DELETE FROM {table}")


def test_unconfigured_notifications_remain_pending_without_failed_tasks():
    """Initial setup absence is an admission hold, not an exhausted delivery budget."""
    record_observation(
        IncidentKind.STORAGE_INTEGRITY,
        level=IncidentLevel.CRITICAL,
        policy=IncidentPolicy(),
    )
    assert schedule() == ()
    assert OperationalNotice.objects.count() == 1
    assert not TaskRun.objects.exists()

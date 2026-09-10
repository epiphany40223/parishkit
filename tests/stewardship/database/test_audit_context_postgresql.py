"""Audited effects roll back together and SQL enforces privacy even for raw writes."""

import json
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction

from parishkit.stewardship.audit.models import AuditContext, AuditEvent, OperationalLog
from parishkit.stewardship.audit.schemas import Action, ActorKind, ContextKind, Outcome
from parishkit.stewardship.audit.services import (
    audited_effect,
    operational,
    record_action,
)
from parishkit.stewardship.observability import Event
from parishkit.stewardship.storage import StorageInvariantError

pytestmark = pytest.mark.django_db(transaction=True)


def test_success_and_failure_share_owning_transaction():
    evidence = {
        "actor_kind": ActorKind.PORTAL_USER,
        "actor_id": uuid4(),
        "context": {
            "before_version": 1,
            "after_version": 2,
            "outcome": Outcome.SUCCEEDED,
        },
    }
    with transaction.atomic():
        assert (
            audited_effect(
                Action.ROLES_APPLIED,
                authorize=lambda: True,
                operation=lambda: 42,
                **evidence,
            )
            == 42
        )
    assert AuditEvent.objects.get().event_type == "roles_applied"
    assert AuditContext.objects.get().context["after_version"] == 2
    with pytest.raises(PermissionError), transaction.atomic():
        audited_effect(
            Action.ROLES_APPLIED,
            authorize=lambda: False,
            operation=lambda: 43,
            **evidence,
        )
    assert AuditEvent.objects.count() == 1
    with pytest.raises(ValueError), transaction.atomic():
        audited_effect(
            Action.ROLES_APPLIED,
            authorize=lambda: True,
            operation=lambda: AuditEvent.objects.create(event_type="rollback"),
            actor_kind=ActorKind.PORTAL_USER,
            context={"code": "PRIVATE"},
        )
    assert AuditEvent.objects.count() == 1
    with pytest.raises(StorageInvariantError):
        record_action(Action.ROLES_APPLIED, actor_kind=ActorKind.SYSTEM)


@pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
def test_operational_levels_and_safe_context(level):
    result = operational(
        Event.REQUEST_COMPLETED,
        level=level,
        schema=ContextKind.REQUEST,
        context={"status": 200},
    )
    assert result.context == {"status": 200} and result.level == level
    with pytest.raises(ValueError):
        operational(Event.TASK_FAILED, context={"exception": "PRIVATE"})


@pytest.mark.parametrize(
    "context",
    [
        {"token": "PRIVATE"},
        {"status": "PRIVATE"},
        {"status": True},
        {"status": 999},
        {"outcome": "PRIVATE"},
        [],
    ],
)
def test_raw_sql_cannot_store_secret_bearing_context(context):
    with (
        pytest.raises(IntegrityError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "INSERT INTO stewardship_operational_log"
            "(id,correlation_id,event,level,schema,context) VALUES(%s,%s,%s,%s,%s,%s)",
            [
                uuid4(),
                uuid4(),
                "request_completed",
                "INFO",
                "request",
                json.dumps(context),
            ],
        )
    assert not OperationalLog.objects.exists()

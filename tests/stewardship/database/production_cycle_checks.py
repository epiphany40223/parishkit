"""Current-cycle rejection probes using actual restricted service identities."""

from copy import copy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection
from django.db.models import F

from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import Campaign, ScheduleOccurrence
from parishkit.stewardship.campaigns.schedules import occurrence_key
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs import family_mail_tasks
from parishkit.stewardship.jobs.delivery_resolution import _prepare
from parishkit.stewardship.jobs.family_mail_dispatch import disposition
from parishkit.stewardship.jobs.outbox_models import OutboxMessage

from .campaign_builders import campaign_clock, command
from .test_background_grants_postgresql import task_login


def withdraw_before_start(campaign, actor):
    """Exercise domain transitions, not an UPDATE or disabled cycle guard."""
    with campaign_clock(campaign.active_configuration.starts_at - timedelta(days=1)):
        command(campaign, actor, Action.ACTIVATE)
        command(campaign, actor, Action.WITHDRAW, reason="Correct setup")
    campaign.refresh_from_db()
    assert campaign.production_cycle == 1


def reject_retired_cycle(item, previous, message, *, failed):
    """Neither a privileged worker nor a retained delivery can revive a cycle."""
    campaign = item.campaign
    clone = {
        field.attname: getattr(campaign, field.attname)
        for field in campaign._meta.concrete_fields
    } | {"id": uuid4(), "version": 1}
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        for cycle in (0, 2):
            with pytest.raises(DatabaseError, match="atomic setup ownership"):
                Campaign.objects.filter(pk=campaign.pk).update(
                    production_cycle=cycle, version=F("version") + 1
                )
        with pytest.raises(DatabaseError, match="atomic setup ownership"):
            Campaign.objects.create(**clone)
    with (
        task_login(ServiceRole.SCHEDULER, exact=True, reconnect=True),
        pytest.raises(DatabaseError, match="retired Production cycle"),
    ):
        ScheduleOccurrence.objects.create(
            definition_id=previous.definition_id,
            revision_id=previous.revision_id,
            mode="production",
            routing="production",
            target=previous.target,
            slot=previous.slot,
            due_at=previous.due_at,
            occurrence_key=occurrence_key(
                previous.revision_id, "production", previous.target, previous.slot
            ),
            actor_id=uuid4(),
            correlation_id=uuid4(),
        )
    if failed:
        with pytest.raises(DatabaseError, match="retired Production cycle"):
            ScheduleOccurrence.objects.filter(pk=previous.pk).update(
                state="pending",
                version=F("version") + 1,
                retry_command_id=uuid4(),
            )
        with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
            # A preparation can fail before creating its outbox. Exercise that
            # read projection separately from the retained delivered-render case.
            unrendered = copy(previous)
            unrendered.outbox_id = None
            with (
                work_transaction(),
                patch.object(family_mail_tasks, "_row", return_value=unrendered),
            ):
                assert (
                    family_mail_tasks.disposition(
                        SimpleNamespace(
                            occurrence_id=previous.pk,
                            mode="production",
                            rehearsal_epoch_id=None,
                        )
                    )
                    == "safe_cancel"
                )
    # Worker writes are independently denied by the narrower setup owner;
    # schema-owner probes also reach the cycle-specific guard without bypassing it.
    for cycle in (0, 2):
        with pytest.raises(DatabaseError, match="Production cycle requires"):
            Campaign.objects.filter(pk=campaign.pk).update(
                production_cycle=cycle, version=F("version") + 1
            )
    with pytest.raises(DatabaseError, match="Production cycle requires"):
        Campaign.objects.create(**clone)
    if failed:
        retained = OutboxMessage.objects.get(pk=message.message_id)
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT stewardship_delivery_retry_admitted_v1(%s)", [retained.pk]
                )
                assert cursor.fetchone()[0] is False
            with (
                work_transaction(),
                pytest.raises(PermissionError, match="earlier scope"),
            ):
                _prepare(
                    retained,
                    general=item.ring.general,
                    public=item.ring.public,
                    public_origin="https://parish.example",
                )
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
            with pytest.raises(
                DatabaseError, match="Family dispatch requires work ownership"
            ):
                OutboxMessage.objects.filter(pk=message.message_id).update(
                    action="retry_failed",
                    state="pending",
                    version=F("version") + 1,
                    command_id=uuid4(),
                )
            with work_transaction():
                assert disposition(retained) == "scope_replaced"
        with pytest.raises(
            DatabaseError, match="Delivery schedule is no longer current"
        ):
            OutboxMessage.objects.filter(pk=message.message_id).update(
                action="retry_failed",
                state="pending",
                version=F("version") + 1,
                command_id=uuid4(),
            )

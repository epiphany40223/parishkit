"""Capture never leaves retained UUID-only workflow pointers dangling."""

from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import F

from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.campaigns.models import (
    PostCloseMailResolution,
    ScheduleDefinition,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.production_models import (
    ProductionTransitionRequest,
)
from parishkit.stewardship.jobs.outbox_models import OutboxMessage

from .campaign_builders import occurrence
from .test_cleanup_batches_postgresql import make_testing_mail
from .test_cleanup_tasks_postgresql import queued, run

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("kind", ["occurrence", "postclose"])
def test_retained_history_pointer_rejects_capture(response_service, kind):
    """Fail closed on malformed retained history without repairing or deleting it."""
    message = make_testing_mail(response_service)
    row = (
        occurrence(ScheduleDefinition.objects.first(), uuid4())
        if kind == "occurrence"
        else None
    )
    # Ordinary routing/lifecycle guards prohibit these malformed historical
    # states. Only the disposable schema-owner fixture installs them; restore
    # every guard before invoking the real cleanup capture transaction.
    with transaction.atomic(), connection.cursor() as cursor:
        if kind == "occurrence":
            cursor.execute(
                "ALTER TABLE stewardship_schedule_occurrence DISABLE TRIGGER USER"
            )
            ScheduleOccurrence.objects.filter(pk=row.pk).update(
                mode="production",
                routing="production",
                state="skipped",
                reason="synthetic_retained_history",
                outbox_id=message.message_id,
                version=F("version") + 1,
            )
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cursor.execute(
                "ALTER TABLE stewardship_schedule_occurrence ENABLE TRIGGER USER"
            )
            retained = ScheduleOccurrence.objects.filter(pk=row.pk)
        else:
            cursor.execute(
                "ALTER TABLE stewardship_postclose_resolution DISABLE TRIGGER USER"
            )
            row = PostCloseMailResolution.objects.create(
                campaign=response_service.campaign,
                mode="production",
                obligation_key="synthetic_retained_history",
                coverage_digest="a" * 64,
                coverage=[],
                reason="synthetic retained pointer",
                outbox_id=message.message_id,
                actor_id=uuid4(),
                correlation_id=uuid4(),
            )
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cursor.execute(
                "ALTER TABLE stewardship_postclose_resolution ENABLE TRIGGER USER"
            )
            retained = PostCloseMailResolution.objects.filter(pk=row.pk)
    before = list(retained.values())
    credentials = CampaignCredentialState.objects.get(
        campaign=response_service.campaign
    )
    with pytest.raises(IntegrityError, match="external outbox references"):
        queued(response_service)
    after = CampaignCredentialState.objects.get(pk=credentials.pk)
    assert (after.go_live_gate, after.rehearsal_epoch_id, after.version) == (
        credentials.go_live_gate,
        credentials.rehearsal_epoch_id,
        credentials.version,
    )
    assert list(retained.values()) == before
    assert OutboxMessage.objects.filter(pk=message.message_id).exists()
    assert not ProductionTransitionRequest.objects.exists()


def test_inventoried_occurrence_and_outbox_remain_deletable(response_service):
    """A valid same-inventory reference is removed through ordinary batch order."""
    message = make_testing_mail(response_service)
    row = occurrence(ScheduleDefinition.objects.first(), uuid4())
    ScheduleOccurrence.objects.filter(pk=row.pk).update(
        state="skipped",
        reason="synthetic_skip",
        outbox_id=message.message_id,
        version=F("version") + 1,
    )
    status = queued(response_service)
    assert run(status)
    assert not ScheduleOccurrence.objects.filter(pk=row.pk).exists()
    assert not OutboxMessage.objects.filter(pk=message.message_id).exists()

"""Revision identity, explicit retries, immutable outcomes and semantic coverage."""

from uuid import uuid4

import pytest
from django.db import IntegrityError

from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.configuration_requests import record_request
from parishkit.stewardship.campaigns.admission import CampaignAdmissionUnavailable
from parishkit.stewardship.campaigns.models import (
    ScheduleDefinition,
    ScheduleFulfillment,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.schedules import (
    change_occurrence,
    create_occurrence,
    record_fulfillment,
)

from .campaign_builders import admit_test_work, campaign_clock, draft_campaign
from .test_boundary_catchup_postgresql import claimed_task

pytestmark = pytest.mark.django_db(transaction=True)


def occurrence(definition, actor, target="family:1"):
    """Allocate ordinary rehearsal work under a real canonical schedule revision."""
    return create_occurrence(
        definition_id=definition.pk,
        revision_id=definition.current_revision_id,
        mode="testing",
        target=target,
        slot="once",
        due_at=definition.current_revision.due_at,
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
    )


def advance(row, actor, state, **kwargs):
    """Supply fresh optimistic versions without bypassing occurrence SQL guards."""
    row.refresh_from_db()
    return change_occurrence(
        occurrence_id=row.pk,
        state=state,
        expected_version=row.version,
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
        **kwargs,
    )


def test_semantic_delivery_survives_revision_change(tmp_path):
    """Changing a template/time cannot resend an already fulfilled logical slot."""
    store, campaign, actor = draft_campaign(tmp_path)
    definition = ScheduleDefinition.objects.select_related("current_revision").get()
    with campaign_clock(definition.current_revision.due_at):
        row = occurrence(definition, actor)
        assert occurrence(definition, actor).pk == row.pk
        run = claimed_task("schedule_occurrence", row.pk, actor)
        row = advance(row, actor, "running", task_id=run.run_id, fence=run.fence)
        row = advance(row, actor, "succeeded", fence=run.fence)
        fulfillment = record_fulfillment(
            occurrence_id=row.pk,
            disposition="delivered",
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
        request = record_request(
            base_digest=store.active().digest,
            patch=[
                {
                    "operation": "update",
                    "section": "schedules",
                    "id": str(definition.pk),
                    "values": {"time": "10:00:00"},
                }
            ],
            actor_id=actor,
            request_key=uuid4(),
            correlation_id=uuid4(),
        )
        assert (
            install_request(
                store, request_id=request.request_id, correlation_id=uuid4()
            ).state
            == "applied"
        )
        definition.refresh_from_db()
        with pytest.raises(IntegrityError, match="not admitted"):
            occurrence(definition, actor)
    assert ScheduleFulfillment.objects.get().pk == fulfillment.pk
    row.refresh_from_db()
    assert row.state == "succeeded"


def test_running_work_blocks_replacement_before_yaml_selection(tmp_path):
    """Temporary in-flight blockers preserve staged intent and selected YAML."""
    store, campaign, actor = draft_campaign(tmp_path)
    definition = ScheduleDefinition.objects.select_related("current_revision").get()
    with campaign_clock(definition.current_revision.due_at):
        row = occurrence(definition, actor)
        run = claimed_task("schedule_occurrence", row.pk, actor)
        advance(row, actor, "running", task_id=run.run_id, fence=run.fence)
        before = store.active()
        request = record_request(
            base_digest=before.digest,
            patch=[
                {
                    "operation": "update",
                    "section": "schedules",
                    "id": str(definition.pk),
                    "values": {"time": "10:00:00"},
                }
            ],
            actor_id=actor,
            request_key=uuid4(),
            correlation_id=uuid4(),
        )
        with pytest.raises(CampaignAdmissionUnavailable):
            install_request(
                store, request_id=request.request_id, correlation_id=uuid4()
            )
        assert store.active() == before


def test_failed_retry_preserves_occurrence_and_exact_command(tmp_path):
    """An explicit retry advances existing work instead of allocating another send."""
    _, campaign, actor = draft_campaign(tmp_path)
    definition = ScheduleDefinition.objects.select_related("current_revision").get()
    with campaign_clock(definition.current_revision.due_at):
        row = occurrence(definition, actor)
        run = claimed_task("schedule_occurrence", row.pk, actor)
        advance(row, actor, "running", task_id=run.run_id, fence=run.fence)
        advance(row, actor, "failed", fence=run.fence, reason="definitive_failure")
        row.refresh_from_db()
        arguments = dict(
            occurrence_id=row.pk,
            state="pending",
            expected_version=row.version,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
            retry_command_id=uuid4(),
            reason="admin_retry",
        )
        retried = change_occurrence(**arguments)
        assert change_occurrence(**arguments).pk == retried.pk == row.pk
    assert ScheduleOccurrence.objects.count() == 1
    assert retried.attempts == 1 and retried.state == "pending"


def test_safe_replacement_skips_unallocated_pending_work(tmp_path):
    """Removal history and cancellation share the config activation transaction."""
    store, campaign, actor = draft_campaign(tmp_path)
    definition = ScheduleDefinition.objects.select_related("current_revision").get()
    with campaign_clock(definition.current_revision.due_at):
        row = occurrence(definition, actor)
        request = record_request(
            base_digest=store.active().digest,
            patch=[
                {
                    "operation": "remove",
                    "section": "schedules",
                    "id": str(definition.pk),
                }
            ],
            actor_id=actor,
            request_key=uuid4(),
            correlation_id=uuid4(),
        )
        assert (
            install_request(
                store, request_id=request.request_id, correlation_id=uuid4()
            ).state
            == "applied"
        )
    definition.refresh_from_db()
    row.refresh_from_db()
    assert definition.current_revision_id is None and definition.removed_at is not None
    assert row.state == "skipped" and row.reason == "schedule_removed"

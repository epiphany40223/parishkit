"""Revision identity, explicit retries, immutable outcomes and semantic coverage."""

from uuid import uuid4

import pytest
from django.db import IntegrityError

from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.configuration_requests import record_request
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.admission import CampaignAdmissionUnavailable
from parishkit.stewardship.campaigns.controls import change_control
from parishkit.stewardship.campaigns.lifecycle import Action
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

from .campaign_builders import (
    admit_test_work,
    campaign_clock,
    command,
    draft_campaign,
    restored_runtime,
)
from .test_boundary_catchup_postgresql import claimed_task
from .test_exceptional_end_postgresql import complete_empty_catchup

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


def test_restore_blocks_schedule_change_but_not_unrelated_configuration(tmp_path):
    """Restore holds leave a candidate staged without blocking identical projections."""
    from parishkit.stewardship.accounts.configuration_requests import _status
    from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest

    from .test_policy_postgresql import change

    store, campaign, actor = draft_campaign(tmp_path)
    definition = ScheduleDefinition.objects.get()
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
    with restored_runtime(campaign.active_configuration.starts_at):
        with pytest.raises(CampaignAdmissionUnavailable):
            install_request(
                store, request_id=request.request_id, correlation_id=uuid4()
            )
        assert store.active() == before
        assert (
            _status(ConfigurationChangeRequest.objects.get(pk=request.request_id)).state
            == "staged"
        )
        parish = before.document()["sections"]["parish"][0]
        result = change(
            store,
            before,
            actor,
            [
                {
                    "operation": "update",
                    "section": "parish",
                    "id": parish["id"],
                    "values": {"name": "Updated parish"},
                }
            ],
        )
        assert result.state == "applied"


def test_production_pause_prevents_occurrence_claim(tmp_path):
    """A production allocation cannot dispatch while its durable pause is set."""
    _, campaign, actor = draft_campaign(tmp_path)
    definition = ScheduleDefinition.objects.get()
    with campaign_clock(definition.current_revision.due_at):
        command(campaign, actor, Action.ACTIVATE)
        complete_empty_catchup(campaign, actor)
        campaign.refresh_from_db()
        change_control(
            campaign_id=campaign.pk,
            request_id=uuid4(),
            action="pause",
            expected_version=campaign.version,
            expected_runtime_version=SystemConfiguration.objects.get().version,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
            reason="Reviewed",
        )
        row = create_occurrence(
            definition_id=definition.pk,
            revision_id=definition.current_revision_id,
            mode="production",
            target="family:1",
            slot="once",
            due_at=definition.current_revision.due_at,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
        run = claimed_task("schedule_occurrence", row.pk, actor)
        with pytest.raises(IntegrityError, match="current fenced work"):
            advance(row, actor, "running", task_id=run.run_id, fence=run.fence)
    row.refresh_from_db()
    assert row.state == "pending" and row.pause_version is not None


@pytest.mark.parametrize("source", ["running", "delivery_unknown"])
@pytest.mark.parametrize(
    "action,outcome",
    [
        ("recovery_retry", "pending"),
        ("recovery_unknown", "delivery_unknown"),
        ("recovery_complete", "succeeded"),
        ("recovery_fail", "failed"),
        ("recovery_skip", "skipped"),
        ("recovery_coalesce", "coalesced"),
    ],
)
def test_recovery_matches_canonical_source_action_matrix(
    tmp_path, source, action, outcome
):
    """Only reconciled TaskRun ownership authorizes the specified recovery edges."""
    from parishkit.stewardship.campaigns.schedules import recover_occurrence
    from parishkit.stewardship.jobs.storage import change_run
    from parishkit.stewardship.storage import StorageInvariantError

    _, _, actor = draft_campaign(tmp_path)
    definition = ScheduleDefinition.objects.get()
    with campaign_clock(definition.current_revision.due_at):
        row = occurrence(definition, actor)
        replacement = occurrence(definition, actor, target="family:replacement")
        run = claimed_task("schedule_occurrence", row.pk, actor)
        row = advance(row, actor, "running", task_id=run.run_id, fence=run.fence)
        if source == "delivery_unknown":
            row = advance(
                row,
                actor,
                "delivery_unknown",
                fence=run.fence,
                reason="provider_ambiguous",
            )
        args = dict(
            occurrence_id=row.pk,
            expected_version=row.version,
            action=action,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
            replacement_id=replacement.pk if outcome == "coalesced" else None,
        )
        with pytest.raises(StorageInvariantError):
            recover_occurrence(**args)
        change_run(
            run_id=run.run_id,
            action="safe_cancel",
            expected_version=run.version,
            fence=run.fence,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
        if source == "delivery_unknown" and outcome in {
            "delivery_unknown",
            "skipped",
            "coalesced",
        }:
            with pytest.raises(StorageInvariantError, match="resolved outcome"):
                recover_occurrence(**args)
            row.refresh_from_db()
            assert row.state == source and row.version == args["expected_version"]
        else:
            result = recover_occurrence(**args)
            assert (
                result.state == outcome
                and result.version == args["expected_version"] + 1
            )
            if outcome == "coalesced":
                # Coverage is committed before dispatch, not provider success.
                coverage = record_fulfillment(
                    occurrence_id=row.pk,
                    disposition="coalesced",
                    actor_id=actor,
                    correlation_id=uuid4(),
                    admit=admit_test_work,
                )
                replacement.refresh_from_db()
                assert coverage.occurrence_id == replacement.pk
                assert replacement.state == "pending"
                assert not ScheduleFulfillment.objects.filter(
                    disposition="delivered"
                ).exists()


@pytest.mark.parametrize("outcome", ["pending", "succeeded", "failed"])
def test_unknown_delivery_cannot_be_resolved_by_an_unattributed_raw_write(
    tmp_path, outcome
):
    """Even a well-shaped state edge needs reconciled ownership and coded evidence."""
    from django.db import transaction

    _, _, actor = draft_campaign(tmp_path)
    definition = ScheduleDefinition.objects.get()
    with campaign_clock(definition.current_revision.due_at):
        row = occurrence(definition, actor)
        run = claimed_task("schedule_occurrence", row.pk, actor)
        advance(row, actor, "running", task_id=run.run_id, fence=run.fence)
        row = advance(
            row, actor, "delivery_unknown", fence=run.fence, reason="provider_ambiguous"
        )
        with (
            pytest.raises(IntegrityError, match="reconciled ownership"),
            transaction.atomic(),
        ):
            ScheduleOccurrence.objects.filter(pk=row.pk).update(
                state=outcome, version=row.version + 1, reason="arbitrary"
            )


def test_abandoned_execution_must_be_reconciled_before_schedule_replacement(tmp_path):
    """Abandonment is nonterminal; resolve its durable retry chain before removal."""
    from parishkit.stewardship.campaigns.schedules import recover_occurrence
    from parishkit.stewardship.jobs.storage import enqueue

    from .test_taskrun_postgresql import act, expire

    store, _, actor = draft_campaign(tmp_path)
    definition = ScheduleDefinition.objects.get()
    with campaign_clock(definition.current_revision.due_at):
        row = occurrence(definition, actor)
        queued = enqueue(
            task_type="schedule_occurrence",
            domain_request_id=row.pk,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
        run = act(queued, "claim", lease_seconds=1, actor_id=actor)
        advance(row, actor, "running", task_id=run.run_id, fence=run.fence)
        abandoned = expire(run)
        row.refresh_from_db()
        recover_occurrence(
            occurrence_id=row.pk,
            expected_version=row.version,
            action="recovery_retry",
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
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
        with pytest.raises(CampaignAdmissionUnavailable):
            install_request(
                store, request_id=request.request_id, correlation_id=uuid4()
            )
        act(abandoned, "recovery_cancel", actor_id=actor)
        assert (
            install_request(
                store, request_id=request.request_id, correlation_id=uuid4()
            ).state
            == "applied"
        )
    row.refresh_from_db()
    assert row.state == "skipped" and row.reason == "schedule_removed"

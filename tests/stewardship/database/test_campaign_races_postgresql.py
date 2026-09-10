"""Independent connections cannot commit competing lifecycle confirmations."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import close_old_connections

from parishkit.stewardship.accounts.installation_lock import ConfigurationBusy
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import CampaignTransition
from parishkit.stewardship.campaigns.runtime import (
    return_to_testing,
    transition_campaign,
)
from parishkit.stewardship.storage import StaleRecordError

from .campaign_builders import (
    admit_test_work,
    campaign_clock,
    close_campaign,
    command,
    complete_empty_catchup,
    draft_campaign,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    "action",
    [
        Action.ACTIVATE,
        Action.WITHDRAW,
        Action.ARCHIVE,
        Action.UNARCHIVE,
        Action.RETURN_TESTING,
    ],
)
def test_competing_confirmations_have_one_winner(tmp_path, action):
    """Global/session serialization and optimistic versions agree across connections."""
    _, campaign, actor = draft_campaign(tmp_path)
    instant = campaign.active_configuration.starts_at - timedelta(days=1)
    with campaign_clock(instant):
        if action is not Action.ACTIVATE:
            command(campaign, actor, Action.ACTIVATE)
        if action is Action.WITHDRAW:
            complete_empty_catchup(campaign, actor)
        elif action in {Action.ARCHIVE, Action.UNARCHIVE, Action.RETURN_TESTING}:
            close_campaign(campaign, actor)
            if action in {Action.UNARCHIVE, Action.RETURN_TESTING}:
                command(campaign, actor, Action.ARCHIVE)
        campaign.refresh_from_db()
        runtime = SystemConfiguration.objects.get()
        count = CampaignTransition.objects.count()
        barrier = Barrier(2)

        def confirm(_):
            """Two browsers submit one displayed version under different request IDs."""
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                shared = dict(
                    campaign_id=campaign.pk,
                    request_id=uuid4(),
                    expected_runtime_version=runtime.version,
                    actor_id=actor,
                    correlation_id=uuid4(),
                    admit=admit_test_work,
                )
                if action is Action.RETURN_TESTING:
                    return_to_testing(**shared)
                else:
                    transition_campaign(
                        **shared,
                        expected_version=campaign.version,
                        action=action,
                        token_generation_id=uuid4()
                        if action is Action.ACTIVATE
                        else None,
                        reason="reviewed" if action is Action.WITHDRAW else "",
                    )
                return "committed"
            except (ConfigurationBusy, StaleRecordError):
                return "retry"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(confirm, range(2)))
        # The race may lose at the installer lock. Independently pin the stale
        # version branch after contention has ended, under a fresh command ID.
        stale = dict(
            campaign_id=campaign.pk,
            request_id=uuid4(),
            expected_runtime_version=runtime.version,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
        with pytest.raises(StaleRecordError):
            if action is Action.RETURN_TESTING:
                return_to_testing(**stale)
            else:
                transition_campaign(
                    **stale,
                    expected_version=campaign.version,
                    action=action,
                    token_generation_id=uuid4() if action is Action.ACTIVATE else None,
                )
    assert sorted(results) == ["committed", "retry"]
    assert SystemConfiguration.objects.get().version == runtime.version + 1
    assert CampaignTransition.objects.count() == count + (
        action is not Action.RETURN_TESTING
    )


def concurrent_calls(*callbacks):
    """Race separately connected owners; return typed admission/conflict outcomes."""
    from parishkit.stewardship.campaigns.admission import CampaignAdmissionUnavailable

    barrier = Barrier(len(callbacks))

    def run(callback):
        """The barrier precedes every owning lock and never shares a DB connection."""
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            result = callback()
            return getattr(result, "state", "committed")
        except (
            ConfigurationBusy,
            StaleRecordError,
            CampaignAdmissionUnavailable,
        ):
            return "retry"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=len(callbacks)) as executor:
        return list(executor.map(run, callbacks))


def test_competing_reopen_candidates_have_one_atomic_winner(tmp_path):
    """Only one extended YAML, runtime/token selection and reopen command commits."""
    from parishkit.stewardship.accounts.configuration_installation import (
        install_request,
    )

    from .campaign_builders import end_request

    store, campaign, actor = draft_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at):
        command(campaign, actor, Action.ACTIVATE)
    close_campaign(campaign, actor)
    with campaign_clock(campaign.active_configuration.ends_at + timedelta(days=1)):
        candidates = [
            end_request(store, campaign, actor, "reopen", date)
            for date in ("2026-11-10", "2026-11-11")
        ]
        callbacks = [
            lambda request=request: install_request(
                store,
                request_id=request.request_id,
                correlation_id=uuid4(),
                admit_campaign=admit_test_work,
            )
            for request, _ in candidates
        ]
        results = concurrent_calls(*callbacks)
    assert results.count("applied") == 1 and all(
        value in {"applied", "failed", "retry"} for value in results
    )
    campaign.refresh_from_db()
    assert campaign.state == "active"
    assert (
        campaign.active_token_generation_id == candidates[results.index("applied")][1]
    )
    assert CampaignTransition.objects.filter(action="reopen").count() == 1
    assert (
        campaign.active_configuration.configuration_id
        == store.active().version_id
        == SystemConfiguration.objects.get().active_configuration_id
    )


@pytest.mark.parametrize(
    "values",
    [
        {"end_date": "2026-11-11"},
        {"timezone": "America/Los_Angeles"},
    ],
)
def test_draft_end_edit_races_activation_without_partial_configuration(
    tmp_path, values
):
    """Either mutable draft configuration wins, or activation freezes its structure."""
    from parishkit.stewardship.accounts.configuration_installation import (
        install_request,
    )
    from parishkit.stewardship.accounts.configuration_requests import record_request

    store, campaign, actor = draft_campaign(tmp_path)
    runtime = SystemConfiguration.objects.get()
    request = record_request(
        base_digest=store.active().digest,
        patch=[
            {
                "operation": "update",
                "section": "campaigns",
                "id": str(campaign.pk),
                "values": values,
            }
        ],
        actor_id=actor,
        request_key=uuid4(),
        correlation_id=uuid4(),
    )
    with campaign_clock(campaign.active_configuration.starts_at + timedelta(hours=8)):
        results = concurrent_calls(
            lambda: install_request(
                store, request_id=request.request_id, correlation_id=uuid4()
            ),
            lambda: transition_campaign(
                campaign_id=campaign.pk,
                request_id=uuid4(),
                action=Action.ACTIVATE,
                expected_version=campaign.version,
                expected_runtime_version=runtime.version,
                token_generation_id=uuid4(),
                actor_id=actor,
                correlation_id=uuid4(),
                admit=admit_test_work,
            ),
        )
    assert sum(value in {"applied", "committed"} for value in results) == 1
    campaign.refresh_from_db()
    assert (
        campaign.active_configuration.configuration_id
        == store.active().version_id
        == SystemConfiguration.objects.get().active_configuration_id
    )
    assert campaign.state == ("draft" if results[0] == "applied" else "active")
    assert CampaignTransition.objects.filter(action="activate").count() == (
        campaign.state == "active"
    )


@pytest.mark.parametrize("competitor", ["claim", "replace"])
def test_occurrence_claim_races_claim_or_replacement(tmp_path, competitor):
    """The same pending version cannot be both cancelled and dispatched."""
    from parishkit.stewardship.accounts.configuration_installation import (
        install_request,
    )
    from parishkit.stewardship.accounts.configuration_requests import record_request
    from parishkit.stewardship.campaigns.models import (
        OccurrenceTransition,
        ScheduleDefinition,
    )
    from parishkit.stewardship.campaigns.schedules import change_occurrence

    from .campaign_builders import claimed_task, occurrence

    store, campaign, actor = draft_campaign(tmp_path)
    definition = ScheduleDefinition.objects.get()
    with campaign_clock(definition.current_revision.due_at):
        row = occurrence(definition, actor)
        run = claimed_task("schedule_occurrence", row.pk, actor)

        def claim():
            """Attempt ownership of the exact displayed pending version."""
            return change_occurrence(
                occurrence_id=row.pk,
                state="running",
                expected_version=row.version,
                task_id=run.run_id,
                fence=run.fence,
                actor_id=actor,
                correlation_id=uuid4(),
                admit=admit_test_work,
            )

        if competitor == "claim":
            other = claim
        else:
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

            def other():
                """Try replacing the schedule under the same deployment locks."""
                return install_request(
                    store, request_id=request.request_id, correlation_id=uuid4()
                )

        results = concurrent_calls(claim, other)
    assert sum(value in {"running", "applied"} for value in results) == 1
    row.refresh_from_db()
    definition.refresh_from_db()
    assert (
        row.version == 2
        and OccurrenceTransition.objects.filter(occurrence=row).count() == 2
    )
    assert row.state in {"running", "skipped"}
    assert (row.revision_id == definition.current_revision_id) == (
        row.state == "running"
    )


def test_concurrent_restore_decisions_cannot_overwrite_review(tmp_path):
    """Conflicting reviews create one immutable receipt and one mutable version."""
    from parishkit.stewardship.campaigns.models import (
        RestoreDeliveryHold,
        RestoreHoldResolution,
    )
    from parishkit.stewardship.campaigns.resolutions import resolve_restore_hold

    from .campaign_builders import inventory_values, restored_runtime

    _, campaign, actor = draft_campaign(tmp_path)
    with restored_runtime(campaign.active_configuration.starts_at) as restore_id:
        hold = RestoreDeliveryHold.objects.create(
            **inventory_values(campaign, actor, restore_id)
        )
    results = concurrent_calls(
        *[
            lambda state=state: resolve_restore_hold(
                hold_id=hold.pk,
                expected_version=hold.version,
                state=state,
                evidence="Reviewed",
                actor_id=actor,
                correlation_id=uuid4(),
                admit=admit_test_work,
            )
            for state in ("assumed_delivered", "not_applicable")
        ]
    )
    assert results.count("retry") == 1
    hold.refresh_from_db()
    assert hold.version == 2 and RestoreHoldResolution.objects.count() == 1
    assert hold.state in results


def test_competing_close_workers_commit_one_boundary_history(tmp_path):
    """Repeated close hints cannot split state, tokens, or immutable history."""
    from parishkit.stewardship.campaigns.boundaries import apply_due_boundaries
    from parishkit.stewardship.campaigns.models import CampaignBoundaryOccurrence

    from .campaign_builders import claimed_task

    _, campaign, actor = draft_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at):
        command(campaign, actor, Action.ACTIVATE)
        complete_empty_catchup(campaign, actor)
    runs = [claimed_task("campaign_boundary", campaign.pk, actor) for _ in range(2)]
    with campaign_clock(campaign.active_configuration.ends_at):
        results = concurrent_calls(
            *[
                lambda run=run: apply_due_boundaries(
                    campaign_id=campaign.pk,
                    task_id=run.run_id,
                    fence=run.fence,
                    actor_id=actor,
                    correlation_id=uuid4(),
                    admit=admit_test_work,
                )
                for run in runs
            ]
        )
    assert "committed" in results and set(results) <= {"committed", "retry"}
    campaign.refresh_from_db()
    assert campaign.state == "closed" and campaign.active_token_generation_id is None
    assert CampaignTransition.objects.filter(action="close").count() == 1
    assert (
        CampaignBoundaryOccurrence.objects.filter(
            kind="close", state="succeeded"
        ).count()
        == 1
    )


def test_parish_default_timezone_races_creation_without_rebucketing(tmp_path):
    """Draft timezone is an explicit snapshot; stale parish defaults cannot drift it."""
    from parishkit.stewardship.accounts.configuration_installation import (
        install_request,
    )
    from parishkit.stewardship.accounts.configuration_requests import record_request
    from parishkit.stewardship.campaigns.models import Campaign

    from ..campaign_factory import campaign as campaign_record
    from ..campaign_factory import schedule
    from .campaign_builders import initialized

    store, root, actor = initialized(tmp_path)
    row = campaign_record()
    parish = root.document()["sections"]["parish"][0]
    patches = [
        [
            {"operation": "add", "section": "campaigns", **row},
            {"operation": "add", "section": "schedules", **schedule(row["id"])},
        ],
        [
            {
                "operation": "update",
                "section": "parish",
                "id": parish["id"],
                "values": {"timezone": "America/Chicago"},
            }
        ],
    ]
    requests = [
        record_request(
            base_digest=root.digest,
            patch=patch,
            actor_id=actor,
            request_key=uuid4(),
            correlation_id=uuid4(),
        )
        for patch in patches
    ]
    results = concurrent_calls(
        *[
            lambda request=request: install_request(
                store, request_id=request.request_id, correlation_id=uuid4()
            )
            for request in requests
        ]
    )
    assert results.count("applied") == 1
    assert (
        store.active().version_id
        == SystemConfiguration.objects.get().active_configuration_id
    )
    if results[0] == "applied":
        assert (
            Campaign.objects.get().active_configuration.timezone == "America/New_York"
        )
    else:
        assert not Campaign.objects.exists()

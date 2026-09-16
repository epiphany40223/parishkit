"""Real source/response inputs become complete, reproducible report generations."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.ownership import TaskClaim, TaskOwnershipLost
from parishkit.stewardship.reports.facts import (
    FactUnavailable,
    begin_fact_set,
    stage_fact_days,
)
from parishkit.stewardship.reports.inputs import FactInputs
from parishkit.stewardship.reports.materialization import (
    materialize_fact_set,
    verify_fact_set,
)
from parishkit.stewardship.reports.models import (
    CampaignDailyFactSet,
    CampaignFactPointer,
)

from .response_builders import response_source
from .source_builders import running_source_task
from .test_family_auth_postgresql import login
from .test_response_submission_postgresql import form_and_answers, submit
from .test_source_families_postgresql import prepare, promote
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)


def allocation(harness, *, population="historical", watermark=0, source=None):
    """Bind an actual task lease and exact immutable campaign/source configuration."""
    values = running_source_task()
    claim = TaskClaim(values["task_id"], values["task_fence"], values["worker_id"])
    inputs = FactInputs(
        harness.campaign.pk,
        population,
        (source or harness.snapshot).pk,
        watermark,
        harness.campaign.active_configuration_id,
        harness.campaign.active_configuration.start_date + timedelta(days=1),
    )
    with work_transaction():
        record = begin_fact_set(inputs, claim, admit=permit)
    return record, claim


def respond(harness):
    """Submit through the real validation transaction, with no fabricated versions."""
    form, answers = form_and_answers(harness)
    answers["testing_acknowledged"] = False
    return submit(harness, form, answers).submission


@pytest.mark.parametrize("population", ["historical", "current"])
def test_materialization_and_verification_use_real_live_inputs(
    live_response_service, population
):
    harness = live_response_service
    response = respond(harness)
    record, claim = allocation(
        harness, population=population, watermark=response.campaign_sequence
    )
    ready = materialize_fact_set(record.pk, claim, admit=permit, interactive=True)
    assert ready.state == "ready"
    rows = list(ready.days.order_by("local_date"))
    assert [row.first_responses for row in rows] == [1, 0]
    assert [row.cumulative_responses for row in rows] == [1, 1]
    assert [row.cohort_denominator for row in rows] == [1, 1]
    assert all(not row.pledge_available and row.pledge_total is None for row in rows)
    assert CampaignFactPointer.objects.get().fact_set_id == ready.pk
    assert verify_fact_set(ready.pk, admit=permit) == ()


@pytest.mark.parametrize("population", ["historical", "current"])
def test_frozen_watermark_does_not_follow_a_submission_arriving_before_build(
    live_response_service, population
):
    harness = live_response_service
    record, claim = allocation(harness, population=population)
    assert respond(harness).campaign_sequence == 1
    ready = materialize_fact_set(record.pk, claim, admit=permit)
    assert list(ready.days.values_list("cumulative_responses", flat=True)) == [0, 0]
    assert verify_fact_set(record.pk, admit=permit) == ()


def test_repeated_real_submission_does_not_duplicate_participation(
    live_response_service,
):
    harness = live_response_service
    respond(harness)
    client, response = login(harness.code)
    harness = replace(harness, client=client, request=response.wsgi_request)
    assert respond(harness).campaign_sequence == 2
    record, claim = allocation(harness, watermark=2)
    ready = materialize_fact_set(record.pk, claim, admit=permit)
    assert list(
        ready.days.order_by("local_date").values_list("first_responses", flat=True)
    ) == [1, 0]


def test_test_responses_do_not_enter_live_facts(response_service):
    harness = response_service
    form, answers = form_and_answers(harness)
    assert submit(harness, form, answers).submission.mode == "test"
    record, claim = allocation(harness, watermark=1)
    ready = materialize_fact_set(record.pk, claim, admit=permit)
    assert not ready.days.exclude(cumulative_responses=0).exists()


def test_source_inactivation_changes_only_new_current_population(live_response_service):
    harness = live_response_service
    respond(harness)
    original, original_claim = allocation(harness, population="current", watermark=1)
    data = response_source()
    data.family_groups[7] = "Inactive"
    snapshot, lease = prepare(data)
    snapshot = promote(snapshot, lease, harness.campaign, harness.rings)
    for population, expected in (("historical", 1), ("current", 0)):
        record, claim = allocation(
            harness, population=population, watermark=1, source=snapshot
        )
        ready = materialize_fact_set(record.pk, claim, admit=permit)
        assert set(
            ready.days.values_list("cohort_denominator", "cumulative_responses")
        ) == {(expected, expected)}
        assert verify_fact_set(record.pk, admit=permit) == ()
    # Building after the refresh still uses its exact old snapshot, never today's
    # mutable FamilyCampaign.portal_eligible or effective-response selector.
    ready = materialize_fact_set(original.pk, original_claim, admit=permit)
    assert set(ready.days.values_list("cumulative_responses", flat=True)) == {1}
    assert verify_fact_set(original.pk, admit=permit) == ()


def test_rejected_admission_and_lost_claim_cannot_publish(response_service):
    record, claim = allocation(response_service)
    with pytest.raises(PermissionError):
        materialize_fact_set(record.pk, claim, admit=lambda *args: False)
    with pytest.raises(TaskOwnershipLost):
        materialize_fact_set(record.pk, replace(claim, worker_id=uuid4()), admit=permit)
    assert CampaignDailyFactSet.objects.get(pk=record.pk).state == "building"
    assert not record.days.exists()


def test_verification_detects_coherent_but_wrong_stored_values(response_service):
    from parishkit.stewardship.reports.facts import fact_inputs, publish_fact_set

    from .fact_builders import staged_facts

    record, claim = allocation(response_service, population="current")
    record, _ = staged_facts(fact_inputs(record), claim, response_service.snapshot)
    publish_fact_set(record.pk, claim, admit=permit)
    assert verify_fact_set(record.pk, admit=permit) == tuple(
        record.first_date + timedelta(days=index)
        for index in range(record.expected_count)
    )


def test_conflicting_partial_chunk_is_never_overwritten(response_service):
    record, claim = allocation(response_service, population="current")
    snapshot = response_service.snapshot
    stage_fact_days(
        record.pk,
        claim,
        days=[
            dict(
                local_date=record.first_date,
                first_responses=0,
                cumulative_responses=0,
                cohort_denominator=99,
                source_generation=snapshot.generation,
                source_as_of=snapshot.promoted_at,
                population_available=True,
                pledge_available=False,
                pledge_total=None,
            )
        ],
        admit=permit,
    )
    with pytest.raises(FactUnavailable, match="differs"):
        materialize_fact_set(record.pk, claim, admit=permit)
    assert CampaignDailyFactSet.objects.get(pk=record.pk).state == "building"
    assert record.days.get().cohort_denominator == 99


def test_historical_rebuild_survives_compacted_source_membership(tmp_path, monkeypatch):
    """The permanent cutoff and first eligibility suffice after real compaction."""
    from types import SimpleNamespace

    from parishkit.stewardship.source import snapshots
    from parishkit.stewardship.source.leases import _now
    from parishkit.stewardship.source.models import SourceCurrent, SourceMutationLease
    from parishkit.stewardship.source.version_models import SnapshotFamily

    from .test_source_compaction_postgresql import cleanup
    from .test_source_families_postgresql import setup

    campaign, rings = setup(tmp_path)
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)
    with work_transaction():
        now = _now()
    old = (now - timedelta(days=100)).replace(hour=10, minute=0, second=0)
    history = []
    for instant in (old, old + timedelta(hours=1), now):
        with monkeypatch.context() as changed:
            changed.setattr(snapshots, "_now", lambda instant=instant: instant)
            snapshot, lease = prepare(response_source())
            history.append(promote(snapshot, lease, campaign, rings))
    harness = SimpleNamespace(campaign=campaign, snapshot=history[0])
    record, claim = allocation(harness)
    assert cleanup().snapshot_count == 1
    history[0].refresh_from_db()
    assert history[0].compacted_at is not None
    assert not SnapshotFamily.objects.filter(snapshot=history[0]).exists()
    ready = materialize_fact_set(record.pk, claim, admit=permit)
    assert set(ready.days.values_list("cohort_denominator", flat=True)) == {1}
    assert verify_fact_set(record.pk, admit=permit) == ()

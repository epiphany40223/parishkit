"""Authorized current/stale report selection inside actual read-only guards."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import connection

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.export_tasks import load_document
from parishkit.stewardship.reports.facts import (
    begin_fact_set,
    fact_inputs,
    publish_fact_set,
    stage_fact_days,
)
from parishkit.stewardship.reports.models import CampaignFactPointer
from parishkit.stewardship.reports.selection import current_inputs, participation_report

from ..policy_factory import address, assignment
from .test_background_grants_postgresql import task_login
from .test_export_authorization_postgresql import add_policy
from .test_export_jobs_postgresql import request_export, scenario  # noqa: F401
from .test_policy_postgresql import user
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)


def select(setup, **options):
    """Supply the same owner/scope/response inputs a portal adapter will bind."""
    store, principal, facts, _ = setup
    values = dict(
        campaign_id=facts.campaign_id,
        population_scope=facts.population_scope,
        browser_timezone="America/New_York",
        abort=lambda: None,
    )
    return participation_report(store, principal.pk, **(values | options))


def pointer(facts):
    """Publish a synthetic ready fixture through its guarded reference table."""
    return CampaignFactPointer.objects.create(
        campaign_id=facts.campaign_id,
        population_scope=facts.population_scope,
        fact_set=facts,
    )


def ready_current(old):
    """Stage a deterministic zero-response series, including an empty date range."""
    from parishkit.stewardship.jobs.ownership import TaskClaim

    inputs, _ = current_inputs(old.campaign_id, old.population_scope)
    claim = TaskClaim(old.task_id, old.task_fence, old.worker_id)
    exact = begin_fact_set(inputs, claim, admit=permit)
    stage_fact_days(
        exact.pk,
        claim,
        admit=permit,
        days=[
            dict(
                local_date=exact.first_date + timedelta(days=index),
                first_responses=0,
                cumulative_responses=0,
                cohort_denominator=10,
                source_generation=old.source_generation,
                source_as_of=old.source.promoted_at,
                population_available=True,
                pledge_available=False,
                pledge_total=None,
            )
            for index in range(exact.expected_count)
        ],
    )
    return publish_fact_set(exact.pk, claim, admit=permit), inputs


def test_missing_generation_is_unavailable_not_a_zero_chart(scenario):  # noqa: F811
    with task_login(ServiceRole.WEB, reconnect=True), select(scenario) as report:
        assert report.status == "unavailable"
        assert report.updating
        assert report.document is report.selected is None
        assert report.expected is not None
        with connection.cursor() as cursor:
            cursor.execute("SHOW transaction_read_only")
            assert cursor.fetchone() == ("on",)


def test_stale_document_carries_its_own_asof_and_matches_export(scenario):  # noqa: F811
    _, _, facts, _ = scenario
    pointer(facts)
    request = request_export(scenario)
    exported = load_document(request)
    with task_login(ServiceRole.WEB, reconnect=True), select(scenario) as report:
        assert report.status == "updating" and report.updating
        assert report.selected == fact_inputs(facts)
        assert report.selected != report.expected
        assert report.document == replace(exported, requested_at=report.selected_at)
        assert report.document.submission_watermark == facts.submission_watermark
        assert report.document.source_generation == facts.source_generation


def test_exact_ready_generation_wins_without_mutating_pointer(scenario):  # noqa: F811
    _, _, old, _ = scenario
    reference = pointer(old)
    exact, inputs = ready_current(old)
    with task_login(ServiceRole.WEB, reconnect=True), select(scenario) as report:
        assert report.status == "current" and not report.updating
        assert report.selected == report.expected == inputs
        assert report.document.fact_set_id == exact.pk
    reference.refresh_from_db()
    assert reference.fact_set_id == old.pk


def test_population_scope_never_falls_back_to_another_scope(scenario):  # noqa: F811
    pointer(scenario[2])
    with select(scenario, population_scope="historical") as report:
        assert report.status == "unavailable"
        assert report.document is None
        assert report.expected.population_scope == "historical"


@pytest.mark.parametrize(
    "options",
    [
        {"campaign_id": "not-a-uuid"},
        {"population_scope": []},
        {"population_scope": "all"},
        {"browser_timezone": []},
        {"browser_timezone": "not-a-timezone"},
        {"abort": None},
    ],
)
def test_malformed_request_is_rejected_before_reading(scenario, options):  # noqa: F811
    with pytest.raises((ValueError, TypeError)), select(scenario, **options):
        pytest.fail("Malformed selections must not reach report data")


def test_staff_can_read_and_leader_cannot_even_with_assignment(scenario):  # noqa: F811
    pointer(scenario[2])
    add_policy(
        scenario,
        address("staff@example.org", ("staff",)),
        address("leader@example.org", ("ministry_leader",)),
        assignment(),
    )
    store, _, facts, root = scenario
    staff = user("staff@example.org")
    with (
        task_login(ServiceRole.WEB, reconnect=True),
        select((store, staff, facts, root)) as report,
    ):
        assert report.document.fact_set_id == facts.pk
    leader = user("leader@example.org")
    with pytest.raises(PermissionError), select((store, leader, facts, root)):
        pytest.fail("A Ministry assignment cannot grant parish-wide financial data")


def test_unknown_campaign_is_not_an_empty_authorized_report(scenario):  # noqa: F811
    from parishkit.stewardship.campaigns.read_guards import ReadUnavailable

    with pytest.raises(ReadUnavailable), select(scenario, campaign_id=uuid4()):
        pytest.fail("An unknown campaign must fail closed")


def test_no_source_has_no_invented_input_cutoff(tmp_path):
    """A configured campaign need not yet have a successfully promoted corpus."""
    from parishkit.stewardship.campaigns.credential_models import (
        CampaignCredentialState,
    )

    from .campaign_builders import draft_campaign

    store, campaign, _ = draft_campaign(tmp_path)
    CampaignCredentialState.objects.create(campaign=campaign)
    principal = user("admin@example.org")
    with participation_report(
        store,
        principal.pk,
        campaign_id=campaign.pk,
        browser_timezone="UTC",
        abort=lambda: None,
    ) as report:
        assert report.expected is report.document is report.selected is None
        assert report.status == "unavailable" and report.updating


def test_input_capture_counts_only_live_submissions(response_service):
    """Test versions cannot advance the watermark used for current freshness."""
    from .test_response_submission_postgresql import form_and_answers, submit

    harness = response_service
    form, answers = form_and_answers(harness)
    submit(harness, form, answers)
    inputs, instant = current_inputs(harness.campaign.pk, "historical")
    assert inputs.submission_watermark == 0
    assert inputs.source_id == harness.snapshot.pk
    assert instant.utcoffset().total_seconds() == 0


def test_live_response_advances_request_time_watermark(live_response_service):
    """Currentness follows the accepted live version even before another build."""
    from .test_fact_materialization_postgresql import respond

    harness = live_response_service
    before, _ = current_inputs(harness.campaign.pk, "historical")
    response = respond(harness)
    after, _ = current_inputs(harness.campaign.pk, "historical")
    assert before.submission_watermark == 0
    assert after.submission_watermark == response.campaign_sequence == 1
    assert replace(after, submission_watermark=0) == before


def test_revoked_user_cannot_select_a_previous_report(scenario):  # noqa: F811
    _, principal, facts, _ = scenario
    pointer(facts)
    principal.disabled = True
    principal.version += 1
    principal.save()
    with pytest.raises(type(principal).DoesNotExist), select(scenario):
        pytest.fail("Previously authorized data cannot bypass fresh account checks")


def test_lost_candidate_reports_unavailable_without_substituting(scenario, monkeypatch):  # noqa: F811
    """A selector losing cleanup reports no data, never an unrelated ready row."""
    from contextlib import contextmanager

    from parishkit.stewardship.reports import selection
    from parishkit.stewardship.reports.facts import FactUnavailable

    pointer(scenario[2])

    @contextmanager
    def gone(identifier, *, admit):
        """Inject the narrow race already tested with actual locks in retention."""
        raise FactUnavailable("Generation was compacted.")
        yield  # pragma: no cover

    monkeypatch.setattr(selection, "read_fact_set", gone)
    with select(scenario) as report:
        assert report.status == "unavailable"
        assert report.document is None


def test_busy_exact_candidate_uses_only_published_scoped_fallback(
    scenario,  # noqa: F811
    monkeypatch,
):
    """A busy exact generation cannot stop a complete, labeled fallback response."""
    from contextlib import contextmanager

    from parishkit.stewardship.reports import selection
    from parishkit.stewardship.reports.facts import FactUnavailable

    old = scenario[2]
    pointer(old)
    exact, _ = ready_current(old)
    original = selection.read_fact_set
    visited = []

    @contextmanager
    def busy(identifier, *, admit):
        """Only the exact candidate loses; the pointer uses its real read lock."""
        visited.append(identifier)
        if identifier == exact.pk:
            raise FactUnavailable("Exact generation is busy.")
        with original(identifier, admit=admit) as record:
            yield record

    monkeypatch.setattr(selection, "read_fact_set", busy)
    with select(scenario) as report:
        assert report.document.fact_set_id == old.pk
        assert report.status == "updating"
        assert report.selected == fact_inputs(old)
        assert visited == [exact.pk, old.pk]


def test_exact_pointer_candidate_is_attempted_once(scenario, monkeypatch):  # noqa: F811
    """Exact and pointer identity deduplicate even if read protection is busy."""
    from contextlib import contextmanager

    from parishkit.stewardship.reports import selection
    from parishkit.stewardship.reports.facts import FactUnavailable

    exact, _ = ready_current(scenario[2])
    pointer(exact)
    visited = []

    @contextmanager
    def busy(identifier, *, admit):
        """Count attempts while preserving the real candidate-selection queries."""
        visited.append(identifier)
        raise FactUnavailable("Busy generation.")
        yield  # pragma: no cover

    monkeypatch.setattr(selection, "read_fact_set", busy)
    with select(scenario) as report:
        assert report.status == "unavailable"
        assert visited == [exact.pk]


@pytest.mark.parametrize("respond_first", [False, True])
def test_request_inputs_match_real_producer_for_both_scopes(
    live_response_service, respond_first
):
    """Prevent reader/producer drift while preserving their distinct lock owners."""
    from parishkit.stewardship.campaigns.work_locks import work_transaction
    from parishkit.stewardship.reports import fact_production
    from parishkit.stewardship.reports.demand import requested_inputs

    from .test_fact_materialization_postgresql import respond

    harness = live_response_service
    if respond_first:
        respond(harness)
    expected, _ = current_inputs(harness.campaign.pk, "historical")
    # The fixture binds the shared campaign SQL clock. Both services must use
    # that same production clock, rather than independently inventing "today".
    with work_transaction():
        demands = fact_production.hint_current_facts(harness.campaign.pk)
    assert {row.population_scope for row in demands} == {"historical", "current"}
    for row in demands:
        assert requested_inputs(row) == replace(
            expected, population_scope=row.population_scope
        )

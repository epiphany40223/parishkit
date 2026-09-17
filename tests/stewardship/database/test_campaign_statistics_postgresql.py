"""Single-statement statistics capture with real source and authorization guards."""

from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.statistics import calculate_statistics
from parishkit.stewardship.reports.statistics_selection import (
    capture_statistics,
    statistics_report,
)

from .test_background_grants_postgresql import task_login
from .test_financial_source_postgresql import financial_source
from .test_policy_postgresql import user

pytestmark = pytest.mark.django_db(transaction=True)


def selection(harness, principal, **options):
    """Bind the real configuration store and consumer guard without a new route."""
    return statistics_report(
        harness.service.store,
        principal.pk,
        **dict(campaign_id=harness.campaign.pk, abort=lambda: None, **options),
    )


def test_single_statement_captures_current_population_and_safe_projection(
    response_service,
):
    harness = response_service
    with CaptureQueriesContext(connection) as queries:
        inputs = capture_statistics(harness.campaign.pk)
    assert len(queries) == 1
    document = inputs.document()
    assert document["source"]["id"] == str(harness.snapshot.pk)
    assert document["submission_watermark"] == 0
    assert all(
        set(value)
        == {
            "schema_version",
            "active",
            "parishioner",
            "portal_eligible",
            "email_eligible",
            "active_head_duids",
        }
        for value in document["corpus"]["family"].values()
    )
    result = calculate_statistics(inputs)
    assert result.active.families == 1
    assert result.active.responses == 0
    assert set(document["corpus"]["contact"]) == {"member:3"}


@pytest.mark.parametrize("covered,empty", [(True, False), (True, True), (False, False)])
def test_actual_web_role_financial_source_and_readonly_guard(
    response_service, covered, empty
):
    harness = response_service
    snapshot, _ = financial_source(harness, covered=covered, empty=empty)
    principal = user("admin@example.org")
    with (
        task_login(ServiceRole.WEB, reconnect=True),
        statistics_report(
            harness.service.store,
            principal.pk,
            campaign_id=harness.campaign.pk,
            abort=lambda: None,
        ) as selected,
    ):
        assert selected.statistics.source_id == snapshot.pk
        assert selected.statistics.active.comparison_pledge.canonical == (
            None if not covered else "0.00" if empty else "1200.00"
        )
        assert calculate_statistics(selected.inputs) == selected.statistics
        # The complete source includes a never-eligible Family's pledge, but
        # only our report population's private values may leave the database.
        assert "9999.00" not in selected.inputs.canonical
        if covered and not empty:
            assert selected.inputs.document()["source"]["pledge_count"] == 2
            assert len(selected.inputs.document()["pledges"]) == 1
        with connection.cursor() as cursor:
            cursor.execute("SHOW transaction_read_only")
            assert cursor.fetchone() == ("on",)


@pytest.mark.parametrize("role", [ServiceRole.WEB, ServiceRole.WORKER])
def test_compiled_roles_can_capture_without_new_mutation_authority(
    response_service, role
):
    with task_login(role, reconnect=True):
        result = calculate_statistics(capture_statistics(response_service.campaign.pk))
    assert result.active.families == 1


@pytest.mark.parametrize("live", [False, True])
def test_latest_live_annual_pledge_counts_a_family_once(response_service, live):
    """Use real form validation/submission, never synthetic response INSERTs."""
    from .response_builders import activate_response_service
    from .test_ministry_responses_postgresql import respond, revisit
    from .test_response_http_postgresql import answers_for, load_form

    harness = response_service
    financial_source(harness)
    if live:
        harness = activate_response_service(harness)
    before = capture_statistics(harness.campaign.pk)
    form = load_form(harness)
    answers = answers_for(form)
    answers["financial"] = {
        "annual_pledge": "100.01",
        "frequency": "monthly",
        "shares": {},
    }
    first = respond(harness, form, answers)
    first_inputs = capture_statistics(harness.campaign.pk)
    form = revisit(harness)
    answers = answers_for(form)
    answers["financial"] = {
        "annual_pledge": "250.13",
        "frequency": "weekly",
        "shares": {},
    }
    second = respond(harness, form, answers)
    after = calculate_statistics(capture_statistics(harness.campaign.pk))
    assert first.pk != second.pk and second.family_version == 2
    assert after.submission_watermark == (2 if live else 0)
    assert after.active.responses == int(live)
    assert after.active.annual_pledge.canonical == ("250.13" if live else "0.00")
    assert calculate_statistics(before).active.responses == 0
    assert calculate_statistics(first_inputs).active.annual_pledge.canonical == (
        "100.01" if live else "0.00"
    )


def test_refusal_then_source_correction_changes_only_new_observations(
    live_response_service,
):
    from .response_builders import response_source
    from .test_recipient_suppressions_postgresql import refresh, refused, remember

    harness = live_response_service
    before = capture_statistics(harness.campaign.pk)
    remember(refused(harness))
    refused_inputs = capture_statistics(harness.campaign.pk)
    assert calculate_statistics(refused_inputs).active.eligible_email == 1
    assert calculate_statistics(refused_inputs).active.deliverable_email == 0
    corrected = response_source()
    corrected.members[3]["emailAddress"] = "corrected@example.org"
    refresh(harness, corrected)
    after = capture_statistics(harness.campaign.pk)
    assert calculate_statistics(after).active.deliverable_email == 1
    assert calculate_statistics(before).active.deliverable_email == 1
    assert calculate_statistics(refused_inputs).active.deliverable_email == 0
    assert after.document()["refusals"] == []
    assert after.document()["source"]["id"] != before.document()["source"]["id"]


def test_source_inactivation_preserves_response_only_in_separate_subtotal(
    live_response_service,
):
    from .response_builders import response_source
    from .test_fact_materialization_postgresql import respond
    from .test_source_families_postgresql import prepare, promote

    harness = live_response_service
    respond(harness)
    before = capture_statistics(harness.campaign.pk)
    data = response_source()
    data.family_groups[7] = "Inactive"
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    after = capture_statistics(harness.campaign.pk)
    result = calculate_statistics(after, include_inactive=True)
    assert result.active.families == result.active.responses == 0
    assert result.inactive.families == result.inactive.responses == 1
    assert calculate_statistics(before).active.responses == 1


def test_frozen_capture_survives_a_concurrent_refusal_before_consumption(
    live_response_service,
):
    """A writer commits after SELECT executes but before the consumer gets its row."""
    from concurrent.futures import ThreadPoolExecutor

    from django.db import connections

    from .test_recipient_suppressions_postgresql import refused, remember

    harness = live_response_service
    event = refused(harness)
    reached = []

    def refuse():
        """Use a separate real transaction while the report reader stays open."""
        try:
            return remember(event).pk
        finally:
            connections.close_all()

    def after_execute(execute, sql, params, many, context):
        """Interleave only the one private snapshot query, not admission checks."""
        result = execute(sql, params, many, context)
        if "campaign-statistics-v1" in sql:
            with ThreadPoolExecutor(max_workers=1) as pool:
                reached.append(pool.submit(refuse).result(timeout=15))
        return result

    principal = user("admin@example.org")
    with (
        connection.execute_wrapper(after_execute),
        selection(harness, principal) as report,
    ):
        assert report.statistics.active.deliverable_email == 1
    assert len(reached) == 1
    assert (
        calculate_statistics(
            capture_statistics(harness.campaign.pk)
        ).active.deliverable_email
        == 0
    )


def test_staff_allowed_but_assigned_leader_denied(response_service):
    from ..policy_factory import address, assignment
    from .test_export_authorization_postgresql import add_policy

    harness = response_service
    admin = user("admin@example.org")
    add_policy(
        (harness.service.store, admin, None, None),
        address("staff@example.org", ("staff",)),
        address("leader@example.org", ("ministry_leader",)),
        assignment(),
    )
    with selection(harness, user("staff@example.org")) as selected:
        assert selected.statistics.active.families == 1
    with pytest.raises(PermissionError), selection(harness, user("leader@example.org")):
        pytest.fail("Ministry scope cannot expose parish financial statistics")


def test_revoked_user_is_denied_before_private_capture(response_service, monkeypatch):
    from parishkit.stewardship.reports import statistics_selection

    principal = user("admin@example.org")
    principal.disabled = True
    principal.version += 1
    principal.save()

    def forbidden(identifier):
        """A revoked request must not even execute the private projection query."""
        pytest.fail("Revoked reader reached private input capture")

    monkeypatch.setattr(statistics_selection, "capture_statistics", forbidden)
    with (
        pytest.raises(type(principal).DoesNotExist),
        selection(response_service, principal),
    ):
        pytest.fail("Revoked users cannot read old or current statistics")


@pytest.mark.parametrize("options", [{"include_inactive": 1}, {"abort": None}])
def test_malformed_report_options_fail_before_data(response_service, options):
    principal = user("admin@example.org")
    with (
        pytest.raises((TypeError, ValueError)),
        statistics_report(
            response_service.service.store,
            principal.pk,
            **(
                dict(campaign_id=response_service.campaign.pk, abort=lambda: None)
                | options
            ),
        ),
    ):
        pytest.fail("Malformed requests cannot produce a report")


def test_unknown_campaign_is_not_an_empty_report(response_service):
    from parishkit.stewardship.campaigns.read_guards import ReadUnavailable

    with (
        pytest.raises(ReadUnavailable),
        statistics_report(
            response_service.service.store,
            user("admin@example.org").pk,
            campaign_id=uuid4(),
            abort=lambda: None,
        ),
    ):
        pytest.fail("Unknown campaign cannot become an available empty report")


def test_configured_campaign_without_source_is_unavailable(tmp_path):
    from .campaign_builders import draft_campaign

    store, campaign, _ = draft_campaign(tmp_path)
    with statistics_report(
        store,
        user("admin@example.org").pk,
        campaign_id=campaign.pk,
        abort=lambda: None,
    ) as selected:
        assert selected.statistics.active is None
        assert selected.statistics.source_id is None


def test_archived_financial_observation_does_not_follow_a_new_global_source(
    response_service,
):
    """Archived cards use their own retained giving window, not a successor's."""
    from parishkit.stewardship.campaigns.lifecycle import Action
    from parishkit.stewardship.campaigns.work_locks import work_transaction
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.storage import _status
    from parishkit.stewardship.source.leases import release_source
    from parishkit.stewardship.source.snapshots import promote_snapshot

    from .campaign_builders import campaign_clock, close_campaign, command
    from .response_builders import activate_response_service, response_source
    from .test_source_families_postgresql import prepare
    from .test_source_snapshots_postgresql import permit
    from .test_taskrun_postgresql import act

    retained, _ = financial_source(response_service)
    harness = activate_response_service(response_service)
    actor = uuid4()
    close_campaign(harness.campaign, actor)
    for row in TaskRun.objects.filter(state="running"):
        act(_status(row), "permanent_failure")
    with campaign_clock(harness.campaign.active_configuration.ends_at):
        command(harness.campaign, actor, Action.ARCHIVE)
        snapshot, claim = prepare(response_source())
        try:
            with work_transaction():
                promote_snapshot(snapshot.pk, claim, admit=permit, reconcile=permit)
        finally:
            release_source(claim)
        result = calculate_statistics(capture_statistics(harness.campaign.pk))
    assert result.source_id == retained.pk
    assert result.active.comparison_pledge.canonical == "1200.00"
    assert result.giving is not None


def test_reference_population_capture_is_one_query_and_within_page_budget(
    response_service,
):
    """Use 5,000 actual normalized households, not synthetic Python-only inputs."""
    from dataclasses import replace
    from time import perf_counter

    from .response_builders import response_source
    from .test_source_families_postgresql import prepare, promote

    data = replace(
        response_source(),
        families={
            index: dict(familyDUID=index, registeredOrganizationID=5, famGroupID=7)
            for index in range(1, 5001)
        },
        members={
            index + 10000: dict(
                memberDUID=index + 10000,
                familyDUID=index,
                memberType="Head",
                memberStatus="Active",
                emailAddress=f"head{index}@example.org",
            )
            for index in range(1, 5001)
        },
        member_contactinfos={},
        ministry_types={},
        ministry_type_memberships={},
    )
    snapshot, claim = prepare(data)
    promote(snapshot, claim, response_service.campaign, response_service.rings)
    timings = []
    with task_login(ServiceRole.WEB, reconnect=True):
        for _ in range(20):
            with CaptureQueriesContext(connection) as queries:
                started = perf_counter()
                result = calculate_statistics(
                    capture_statistics(response_service.campaign.pk)
                )
                timings.append(perf_counter() - started)
            assert len(queries) == 1
            assert result.active.families == result.active.active_members == 5000
            assert (
                result.active.eligible_email == result.active.deliverable_email == 5000
            )
    p95 = sorted(timings)[18]
    assert p95 < 2.0, {"p95_seconds": p95, "sample_seconds": timings}


def test_nonparishioner_head_address_never_enters_private_projection(response_service):
    from .response_builders import response_source
    from .test_source_families_postgresql import prepare, promote

    data = response_source()
    data.families[2]["registeredOrganizationID"] = 8
    data.members[4] = dict(
        memberDUID=4,
        familyDUID=2,
        memberStatus="Active",
        memberType="Head",
        emailAddress="private-unrelated-head@example.org",
    )
    snapshot, claim = prepare(data)
    promote(snapshot, claim, response_service.campaign, response_service.rings)
    inputs = capture_statistics(response_service.campaign.pk)
    assert "private-unrelated-head" not in inputs.canonical
    assert "member:4" not in inputs.document()["corpus"]["contact"]
    assert calculate_statistics(inputs).active.deliverable_email == 1


def test_inactive_family_refusal_and_head_address_are_not_detached(
    live_response_service,
):
    from parishkit.stewardship.jobs.recipient_models import RecipientRefusal

    from .response_builders import response_source
    from .test_recipient_suppressions_postgresql import refresh, refused, remember

    harness = live_response_service
    remember(refused(harness))
    data = response_source()
    data.family_groups[7] = "Inactive"
    refresh(harness, data)
    assert RecipientRefusal.objects.count() == 1
    inputs = capture_statistics(harness.campaign.pk)
    assert inputs.document()["refusals"] == []
    assert inputs.document()["corpus"]["contact"] == {}
    assert "valid@example.org" not in inputs.canonical
    assert calculate_statistics(inputs, include_inactive=True).inactive.families == 1


@pytest.mark.parametrize("corrected_email", ["corrected@example.org", "invalid-text"])
def test_stale_unresolved_address_is_not_detached_for_an_active_family(
    live_response_service, corrected_email
):
    """Even late refusal evidence must match the selected current head address."""
    from .response_builders import response_source
    from .test_recipient_suppressions_postgresql import refresh, refused, remember

    harness = live_response_service
    event = refused(harness)
    corrected = response_source()
    corrected.members[3]["emailAddress"] = corrected_email
    refresh(harness, corrected)
    # Evidence can arrive after the source changed; its original immutable
    # address remains valid history but is not a current statistics input.
    remember(event)
    inputs = capture_statistics(harness.campaign.pk)
    assert inputs.document()["refusals"] == []
    assert "valid@example.org" not in inputs.canonical
    assert calculate_statistics(inputs).active.deliverable_email == int(
        corrected_email == "corrected@example.org"
    )


def test_higher_testing_sequence_cannot_override_first_live_observation(
    response_service,
):
    from parishkit.stewardship.responses.models import Submission

    from .response_builders import activate_response_service
    from .test_ministry_responses_postgresql import respond, revisit
    from .test_response_http_postgresql import answers_for, load_form

    harness = response_service
    for index in range(2):
        form = load_form(harness) if index == 0 else revisit(harness)
        respond(harness, form, answers_for(form))
    assert Submission.objects.filter(mode="test", campaign_sequence=2).exists()
    assert capture_statistics(harness.campaign.pk).document()["responses"] == []
    harness = activate_response_service(harness)
    assert not Submission.objects.filter(mode="test").exists()
    form = load_form(harness)
    respond(harness, form, answers_for(form))
    result = calculate_statistics(capture_statistics(harness.campaign.pk))
    assert result.submission_watermark == result.active.responses == 1


def test_incomplete_draft_giving_mapping_keeps_population_readable(response_service):
    """Draft editing may enable financial before the required period is supplied."""
    from .campaign_builders import change

    harness = response_service
    receipt = change(
        harness.service.store,
        harness.service.store.active(),
        uuid4(),
        [
            dict(
                operation="update",
                section="campaigns",
                id=str(harness.campaign.pk),
                values=dict(modules=["financial"], financial=None),
            )
        ],
    )
    assert receipt.state == "applied"
    with selection(harness, user("admin@example.org")) as selected:
        result = selected.statistics
        assert result.financial_enabled and result.financial is None
        assert result.active.families == 1
        assert not result.active.comparison_pledge.available


@pytest.mark.parametrize(
    "excluded", [{"effective_date": "2027-01-01"}, {"fund_key": "4"}]
)
def test_unmapped_pledge_is_counted_for_coverage_but_not_detached(
    response_service, excluded
):
    from ..financial_factory import record

    financial_source(
        response_service,
        extra_pledges={"203": record("7654.32", **excluded)},
        extra_funds={4: dict(fundId=4, name="Unmapped", active=True)},
    )
    inputs = capture_statistics(response_service.campaign.pk)
    assert "7654.32" not in inputs.canonical
    assert inputs.document()["source"]["pledge_count"] == 3
    assert len(inputs.document()["pledges"]) == 1
    assert calculate_statistics(inputs).active.comparison_pledge.canonical == "1200.00"


@pytest.mark.parametrize("has_valid", [False, True])
def test_invalid_head_email_text_never_leaves_capture(response_service, has_valid):
    """Invalid-only heads and invalid companions are not calculation inputs."""
    from .response_builders import response_source
    from .test_source_families_postgresql import prepare, promote

    data = response_source()
    data.members[3]["emailAddress"] = (
        "valid@example.org; private-invalid-text"
        if has_valid
        else "private-invalid-text"
    )
    snapshot, claim = prepare(data)
    promote(snapshot, claim, response_service.campaign, response_service.rings)
    inputs = capture_statistics(response_service.campaign.pk)
    assert "private-invalid-text" not in inputs.canonical
    contacts = inputs.document()["corpus"]["contact"]
    if has_valid:
        assert contacts["member:3"]["emails"] == [
            dict(valid=True, value="valid@example.org")
        ]
    else:
        assert contacts == {}
    result = calculate_statistics(inputs).active
    assert result.families == 1
    assert result.eligible_email == result.deliverable_email == int(has_valid)

"""Real live submissions, source promotions and least-privilege weekly capture."""

import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, connections
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.weekly_digest import WeeklyCorrection
from parishkit.stewardship.reports.weekly_observation import (
    CAPTURE,
    WeeklyUnavailable,
    capture_weekly_observation,
)
from parishkit.stewardship.reports.weekly_selection import WeeklyHistory, select_weekly

from .response_builders import response_source
from .test_background_grants_postgresql import task_login
from .test_response_revisit_postgresql import revisit
from .test_response_submission_postgresql import form_and_answers, submit
from .test_source_families_postgresql import prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)


def respond(harness, text, *, live=True):
    """Create a real final submission and its transactionally derived item."""
    form, answers = form_and_answers(harness)
    answers.update(additional_information=text, testing_acknowledged=not live)
    return submit(harness, form, answers).submission


def test_capture_is_one_statement_and_excludes_rehearsal_answers(response_service):
    harness = response_service
    respond(harness, "Private testing request", live=False)
    with CaptureQueriesContext(connection) as queries:
        observed = capture_weekly_observation(harness.campaign.pk)
    assert len(queries) == 1
    assert observed.items == () and observed.watermark == 0
    assert observed.source_id == harness.snapshot.pk
    assert observed.configuration_id == harness.campaign.active_configuration_id


def test_unchanged_replaced_and_cleared_text_follow_real_submission_history(
    live_response_service,
):
    harness = live_response_service
    respond(harness, "First private request")
    first = capture_weekly_observation(harness.campaign.pk)
    original = first.items[0].value
    assert first.watermark == 1
    assert original.text == "First private request"
    history = WeeklyHistory(harness.campaign.pk, 1, frozenset({original.item_id}))
    harness, form, answers, _ = revisit(harness)
    submit(harness, form, answers)
    repeated = capture_weekly_observation(harness.campaign.pk)
    assert repeated.watermark == 2 and len(repeated.items) == 1
    assert select_weekly(repeated, history).empty

    harness, form, answers, _ = revisit(harness)
    answers["additional_information"] = "New private request"
    submit(harness, form, answers)
    replaced = capture_weekly_observation(harness.campaign.pk)
    result = select_weekly(replaced, history)
    assert result.information == (replaced.items[1].value,)
    assert result.corrections == (replaced.items[0].value,)
    assert result.corrections[0].disposition == "superseded"
    assert result.information[0].text == "New private request"
    assert original.text == "First private request"  # the first observation is frozen

    history = WeeklyHistory(
        harness.campaign.pk,
        3,
        frozenset({original.item_id, result.information[0].item_id}),
        frozenset({(original.item_id, "superseded")}),
    )
    harness, form, answers, _ = revisit(harness)
    answers["additional_information"] = ""
    submit(harness, form, answers)
    cleared = capture_weekly_observation(harness.campaign.pk)
    result = select_weekly(cleared, history)
    assert result.information == ()
    assert len(result.corrections) == 1
    assert result.corrections[0].disposition == "withdrawn"
    assert cleared.watermark == 4
    # Privacy is enforced by SQL projection, not just by Python object shape.
    with connection.cursor() as cursor:
        cursor.execute(CAPTURE, [harness.campaign.pk])
        raw = cursor.fetchone()[0]
    assert "First private request" not in raw and "New private request" not in raw
    assert all(row[-1] is None for row in json.loads(raw)["items"])


@pytest.mark.parametrize("disposition", ["clear", "replace"])
def test_never_reported_terminal_item_does_not_require_a_correction(
    live_response_service,
    disposition,
):
    harness = live_response_service
    respond(harness, "Unreported request")
    harness, form, answers, _ = revisit(harness)
    answers["additional_information"] = "" if disposition == "clear" else "Replacement"
    submit(harness, form, answers)
    result = select_weekly(
        capture_weekly_observation(harness.campaign.pk),
        WeeklyHistory(harness.campaign.pk),
    )
    assert result.corrections == ()
    assert len(result.information) == int(disposition == "replace")


@pytest.mark.parametrize("change", ["inactive", "removed", "name"])
def test_current_source_changes_never_drop_a_submitted_item(
    live_response_service, change
):
    harness = live_response_service
    respond(harness, "Please follow up")
    before = capture_weekly_observation(harness.campaign.pk)
    data = response_source()
    if change == "inactive":
        data.family_groups[7] = "Inactive"
    elif change == "removed":
        data.families.clear()
        data.members.clear()
        data.member_contactinfos.clear()
        data.ministry_type_memberships.clear()
    else:
        data.families[1]["mailingName"] = "Changed household"
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    with task_login(ServiceRole.WORKER, exact=True):
        after = capture_weekly_observation(harness.campaign.pk)
    assert len(after.items) == 1
    assert after.items[0].value.text == "Please follow up"
    assert after.items[0].value.family_duid == 1
    assert after.source_id == snapshot.pk != before.source_id
    if change == "removed":
        assert after.items[0].value.family_name == "Family"
    elif change == "name":
        assert after.items[0].value.family_name == "Changed household"
        assert before.items[0].value.family_name != "Changed household"


def test_concurrent_replacement_does_not_mix_capture_disposition_and_watermark(
    live_response_service,
):
    harness = live_response_service
    respond(harness, "Captured original request")
    harness, form, answers, _ = revisit(harness)
    answers["additional_information"] = "Concurrent replacement"
    interleaved = []

    def replace_in_another_connection():
        """Commit after capture executes but before its client fetches the result."""
        try:
            return submit(harness, form, answers).submission.pk
        finally:
            connections.close_all()

    def after_execute(execute, sql, params, many, context):
        """Interleave the real writer only at the one private capture statement."""
        result = execute(sql, params, many, context)
        if sql == CAPTURE:
            with ThreadPoolExecutor(max_workers=1) as pool:
                interleaved.append(
                    pool.submit(replace_in_another_connection).result(timeout=15)
                )
        return result

    with connection.execute_wrapper(after_execute):
        observed = capture_weekly_observation(harness.campaign.pk)
    assert len(interleaved) == 1
    assert observed.watermark == 1 and len(observed.items) == 1
    assert observed.items[0].value.text == "Captured original request"
    later = capture_weekly_observation(harness.campaign.pk)
    assert later.watermark == 2 and len(later.items) == 2
    assert type(later.items[0].value) is WeeklyCorrection
    assert later.items[1].value.text == "Concurrent replacement"


@pytest.mark.parametrize("role", [ServiceRole.WORKER, ServiceRole.WEB])
def test_authorized_reader_roles_can_capture_without_answer_mutation(
    live_response_service, role
):
    harness = live_response_service
    respond(harness, "Actual-role request")
    with task_login(role, exact=True):
        observed = capture_weekly_observation(harness.campaign.pk)
        assert observed.items[0].value.text == "Actual-role request"


@pytest.mark.parametrize("role", [ServiceRole.SCHEDULER, ServiceRole.MAIL_DISPATCH])
def test_metadata_and_transport_roles_cannot_read_raw_requests(response_service, role):
    with (
        task_login(role, exact=True),
        pytest.raises(DatabaseError) as error,
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT text FROM stewardship_additional_information")
    assert error.value.__cause__.sqlstate == "42501"


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT answers FROM stewardship_submission",
        "SELECT follow_up_needed FROM stewardship_additional_information",
        "UPDATE stewardship_additional_information SET text='changed'",
        "UPDATE stewardship_additional_information SET disposition='withdrawn'",
        "DELETE FROM stewardship_additional_information",
    ],
)
def test_worker_capture_grants_do_not_include_extra_private_columns_or_writes(
    response_service, statement
):
    with (
        task_login(ServiceRole.WORKER, exact=True),
        pytest.raises(DatabaseError) as error,
        connection.cursor() as cursor,
    ):
        cursor.execute(statement)
    assert error.value.__cause__.sqlstate == "42501"


def test_unknown_campaign_is_not_a_successful_empty_observation(response_service):
    with pytest.raises(WeeklyUnavailable):
        capture_weekly_observation(uuid4())

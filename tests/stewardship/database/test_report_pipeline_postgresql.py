"""The report email pipeline's database objects (#1786, frozen file 0156).

Delivery plan item 3 installs the pipeline unused: the shared guards that
admit a report's occurrence, outbox, pin and cleanup writes are rewritten by
the conversion (item 4), whose tests send reports end to end. These tests
check the new objects directly: the slot evaluation and the next-send rule
against ``ReportPlan`` through the shared case table, the recorded inputs,
cohort and open-request reads against their Python twins, the guards'
refusal of any write without its ownership, and the task-type maps.
"""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.campaigns.schedule_evaluation import SchedulePlan
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.report_email_capture import open_requests
from parishkit.stewardship.reports.report_email_cohort import frozen_cohort
from parishkit.stewardship.reports.report_email_inputs import recorded_inputs
from parishkit.stewardship.responses.information import update_information
from parishkit.stewardship.responses.models import AdditionalInformationItem

from ..test_report_schedule import report
from .auth_builders import signed_in
from .test_background_grants_postgresql import task_login
from .test_leader_emails_postgresql import add_users
from .test_weekly_observation_postgresql import respond
from .user_builders import convert

CASES = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "report_slot_cases.json").read_text()
)["cases"]


def scalar(sql, parameters):
    """One value from the database, as the schema owner runs it."""
    with connection.cursor() as cursor:
        cursor.execute(sql, parameters)
        return cursor.fetchone()[0]


@pytest.mark.django_db
@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_slot_evaluation_matches_the_shared_case_table(case):
    """The database's slot check gives each slot the table's due instant."""
    campaign = case["campaign"]
    values = json.dumps(case["values"])
    for slot, expected in case["slots"].items():
        due = scalar(
            "SELECT stewardship_report_slot_due_v1(%s::jsonb,%s,%s,%s,%s)",
            [
                values,
                campaign["start_date"],
                campaign["end_date"],
                campaign["timezone"],
                slot,
            ],
        )
        assert (due and due.astimezone(UTC).isoformat()) == expected, slot


@pytest.mark.django_db
@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_the_next_send_matches_the_evaluator(case):
    """The page proof's next candidate send is ReportPlan's, from any day.

    The proof ends a dates page only when the next candidate is due after
    the cutoff, so the two must find the same candidate.
    """
    campaign = case["campaign"]
    plan = SchedulePlan.from_values(report(**case["values"]), campaign)
    start = date.fromisoformat(campaign["start_date"])
    for after in [None] + [start + timedelta(days=n) for n in range(-3, 45, 4)]:
        expected = plan._next(date.min if after is None else after)
        found = scalar(
            "SELECT stewardship_report_next_send_v1(%s::jsonb,%s,%s,%s)",
            [
                json.dumps(case["values"]),
                campaign["start_date"],
                campaign["end_date"],
                after,
            ],
        )
        assert found == expected, after


INPUT_CASES = [
    # (as_of, slot basis, covered slots, local capture, fact block)
    ("previous_day_end", "report_date", ["2026-10-08"], "2026-10-09T07:00", True),
    (
        "previous_day_end",
        "report_date",
        ["2026-10-05", "2026-10-08"],
        "2026-10-09T07:00",
        True,
    ),
    ("previous_day_end", "send_date", ["2026-10-09"], "2026-10-09T07:00", True),
    ("previous_day_end", "send_date", [], "2026-10-09T00:30", False),
    ("previous_day_end", "report_date", ["2026-11-06"], "2026-11-07T07:00", True),
    ("previous_day_end", "send_date", ["2026-11-20"], "2026-11-20T07:00", True),
    ("previous_day_end", "send_date", ["2026-10-01"], "2026-10-01T07:00", True),
    ("send_time", "send_date", ["2026-10-12"], "2026-10-12T08:00", True),
    ("send_time", "send_date", [], "2026-10-02T09:00", True),
    ("send_time", "send_date", ["2026-11-09"], "2026-11-09T08:00", False),
    ("previous_day_end", "report_date", ["2026-10-31"], "2026-11-01T01:30", True),
]


@pytest.mark.django_db
@pytest.mark.parametrize("case", INPUT_CASES)
def test_recorded_inputs_match_the_database(case):
    """The guard's derivation of a send's inputs is the worker's, case for case."""
    from zoneinfo import ZoneInfo

    as_of, basis, covered, local, facts = case
    blocks = [{"block": "response_funnel", "version": 1, "options": {}}]
    if facts:
        blocks.append({"block": "campaign_totals", "version": 1, "options": {}})
    values = report(as_of=as_of, slot_basis=basis, blocks=blocks)
    zone = "America/New_York"
    captured = datetime.fromisoformat(local).replace(tzinfo=ZoneInfo(zone))
    captured = captured.astimezone(UTC)
    start, end = date(2026, 10, 5), date(2026, 11, 6)
    expected = recorded_inputs(values, covered, captured, start, end, zone)
    found = json.loads(
        scalar(
            "SELECT stewardship_report_email_inputs_v1("
            "%s::jsonb,%s::jsonb,%s,%s,%s,%s)::text",
            [json.dumps(values), json.dumps(covered), captured, start, end, zone],
        )
    )
    assert found["report_day"] == (
        expected.report_day and expected.report_day.isoformat()
    )
    assert found["through_date"] == (
        expected.through_date and expected.through_date.isoformat()
    )
    assert found["held"] is expected.held
    assert datetime.fromisoformat(found["as_of"]) == expected.as_of


@pytest.mark.django_db(transaction=True)
def test_the_cohort_matches_the_worker_and_expands_the_groups(auth_service):
    """Groups expand to their role's users, users must be eligible, once each."""
    store = auth_service.store
    convert(store)
    add_users(
        store,
        ("Staff.One@example.org", "One, Staff", "staff"),
        ("staff-two@example.org", "Two, Staff", "staff"),
        ("leader@example.org", "Leader, Only", "ministry_leader"),
        ("second-admin@example.org", "Second, Admin", "administrator"),
    )
    configuration = store.active()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT a.active_configuration_id FROM stewardship_system_configuration a"
        )
        configuration_id = cursor.fetchone()[0]
        cursor.execute(
            "SELECT lower(email),record_id::text FROM stewardship_address_rule "
            "WHERE configuration_id=%s",
            [configuration_id],
        )
        records = dict(cursor.fetchall())
    assert configuration is not None
    entries = [
        [{"group": "staff"}],
        [{"group": "administrators"}, {"address": "council@example.org"}],
        [
            {"user": records["leader@example.org"]},
            {"user": records["staff.one@example.org"]},
            {"group": "staff"},
            {"address": "staff-two@example.org"},
        ],
        [{"user": str(uuid4())}],
    ]
    expected = [
        {"staff.one@example.org", "staff-two@example.org"},
        {"admin@example.org", "second-admin@example.org", "council@example.org"},
        {"staff.one@example.org", "staff-two@example.org"},
        set(),
    ]
    for recipients, addresses in zip(entries, expected, strict=True):
        values = report(recipients=recipients)
        python = frozen_cohort(values, configuration_id)
        sql = json.loads(
            scalar(
                "SELECT stewardship_report_email_cohort_v1(%s::jsonb,%s)::text",
                [json.dumps(values), configuration_id],
            )
        )
        assert python == sql
        assert {member["address"] for member in sql} == addresses
    # The first entry that chose an address is how it was chosen.
    third = frozen_cohort(report(recipients=entries[2]), configuration_id)
    assert {(m["address"], m["kind"]) for m in third} == {
        ("staff.one@example.org", "user"),
        ("staff-two@example.org", "group"),
    }


@pytest.mark.django_db(transaction=True)
def test_open_requests_are_every_current_request_not_followed_up(
    live_response_service, google
):
    """To review and To handle requests by the as-of moment, oldest first.

    A followed-up request leaves the list; one submitted after the as-of
    moment is not on it yet; the worker's read is the guard's.
    """
    from parishkit.stewardship.accounts.models import PortalUser

    harness = live_response_service
    submitted = respond(harness, "Please call us").submitted_at
    source = scalar("SELECT snapshot_id FROM stewardship_source_current", [])
    campaign = harness.campaign.pk
    first = open_requests(campaign, source, submitted)
    assert [item.text for item in first] == ["Please call us"]
    assert open_requests(campaign, source, submitted - timedelta(microseconds=1)) == ()
    browser, login = signed_in()
    assert login.status_code == 302
    actor = PortalUser.objects.get(email="admin@example.org").pk
    item = AdditionalInformationItem.objects.get(pk=first[0].item_id)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        update_information(
            harness.service.store,
            actor,
            item.pk,
            expected_version=item.version,
            request_key=uuid4(),
            follow_up_needed=True,
            followed_up=False,
            confirm_clear=False,
            notes="",
        )
    later = submitted + timedelta(days=1)
    assert [item.text for item in open_requests(campaign, source, later)] == [
        "Please call us"
    ]
    item.refresh_from_db()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        update_information(
            harness.service.store,
            actor,
            item.pk,
            expected_version=item.version,
            request_key=uuid4(),
            follow_up_needed=True,
            followed_up=True,
            confirm_clear=False,
            notes="Called",
        )
    # The worker, which captures sends and runs the snapshot guard, may
    # read the requests and their follow-up state.
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert open_requests(campaign, source, later) == ()


REFUSED = {
    "stewardship_report_email_preparation": (
        "INSERT INTO stewardship_report_email_preparation(id,correlation_id,version,"
        "campaign_id,definition_id,revision_id,campaign_configuration_id,task_id,"
        "mode,cutoff,phase) VALUES(%s,%s,1,%s,%s,%s,%s,%s,'production',"
        "statement_timestamp()-interval '1 minute','dates')",
        "Report preparation lacks current schedule ownership",
        7,
    ),
    "stewardship_report_email_snapshot": (
        "INSERT INTO stewardship_report_email_snapshot(id,correlation_id,"
        "preparation_id,campaign_id,configuration_id,values,blocks,has_content,"
        "recipients,subject,html,text,images,run_id,fence,worker_id) "
        "VALUES(%s,%s,%s,%s,%s,'{}','[]',true,'[]','s','h','t','[]',%s,1,%s)",
        "Report snapshot lacks its exact capture",
        7,
    ),
    "stewardship_report_email_recipient": (
        "INSERT INTO stewardship_report_email_recipient(id,correlation_id,"
        "snapshot_id,address,kind,ref,disposition) "
        "VALUES(%s,%s,%s,'a@example.org','outside',NULL,'revoked')",
        "Report recipient lacks its exact cohort message",
        3,
    ),
    "stewardship_report_email_manual_request": (
        "INSERT INTO stewardship_report_email_manual_request(id,correlation_id,"
        "campaign_id,definition_id,revision_id,configuration_id,task_id) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s)",
        "Report manual request requires the current report and no open send",
        7,
    ),
}


@pytest.mark.django_db
@pytest.mark.parametrize("table", REFUSED)
def test_no_report_record_exists_without_its_ownership(table):
    """Each guard refuses a write no live, current report send owns.

    Until the conversion admits report schedules no such owner can exist,
    so these refusals are all the pipeline can do in this release.
    """
    sql, message, count = REFUSED[table]
    with (
        pytest.raises(DatabaseError, match=message),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(sql, [uuid4() for _ in range(count)])


@pytest.mark.django_db
def test_the_task_maps_and_the_inert_mail_trigger():
    """report_email_prepare runs in the worker, created by scheduler and web.

    The message trigger fires only for the report purpose, which the outbox
    does not admit until the conversion.
    """
    assert (
        scalar("SELECT stewardship_task_type_login_v1(%s)", ["report_email_prepare"])
        == "pk_stewardship_worker"
    )
    assert scalar(
        "SELECT stewardship_task_type_creators_v1(%s)", ["report_email_prepare"]
    ) == ["pk_stewardship_scheduler", "pk_stewardship_web"]
    assert (
        scalar("SELECT stewardship_task_type_login_v1(%s)", ["report_email_finalize"])
        is None
    )
    definition = scalar(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname='outbox_purpose'",
        [],
    )
    assert "admin_report" not in definition
    # No send exists, so none is skipped as empty.
    assert scalar("SELECT stewardship_report_email_skipped_v1(%s)", [uuid4()]) is False

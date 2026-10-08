"""The roster Entered in ParishSoft tick under the real web role (#528, step 4).

Migration 0033 (``schema/migrations/0033_roster_entries.sql``) adds the
tick's history. These tests run the service, the pages and the SQL guards as
installed, and check the file's own DO block against a database without
them.
"""

import io
import re
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction
from openpyxl import load_workbook

from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.workflows.models import MinistryRosterEntry
from parishkit.stewardship.workflows.roster import set_roster_entered

from .auth_builders import signed_in
from .test_background_grants_postgresql import task_login
from .test_export_views_postgresql import restricted_download_pool
from .test_information_followup_postgresql import search
from .test_ministry_exports_postgresql import leader
from .test_ministry_followup_postgresql import edit, requests
from .test_ministry_reports_postgresql import setup
from .test_policy_postgresql import user
from .test_report_workspace_postgresql import read as get

pytestmark = pytest.mark.django_db(transaction=True)

FROZEN = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0033_roster_entries.sql"
)


def resolved(response_service):
    """The harness's join (Ministry 9) resolved as joined by an Administrator."""
    harness = setup(response_service)
    admin = user("admin@example.org").pk
    join, leave = requests()
    edit(harness, admin, join, state="resolved", outcome="joined")
    join.refresh_from_db()
    return harness, admin, join, leave


def tick(harness, actor, target, entered, *, sequence=None, key=None):
    """Set or clear the tick through the service on the restricted web login."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        latest = (
            MinistryRosterEntry.objects.filter(request=target)
            .order_by("-sequence")
            .first()
        )
        return set_roster_entered(
            harness.service.store,
            actor,
            target.pk,
            sequence=(latest.sequence if latest else 0) + 1
            if sequence is None
            else sequence,
            entered=entered,
            request_key=key or uuid4(),
        )


def test_set_clear_and_set_again_with_history_and_audit(response_service):
    """Each set or clear is one row in sequence; a replay is the same row;
    the audit names the Ministry and the sequences only."""
    harness, admin, join, _ = resolved(response_service)
    key = uuid4()
    first = tick(harness, admin, join, True, key=key)
    assert tick(harness, admin, join, True, sequence=1, key=key).pk == first.pk
    tick(harness, admin, join, False)
    tick(harness, admin, join, True)
    assert list(
        MinistryRosterEntry.objects.filter(request=join)
        .order_by("sequence")
        .values_list("sequence", "entered")
    ) == [(1, True), (2, False), (3, True)]
    contexts = [
        AuditContext.objects.get(event=event).context
        for event in AuditEvent.objects.filter(
            event_type="ministry_roster_entered"
        ).order_by("created_at")
    ]
    assert contexts[0] == {
        "outcome": "changed",
        "before_version": 0,
        "after_version": 1,
        "ministry_duid": 9,
    }
    assert len(contexts) == 3


def test_service_refuses_leaders_open_requests_stale_and_noop(response_service, google):
    """Only Admin and Staff tick, only resolved roster changes, only at the
    next sequence, and only as a real change."""
    harness, admin, join, leave = resolved(response_service)
    _, head, _, _ = leader(harness, google)
    with pytest.raises(PermissionError):
        tick(harness, head, join, True)
    with pytest.raises(ValueError):
        tick(harness, admin, leave, True)  # still open
    with pytest.raises(StaleRecordError):
        tick(harness, admin, join, True, sequence=2)
    with pytest.raises(ValueError):
        tick(harness, admin, join, False)  # already not entered
    edit(harness, admin, leave, state="resolved", outcome="declined")
    leave.refresh_from_db()
    with pytest.raises(ValueError):
        tick(harness, admin, leave, True)  # not a roster change


def forge(target, actor, *, sequence=1, entered=True):
    """Insert a tick row directly as the web login, with no audit."""
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        work_transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "INSERT INTO stewardship_ministry_roster_entry (id,correlation_id,"
            "actor_id,request_id,sequence,entered,request_key) VALUES "
            "(gen_random_uuid(),gen_random_uuid(),%s,%s,%s,%s,gen_random_uuid())",
            [actor, target.pk, sequence, entered],
        )


def test_sql_refuses_forged_and_unaudited_ticks(response_service, google):
    """The guard holds when the service is bypassed, and a row without its
    audit cannot commit."""
    harness, admin, join, leave = resolved(response_service)
    _, head, _, _ = leader(harness, google)
    for target, actor, values in (
        (join, head, {}),
        (leave, admin, {}),
        (join, admin, {"sequence": 2}),
        (join, admin, {"entered": False}),
        (join, uuid4(), {}),
    ):
        with pytest.raises((IntegrityError, DatabaseError)):
            forge(target, actor, **values)
    with pytest.raises((IntegrityError, DatabaseError), match="requires its audit"):
        forge(join, admin)
    assert MinistryRosterEntry.objects.count() == 0


def test_tick_history_is_immutable(response_service):
    """A recorded tick can never be changed or removed."""
    harness, admin, join, _ = resolved(response_service)
    entry = tick(harness, admin, join, True)
    for statement in (
        "UPDATE stewardship_ministry_roster_entry SET entered=false WHERE id=%s",
        "DELETE FROM stewardship_ministry_roster_entry WHERE id=%s",
    ):
        with (
            pytest.raises((IntegrityError, DatabaseError)),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement, [entry.pk])


def test_pages_list_tick_and_download_the_roster_changes(
    response_service, google, settings
):
    """The list shows the resolved join to enter; the tick answers in place
    on the request page; the list and its download follow the tick; the view
    and download are audited as counts."""
    settings.STEWARDSHIP_DOWNLOAD_POOL = None  # restricted_download_pool sets it
    harness, _, join, _ = resolved(response_service)
    browser, login = signed_in()
    assert login.status_code == 302
    campaign = harness.campaign.pk
    route = f"/admin/reports/{campaign}/ministries/roster/"
    with restricted_download_pool(settings):
        response, body = get(browser, route)
        assert response.status_code == 200 and b"Not yet entered in ParishSoft" in body
        assert b"Mark entered in ParishSoft" in body
        assert get(browser, route + "?show=bad")[0].status_code == 400
        assert get(browser, route + "?sort=id")[0].status_code == 400
        tick_url = f"/admin/reports/{campaign}/ministries/follow-up/{join.pk}/roster"
        response, _ = search(
            browser,
            tick_url,
            {
                "sequence": "1",
                "entered": "yes",
                "request_key": str(uuid4()),
                "return_to": "roster",
                "show": "all",
                "sort": "member",
                "size": "25",
                "page": "1",
            },
        )
        # The answer keeps the list's own view (#821 review).
        assert response.status_code == 302
        location = response["Location"]
        assert "/ministries/roster/?" in location and "show=all" in location
        assert "sort=member" in location and "size=25" in location
        # The same form again is now stale: on the request page a 409; on
        # the list, the list again with a notice, keeping its view.
        stale = {"sequence": "1", "entered": "yes", "request_key": str(uuid4())}
        assert search(browser, tick_url, stale)[0].status_code == 409
        response, _ = search(
            browser, tick_url, stale | {"return_to": "roster", "show": "all"}
        )
        assert response.status_code == 302
        assert (
            "changed=1" in response["Location"] and "show=all" in response["Location"]
        )
        _, body = get(browser, response["Location"])
        assert b"changed by someone else first" in body
        assert (
            search(browser, tick_url, stale | {"entered": "maybe"})[0].status_code
            == 400
        )
        assert search(browser, tick_url, stale | {"show": "bad"})[0].status_code == 400
        assert search(browser, tick_url, stale | {"sort": "id"})[0].status_code == 400
        response, body = get(browser, route)
        assert b"No roster changes to enter." in body
        response, body = get(browser, route + "?show=all")
        assert b"Clear Entered in ParishSoft" in body
        item = f"/admin/reports/{campaign}/ministries/follow-up/{join.pk}/"
        assert b"Entered in ParishSoft" in get(browser, item)[1]
        export = route + "export"
        response, body = search(
            browser, export, {"format": "csv", "timezone": "UTC", "show": "all"}
        )
        assert response.status_code == 200 and response["Content-Type"] == "text/csv"
        assert "stewardship-roster-changes-" in response["Content-Disposition"]
        assert b"Entered in ParishSoft" in body and b",Yes," in body
        response, body = search(
            browser, export, {"format": "xlsx", "timezone": "UTC", "show": "all"}
        )
        sheet = load_workbook(io.BytesIO(body))["Roster changes"]
        assert sheet["H1"].value == "Resolved by"
        assert sheet["H2"].value == "admin@example.org" and sheet["I2"].value == "Yes"
        # The Ministry change summary shows the tick read-only, and its
        # stored export is unchanged: the summary's list reads it on screen.
        _, body = search(
            browser, f"/admin/reports/{campaign}/ministries/join/", {"ministry": "9"}
        )
        assert b"Entered in ParishSoft" in body
        assert search(browser, export, {"format": "pdf"})[0].status_code == 400
    for event in ("roster_changes_viewed", "roster_changes_exported"):
        contexts = [
            AuditContext.objects.get(event=row).context
            for row in AuditEvent.objects.filter(event_type=event)
        ]
        assert contexts and all(set(c) == {"outcome", "count"} for c in contexts)


def test_a_leader_sees_the_tick_but_not_the_list(response_service, google):
    """A Ministry leader reads the tick on their own request, has no tick
    form, and is refused the list and the tick."""
    harness, admin, join, _ = resolved(response_service)
    tick(harness, admin, join, True)
    browser, _, _, _ = leader(harness, google)
    campaign = harness.campaign.pk
    item = f"/admin/reports/{campaign}/ministries/follow-up/{join.pk}/"
    response, body = get(browser, item)
    assert response.status_code == 200 and b"Entered in ParishSoft" in body
    assert b"Clear Entered in ParishSoft" not in body
    assert get(browser, f"/admin/reports/{campaign}/ministries/roster/")[
        0
    ].status_code in {403, 404}
    response, _ = search(
        browser,
        f"/admin/reports/{campaign}/ministries/follow-up/{join.pk}/roster",
        {"sequence": "2", "entered": "no", "request_key": str(uuid4())},
    )
    assert response.status_code in {403, 404}


class Rollback(Exception):
    """Ends a check that must leave the database as it found it."""


def test_migration_self_check_refuses_a_database_without_the_guards():
    """The DO block fails once the guard trigger is gone; the whole file,
    run on a database without its objects, installs them."""
    text = FROZEN.read_text(encoding="utf-8")
    check = re.search(r"^DO \$check\$.*?\$check\$;", text, re.M | re.S)[0]
    with connection.cursor() as cursor:
        cursor.execute(check)
        with (
            pytest.raises(DatabaseError, match="not guarded as declared"),
            transaction.atomic(),
        ):
            cursor.execute(
                "DROP TRIGGER stewardship_ministry_roster_entry_guard"
                " ON stewardship_ministry_roster_entry"
            )
            cursor.execute(check)
        with pytest.raises(Rollback), transaction.atomic():
            cursor.execute("DROP TABLE stewardship_ministry_roster_entry")
            cursor.execute(
                "DROP FUNCTION stewardship_ministry_roster_entry_guard_v1(),"
                " stewardship_ministry_roster_entry_effect_v1()"
            )
            cursor.execute(text)
            raise Rollback


def test_a_resubmitted_family_keeps_its_resolved_change_on_the_list(
    response_service, google
):
    """A Family answering again creates a new request for the same join; the
    resolved one, not yet entered, stays on the list and its download
    (#821 review)."""
    from .test_ministry_responses_postgresql import respond, revisit
    from .test_response_http_postgresql import answers_for
    from .test_runtime_auth_grants_postgresql import web_login

    harness, _, join, _ = resolved(response_service)
    with web_login():
        form = revisit(harness)
        answers = answers_for(form)
        answers["ministries"]["members"]["3"] = {"join": [9], "leave": []}
        respond(harness, form, answers)
    join.refresh_from_db()
    assert join.state == "resolved"
    browser, _ = signed_in()
    route = f"/admin/reports/{harness.campaign.pk}/ministries/roster/"
    _, body = get(browser, route)
    assert f"follow-up/{join.pk}/".encode() in body
    assert b"Replaced by a later Family response; still to enter" in body
    _, body = search(browser, route + "export", {"format": "csv", "timezone": "UTC"})
    assert b"Food pantry" in body


def test_staff_tick_a_leave_but_not_a_source_resolved_request(response_service):
    """A Staff member ticks a resolved leave; a request the ParishSoft roster
    already resolved takes no tick, in the service or in SQL."""
    from parishkit.stewardship.source.models import SourceCurrent

    from ..policy_factory import address
    from .campaign_builders import change

    harness = setup(response_service)
    store = harness.service.store
    change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address("staff@example.org", ("staff",)),
            }
        ],
    )
    staff = user("staff@example.org").pk
    join, leave = requests()
    edit(harness, staff, leave, state="resolved", outcome="leave_confirmed")
    leave.refresh_from_db()
    assert tick(harness, staff, leave, True).entered is True
    # Mark the join resolved from the roster, as a refresh would.
    snapshot = SourceCurrent.objects.values_list("snapshot_id", flat=True).get()
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica")
        cursor.execute(
            "UPDATE stewardship_ministry_request SET state='resolved',"
            " outcome='joined', resolved_at=now(), resolution_source_id=%s,"
            " version=version+1 WHERE id=%s",
            [snapshot, join.pk],
        )
    join.refresh_from_db()
    with pytest.raises(ValueError):
        tick(harness, staff, join, True)
    with pytest.raises((IntegrityError, DatabaseError)):
        forge(join, staff)


def test_a_gated_campaign_takes_no_tick_and_draws_no_form(response_service, google):
    """While other campaign work holds the campaign, the tick is refused and
    the list draws no form; an HTTP replay is one row."""
    from parishkit.stewardship.campaigns.runtime_models import CampaignWorkGate

    harness, admin, join, _ = resolved(response_service)
    browser, _ = signed_in()
    campaign = harness.campaign.pk
    tick_url = f"/admin/reports/{campaign}/ministries/follow-up/{join.pk}/roster"
    form = {"sequence": "1", "entered": "yes", "request_key": str(uuid4())}
    for _ in range(2):
        assert search(browser, tick_url, form)[0].status_code == 302
    assert MinistryRosterEntry.objects.count() == 1
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_campaign_work_gate DISABLE TRIGGER USER"
        )
        CampaignWorkGate.objects.create(
            campaign=harness.campaign,
            request_id=uuid4(),
            initiated_by_id=uuid4(),
            state="preparing",
        )
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute("ALTER TABLE stewardship_campaign_work_gate ENABLE TRIGGER USER")
    _, body = get(browser, f"/admin/reports/{campaign}/ministries/roster/?show=all")
    assert b"Other campaign work is under way" in body
    assert b"Clear Entered in ParishSoft" not in body
    with pytest.raises(PermissionError):
        tick(harness, admin, join, False)

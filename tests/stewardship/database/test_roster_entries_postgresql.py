"""The roster Entered in ParishSoft tick under the real web role (#528, step 4).

Migration 0061 (``schema/migrations/0061_roster_entries.sql``) adds the
tick's history. These tests run the service, the pages and the SQL guards as
installed, and check the file's own DO block against a database without
them.
"""

import csv
import io
import re
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.workflows.models import (
    MinistryRequest,
    MinistryRosterEntry,
    MinistryWorkflowRevision,
)
from parishkit.stewardship.workflows.review import undo_entered
from parishkit.stewardship.workflows.roster import set_roster_entered

from .auth_builders import signed_in
from .test_background_grants_postgresql import task_login
from .test_export_views_postgresql import restricted_download_pool
from .test_information_followup_postgresql import search
from .test_ministry_exports_postgresql import leader
from .test_ministry_followup_postgresql import edit, member_gone, requests
from .test_ministry_reports_postgresql import setup
from .test_policy_postgresql import user
from .test_report_workspace_postgresql import read as get

pytestmark = pytest.mark.django_db(transaction=True)

FROZEN = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0061_roster_entries.sql"
)


def resolved(response_service):
    """The harness's join (Ministry 9) resolved as joined by an Administrator."""
    harness = setup(response_service)
    admin = user("admin@example.org").pk
    join, leave = requests()
    edit(harness, admin, join, state="resolved", outcome="joined")
    join.refresh_from_db()
    return harness, admin, join, leave


def tick(harness, actor, target, entered, *, sequence=None, key=None, note="Typo"):
    """Set or clear the tick through the service on the restricted web login.

    Clearing is Back to review of the mark, with its required ``note``
    (``review.undo_entered``, #1840).
    """
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        latest = (
            MinistryRosterEntry.objects.filter(request=target)
            .order_by("-sequence")
            .first()
        )
        sequence = (
            (latest.sequence if latest else 0) + 1 if sequence is None else sequence
        )
        if not entered:
            return undo_entered(
                harness.service.store,
                actor,
                target.pk,
                sequence=sequence,
                note=note,
                request_key=key or uuid4(),
            )
        return set_roster_entered(
            harness.service.store,
            actor,
            target.pk,
            sequence=sequence,
            entered=True,
            request_key=key or uuid4(),
        )


def test_set_clear_and_set_again_with_history_and_audit(response_service):
    """Each set or clear is one row in sequence; a replay is the same row;
    the audit names the Ministry and the sequences only. A clear is Back to
    review of the mark (#1840): its revision keeps the request joined, with
    the note, one version on, and a replay of it records nothing more."""
    harness, admin, join, _ = resolved(response_service)
    key = uuid4()
    first = tick(harness, admin, join, True, key=key)
    assert tick(harness, admin, join, True, sequence=1, key=key).pk == first.pk
    version = MinistryRequest.objects.get(pk=join.pk).version
    clear = uuid4()
    cleared = tick(harness, admin, join, False, key=clear)
    assert tick(harness, admin, join, False, sequence=2, key=clear).pk == cleared.pk
    undo = MinistryWorkflowRevision.objects.get(request=join, request_key=clear)
    assert (undo.state, undo.outcome, undo.decision_note) == (
        "resolved",
        "joined",
        "Typo",
    )
    after = MinistryRequest.objects.get(pk=join.pk)
    assert (after.state, after.outcome, after.version) == (
        "resolved",
        "joined",
        version + 1,
    )
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
    tick(harness, admin, join, True)
    with pytest.raises(ValueError, match="needs a note"):
        tick(harness, admin, join, False, note=" ")
    with pytest.raises(PermissionError):
        tick(harness, head, join, False)
    # The mark alone is never cleared without its Back to review.
    with (
        pytest.raises(ValueError),
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
    ):
        set_roster_entered(
            harness.service.store,
            admin,
            join.pk,
            sequence=2,
            entered=False,
            request_key=uuid4(),
        )
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


# Staff mark roster changes on Ministry requests at its To enter by hand
# Stage: Roster changes to enter, at OLD, was folded into it with no
# redirect (R39 finding 2).
ROUTE = "/admin/reports/ministries/"
EXPORT = "/admin/reports/ministries/follow-up/exports/"
OLD = "/admin/reports/ministries/follow-up/roster/"


def mark_form(target, **values):
    """A row's Entered by hand post to Ministry requests, with its view."""
    return {
        "decide": "entered_by_hand",
        "item": str(target.pk),
        "version": str(target.version),
        "request_key": str(uuid4()),
        "note": "",
        "stage": "to_enter_by_hand",
    } | values


def bulk_form(*items, **values):
    """The bulk bar's Entered by hand post to Ministry requests, with its view."""
    return {
        "bulk": "entered_by_hand",
        "bulk_key": str(uuid4()),
        "bulk_note": "",
        "item": list(items),
        "stage": "to_enter_by_hand",
    } | values


def lines(body):
    """A CSV download's rows, each as a dict by heading."""
    heading, *rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
    return [dict(zip(heading, row, strict=True)) for row in rows]


def test_ministry_requests_lists_ticks_and_downloads_the_roster_changes(
    response_service, google, settings
):
    """Ministry requests' To enter by hand Stage is the roster list (R39
    finding 2): its Entered by hand takes no note and answers in place with
    its notice; a mark already made says so; Entered by hand lists it with
    who marked it; the export carries the roster-entry columns. The old
    Roster changes to enter address is gone, with no redirect."""
    settings.STEWARDSHIP_DOWNLOAD_POOL = None  # restricted_download_pool sets it
    harness, _, join, _ = resolved(response_service)
    browser, login = signed_in()
    assert login.status_code == 302
    with restricted_download_pool(settings):
        response, body = get(browser, ROUTE + "?stage=to_enter_by_hand")
        assert response.status_code == 200 and b"To enter by hand (1)" in body
        assert b'data-row-action="entered_by_hand"' in body
        for old in (OLD, OLD + "exports/"):
            assert get(browser, old)[0].status_code == 404, old
        # A note (the mark records none) is the plain 400; nothing is saved.
        refused = search(browser, ROUTE, mark_form(join, note="Typed"))[0]
        assert refused.status_code == 400
        assert MinistryRosterEntry.objects.count() == 0
        response, body = search(browser, ROUTE, mark_form(join))
        assert response.status_code == 200
        assert b"marked entered by hand." in body
        # The same row's form again (its page drawn before) is now stale.
        response, body = search(browser, ROUTE, mark_form(join))
        assert response.status_code == 200
        assert b": not recorded, changed by someone else." in body
        assert MinistryRosterEntry.objects.filter(request=join).count() == 1
        # The request page keeps its own tick: a stale one is a 409 there.
        tick_url = f"/admin/reports/ministries/follow-up/{join.pk}/roster/"
        stale = {"sequence": "1", "entered": "yes", "request_key": str(uuid4())}
        assert search(browser, tick_url, stale)[0].status_code == 409
        # Entered by hand: who marked it under its stage.
        response, body = search(browser, ROUTE, {"stage": "entered_by_hand"})
        assert response.status_code == 200 and b"Entered by hand (1)" in body
        assert b"Entered by admin@example.org" in body
        item = f"/admin/reports/ministries/follow-up/{join.pk}/"
        # Its Back to review is the worklist's (#1893): the row's title and
        # the dialog's words for this request's placement.
        page = get(browser, item)[1]
        assert re.search(rb"Send [^<]+ back to review\?", page)
        assert (
            b"Entered by hand is undone: the item goes back to To enter by hand, "
            b"with its decision kept. Say why."
        ) in page
        response, body = search(
            browser,
            EXPORT,
            {"format": "csv", "timezone": "UTC", "stage": "entered_by_hand"},
        )
        assert response.status_code == 200 and response["Content-Type"] == "text/csv"
        (line,) = lines(body)
        assert line["Stage"] == "Entered by hand"
        assert line["Entered by"] == "admin@example.org" and line["Entered at"]
        assert line["Family DUID"] and line["Replaced by a later response"] == ""


def test_a_leader_never_sees_the_tick_nor_the_list(response_service, google):
    """A Ministry leader has no ParishSoft access (#1160): their request page
    and queue draw no tick (state, who, when or form) and tell them Staff
    enter roster changes; the list and the tick are refused. Staff and
    Administrators keep seeing it and the "enter the change in ParishSoft by hand"
    help."""
    harness, admin, join, _ = resolved(response_service)
    item = f"/admin/reports/ministries/follow-up/{join.pk}/"
    queue = "/admin/reports/ministries/"
    browser, _, _, _ = leader(harness, google)

    def hidden(entered):
        """The leader's request page and queue show nothing of the tick."""
        response, body = get(browser, item)
        assert response.status_code == 200 and b"Asked to join" in body
        for words in (
            b"Entered by hand",
            b"To enter by hand",
            b"Mark Entered",
            f"{join.pk}/roster/".encode(),
            b"followup-roster",
            b"enter the change in ParishSoft by hand",
        ):
            assert words not in body, (entered, words)
        assert b"Staff enter roster changes in ParishSoft" in body
        response, body = search(browser, queue, {"stage": "all"})
        assert response.status_code == 200 and f"{join.pk}/".encode() in body
        # A leader's worklist says Accepted, never the update phase (#1684).
        assert b"Entered by hand" not in body and b"To enter by hand" not in body
        assert b'<span class="status-word">Accepted</span>' in body

    hidden(False)
    # Home's To enter by hand link opens a leader's Accepted (their Stage
    # filter has no update phase), not an empty list.
    response, body = get(browser, queue + "?stage=to_enter_by_hand")
    assert response.status_code == 200 and f"{join.pk}/".encode() in body
    assert b'<option value="accepted" selected>' in body
    # A forged single-row Entered by hand is refused, and records nothing.
    assert search(browser, queue, mark_form(join))[0].status_code == 403
    assert not MinistryRosterEntry.objects.filter(request=join).exists()
    tick(harness, admin, join, True)
    hidden(True)
    # The retired Roster changes to enter address is gone (no redirect).
    assert (
        get(browser, "/admin/reports/ministries/follow-up/roster/")[0].status_code
        == 404
    )
    response, _ = search(
        browser,
        f"/admin/reports/ministries/follow-up/{join.pk}/roster/",
        {"sequence": "2", "entered": "no", "request_key": str(uuid4())},
    )
    assert response.status_code == 403
    assert MinistryRosterEntry.objects.filter(request=join).count() == 1
    # The Administrator still sees the mark, its form and the queue line;
    # leader() pointed the one synthetic Google account at the leader.
    google[0]["email"] = "admin@example.org"
    staff, login = signed_in()
    assert login.status_code == 302
    _, body = get(staff, item)
    assert b"Entered by hand by admin@example.org" in body
    assert b'data-confirm-open="followup-roster-clear-dialog"' in body
    assert b"enter the change in ParishSoft by hand" in body
    assert b"Staff enter roster changes" not in body
    shown = search(staff, queue, {"stage": "all"})[1]
    assert b'<span class="status-word">Entered by hand</span>' in shown


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


# The rule before the reviewed Family form (#1705): forms drawn before
# its release, as one open across the deploy is.
@pytest.mark.usefixtures("earlier_rule")
def test_a_resubmitted_family_keeps_its_resolved_change_on_the_list(
    response_service, google
):
    """A Family answering again with the same join creates no new request
    (#1684, "Family asked again"): the resolved one, not yet entered, stays
    on Ministry requests' To enter by hand and its download as the current
    request (#821 review)."""
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
    assert MinistryRequest.objects.filter(ministry_duid=9).count() == 1
    browser, _ = signed_in()
    _, body = get(browser, ROUTE + "?stage=to_enter_by_hand")
    assert f"follow-up/{join.pk}/".encode() in body
    # One request, which the Family asked for again (#1684): not replaced.
    assert b"Family asked again" in body
    assert b"Replaced by a later response" not in body
    _, body = search(
        browser,
        EXPORT,
        {"format": "csv", "timezone": "UTC", "stage": "to_enter_by_hand"},
    )
    (line,) = lines(body)
    assert (line["Ministry"], line["Request"], line["Stage"]) == (
        "Food pantry",
        "Join",
        "To enter by hand",
    )


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
    Ministry requests draws no form; an HTTP replay is one row."""
    from parishkit.stewardship.campaigns.runtime_models import CampaignWorkGate

    harness, admin, join, _ = resolved(response_service)
    browser, _ = signed_in()
    tick_url = f"/admin/reports/ministries/follow-up/{join.pk}/roster/"
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
    _, body = get(browser, ROUTE + "?stage=all")
    assert b'id="followup-locked"' in body
    assert b"data-row-action" not in body and b'id="requests-bulk"' not in body
    # A forged mark posted to Ministry requests is refused too, and records
    # nothing.
    assert search(browser, ROUTE, mark_form(join))[0].status_code == 403
    assert MinistryRosterEntry.objects.count() == 1
    # The request's own page shows its pressed Entered by hand toggle
    # (#2131) unavailable, named by the locked notice, rather than leaving
    # it out (#563); a forged POST is refused.
    _, body = get(browser, f"/admin/reports/ministries/follow-up/{join.pk}/")
    assert b'id="followup-locked"' in body
    assert re.search(
        rb'<button[^>]*disabled aria-describedby="followup-locked"'
        rb'[^>]*aria-pressed="true"[^>]*>Entered by hand</button>',
        body,
    )
    refused = dict(form, sequence="2", entered="no", request_key=str(uuid4()))
    assert search(browser, tick_url, refused)[0].status_code != 302
    assert MinistryRosterEntry.objects.get().entered is True
    with pytest.raises(PermissionError):
        tick(harness, admin, join, False)


def test_a_change_for_a_member_no_longer_in_parishsoft_stays_listed(
    response_service, google, settings
):
    """A resolved join for a Member ParishSoft now lists in another Family
    may still need entering, so Ministry requests' To enter by hand keeps it
    under the default Members choice, with the Member's ParishSoft status on
    the page and in the download, offering Entered by hand alone, which
    still works (#1217); the review stages leave it out."""
    settings.STEWARDSHIP_DOWNLOAD_POOL = None  # restricted_download_pool sets it
    harness, admin, join, _ = resolved(response_service)
    member_gone(harness, "moved")
    browser, login = signed_in()
    assert login.status_code == 302
    with restricted_download_pool(settings):
        _, body = get(browser, ROUTE + "?stage=to_enter_by_hand")
        assert f"follow-up/{join.pk}/".encode() in body
        assert b'data-source-status="moved">Not in ParishSoft</span>' in body
        # It offers Entered by hand alone (#1217).
        assert b'data-row-action="entered_by_hand"' in body
        assert b"decide=back_to_review" not in body
        response, body = search(
            browser,
            EXPORT,
            {"format": "csv", "timezone": "UTC", "stage": "to_enter_by_hand"},
        )
        assert response.status_code == 200
        (line,) = lines(body)
        assert line["ParishSoft status"] == "Not in ParishSoft"
        # Its review stages leave it out.
        _, body = get(browser, ROUTE)
        assert f"follow-up/{join.pk}/".encode() not in body
        # Entered by hand records for such a Member, as a single tick does.
        response, body = search(browser, ROUTE, mark_form(join))
        assert response.status_code == 200 and b"marked entered by hand." in body
    assert MinistryRosterEntry.objects.filter(request=join, entered=True).exists()


def test_a_leader_cannot_mark_a_gone_members_change(response_service, google):
    """The roster work of a Member no longer in ParishSoft stays Staff's: a
    Ministry leader's forged Entered by hand on it, single or bulk, is
    refused (``can_tick``) and records nothing."""
    harness, _, join, _ = resolved(response_service)
    member_gone(harness, "moved")
    browser, _, _, _ = leader(harness, google)
    assert search(browser, ROUTE, mark_form(join))[0].status_code == 403
    item = f"{join.pk}:{join.version}"
    assert search(browser, ROUTE, bulk_form(item))[0].status_code == 403
    assert not MinistryRosterEntry.objects.exists()


def test_bulk_marks_several_changes_all_or_none(response_service, google):
    """Ministry requests' bulk Entered by hand (#879, R39 finding 2): the page
    draws a box for each request at To enter by hand, and its dialog's post
    marks every chosen request at once, each with its own entry and audit
    event, replay-safe. A request someone else marked first records nothing
    and the page says so; a malformed form or a note is a 400; a leader is
    refused. Nothing is saved by a refused post."""
    harness, admin, join, leave = resolved(response_service)
    edit(harness, admin, leave, state="resolved", outcome="leave_confirmed")
    leave.refresh_from_db()
    browser, login = signed_in()
    assert login.status_code == 302
    route = ROUTE
    _, body = get(browser, route + "?stage=to_enter_by_hand")
    for target in (join, leave):
        assert (
            f'value="{target.pk}:{target.version}" form="requests-bulk"'.encode()
            in body
        )
    form = bulk_form(f"{join.pk}:{join.version}", f"{leave.pk}:{leave.version}")

    def entries():
        """Every tick row, as (request, sequence, entered), in a fixed order."""
        return sorted(
            MinistryRosterEntry.objects.values_list("request_id", "sequence", "entered")
        )

    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        for _ in range(2):  # the second post replays the first
            response, body = search(browser, route, form)
            assert response.status_code == 200, body
            assert b"2 requests marked entered by hand." in body
        assert entries() == sorted([(join.pk, 1, True), (leave.pk, 1, True)])
    assert AuditEvent.objects.filter(event_type="ministry_roster_entered").count() == 2
    tick(harness, admin, join, False)
    before = entries()
    join_item, leave_item = f"{join.pk}:{join.version}", f"{leave.pk}:{leave.version}"
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        # Leave is already marked, so the whole post records nothing.
        response, body = search(browser, route, bulk_form(join_item, leave_item))
        assert (
            response.status_code == 200
            and b"not recorded, changed by someone else" in body
        )
        for bad in (
            bulk_form(join_item, bulk_note="Typed"),
            bulk_form(),
            bulk_form(join_item, join_item),
            bulk_form(f"{join.pk}"),
            bulk_form(join_item, bulk_key="x"),
        ):
            assert search(browser, route, bad)[0].status_code == 400, bad
        assert entries() == before
    leader_browser, _, _, _ = leader(harness, google)
    refused, _ = search(leader_browser, route, bulk_form(join_item))
    assert refused.status_code == 403
    assert entries() == before


def test_staff_bulk_mark_a_gone_members_change_as_a_single_tick_does(
    response_service, google
):
    """Staff use the bulk bar as Administrators do (#879), and a request for
    a Member ParishSoft no longer lists is marked like a single tick (#1217):
    the roster work may still be real. A mark already made records nothing
    and says so."""
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
    edit(harness, staff, join, state="resolved", outcome="joined")
    edit(harness, staff, leave, state="resolved", outcome="leave_confirmed")
    member_gone(harness, "moved")
    google[0]["email"] = "staff@example.org"
    browser, login = signed_in()
    assert login.status_code == 302
    join.refresh_from_db()
    leave.refresh_from_db()
    _, body = get(browser, ROUTE + "?stage=to_enter_by_hand")
    assert f'value="{join.pk}:{join.version}" form="requests-bulk"'.encode() in body
    form = bulk_form(f"{join.pk}:{join.version}", f"{leave.pk}:{leave.version}")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert search(browser, ROUTE, form)[0].status_code == 200
    marked = MinistryRosterEntry.objects.filter(entered=True)
    assert set(marked.values_list("request_id", flat=True)) == {join.pk, leave.pk}
    # The signed-in Staff member made them (a fresh identity for the same
    # address, as signed_in() creates one).
    assert set(
        PortalUser.objects.filter(
            pk__in=marked.values_list("actor_id", flat=True)
        ).values_list("email", flat=True)
    ) == {"staff@example.org"}
    assert AuditEvent.objects.filter(event_type="ministry_roster_entered").count() == 2
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = search(browser, ROUTE, bulk_form(f"{join.pk}:{join.version}"))
    assert response.status_code == 200
    assert b"not recorded, changed by someone else." in body
    assert MinistryRosterEntry.objects.filter(request=join).count() == 1

"""A user's Timeline (#1866) under the real web login, and its index.

The page reads one user's records in every configuration, the Google
accounts of the address and their audit records (through
``audit_event_actor_time``, frozen migration 0240), and the emails meant for
the address through Sent mail's Administrator-only read (0116). These tests
check what the page shows, who may open it, its audit, its filters and
paging, the Users page's links, ``pk-admin users timeline``, the index the
actor read uses and the frozen file's closing check.
"""

import re
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship import admin_cli
from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.accounts.user_timelines import audit_records
from parishkit.stewardship.audit.log_descriptions import describe
from parishkit.stewardship.audit.models import AuditContext, AuditEvent

from ..policy_factory import user
from .auth_builders import signed_in
from .automation_builders import paired
from .campaign_builders import change
from .response_builders import activate_response_service
from .test_admin_status_cli_postgresql import admin, one, views  # noqa: F401
from .test_sent_mail_postgresql import family_message
from .user_builders import USERS, add_rules, convert, user_id, web

pytestmark = pytest.mark.django_db(transaction=True)

FROZEN = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0240_audit_actor_time.sql"
).read_text(encoding="utf-8")
CHECK = FROZEN[FROZEN.index("DO $check$") :]
# One of the Family's two heads in ``family_message``: a user here too.
SECOND = "second@example.org"
# A Ministry leader user, who may not open any user's Timeline.
LEADER = "leader@example.org"


def timeline_path(record):
    """A user's Timeline address."""
    return f"/admin/users/{record}/timeline/"


def get(browser, path):
    """One page as the signed-in browser gets it, under the web login."""
    with web():
        response = browser.get(path)
    return response.status_code, response.content.decode()


def viewed():
    """The ``user_timeline_viewed`` records: (actor, subject, context)."""
    return [
        (event.actor_id, event.subject_id, event.auditcontext.context)
        for event in AuditEvent.objects.filter(
            event_type="user_timeline_viewed"
        ).select_related("auditcontext")
    ]


def update(store, record, values=None):
    """Change one user's role, or remove the user (``values`` None)."""
    operation = (
        {"operation": "remove", "section": "login_rules", "id": record}
        if values is None
        else {
            "operation": "update",
            "section": "login_rules",
            "id": record,
            "values": values,
        }
    )
    assert change(store, store.active(), uuid4(), [operation]).state == "applied"


@pytest.fixture
def people(response_service, google):
    """A live campaign, the Administrator and a second user who was a Ministry
    leader and is now Staff, has signed in, did something and was emailed.

    Returns ``(store, administrator browser, second user's record id,
    second user's portal user id, Family email id)``.
    """
    harness = activate_response_service(response_service)
    store = harness.service.store
    convert(store)
    add_rules(store, user(SECOND, role="ministry_leader", name="Second, Sam"))
    record = user_id(store, SECOND)
    update(store, record, {"role": "staff"})
    browser, login = signed_in()
    assert login.status_code == 302
    claims = google[0]
    claims.update(email=SECOND, sub="synthetic-second")
    _, second_login = signed_in()
    assert second_login.status_code == 302
    claims.update(email="admin@example.org", sub="synthetic-google-subject")
    portal = PortalUser.objects.get(email=SECOND).pk
    # An action and a page view by the second user; only the action is shown.
    AuditEvent.objects.create(event_type="export_requested", actor_id=portal)
    AuditEvent.objects.create(event_type="family_directory_viewed", actor_id=portal)
    message = family_message(harness)
    return store, browser, record, portal, message


def test_an_administrator_sees_the_whole_timeline(people):
    """Summary, role changes, sign-in, action and email; audited by id."""
    store, browser, record, portal, message = people
    status, page = get(browser, timeline_path(record))
    assert status == 200, page
    assert "<h1>Second, Sam</h1>" in page
    assert "Second" not in page.split("</title>")[0]
    assert SECOND in page and "Staff" in page
    assert f'href="/admin/users/{record}/edit/"' in page
    assert "Became a user" in page and "Ministry leader to Staff" in page
    assert "Signed in" in page
    assert str(describe("export_requested")) in page
    # Page views are left out until #1570's slice 3.
    assert str(describe("family_directory_viewed")) not in page
    # The Family email that went to the address links its Mail message page.
    assert f'href="/admin/mail/sent/{message}/"' in page
    assert f'name="actor" value="{portal}"' in page
    administrator = PortalUser.objects.get(email="admin@example.org").pk
    assert viewed() == [(administrator, UUID(record), {"outcome": "succeeded"})]
    # Nothing but the outcome is recorded; never the address.
    assert "@" not in str(
        AuditContext.objects.filter(event__event_type="user_timeline_viewed").values()
    )


def test_filters_narrow_in_place_and_refuse_anything_else(people):
    """Kind and days narrow the lines; a Family kind, a mode or a bad sort is 400."""
    store, browser, record, portal, message = people
    status, page = get(browser, timeline_path(record) + "?kind=roles")
    assert status == 200
    assert "Became a user" in page and "Signed in</th>" not in page
    status, page = get(
        browser,
        timeline_path(record) + "?start=2099-01-01&zone=America/New_York",
    )
    assert status == 200 and "No lines match these filters" in page
    for query in (
        "kind=form",
        "mode=testing",
        "sort=what",
        "size=7",
        "start=2026-10-01",
    ):
        status, _page = get(browser, timeline_path(record) + "?" + query)
        assert status == 400, query
    # Only the two pages that were shown are audited.
    assert len(viewed()) == 2


def test_only_administrators_open_it_and_unknown_users_leave_no_record(people, google):
    """Staff and Ministry leaders are denied; an unknown id is not found;
    none of them is audited."""
    store, browser, record, portal, message = people
    status, _page = get(browser, timeline_path(uuid4()))
    assert status == 404
    google[0].update(email=SECOND, sub="synthetic-second")
    staff, _ = signed_in()
    for path in (
        timeline_path(record),
        timeline_path(user_id(store, "admin@example.org")),
    ):
        status, _page = get(staff, path)
        assert status == 403, path
    add_rules(store, user(LEADER, role="ministry_leader", name="Leader, Lee"))
    google[0].update(email=LEADER, sub="synthetic-leader")
    leader, login = signed_in()
    assert login.status_code == 302
    for path in (timeline_path(record), timeline_path(user_id(store, LEADER))):
        status, _page = get(leader, path)
        assert status == 403, path
    assert viewed() == []


def test_a_former_user_reads_as_no_longer_a_user(people):
    """Removed: the removal is a line, the summary says since when, no Edit."""
    store, browser, record, portal, message = people
    update(store, record)
    status, page = get(browser, timeline_path(record))
    assert status == 200, page
    assert "No longer a user since" in page and "Removed as a user" in page
    assert f"/admin/users/{record}/edit/" not in page
    # The Name and address are the last configuration's that held the user.
    assert "<h1>Second, Sam</h1>" in page and SECOND in page


def test_users_and_edit_user_link_the_timeline(people):
    """Each Users row's Email links its Timeline; Edit user links it too."""
    store, browser, record, portal, message = people
    status, page = get(browser, USERS)
    assert status == 200
    assert (
        f'<a href="{timeline_path(record)}" class="cell-ellipsis" title="{SECOND}">'
        in page
    )
    status, page = get(browser, f"/admin/users/{record}/edit/")
    assert status == 200
    assert f'href="{timeline_path(record)}"' in page


def test_the_page_reads_a_fixed_number_of_statements(people):
    """More audit records add rows, not statements: each source is one read."""
    store, browser, record, portal, message = people

    def statements():
        """How many statements one view runs."""
        with CaptureQueriesContext(connection) as queries:
            status, _page = get(browser, timeline_path(record))
        assert status == 200
        return len(queries)

    before = statements()
    for _ in range(30):
        AuditEvent.objects.create(event_type="export_requested", actor_id=portal)
    assert statements() == before


def test_the_actor_read_uses_the_new_index(people):
    """With sequential scans priced out, the page's own audit read (its
    page-view exclusion, outcome join and order included) uses the index."""
    store, browser, record, portal, message = people
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL enable_seqscan = off")
        plan = audit_records([portal, uuid4()]).explain()
    assert "audit_event_actor_time" in plan, plan


def test_the_migration_check_accepts_the_catalog():
    """The installed index is exactly the declared one."""
    with connection.cursor() as cursor:
        cursor.execute(CHECK)


@pytest.mark.parametrize(
    "statement",
    [
        "DROP INDEX public.audit_event_actor_time",
        # Same name, different definition (actor only).
        "DROP INDEX public.audit_event_actor_time; CREATE INDEX "
        "audit_event_actor_time ON public.stewardship_audit_event (actor_id)",
    ],
)
def test_the_migration_check_refuses_a_missing_or_different_index(statement):
    """The DO block raises for a missing index or a changed one."""
    with (
        pytest.raises(DatabaseError, match="Migration 0240"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(statement)
        cursor.execute(CHECK)


def test_the_file_runs_again_harmlessly():
    """Its statement is safe to repeat, as the users swap test may."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(FROZEN)
        transaction.set_rollback(True)


def test_users_timeline_prints_the_page(admin, google):  # noqa: F811
    """The page's summary and lines; filters as the page's; audited by id."""
    store = admin.service.store
    convert(store)
    add_rules(store, user(SECOND, role="ministry_leader", name="Second, Sam"))
    record = user_id(store, SECOND)
    update(store, record, {"role": "staff"})
    _, secret, _ = paired(admin.service, scope="read-only")
    before = views("user_timeline_viewed")
    code, document = one(admin, "users", "timeline", "--user", record, secret=secret)
    assert code == 0 and document["ok"], document
    result = document["result"]
    assert set(result) == set(admin_cli.BY_NAME["users timeline"].result_fields)
    assert result["user"]["email"] == SECOND and result["user"]["role"] == "staff"
    assert result["kind"] == "all"
    assert [line["what"] for line in result["lines"]] == [
        "Role changed",
        "Became a user",
    ]
    assert result["summary"]["last_sign_in"] is None
    assert views("user_timeline_viewed") == before + 1
    code, document = one(
        admin,
        "users",
        "timeline",
        "--user",
        record,
        "--kind",
        "sessions",
        secret=secret,
    )
    assert code == 0 and document["result"]["lines"] == [], document
    # A Family kind, or a day without a zone, is refused before reading.
    for options in (("--kind", "form"), ("--from", "2026-10-01")):
        code, document = one(
            admin, "users", "timeline", "--user", record, *options, secret=secret
        )
        assert code == 1 and document["error"]["code"] == "invalid", document
    code, document = one(
        admin, "users", "timeline", "--user", str(uuid4()), secret=secret
    )
    assert code == 1 and document["error"]["code"] == "not_available", document
    assert views("user_timeline_viewed") == before + 2
    assert re.fullmatch(r"[0-9a-f-]{36}", record)

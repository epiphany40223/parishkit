"""Ministry requests' Leaders column (#1594), under the real web login.

The coverage definer (migration 0169) answers only a live Staff or
Administrator session with per-Ministry counts (leader addresses, users,
users who signed in), never an address, and audits each read by count; the
Ministry requests page shows it as By Ministry's Leaders column to Staff and
Administrators, never to a Ministry leader, at the cost of one definer call
per rendering; ``pk-admin report ministry`` prints the same status.
"""

import re
from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction
from django.db.models import F
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.leader_coverage import (
    CoverageRefused,
    leader_status,
    read_coverage,
)

from ..policy_factory import address
from .auth_builders import signed_in
from .automation_builders import paired
from .test_admin_status_cli_postgresql import admin, one  # noqa: F401
from .test_background_grants_postgresql import task_login
from .test_leader_import_postgresql import leaders_source, live_key
from .test_report_workspace_postgresql import read
from .user_builders import add_rules, convert

pytestmark = pytest.mark.django_db(transaction=True)

PAGE = "/admin/reports/ministries/"
MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0169_ministry_leader_coverage.sql"
)
DEFINER = "stewardship_ministry_leader_coverage_v1"
EVENT = "ministry_leader_coverage_viewed"
# leaders_source's leaders (test_leader_import_postgresql.LEADERS), by
# Ministry: Choir (4) is led by shared@, parish@ and the response fixture's
# own Chairperson valid@, none of them a user; Ministry 9 by ann@ (a
# Ministry leader user here), shared@ and staff@ (a Staff user).
COUNTS = {
    4: {"ministry": 4, "leaders": 3, "users": 0, "signed_in": 0},
    9: {"ministry": 9, "leaders": 3, "users": 2, "signed_in": 0},
}


@pytest.fixture
def deployment(auth_service, google):
    """Users with ParishSoft leaders; the Administrator signed in.

    staff@example.org is a Staff user and ann@example.org a Ministry leader
    user, both leaders of Ministry 9; neither has signed in yet.
    """
    store = auth_service.store
    add_rules(
        store,
        address("staff@example.org", ("staff",)),
        address("ann@example.org", ("ministry_leader",)),
    )
    convert(store)
    leaders_source(store)
    browser, login = signed_in()
    assert login.status_code == 302
    return store, browser


def sign_in_as(google, email):
    """A fresh browser signed in as ``email`` (a new Google identity)."""
    google[0].update(email=email, sub=f"subject-{email}")
    browser, login = signed_in()
    assert login.status_code == 302
    return browser


def as_web(key):
    """``read_coverage`` under the real web login."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return read_coverage(key)


def viewed():
    """The coverage reads recorded so far."""
    return AuditEvent.objects.filter(event_type=EVENT)


def counted_page(browser):
    """The Ministry requests page under the web login, and how many of its
    statements called the coverage definer (the login's own grants, made on
    entering it, are not counted)."""
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        CaptureQueriesContext(connection) as queries,
    ):
        response, body = read(browser, PAGE)
    assert response.status_code == 200, body
    return body.decode(), [DEFINER in query["sql"] for query in queries].count(True)


def page(browser):
    """The Ministry requests page as this browser sees it, under the web login."""
    return counted_page(browser)[0]


def by_ministry(body):
    """The By Ministry section's HTML."""
    section = body.split('<section id="by-ministry"', 1)[1]
    return section.split("</section>", 1)[0]


def leaders_cells(body):
    """Each By Ministry row's Ministry name and Leaders word, in order."""
    return re.findall(
        r'<th scope="row">([^<]*)</th>.*?<span class="leader-status status-(\w+)">'
        r'.*?<span class="status-word">([^<]*)</span>',
        by_ministry(body),
        re.S,
    )


def test_the_definer_counts_leaders_users_and_sign_ins(deployment, google):
    """Counts per Ministry, the states they give, and one count-only audit
    event per read; a sign-in moves Ministry 9 to "1 signed in", and a
    disabled identity no longer counts."""
    _, browser = deployment
    assert as_web(live_key(browser)) == COUNTS
    assert [leader_status(COUNTS[duid]).state for duid in (4, 9)] == [
        "no_user",
        "not_signed_in",
    ]
    events = viewed()
    assert events.count() == 1
    context = AuditContext.objects.get(event__in=events)
    assert context.context == {"outcome": "succeeded", "count": 2}
    assert (context.actor_kind, context.schema) == ("portal_user", "action")
    assert "@" not in str(list(events.values())) + str(context.context)

    staff = sign_in_as(google, "staff@example.org")
    counted = as_web(live_key(staff))
    assert counted[9] == {**COUNTS[9], "signed_in": 1}
    assert leader_status(counted[9]).word == "1 signed in"
    PortalUser.objects.filter(email="staff@example.org").update(
        disabled=True, version=F("version") + 1
    )
    assert as_web(live_key(browser))[9]["signed_in"] == 0


def test_a_ministry_with_no_leader_is_left_out(auth_service, google):
    """With no leader Members, Ministry 9 is absent (No leader), while Choir
    keeps the fixture's own Chairperson."""
    convert(auth_service.store)
    leaders_source(auth_service.store, leaders=[])
    browser, _ = signed_in()
    counted = as_web(live_key(browser))
    assert counted == {4: {"ministry": 4, "leaders": 1, "users": 0, "signed_in": 0}}
    assert leader_status(counted.get(9)).state == "no_leader"


def test_the_definer_answers_only_live_staff_or_administrator_sessions(
    deployment, google
):
    """A made-up key, the stored digest, no key, and a Ministry leader's
    session are refused, unaudited; Staff are answered."""
    from parishkit.stewardship.accounts.admin_session_store import digest

    _, browser = deployment
    for refused in (None, "", "not-a-session-key", digest(live_key(browser))):
        with pytest.raises(CoverageRefused):
            as_web(refused)
    leader = sign_in_as(google, "ann@example.org")
    with pytest.raises(CoverageRefused):
        as_web(live_key(leader))
    assert viewed().count() == 0
    staff = sign_in_as(google, "staff@example.org")
    assert set(as_web(live_key(staff))) == {4, 9}
    assert viewed().count() == 1


def test_the_definer_is_installed_as_declared():
    """Migration 0169: a definer with a fixed path, web's alone to run, and a
    check block that refuses a wrongly installed one."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT p.prosecdef, p.proconfig, "
            "has_function_privilege('public', p.oid, 'EXECUTE') "
            "FROM pg_proc p WHERE p.proname=%s",
            [DEFINER],
        )
        assert cursor.fetchall() == [
            (True, ["search_path=pg_catalog, public, pg_temp"], False)
        ]
    with (
        task_login(ServiceRole.WORKER, exact=True, reconnect=True),
        pytest.raises(DatabaseError, match="permission denied"),
    ):
        read_coverage("no-session")
    text = MIGRATION.read_text(encoding="utf-8")
    check = re.search(r"^DO \$check\$.*?^\$check\$;", text, re.M | re.S)[0]
    # The real installation passes; an invoker does not.
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(check)
    with (
        pytest.raises(DatabaseError, match="Migration 0169"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(f"ALTER FUNCTION public.{DEFINER}(text, uuid) SECURITY INVOKER")
        cursor.execute(check)


def test_staff_and_administrators_see_the_leaders_column(deployment, google):
    """Each By Ministry row has its Leaders status; the page help explains
    it; the page reads the definer once per rendering."""
    _, browser = deployment
    body, calls = counted_page(browser)
    assert calls == 1
    assert '<th scope="col">Leaders</th>' in by_ministry(body)
    assert leaders_cells(body) == [
        ("Choir", "warning", "No user yet"),
        ("Ministry 9", "warning", "Not signed in yet"),
    ]
    assert "Leaders says whether anyone besides Staff" in body
    assert viewed().count() == 1

    staff = sign_in_as(google, "staff@example.org")
    assert leaders_cells(page(staff))[1] == ("Ministry 9", "working", "1 signed in")
    assert viewed().count() == 2
    # No leader's address or name reaches the column.
    assert "@" not in by_ministry(page(staff))


def test_a_ministry_leader_never_sees_the_column(deployment, google):
    """A leader's page has no Leaders column or help and reads no coverage."""
    leader = sign_in_as(google, "ann@example.org")
    body, calls = counted_page(leader)
    assert calls == 0
    assert "Leaders" not in by_ministry(body) and "leader-status" not in body
    assert "Leaders says whether" not in body
    assert viewed().count() == 0


def test_report_ministry_prints_the_leaders_status(admin, google):  # noqa: F811
    """``report ministry`` prints each Ministry's Leaders status to an
    Administrator, read through the same audited definer."""
    store = admin.service.store
    add_rules(store, address("staff@example.org", ("staff",)))
    convert(store)
    leaders_source(store)
    google[0].update(email="admin@example.org", sub="synthetic-google-subject")
    _browser, secret, _ = paired(admin.service)
    before = viewed().count()
    code, document = one(admin, "report", "ministry", secret=secret)
    assert code == 0 and document["ok"], document
    assert {
        row["ministry"]: row["leaders"] for row in document["result"]["summaries"]
    } == {
        4: {"state": "no_user", "signed_in": 0},
        9: {"state": "not_signed_in", "signed_in": 0},
    }
    assert viewed().count() == before + 1
    assert "@" not in str(document)

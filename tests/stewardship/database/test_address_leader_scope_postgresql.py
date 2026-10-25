"""The Ministries one user's address leads (#1925, frozen migration 0167).

A user's Timeline shows "Ministry leader of" from the shared leader scope,
which is keyed by a Google account. A Ministry leader who never signed in
has none, so the page reads their address through
``stewardship_address_leader_scope_v1`` instead: read-only, for the web
login and an Administrator only, answering what the shared scope would
answer for a Google account with that address. These tests check its
grants and refusals, that it follows the configured leader roles and the
users' role gate, that it compares addresses in lower case, the page's use
of it, and the frozen file's closing check.
"""

import json
import re
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.db.models import F
from psycopg import sql

from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.deployment import ServiceRole

from .auth_builders import signed_in
from .campaign_builders import change
from .leader_builders import leader
from .test_background_grants_postgresql import task_login
from .test_leader_emails_postgresql import add_users
from .test_leader_import_postgresql import leaders_source
from .user_builders import convert, user_id, web

pytestmark = pytest.mark.django_db(transaction=True)

FROZEN = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0167_address_leader_scope.sql"
).read_text(encoding="utf-8")
CHECK = FROZEN[FROZEN.index("DO $check$") :]
FUNCTION = "public.stewardship_address_leader_scope_v1(text,uuid)"
# 0167's own statement, as CREATE OR REPLACE: 0179 (#2166) has since replaced
# the body with its operator qualified, so the check tests put this back
# first, in transactions that roll back.
_START = FROZEN.index("CREATE FUNCTION public.stewardship_address_leader_scope_v1(")
OWN = (
    "CREATE OR REPLACE"
    + FROZEN[_START + len("CREATE") : FROZEN.index("$$;", _START) + len("$$;")]
)
ADMIN = "admin@example.org"


@pytest.fixture
def deployment(auth_service, google):
    """A users deployment whose leaders never signed in, and the Administrator.

    ParishSoft lists Lee (Staff role, Choir 4), Chris (Chairperson, Food
    pantry 9), Terry (a "Team 1 leader" role, 9), Nora (Staff role, 4, but
    no user), Ada (Staff role, 4) and Max (Staff role, 9 then 4). Lee and
    Terry are Ministry leader users, Chris a Staff user, Ada an
    Administrator user, Max a Ministry leader user, and Pat a Ministry
    leader user ParishSoft lists nowhere. Only the Administrator who reads
    has signed in. Returns ``(store, browser, admin id)``.
    """
    store = auth_service.store
    convert(store)
    leaders_source(
        store,
        [
            leader("lee@example.org", 4),
            leader("chris@example.org", 9, role="Chairperson"),
            leader("terry@example.org", 9, role="Team 1 leader"),
            leader("nora@example.org", 4),
            leader("ada@example.org", 4),
            leader("max@example.org", 9),
            leader("max@example.org", 4),
        ],
    )
    add_users(
        store,
        ("lee@example.org", "Leader, Lee", "ministry_leader"),
        ("chris@example.org", "Chair, Chris", "staff"),
        ("terry@example.org", "Team, Terry", "ministry_leader"),
        ("pat@example.org", "Plain, Pat", "ministry_leader"),
        ("ada@example.org", "Admin, Ada", "administrator"),
        ("max@example.org", "Many, Max", "ministry_leader"),
    )
    browser, login = signed_in()
    assert login.status_code == 302
    return store, browser, PortalUser.objects.get(email=ADMIN).pk


def scope_for(address, actor, service=ServiceRole.WEB):
    """The function's answer under ``service``'s real login."""
    with task_login(service), connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {FUNCTION.split('(')[0]}(%s, %s)::text", [address, actor]
        )
        return json.loads(cursor.fetchone()[0])


def refused(address, actor, service=ServiceRole.WEB):
    """The SQLSTATE and message of a refused call under ``service``."""
    with pytest.raises(DatabaseError) as error:
        scope_for(address, actor, service)
    return error.value.__cause__.sqlstate, str(error.value)


def set_roles(store, roles):
    """Apply the current campaign's Ministry leader roles."""
    campaign = SystemConfiguration.objects.get().current_campaign_id
    operation = {
        "operation": "update",
        "section": "campaigns",
        "id": str(campaign),
        "values": {"ministry_leader_roles": roles},
    }
    assert change(store, store.active(), uuid4(), [operation]).state == "applied"


def test_it_answers_the_shared_scope_for_an_address(deployment):
    """Leaders by the configured roles; the users' role gate; any case."""
    store, _browser, admin = deployment
    assert PortalUser.objects.filter(email__in=["lee@example.org"]).count() == 0
    assert scope_for("lee@example.org", admin) == [4]
    # Compared in lower case, however the address is typed.
    assert scope_for("LEE@Example.ORG", admin) == [4]
    assert scope_for("chris@example.org", admin) == [9]
    # The Administrator role passes the gate too.
    assert scope_for("ada@example.org", admin) == [4]
    # A role outside the default list leads nothing; ParishSoft listing an
    # address that is no user leads nothing (the role gate), and neither
    # does a user ParishSoft lists nowhere.
    assert scope_for("terry@example.org", admin) == []
    assert scope_for("nora@example.org", admin) == []
    assert scope_for("pat@example.org", admin) == []
    assert scope_for("nobody@example.org", admin) == []
    # A custom role list replaces the default.
    set_roles(store, ["Team 1 Leader"])
    assert scope_for("terry@example.org", admin) == [9]
    assert scope_for("lee@example.org", admin) == []
    assert scope_for("chris@example.org", admin) == []


def test_it_answers_as_the_scope_does_after_sign_in(deployment, google):
    """Once Max (two Ministries) signs in, the account's scope and the
    address agree, in the same ascending order."""
    _store, _browser, admin = deployment
    assert scope_for("max@example.org", admin) == [4, 9]
    claims = google[0]
    claims.update(email="max@example.org", sub="synthetic-max")
    _, login = signed_in()
    assert login.status_code == 302
    account = PortalUser.objects.get(email="max@example.org").pk
    with task_login(ServiceRole.WEB), connection.cursor() as cursor:
        cursor.execute(
            "SELECT public.stewardship_ministry_leader_scope_v1(%s)::text", [account]
        )
        assert json.loads(cursor.fetchone()[0]) == [4, 9]
    assert scope_for("max@example.org", admin) == [4, 9]


def test_only_an_administrator_through_the_web_login(deployment, google):
    """A Staff or Ministry leader actor, an unknown or missing actor, a
    disabled Administrator and a malformed address are refused; every other
    login lacks EXECUTE, and one granted it is still refused."""
    _store, _browser, admin = deployment
    claims = google[0]
    claims.update(email="chris@example.org", sub="synthetic-chris")
    assert signed_in()[1].status_code == 302
    claims.update(email="lee@example.org", sub="synthetic-lee")
    assert signed_in()[1].status_code == 302
    staff = PortalUser.objects.get(email="chris@example.org").pk
    ministry_leader = PortalUser.objects.get(email="lee@example.org").pk
    for actor in (staff, ministry_leader, uuid4(), None):
        state, message = refused("lee@example.org", actor)
        assert state == "42501", actor
        assert message.startswith("A user's Ministries require an Administrator")
    for address in ("", "no-at-sign", "x" * 250 + "@a.org", None):
        state, _message = refused(address, admin)
        assert state == "22023", address
    for service in (
        ServiceRole.WORKER,
        ServiceRole.SCHEDULER,
        ServiceRole.MAIL_DISPATCH,
    ):
        state, message = refused("lee@example.org", admin, service)
        assert state == "42501" and "permission denied" in message, service
    # A login granted EXECUTE by mistake is still refused by the body.
    name = "test_background_" + uuid4().hex
    role = sql.Identifier(name)
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE ROLE {} NOINHERIT").format(role))
        cursor.execute(
            sql.SQL("GRANT EXECUTE ON FUNCTION {} TO {}").format(
                sql.SQL(FUNCTION), role
            )
        )
    try:
        with (
            pytest.raises(DatabaseError, match="read only by the web login"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(sql.SQL("SET LOCAL SESSION AUTHORIZATION {}").format(role))
            cursor.execute(
                f"SELECT {FUNCTION.split('(')[0]}(%s, %s)", ["a@b.org", admin]
            )
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("REVOKE ALL ON FUNCTION {} FROM {}").format(
                    sql.SQL(FUNCTION), role
                )
            )
            cursor.execute(sql.SQL("DROP ROLE {}").format(role))
    # A disabled Administrator is refused too (last: it cannot be re-enabled).
    PortalUser.objects.filter(pk=admin).update(disabled=True, version=F("version") + 1)
    state, message = refused("lee@example.org", admin)
    assert state == "42501"
    assert message.startswith("A user's Ministries require an Administrator")


def test_the_timeline_shows_a_never_signed_in_leaders_ministries(deployment):
    """Lee never signed in: Ministry leader of shows Choir, and there is no
    System logs control (System logs filters by Google account)."""
    store, browser, _admin = deployment
    record = user_id(store, "lee@example.org")
    with web():
        response = browser.get(f"/admin/users/{record}/timeline/")
    page = response.content.decode()
    assert response.status_code == 200, page
    assert re.search(r"<dt>Ministry leader of</dt>\s*<dd>Choir</dd>", page), page
    assert 'name="actor"' not in page and "entries in System logs" not in page
    # A user ParishSoft lists nowhere shows no Ministry line.
    with web():
        response = browser.get(
            f"/admin/users/{user_id(store, 'pat@example.org')}/timeline/"
        )
    assert response.status_code == 200
    assert "Ministry leader of" not in response.content.decode()


def test_the_migration_check_accepts_the_catalog():
    """0167's function, as 0167 installed it, is exactly the declared one."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(OWN)
        cursor.execute(CHECK)
        # Keep 0179's body: roll the restore back.
        transaction.set_rollback(True)


@pytest.mark.parametrize(
    "statement",
    [
        f"DROP FUNCTION {FUNCTION}",
        f"ALTER FUNCTION {FUNCTION} SECURITY INVOKER",
        f"ALTER FUNCTION {FUNCTION} SET search_path = public",
        f"ALTER FUNCTION {FUNCTION} VOLATILE",
        f"GRANT EXECUTE ON FUNCTION {FUNCTION} TO PUBLIC",
        "CREATE ROLE test_i1925_grantee; "
        f"GRANT EXECUTE ON FUNCTION {FUNCTION} TO test_i1925_grantee",
        # The same name, without the Administrator check.
        f"CREATE OR REPLACE FUNCTION {FUNCTION.replace('(text,uuid)', '')}"
        "(address_text text, actor_uuid uuid) RETURNS jsonb LANGUAGE sql "
        "STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp "
        "AS $$ SELECT '[]'::jsonb $$",
    ],
)
def test_the_migration_check_refuses_a_changed_function(statement):
    """The DO block raises for a missing, widened or rewritten function."""
    with (
        pytest.raises(DatabaseError, match="Migration 0167"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(OWN)
        cursor.execute(statement)
        cursor.execute(CHECK)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        # The leader definition is dropped (every address leads Ministry 1).
        (
            "JOIN public.stewardship_ministry_leaders_v1() leader "
            "ON leader.email=lower(address_text)",
            "CROSS JOIN (SELECT 1 AS ministry_duid) leader",
        ),
        # The role gate admits only Administrators.
        (
            "?| ARRAY['administrator','staff','ministry_leader']",
            "?| ARRAY['administrator']",
        ),
    ],
)
def test_the_migration_check_refuses_a_dropped_leader_join_or_role_gate(old, new):
    """The installed body is rewritten without one clause; the DO block raises."""
    with (
        pytest.raises(DatabaseError, match="Migration 0167"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(OWN)
        cursor.execute(f"SELECT pg_get_functiondef('{FUNCTION}'::regprocedure)")
        definition = cursor.fetchone()[0]
        assert old in definition
        cursor.execute(definition.replace(old, new))
        cursor.execute(CHECK)

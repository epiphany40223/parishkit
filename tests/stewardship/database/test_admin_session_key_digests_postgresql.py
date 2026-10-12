"""Admin session keys are stored only as digests (#995).

A real sign-in leaves the cookie's key in neither ``django_session`` nor
``stewardship_portal_session``; a stored value replayed as a cookie admits
nothing; the SQL helper computes the Python digest; the insert guard refuses
an undigested key; and frozen file 0057 ends the sessions an older release
stored, refuses to commit without its guard, and leaves Family sessions
alone.
"""

import re
from datetime import timedelta
from importlib import import_module
from pathlib import Path
from uuid import uuid4

import pytest
from django.conf import settings
from django.contrib.sessions.models import Session
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.test import RequestFactory

from parishkit.stewardship.accounts import sessions
from parishkit.stewardship.accounts.admin_session_store import SessionStore, digest
from parishkit.stewardship.accounts.models import PortalSession, PortalUser
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.audit.models import AuditEvent

from .auth_builders import signed_in, unguarded

pytestmark = pytest.mark.django_db(transaction=True)

TEXT_PATH = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0057_admin_session_key_digests.sql"
)
TEXT = TEXT_PATH.read_text(encoding="utf-8")
# The closing check, after the statement that records the replaced
# definers' grants for it (a transaction-local setting): run as one string,
# so both are in one transaction.
CHECK = (
    re.search(
        r"^SELECT set_config\('stewardship\.migration_0057_acl'.*?;$", TEXT, re.M | re.S
    )[0]
    + "\n"
    + re.search(r"^DO \$check\$.*?^\$check\$;", TEXT, re.M | re.S)[0]
)
REVOKE = re.search(r"^WITH migration AS .*?CROSS JOIN migration;", TEXT, re.M | re.S)[0]


def admitted(cookie, auth_service):
    """Whether a request presenting ``cookie`` as pk_admin is a live Admin."""
    request = RequestFactory().get("/admin/")
    request.session = SessionStore(cookie)
    return sessions.authenticated_admin(request, store=auth_service.store) is not None


def undigested_session(principal_id, *, idle=timedelta(0)):
    """A live Admin session as an older release stored it: under its raw key.

    Django's own store keeps the raw key; the row is written past the
    guards, as the older release's rows already exist in the database.
    """
    store = import_module(settings.SESSION_ENGINE).SessionStore()
    store["principal"] = str(principal_id)
    store.save()
    now = database_now() - idle
    with unguarded():
        PortalSession.objects.create(
            session_id=store.session_key,
            principal_id=principal_id,
            authenticated_at=now,
            last_activity_at=now,
            expires_at=now + timedelta(hours=1),
        )
    return store.session_key


def test_sign_in_stores_only_the_digest_and_a_stored_value_does_not_replay(
    auth_service, google
):
    """The cookie admits; its key is stored nowhere; the stored value admits nothing."""
    browser, response = signed_in()
    assert response.status_code == 302
    cookie = browser.cookies["pk_admin"].value
    row = PortalSession.objects.get()
    assert row.session_id == digest(cookie) != cookie
    assert re.fullmatch(r"[0-9a-f]{40}", row.session_id)
    assert not Session.objects.filter(session_key=cookie).exists()
    assert Session.objects.filter(session_key=row.session_id).exists()
    assert admitted(cookie, auth_service)
    # What the web login can read is useless as a cookie.
    assert not admitted(row.session_id, auth_service)
    assert PortalSession.objects.get().revoked_at is None


def test_sql_digest_matches_the_python_store(db):
    """Definers compare session_id with the same digest Python stores."""
    keys = ["abcdefghijklmnopqrstuvwxyz012345", SessionStore()._get_new_session_key()]
    with connection.cursor() as cursor:
        for key in keys:
            cursor.execute("SELECT public.stewardship_admin_session_key_v1(%s)", [key])
            assert cursor.fetchone()[0] == digest(key)


def test_the_insert_guard_refuses_an_undigested_key(auth_service, google):
    """Even an authorized principal's row is refused under a raw Django key."""
    signed_in()
    admin = PortalUser.objects.get()
    store = import_module(settings.SESSION_ENGINE).SessionStore()
    store.save()
    now = database_now()
    with (
        pytest.raises(IntegrityError, match="stores only its key's digest"),
        transaction.atomic(),
    ):
        PortalSession.objects.create(
            session_id=store.session_key,
            principal_id=admin.pk,
            authenticated_at=now,
            last_activity_at=now,
            expires_at=now + timedelta(hours=1),
        )


def test_create_admin_session_refuses_another_store(auth_service, google):
    """Only the Admin store may back an Admin session."""
    from parishkit.stewardship.accounts.policy import current_principal

    signed_in()
    principal = current_principal(auth_service.store, PortalUser.objects.get().pk)
    now = database_now()
    with pytest.raises(TypeError):
        sessions.create_admin_session(
            principal,
            now=now,
            authenticated_at=now,
            expires_at=now + timedelta(hours=1),
            session=import_module(settings.SESSION_ENGINE).SessionStore(),
        )


def test_upgrade_ends_older_sessions_and_the_administrator_signs_in_again(
    auth_service, google
):
    """0057 revokes each undigested live row, audited; nothing else changes.

    The older session's cookie admits nothing once this release runs, the
    revocation records ``admin_revoked``, a digest session and a Family
    (non-Admin) Django session are untouched, and the Administrator signs in
    again as usual.
    """
    browser, _ = signed_in()
    admin = PortalUser.objects.get()
    current = PortalSession.objects.get()
    old_key = undigested_session(admin.pk)
    family = import_module(settings.SESSION_ENGINE).SessionStore()
    family["family"] = str(uuid4())
    family.save()
    assert not admitted(old_key, auth_service)
    with connection.cursor() as cursor:
        cursor.execute(REVOKE)
        cursor.execute(CHECK)
    old = PortalSession.objects.get(session_id=old_key)
    assert old.revoked_at is not None and old.version == 2
    assert list(
        AuditEvent.objects.filter(subject_id=old.pk).values_list(
            "event_type", "actor_id"
        )
    ) == [("admin_revoked", admin.pk)]
    assert PortalSession.objects.get(pk=current.pk).revoked_at is None
    assert Session.objects.filter(session_key=family.session_key).exists()
    again, response = signed_in()
    assert response.status_code == 302
    assert admitted(again.cookies["pk_admin"].value, auth_service)


def test_upgrade_leaves_a_lapsed_session_to_cleanup(auth_service, google):
    """An undigested row already idle past 60 minutes is not admin_revoked.

    Cleanup ends it as admin_timeout, as it would have without the upgrade,
    and the migration's own check accepts it.
    """
    signed_in()
    lapsed = undigested_session(PortalUser.objects.get().pk, idle=timedelta(minutes=61))
    with connection.cursor() as cursor:
        cursor.execute(REVOKE)
        cursor.execute(CHECK)
    row = PortalSession.objects.get(session_id=lapsed)
    assert row.revoked_at is None
    assert not AuditEvent.objects.filter(subject_id=row.pk).exists()
    assert sessions.cleanup_admin_sessions() == 1
    assert list(
        AuditEvent.objects.filter(subject_id=row.pk).values_list(
            "event_type", flat=True
        )
    ) == ["admin_timeout"]


def test_the_check_refuses_a_live_undigested_session(auth_service, google):
    """A database that still holds a live raw-key session fails the migration."""
    signed_in()
    undigested_session(PortalUser.objects.get().pk)
    with (
        pytest.raises(DatabaseError, match="undigested key is still live"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(CHECK)


def test_the_check_refuses_the_database_before_the_guard(db):
    """The pre-0057 state (no insert guard) fails the migration's own check."""
    with (
        pytest.raises(DatabaseError, match="digest guard was not installed"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "DROP TRIGGER stewardship_portal_session_key_digest_v1 "
            "ON public.stewardship_portal_session"
        )
        cursor.execute(CHECK)


def test_the_check_refuses_a_granted_digest_function(db):
    """A digest function PUBLIC may execute fails the migration's own check."""
    with (
        pytest.raises(DatabaseError, match="wrong security, search_path or grant"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "GRANT EXECUTE ON FUNCTION "
            "public.stewardship_admin_session_key_v1(text) TO PUBLIC"
        )
        cursor.execute(CHECK)


def test_the_check_refuses_a_definer_that_compares_the_raw_key(db):
    """0053's body (comparing the key itself) fails 0057's own check."""
    old = (
        Path(TEXT_PATH.parent / "0053_user_name_lookup.sql")
        .read_text(encoding="utf-8")
        .replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
    )
    body = re.search(
        r"^CREATE OR REPLACE FUNCTION public\.stewardship_member_names_for_v1\("
        r".*?^\$\$;",
        old,
        re.M | re.S,
    )[0]
    assert "WHERE login.session_id=admin_session AND" in body
    with (
        pytest.raises(DatabaseError, match="Users session definer is not installed"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(body)
        cursor.execute(CHECK)


def test_the_check_refuses_a_definer_whose_grants_changed(db):
    """A replacement that changed who may run a definer fails the check."""
    with (
        pytest.raises(DatabaseError, match="lost or changed its grants"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        acl, check = CHECK.split("\nDO $check$", 1)
        cursor.execute(acl)
        # A role of this transaction only, so the change rolls back with it.
        cursor.execute("CREATE ROLE stewardship_0057_probe NOLOGIN")
        cursor.execute(
            "GRANT EXECUTE ON FUNCTION public.stewardship_leaders_without_user_v1"
            "(text, uuid) TO stewardship_0057_probe"
        )
        cursor.execute("DO $check$" + check)


@pytest.mark.parametrize(
    "call",
    [
        "SELECT public.stewardship_member_names_for_v1(%s, 'x@example.org', %s)",
        "SELECT public.stewardship_leaders_without_user_v1(%s, %s)",
    ],
)
def test_each_users_definer_refuses_the_stored_session_id(auth_service, google, call):
    """The stored session_id is no key to either definer; the cookie's key is.

    Only the refusal is checked here; the definers' answers are tested with
    the Users page (test_user_views, test_leader_import).
    """
    browser, _ = signed_in()
    stored = PortalSession.objects.get().session_id
    with (
        pytest.raises(DatabaseError, match="requires a live Administrator session"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(call, [stored, uuid4()])
    # The cookie's key passes the session check (whatever the definer then
    # answers on this deployment's configuration).
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(call, [browser.cookies["pk_admin"].value, uuid4()])
    except DatabaseError as error:
        assert "live Administrator session" not in str(error)

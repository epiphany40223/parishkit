"""SQL guards on the Admin identity rows the SQL Admin checks trust (#306 M2).

The web login writes PortalUser and PortalSession rows, and many SQL checks
(exports, go-live, production confirmation, delivery control) trust them.
These tests show that a web logic bug cannot mint, stretch or revive Admin
authority that SQL can see, while every legitimate path still works.
"""

import logging
from datetime import timedelta
from uuid import uuid4

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.db import IntegrityError, connection, transaction
from django.db.models import F

from parishkit.stewardship.accounts.models import PortalSession, PortalUser
from parishkit.stewardship.accounts.sessions import database_now

from .auth_builders import signed_in
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


def forge(principal_id, **values):
    """Insert an Admin session row directly, as a buggy web path could."""
    store = SessionStore()
    store.save()
    now = database_now()
    fields = dict(
        session_id=store.session_key,
        principal_id=principal_id,
        authenticated_at=now,
        last_activity_at=now,
        expires_at=now + timedelta(hours=1),
    )
    with transaction.atomic():
        return PortalSession.objects.create(**(fields | values))


def refused(message="Admin session requires"):
    """The guard's check violation surfaces as an IntegrityError."""
    return pytest.raises(IntegrityError, match=message)


def test_session_inserts_need_a_live_authorized_principal(auth_service, google):
    """Unknown, rule-less and disabled principals get no session; a real one does."""
    signed_in()
    admin = PortalUser.objects.get()
    with refused():
        forge(uuid4())
    stranger = PortalUser.objects.create(
        google_subject="stranger-subject",
        email="stranger@elsewhere.invalid",
        verified_at=database_now(),
    )
    with refused():
        forge(stranger.pk)
    # The current administrator rule admits a correctly bounded row.
    assert forge(admin.pk).principal_id == admin.pk
    PortalUser.objects.filter(pk=admin.pk).update(
        disabled=True, version=F("version") + 1
    )
    with refused():
        forge(admin.pk)
    # The ordinary sign-in of a disabled account is refused before SQL.
    assert signed_in()[1].status_code == 403


def test_session_inserts_cannot_exceed_the_absolute_or_clock_limits(
    auth_service, google
):
    """No row lives past 12 hours or claims a future sign-in or activity."""
    signed_in()
    admin = PortalUser.objects.get()
    now = database_now()
    with refused():
        forge(admin.pk, expires_at=now + timedelta(hours=12, minutes=1))
    future = now + timedelta(minutes=5)
    with refused():
        forge(admin.pk, authenticated_at=future, last_activity_at=future)
    with refused():
        forge(admin.pk, last_activity_at=future)


def test_session_deadline_and_activity_cannot_be_stretched(auth_service, google):
    """The absolute deadline is fixed and idle renewal stops at the clock."""
    signed_in()
    row = PortalSession.objects.get()
    with refused("immutable"), transaction.atomic():
        PortalSession.objects.filter(pk=row.pk).update(
            expires_at=row.expires_at + timedelta(hours=1), version=F("version") + 1
        )
    with refused("future"), transaction.atomic():
        PortalSession.objects.filter(pk=row.pk).update(
            last_activity_at=database_now() + timedelta(minutes=30),
            version=F("version") + 1,
        )
    row.refresh_from_db()
    assert row.expires_at == PortalSession.objects.get().expires_at


def test_disabling_is_one_way(auth_service, google):
    """Disabling succeeds; no runtime write can re-enable the account."""
    signed_in()
    user = PortalUser.objects.get()
    PortalUser.objects.filter(pk=user.pk).update(
        disabled=True, version=F("version") + 1
    )
    with refused("re-enabled"), transaction.atomic():
        PortalUser.objects.filter(pk=user.pk).update(
            disabled=False, version=F("version") + 1
        )
    assert PortalUser.objects.get().disabled


def test_identity_claims_change_only_with_a_fresh_verification(auth_service, google):
    """Email and hosted domain move only with verified_at stamped in the same
    transaction, as the Google callback does; the callback still refreshes."""
    signed_in()
    user = PortalUser.objects.get()
    for values in (
        dict(email="other@example.org"),
        dict(hosted_domain="elsewhere.invalid"),
        dict(email="other@example.org", verified_at=user.verified_at),
        dict(verified_at=user.verified_at - timedelta(seconds=1)),
    ):
        with refused("fresh verification"), transaction.atomic():
            PortalUser.objects.filter(pk=user.pk).update(
                **values, version=F("version") + 1
            )
    with transaction.atomic():
        PortalUser.objects.filter(pk=user.pk).update(
            email="other@example.org",
            verified_at=database_now(),
            version=F("version") + 1,
        )
    assert PortalUser.objects.get().email == "other@example.org"
    # A later Google sign-in rewrites the claims through the same path.
    assert signed_in()[1].status_code == 302
    assert PortalUser.objects.get().email == "admin@example.org"


def test_web_login_is_bound_by_the_same_guards(auth_service, google):
    """The real web login signs in, steps up and logs out, but cannot forge."""
    with web_login():
        browser, response = signed_in()
        assert response.status_code == 302
        assert signed_in(browser)[1].status_code == 302
        admin = PortalUser.objects.get()
        with refused():
            forge(uuid4())
        with refused():
            forge(admin.pk, expires_at=database_now() + timedelta(hours=13))
        response = browser.post(
            "/admin/logout",
            {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value},
        )
        assert response.status_code == 302
    assert PortalSession.objects.filter(revoked_at__isnull=True).count() == 0


def test_a_rule_removed_before_the_insert_is_a_denial(
    auth_service, google, monkeypatch, caplog
):
    """If policy changes between Python's role check and the session insert,
    the SQL guard's refusal becomes the ordinary 403, not a server error."""
    from parishkit.stewardship.accounts import authentication, sessions
    from parishkit.stewardship.accounts.policy import Principal

    def stale(store, user_id):
        """The roles Python read just before the rule was removed."""
        return Principal(user_id, frozenset({"administrator"}))

    monkeypatch.setattr(authentication, "current_principal", stale)
    monkeypatch.setattr(sessions, "current_principal", stale)
    google[0].update(email="removed@unlisted.invalid", sub="removed-subject")
    google[0].update(hd="unlisted.invalid")
    with caplog.at_level(logging.WARNING, logger=authentication.__name__):
        client, response = signed_in()
    assert response.status_code == 403
    assert not PortalSession.objects.exists()
    assert "pk_admin" not in client.cookies or not client.cookies["pk_admin"].value
    assert "SQL session guard" in caplog.text


def test_an_unrelated_integrity_error_is_not_a_denial(
    auth_service, google, monkeypatch, caplog
):
    """Only the session guard's own refusal becomes a 403; any other
    integrity violation in sign-in is a bug that surfaces as an error."""
    from parishkit.stewardship.accounts import authentication

    signed_in()

    def violated(*args, **kwargs):
        """A real unique violation, as a buggy sign-in write would raise."""
        user = PortalUser.objects.get()
        with transaction.atomic():
            PortalUser.objects.create(
                google_subject=user.google_subject,
                email=user.email,
                verified_at=database_now(),
            )

    monkeypatch.setattr(authentication, "issue_admin", violated)
    with (
        caplog.at_level(logging.WARNING, logger=authentication.__name__),
        pytest.raises(IntegrityError),
    ):
        signed_in()
    assert "unexpected database invariant" in caplog.text
    assert PortalSession.objects.count() == 1


def test_user_inserts_need_a_timestamp_inside_the_transaction(auth_service, google):
    """Web may insert only an enabled, unattributed, version-1 user whose
    verified_at lies inside the inserting transaction (#389, the #352
    residual); SQL cannot see the sign-in itself. A sign-in still records one."""
    with web_login():
        assert signed_in()[1].status_code == 302
        assert PortalUser.objects.count() == 1

        def insert(**values):
            """One direct insert, as a buggy web path could make."""
            with transaction.atomic():
                fields = dict(
                    google_subject=str(uuid4()),
                    email="synthetic@example.org",
                    verified_at=database_now(),
                )
                return PortalUser.objects.create(**(fields | values))

        for values in (
            # Verified before this transaction began, or in the future.
            dict(verified_at=database_now() - timedelta(seconds=5)),
            dict(verified_at=database_now() + timedelta(minutes=5)),
            dict(disabled=True),
            dict(actor_id=uuid4()),
            dict(google_subject=" "),
            dict(email=""),
        ):
            with refused("verification inside this transaction"):
                insert(**values)
        # A fresh verification in the inserting transaction is admitted, with
        # the database's own creation time.
        fresh = insert()
        assert fresh.verified_at <= PortalUser.objects.get(pk=fresh.pk).created_at


def test_only_the_web_login_inserts_users():
    """Another runtime login is refused even with a fresh verification."""
    from django.db import DatabaseError

    from parishkit.stewardship.deployment import ServiceRole

    from .test_background_grants_postgresql import task_login

    with (
        task_login(ServiceRole.WORKER),
        connection.cursor() as cursor,
    ):
        cursor.execute("RESET SESSION AUTHORIZATION")
        cursor.execute(
            "GRANT INSERT ON stewardship_portal_user TO pk_stewardship_worker"
        )
        cursor.execute("SET SESSION AUTHORIZATION pk_stewardship_worker")
        # A plain INSERT: the worker holds no SELECT for Django's RETURNING.
        with (
            pytest.raises(DatabaseError, match="Only the web login"),
            transaction.atomic(),
        ):
            cursor.execute(
                "INSERT INTO stewardship_portal_user (id, correlation_id, version, "
                "google_subject, email, verified_at, disabled) VALUES "
                "(%s, %s, 1, %s, 'synthetic@example.org', statement_timestamp(), "
                "false)",
                [uuid4(), uuid4(), str(uuid4())],
            )

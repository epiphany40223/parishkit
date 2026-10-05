"""Admin automation under the real restricted logins and their exact grants.

The other automation tests run as the disposable schema owner, which the
guards exempt from their login checks. Here each step runs as the login that
does it in production, with exactly the grants ``runtime_grants`` declares:
the web login approves, admits commands, records use, ends sessions and
acknowledges notices; the general worker runs the maintenance pass and may end
a session only for role loss, removal or recovery; the offline admin-recovery
login ends every session for a restore, through column grants that offline
admission now declares and verifies.
"""

from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.config import ConfigError
from parishkit.stewardship.accounts import automation_sessions as automation
from parishkit.stewardship.accounts.automation_models import (
    AutomationLogin,
    AutomationNotice,
    AutomationSession,
)
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.runtime_database import offline_columns, offline_grants
from parishkit.stewardship.runtime_grants import admit_columns

from .auth_builders import signed_in, unguarded
from .automation_builders import approve_now, command, paired, start
from .test_background_grants_postgresql import task_login

pytestmark = pytest.mark.django_db(transaction=True)


def test_the_web_login_approves_admits_ends_and_acknowledges(auth_service, google):
    """Every web step of a session's life under pk_stewardship_web's own grants."""
    from parishkit.stewardship.accounts.policy import current_principal

    signed_in()
    secret, code = start(auth_service)
    with task_login(ServiceRole.WEB):
        approve_now(auth_service, code)
        caller = command(auth_service, secret)
        assert AutomationLogin.objects.filter(
            portal_session_id=caller.portal_session.pk
        ).exists()
        automation.close_command_session(caller.portal_session)
        row = AutomationSession.objects.get()
        administrator = current_principal(auth_service.store, row.principal_id)
        with transaction.atomic():
            assert automation.acknowledge_notices(administrator) == 1
        assert automation.revoke(row.pk, administrator, reason="revoked_by_owner")
    row.refresh_from_db()
    assert row.end_reason == "revoked_by_owner" and row.last_used_at is not None


def test_the_worker_runs_the_maintenance_pass(auth_service, google):
    """Cleanup, link deletion, lapsed endings and incidents as pk_stewardship_worker."""
    _, secret, row = paired(auth_service)
    caller = command(auth_service, secret)
    automation.close_command_session(caller.portal_session)
    with unguarded(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE stewardship_address_rule SET roles='[\"staff\"]'::jsonb "
            "WHERE email='admin@example.org'"
        )
    with task_login(ServiceRole.WORKER):
        totals = automation.maintain()
    assert totals["command_logins_removed"] == 1
    assert totals["sessions_ended"] == 1
    assert not PortalSession.objects.filter(pk=caller.portal_session.pk).exists()
    row.refresh_from_db()
    assert row.end_reason == "role_lost"


@pytest.mark.parametrize("reason", ["logout", "misused", "restore"])
def test_the_worker_cannot_end_a_session_for_other_reasons(
    auth_service, google, reason
):
    """The session guard admits only role_lost, user_removed and recovery from it."""
    _, _, row = paired(auth_service)
    with task_login(ServiceRole.WORKER):
        with pytest.raises(DatabaseError), transaction.atomic():
            automation.end_session(row, reason=reason)
        with pytest.raises(DatabaseError), transaction.atomic():
            AutomationLogin.objects.create(
                portal_session_id=uuid4(), automation_session=row
            )
    row.refresh_from_db()
    assert row.revoked_at is None


def test_the_web_login_cannot_delete_or_rewrite_sessions(auth_service, google):
    """No delete grant or guard path, and identity columns are not writable."""
    _, _, row = paired(auth_service)
    with task_login(ServiceRole.WEB):
        with pytest.raises(DatabaseError), transaction.atomic():
            AutomationSession.objects.filter(pk=row.pk).delete()
        with (
            pytest.raises(DatabaseError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE stewardship_automation_session SET scope='full', "
                "version=version+1 WHERE id=%s",
                [row.pk],
            )


def test_admin_recovery_ends_every_session_offline(auth_service, google):
    """``revoke-automation-sessions`` as pk_stewardship_admin_recovery."""
    _, secret, row = paired(auth_service)
    with task_login(ServiceRole.ADMIN_RECOVERY):
        admit_columns(
            connection,
            offline_grants(ServiceRole.ADMIN_RECOVERY),
            offline_columns(ServiceRole.ADMIN_RECOVERY),
        )
        # The guard admits only restore and revoked_by_operator from it.
        with (
            pytest.raises(DatabaseError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE stewardship_automation_session SET revoked_at="
                "statement_timestamp(), end_reason='logout', version=version+1 "
                "WHERE revoked_at IS NULL"
            )
        assert automation.revoke_all_offline("restore", correlation_id=uuid4()) == 1
    row.refresh_from_db()
    assert row.end_reason == "restore"
    assert AutomationNotice.objects.filter(kind="ended", automation_session=row)
    with pytest.raises(automation.SessionUnusable):
        command(auth_service, secret)


def test_offline_admission_verifies_the_recovery_column_grants(auth_service):
    """An extra column grant on the session table is refused as excessive."""
    with task_login(ServiceRole.ADMIN_RECOVERY):
        admit_columns(
            connection,
            offline_grants(ServiceRole.ADMIN_RECOVERY),
            offline_columns(ServiceRole.ADMIN_RECOVERY),
        )
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            cursor.execute(
                "GRANT UPDATE (label) ON stewardship_automation_session "
                "TO pk_stewardship_admin_recovery"
            )
            cursor.execute("SET SESSION AUTHORIZATION pk_stewardship_admin_recovery")
        with pytest.raises(ConfigError, match="excessive"):
            admit_columns(
                connection,
                offline_grants(ServiceRole.ADMIN_RECOVERY),
                offline_columns(ServiceRole.ADMIN_RECOVERY),
            )


def test_a_refused_approval_writes_nothing_under_the_web_login(auth_service, google):
    """A pairing for another address, approved as this one, inserts no row."""
    signed_in()
    _, code = start(auth_service, email="someone@example.org")
    with (
        task_login(ServiceRole.WEB),
        pytest.raises(automation.PairingRefused),
    ):
        approve_now(auth_service, code)
    assert not AutomationSession.objects.exists()


def test_the_worker_never_ends_or_deletes_a_live_admin_session(auth_service, google):
    """Its cleanup grants reach only rows that have already ended."""
    from django.db.models import F

    from parishkit.stewardship.accounts.sessions import database_now

    signed_in()
    live = PortalSession.objects.get()
    with task_login(ServiceRole.WORKER):
        with pytest.raises(DatabaseError), transaction.atomic():
            PortalSession.objects.filter(pk=live.pk).update(
                revoked_at=database_now(), version=F("version") + 1
            )
        with pytest.raises(DatabaseError), transaction.atomic():
            PortalSession.objects.filter(pk=live.pk).delete()
    live.refresh_from_db()
    assert live.revoked_at is None


def test_the_worker_cannot_end_a_live_session_as_lapsed(auth_service, google):
    """role_lost, user_removed and recovery need a session that really lapsed."""
    _, _, row = paired(auth_service)
    with (
        task_login(ServiceRole.WORKER),
        pytest.raises(DatabaseError),
        transaction.atomic(),
    ):
        automation.end_session(row, reason="role_lost")
    row.refresh_from_db()
    assert row.revoked_at is None


def test_only_the_worker_purges_and_only_ended_sessions(auth_service, google):
    """The definer purge never deletes a live session's Django session."""
    from django.contrib.sessions.models import Session

    signed_in()
    live = PortalSession.objects.get()
    with task_login(ServiceRole.WORKER), connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_admin_session_purge_v1(%s::uuid[])", [[live.pk]]
        )
        assert cursor.fetchone() == (0,)
    assert Session.objects.filter(pk=live.session_id).exists()
    with (
        task_login(ServiceRole.WEB),
        pytest.raises(DatabaseError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT stewardship_admin_session_purge_v1(%s::uuid[])", [[live.pk]]
        )

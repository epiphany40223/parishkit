"""Admin automation sessions against PostgreSQL and real Valkey (ADM-11 PR 2).

A browser signed in with the synthetic Google boundary supplies the fresh
Admin session an approval needs; approval runs through the service layer
(the approval page has its own suite), and the command side runs through
``AdminCaller.from_automation`` exactly as the command line does. What is
proven: the pairing round trip and its refusals, the session table's guards,
command sessions and their link rows, endings by every route (owner, another
Administrator, logout, expiry, role loss, removal, recovery, host mismatch,
web misuse) with their notices, audit and incidents, and acknowledgement.
"""

import secrets
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import F
from django.test import Client

from parishkit.stewardship.accounts import automation_sessions as automation
from parishkit.stewardship.accounts.automation_models import (
    AutomationLogin,
    AutomationNotice,
    AutomationNoticeAcknowledgement,
    AutomationSession,
)
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.jobs.operational_models import (
    OperationalIncident,
    OperationalNotice,
)

from .auth_builders import signed_in, stale_sign_in, unguarded
from .automation_builders import (  # noqa: F401
    HOST,
    OTHER_HOST,
    approve_now,
    command,
    paired,
    start,
    two_admins,
)

pytestmark = pytest.mark.django_db(transaction=True)


def test_pairing_round_trip_stores_only_the_digest(auth_service, google):
    """Start, approve, admit a command; the secret is never stored."""
    signed_in()
    secret, code = start(auth_service)
    assert automation.normalized_code(code.lower()[:4] + "-" + code.lower()[4:]) == code
    approve_now(auth_service, code)
    row = AutomationSession.objects.get()
    assert row.secret_digest == automation.secret_digest(secret)
    assert row.host_digest == HOST and row.scope == "full"
    approving = PortalSession.objects.get(revoked_at__isnull=True)
    assert row.approving_session_id == approving.pk
    assert row.authenticated_at == approving.authenticated_at
    assert row.expires_at - database_now() > timedelta(days=29, hours=23)
    # The secret is nowhere in the database.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM stewardship_automation_session "
            "WHERE row_to_json(stewardship_automation_session)::text LIKE %s",
            [f"%{secret}%"],
        )
        assert cursor.fetchone() == (0,)
    assert AuditEvent.objects.filter(
        event_type="automation_session_approved", subject_id=row.pk
    ).exists()
    assert AutomationNotice.objects.get().kind == "approved"
    incident = OperationalIncident.objects.get(kind="automation_approved")
    assert incident.level == "CRITICAL"
    assert OperationalNotice.objects.filter(incident=incident, phase="opened").exists()
    # The approving browser session is unchanged and keeps working.
    assert PortalSession.objects.get(pk=approving.pk).revoked_at is None
    caller = command(auth_service, secret)
    assert caller.channel == "automation" and caller.scope == "full"
    assert "administrator" in caller.principal.roles
    link = AutomationLogin.objects.get()
    assert link.portal_session_id == caller.portal_session.pk
    assert caller.portal_session.authenticated_at == row.authenticated_at
    assert caller.portal_session.expires_at <= database_now() + timedelta(hours=4)
    automation.close_command_session(caller.portal_session)
    assert PortalSession.objects.get(pk=caller.portal_session.pk).revoked_at


def test_approval_refuses_a_pairing_meant_for_another_administrator(two_admins, google):  # noqa: F811
    """The expected address must be the approver's own; nothing is written."""
    signed_in()
    _, code = start(two_admins, email="other@example.org")
    with pytest.raises(automation.PairingRefused):
        approve_now(two_admins, code)
    assert not AutomationSession.objects.exists()


def test_approval_lowers_but_never_raises_scope_or_lifetime(auth_service, google):
    """A read-only request cannot become full; days cannot grow."""
    signed_in()
    _, code = start(auth_service, scope="read-only", days=7)
    with pytest.raises(ValueError):
        approve_now(auth_service, code, scope="full", days=7)
    with pytest.raises(ValueError):
        approve_now(auth_service, code, scope="read_only", days=8)
    assert not AutomationSession.objects.exists()
    approve_now(auth_service, code, scope="read_only", days=3)
    row = AutomationSession.objects.get()
    assert row.scope == "read_only"
    assert row.expires_at - database_now() < timedelta(days=3, minutes=1)


def test_an_approved_code_is_single_use(auth_service, google):
    """The digest is unique: approving the same pairing again is refused."""
    signed_in()
    _, code = start(auth_service)
    approve_now(auth_service, code)
    with pytest.raises(automation.PairingRefused):
        approve_now(auth_service, code)
    assert AutomationSession.objects.count() == 1


def test_the_session_guard_refuses_inserts_that_break_the_pairing_rules(
    auth_service, google
):
    """Old sign-in, long life, a command session as approver, a non-Administrator."""
    _, secret, row = paired(auth_service)
    approving = PortalSession.objects.get(pk=row.approving_session_id)
    caller = command(auth_service, secret)

    def insert(**changes):
        """Insert one session row with these changes to an admissible one."""
        values = {
            "principal_id": row.principal_id,
            "approving_session_id": approving.pk,
            "authenticated_at": approving.authenticated_at,
            "expires_at": database_now() + timedelta(days=1),
            "scope": "full",
            "label": "probe",
            "secret_digest": secrets.token_hex(32),
            "host_digest": HOST,
            **changes,
        }
        with transaction.atomic():
            AutomationSession.objects.create(**values)

    insert()  # the baseline insert is admitted
    for changes in (
        {"expires_at": database_now() + timedelta(days=31)},
        {"approving_session_id": caller.portal_session.pk},
        {"principal_id": uuid4()},
        {"authenticated_at": approving.authenticated_at - timedelta(seconds=1)},
        {"scope": "everything"},
        {"label": "bad\nlabel"},
    ):
        with pytest.raises((DatabaseError, IntegrityError)):
            insert(**changes)
    stale_sign_in()
    with pytest.raises(DatabaseError):
        insert(authenticated_at=approving.authenticated_at - timedelta(minutes=6))
    with pytest.raises(DatabaseError), transaction.atomic():
        AutomationSession.objects.filter(pk=row.pk).delete()


def test_login_rows_need_a_new_command_session_of_the_live_session(
    auth_service, google
):
    """The login guard refuses old, approving and foreign sessions, and edits."""
    _, secret, row = paired(auth_service)
    approving = PortalSession.objects.get(pk=row.approving_session_id)
    with pytest.raises(DatabaseError), transaction.atomic():
        AutomationLogin.objects.create(
            portal_session_id=approving.pk, automation_session=row
        )
    caller = command(auth_service, secret)
    link = AutomationLogin.objects.get()
    with pytest.raises(DatabaseError), transaction.atomic():
        AutomationLogin.objects.filter(pk=link.pk).update(created_at=database_now())
    # An existing (earlier) command session cannot be linked again.
    with pytest.raises((DatabaseError, IntegrityError)), transaction.atomic():
        AutomationLogin.objects.create(
            portal_session_id=caller.portal_session.pk, automation_session=row
        )
    with pytest.raises(DatabaseError), transaction.atomic():
        AutomationLogin.objects.filter(pk=link.pk).delete()


def test_cleanup_removes_ended_command_sessions_then_their_links(auth_service, google):
    """The maintenance pass deletes the ended Admin session, then its link row."""
    _, secret, row = paired(auth_service)
    caller = command(auth_service, secret)
    automation.close_command_session(caller.portal_session)
    totals = automation.maintain()
    assert totals["admin_sessions_removed"] >= 1
    assert totals["command_logins_removed"] == 1
    assert not PortalSession.objects.filter(pk=caller.portal_session.pk).exists()
    assert not AutomationLogin.objects.exists()
    # The automation session itself is kept and still works.
    assert AutomationSession.objects.get(pk=row.pk).revoked_at is None
    automation.close_command_session(command(auth_service, secret).portal_session)


def owner(service, email="admin@example.org"):
    """The current principal of an Administrator, for service calls."""
    from parishkit.stewardship.accounts.policy import current_principal

    return current_principal(service.store, PortalUser.objects.get(email=email).pk)


def test_revocation_by_the_owner_takes_effect_at_the_next_command(auth_service, google):
    """Revoking one's own session records revoked_by_owner."""
    _, secret, row = paired(auth_service)
    assert automation.revoke(row.pk, owner(auth_service), reason="revoked_by_owner")
    row.refresh_from_db()
    assert row.end_reason == "revoked_by_owner" and row.revoked_at
    assert row.actor_id == PortalUser.objects.get(email="admin@example.org").pk
    with pytest.raises(automation.SessionUnusable) as refused:
        command(auth_service, secret)
    assert refused.value.code == "session_ended"
    assert AutomationNotice.objects.filter(kind="ended").exists()


def test_another_administrator_revokes_any_live_session(two_admins, google):  # noqa: F811
    """Any Administrator may revoke any live session (revoked_by_administrator)."""
    _, secret, row = paired(two_admins)
    claims, _ = google
    claims.update(sub="other-subject", email="other@example.org")
    signed_in(Client(enforce_csrf_checks=True))
    other = owner(two_admins, "other@example.org")
    with pytest.raises(LookupError):
        automation.revoke(row.pk, other, reason="revoked_by_owner")
    assert automation.revoke(row.pk, other, reason="revoked_by_administrator")
    row.refresh_from_db()
    assert row.end_reason == "revoked_by_administrator"
    assert row.actor_id == PortalUser.objects.get(email="other@example.org").pk
    with pytest.raises(automation.SessionUnusable):
        command(two_admins, secret)


def test_logout_ends_the_session_for_good(auth_service, google):
    """``logout`` records the Administrator as the actor; the next command fails."""
    _, secret, row = paired(auth_service)
    caller = command(auth_service, secret)
    with transaction.atomic():
        automation.end_session(
            caller.automation_session,
            reason="logout",
            actor_id=caller.principal.identity,
        )
    automation.close_command_session(caller.portal_session)
    row.refresh_from_db()
    assert row.end_reason == "logout"
    with pytest.raises(automation.SessionUnusable):
        command(auth_service, secret)


@pytest.mark.parametrize("change", ["role", "disabled", "recovery"])
def test_losing_administrator_ends_the_session_in_sql_and_python(
    auth_service, google, change
):
    """Liveness drops at once; the next command records the matching reason."""
    _, secret, row = paired(auth_service)
    principal = PortalUser.objects.get(email="admin@example.org")
    before = owner(auth_service)
    with unguarded(), connection.cursor() as cursor:
        if change == "role":
            cursor.execute(
                "UPDATE stewardship_address_rule SET roles='[\"staff\"]'::jsonb "
                "WHERE email='admin@example.org'"
            )
        elif change == "disabled":
            cursor.execute(
                "UPDATE stewardship_portal_user SET disabled=true WHERE id=%s",
                [principal.pk],
            )
        else:
            cursor.execute(
                "INSERT INTO stewardship_admin_revocation "
                "(id, created_at, actor_id, correlation_id, activation_id) "
                "VALUES (%s, statement_timestamp(), NULL, %s, %s)",
                [uuid4(), uuid4(), uuid4()],
            )
    assert not automation.is_live(row.pk)
    # A lapsed session is never mislabelled as revoked by its owner.
    assert not automation.revoke(row.pk, before, reason="revoked_by_owner")
    listed = automation.sessions_of(principal.pk, database_now())
    assert listed[0]["live"] is False
    with pytest.raises(automation.SessionUnusable):
        command(auth_service, secret)
    row.refresh_from_db()
    assert (
        row.end_reason
        == {
            "role": "role_lost",
            "disabled": "user_removed",
            "recovery": "recovery",
        }[change]
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_automation_fresh_v1(%s, %s)",
            [uuid4(), principal.pk],
        )
        assert cursor.fetchone() == (False,)


def test_a_host_mismatch_refuses_and_revokes_with_a_notice(auth_service, google):
    """A session used with a different host digest is ended and alerted."""
    _, secret, row = paired(auth_service)
    with pytest.raises(automation.SessionUnusable) as refused:
        command(auth_service, secret, host=OTHER_HOST)
    assert refused.value.code == "session_ended"
    row.refresh_from_db()
    assert row.end_reason == "host_mismatch"
    assert AutomationNotice.objects.filter(kind="refused", automation_session=row)
    assert AuditEvent.objects.filter(
        event_type="automation_session_refused", subject_id=row.pk
    ).exists()
    assert OperationalIncident.objects.filter(kind="automation_refused").exists()


def test_unknown_secrets_are_refused_at_once_with_one_notice_per_host_hour(
    auth_service, google, caplog
):
    """No rate limit: each unknown secret is refused and logged; one notice."""
    caplog.set_level("WARNING", logger="parishkit.stewardship")
    for _ in range(3):
        with pytest.raises(automation.SessionUnusable) as refused:
            command(auth_service, secrets.token_urlsafe(32))
        assert refused.value.code == "session_missing"
    assert AutomationNotice.objects.filter(kind="refused").count() == 1
    refusals = [
        record
        for record in caplog.records
        if getattr(record, "extra", {}).get("failure_kind")
        is automation.FailureKind.AUTOMATION_REFUSED
    ]
    assert len(refusals) == 3
    with pytest.raises(automation.SessionUnusable):
        command(auth_service, secrets.token_urlsafe(32), host=OTHER_HOST)
    assert AutomationNotice.objects.filter(kind="refused").count() == 2
    # A valid session's command is never held back by them.
    _, secret, _ = paired(auth_service)
    automation.close_command_session(command(auth_service, secret).portal_session)


def test_a_malformed_host_digest_is_logged_and_refused(auth_service, google, caplog):
    """No notice (no host to group by), but the refusal is still logged."""
    caplog.set_level("WARNING", logger="parishkit.stewardship")
    with pytest.raises(automation.SessionUnusable) as refused:
        command(auth_service, secrets.token_urlsafe(32), host="not-a-digest")
    assert refused.value.code == "session_missing"
    assert not AutomationNotice.objects.exists()
    assert [
        record
        for record in caplog.records
        if getattr(record, "extra", {}).get("failure_kind")
        is automation.FailureKind.AUTOMATION_REFUSED
    ]


def test_expiry_ends_the_session(auth_service, google):
    """A session past expires_at is refused without any reason being written."""
    _, secret, row = paired(auth_service)
    with unguarded():
        AutomationSession.objects.filter(pk=row.pk).update(
            expires_at=database_now() - timedelta(seconds=1),
            authenticated_at=F("authenticated_at") - timedelta(days=31),
        )
    with pytest.raises(automation.SessionUnusable) as refused:
        command(auth_service, secret)
    assert refused.value.code == "session_ended"
    row.refresh_from_db()
    assert row.revoked_at is None
    # Revoking it now changes nothing: no revoked stamp, no notice.
    notices = AutomationNotice.objects.count()
    assert not automation.revoke(row.pk, owner(auth_service), reason="revoked_by_owner")
    with transaction.atomic():
        assert not automation.end_session(row, reason="role_lost")
    row.refresh_from_db()
    assert (row.revoked_at, row.end_reason) == (None, None)
    assert AutomationNotice.objects.count() == notices


def test_the_web_refuses_a_command_sessions_key_and_ends_its_session(
    auth_service, google
):
    """A command session's key in a browser cookie is tampering: refused, ended.

    The passive status check is a read-only admission; it alone ends the
    automation session, with no later page request.
    """
    _, secret, row = paired(auth_service)
    caller = command(auth_service, secret)
    intruder = Client(enforce_csrf_checks=True)
    intruder.cookies["pk_admin"] = caller.session.session_key
    assert intruder.get("/admin/session/status").status_code == 401
    row.refresh_from_db()
    assert row.end_reason == "misused"
    assert PortalSession.objects.get(pk=caller.portal_session.pk).revoked_at
    assert AuditEvent.objects.filter(
        event_type="automation_session_refused", subject_id=row.pk
    ).exists()


def test_logout_with_a_command_sessions_key_is_refused_as_misuse(auth_service, google):
    """/admin/logout checks the marker before ending the session."""
    _, secret, row = paired(auth_service)
    caller = command(auth_service, secret)
    intruder = Client()
    intruder.cookies["pk_admin"] = caller.session.session_key
    response = intruder.post("/admin/logout")
    assert response.status_code == 302
    row.refresh_from_db()
    assert row.end_reason == "misused"


def test_a_role_change_that_keeps_administrator_ends_only_the_command_session(
    auth_service, google, monkeypatch
):
    """A changed authority refuses the running command; the session lives on."""
    from parishkit.stewardship.accounts import sessions
    from parishkit.stewardship.accounts.policy import Principal

    _, secret, row = paired(auth_service)
    caller = command(auth_service, secret)
    changed = Principal(row.principal_id, frozenset({"administrator", "staff"}))
    monkeypatch.setattr(sessions, "current_principal", lambda *args: changed)
    assert sessions.authenticated_admin(caller, store=auth_service.store) is None
    monkeypatch.undo()
    row.refresh_from_db()
    assert row.revoked_at is None
    automation.close_command_session(command(auth_service, secret).portal_session)


def test_read_only_sessions_are_refused_activity_and_fresh_gates(auth_service, google):
    """A read-only command can read, but never record activity or pass require_fresh."""
    from parishkit.stewardship.accounts import sessions

    _, secret, _ = paired(auth_service, scope="read-only")
    caller = command(auth_service, secret)
    assert caller.read_only
    with pytest.raises(PermissionError):
        sessions.authenticated_admin(caller, store=auth_service.store, activity=True)
    with pytest.raises(sessions.FreshAuthenticationRequired):
        sessions.require_fresh(caller)


def test_require_fresh_refuses_a_full_session_until_pr_5(auth_service, google):
    """No automation caller passes a fresh-gate before its guard migration."""
    from parishkit.stewardship.accounts import sessions

    _, secret, _ = paired(auth_service)
    caller = command(auth_service, secret)
    with pytest.raises(sessions.FreshAuthenticationRequired):
        sessions.require_fresh(caller)


def test_fresh_v1_names_only_a_live_full_command_session(auth_service, google):
    """The function PR 5's guards call: true for this command session only."""
    _, secret, row = paired(auth_service)
    caller = command(auth_service, secret)
    approving = row.approving_session_id

    def fresh(login):
        """What the PR 5 guards will ask about this Admin session."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT stewardship_automation_fresh_v1(%s, %s)",
                [login, row.principal_id],
            )
            return cursor.fetchone()[0]

    assert fresh(caller.portal_session.pk) is True
    assert fresh(approving) is False
    automation.revoke(row.pk, owner(auth_service), reason="revoked_by_owner")
    assert fresh(caller.portal_session.pk) is False


def test_notices_are_acknowledged_per_administrator(two_admins, google):  # noqa: F811
    """Each Administrator clears only their own view; others still see it."""
    paired(two_admins)
    me = owner(two_admins)
    assert automation.open_notices(me)["total"] == 1
    notice = AutomationNotice.objects.get()
    with transaction.atomic():
        assert automation.acknowledge_notices(me, [notice.pk]) == 1
    assert automation.open_notices(me)["total"] == 0
    claims, _ = google
    claims.update(sub="other-subject", email="other@example.org")
    signed_in(Client(enforce_csrf_checks=True))
    other = owner(two_admins, "other@example.org")
    assert automation.open_notices(other)["rows"][0]["label"] == "launch <assistant>"
    with transaction.atomic():
        assert automation.acknowledge_notices(other) == 1
    assert AutomationNoticeAcknowledgement.objects.count() == 2
    with pytest.raises(DatabaseError), transaction.atomic():
        AutomationNoticeAcknowledgement.objects.create(
            notice=notice,
            administrator_id=uuid4(),
            actor_id=PortalUser.objects.get(email="admin@example.org").pk,
        )


def test_quiet_automation_incidents_resolve_after_an_hour(auth_service, google):
    """The maintenance pass resolves an episode with no event in the last hour."""
    paired(auth_service)
    assert automation.resolve_quiet_incidents() == 0
    with unguarded():
        OperationalIncident.objects.filter(kind="automation_approved").update(
            last_seen=F("last_seen") - timedelta(hours=2),
            first_seen=F("first_seen") - timedelta(hours=2),
            last_notice_at=F("last_notice_at") - timedelta(hours=2),
        )
    assert automation.resolve_quiet_incidents() == 1
    incident = OperationalIncident.objects.get(kind="automation_approved")
    assert incident.resolved_at is not None
    assert OperationalNotice.objects.filter(incident=incident, phase="resolved")


def test_the_maintenance_pass_records_lapsed_endings(auth_service, google):
    """A session whose Administrator lost the role is ended as role_lost."""
    _, _, row = paired(auth_service)
    with unguarded(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE stewardship_address_rule SET roles='[\"staff\"]'::jsonb "
            "WHERE email='admin@example.org'"
        )
    assert automation.maintain()["sessions_ended"] == 1
    row.refresh_from_db()
    assert row.end_reason == "role_lost"
    assert AutomationNotice.objects.filter(kind="ended", automation_session=row)


def test_offline_revocation_ends_every_live_session_and_nothing_else(
    auth_service, google
):
    """``revoke-automation-sessions`` ends live sessions only, with notices."""
    _, _, first = paired(auth_service)
    _, second_secret, second = paired(auth_service)
    automation.revoke(first.pk, owner(auth_service), reason="revoked_by_owner")
    count = automation.revoke_all_offline("restore", correlation_id=uuid4())
    assert count == 1
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.end_reason == "revoked_by_owner"
    assert second.end_reason == "restore"
    assert AuditEvent.objects.filter(
        event_type="automation_session_ended", subject_id=second.pk
    ).exists()
    assert automation.revoke_all_offline("restore", correlation_id=uuid4()) == 0
    with pytest.raises(automation.SessionUnusable):
        command(auth_service, second_secret)


def test_an_acknowledgement_needs_a_live_admin_session(auth_service, google):
    """Without a live Admin session of that Administrator, the guard refuses."""
    from parishkit.stewardship.accounts.models import PortalSession as Session_

    paired(auth_service)
    notice = AutomationNotice.objects.get()
    administrator = PortalUser.objects.get(email="admin@example.org").pk
    with unguarded():
        Session_.objects.update(revoked_at=database_now(), version=F("version") + 1)
    with pytest.raises(DatabaseError), transaction.atomic():
        AutomationNoticeAcknowledgement.objects.create(
            notice=notice, administrator_id=administrator, actor_id=administrator
        )

"""Guards that 0013 opened to automation admit any live session (#1547).

Frozen file 0013 let a live full-scope automation session stand in for a
sign-in under five minutes old. 0113 removed the sign-in age from these
guards altogether, so a session is admitted at any sign-in age: the
actor's live session (unrevoked, unexpired, active in the last 60 minutes),
bound to the row's recorded sign-in instant where it was, and, for an
automation command session, its live full-scope automation session
(``stewardship_automation_fresh_v1``, unchanged since 0013). The
session-bound definer guards are exercised by an insert whose session check
comes first, so the error says which clause refused; the
Family test guard through the real chosen-Family test form; the secret
request guards through the real sealed intake and the installer's
staged-to-testing step, on disposable roles. 0013's own DO block is run
against 0013's bodies, put back in a rolled-back transaction, and refuses
each pre-0013 guard. ``sign_in_instant`` and the still-installed, now unused
``stewardship_automation_fresh_principal_v1`` are covered directly.
"""

import re
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from parishkit.stewardship.accounts import automation_sessions as automation
from parishkit.stewardship.accounts import campaign_family_test as intake
from parishkit.stewardship.accounts import sessions
from parishkit.stewardship.accounts.automation_models import AutomationSession
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.jobs.family_mail_models import FamilyMailTest
from parishkit.stewardship.storage import StorageInvariantError

from .auth_builders import unguarded
from .automation_builders import command, paired
from .test_automation_sessions_postgresql import owner
from .test_credential_isolation_postgresql import (  # noqa: F401
    advance,
    identity,
    isolated_roles,
)
from .test_family_mail_test_postgresql import (  # noqa: F401
    family_test,
    review,
)
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_views_postgresql import post

pytestmark = pytest.mark.django_db(transaction=True)

FROZEN = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0013_automation_fresh_guards.sql"
).read_text(encoding="utf-8")
# Far past 0013's five minutes and 0104's hour: no sign-in age is checked.
STALE = timedelta(hours=3)
FIVE = (
    "              AND login.authenticated_at BETWEEN clock_timestamp()"
    "-interval '5 minutes' AND clock_timestamp()"
)
# Each guard's automation clause, and the text it replaced: undoing these
# gives the definition installed before 0013.
CLAUSES = {
    "stewardship_delivery_control_guard_v1": [
        (
            FIVE.replace("AND login.", "AND (login.")
            + "\n                   OR public.stewardship_automation_fresh_v1"
            "(login.id,NEW.actor_id))",
            FIVE,
        )
    ],
    "stewardship_production_withdrawal_guard_v1": [
        (
            FIVE.replace("AND login.", "AND (login.")
            + "\n                   OR public.stewardship_automation_fresh_v1"
            "(login.id,NEW.actor_id))",
            FIVE,
        )
    ],
    "stewardship_production_confirmation_guard_v1": [
        (
            FIVE.replace("AND login.", "AND (login.")
            + "\n                   OR public.stewardship_automation_fresh_v1"
            "(login.id,NEW.actor_id))",
            FIVE,
        ),
        (
            "AND (event.created_at<=NEW.authenticated_at\n"
            "                   OR public.stewardship_automation_fresh_v1"
            "(NEW.session_id,NEW.actor_id)))",
            "AND event.created_at<=NEW.authenticated_at)",
        ),
    ],
    "stewardship_family_mail_test_guard_v1": [
        (
            "AND (authenticated_at BETWEEN stamp-interval '5 minutes' AND stamp\n"
            "                         OR public.stewardship_automation_fresh_v1"
            "(id,NEW.requested_by_id))",
            "AND authenticated_at BETWEEN stamp-interval '5 minutes' AND stamp",
        )
    ],
    "stewardship_sealed_intake_admission_v1": [
        (
            "coalesce(public.stewardship_automation_fresh_principal_v1(\n"
            "                    NEW.requested_by_id,NEW.reauthenticated_at),false)",
            "false",
        )
    ],
    "stewardship_secret_state_v2": [
        (
            "coalesce(public.stewardship_automation_fresh_principal_v1(\n"
            "                    NEW.requested_by_id,NEW.reauthenticated_at),false)",
            "false",
        )
    ],
}


def definition(name):
    """The frozen file's ``CREATE OR REPLACE`` statement for ``name``."""
    match = re.search(
        rf"^CREATE OR REPLACE FUNCTION public\.{name}\(", FROZEN, re.MULTILINE
    )
    quote = re.compile(r"\bAS\s+(\$\w*\$)").search(FROZEN, match.end())
    end = FROZEN.index(quote[1] + ";", quote.end()) + len(quote[1]) + 1
    return FROZEN[match.start() : end]


def old_definition(name):
    """The guard as it was before 0013: each automation clause undone."""
    text = definition(name)
    for new, old in CLAUSES[name]:
        assert text.count(new) == 1, name
        text = text.replace(new, old)
    assert "stewardship_automation_fresh" not in text, name
    return text


def age(delta):
    """Move every Admin and automation sign-in instant back by ``delta``.

    Both columns are immutable to every runtime login; this is a
    schema-owner fixture edit with the rows' guards suspended for these
    statements only, keeping each command session equal to its automation
    session.
    """
    with transaction.atomic(), connection.cursor() as cursor:
        for table in ("stewardship_portal_session", "stewardship_automation_session"):
            cursor.execute(f"ALTER TABLE {table} DISABLE TRIGGER USER")
            cursor.execute(
                f"UPDATE {table} SET authenticated_at=authenticated_at-%s", [delta]
            )
            cursor.execute(f"ALTER TABLE {table} ENABLE TRIGGER USER")


def shift(row, delta):
    """Give one automation session (and its command sessions) its own instant.

    Sessions approved from one browser share its sign-in instant; a read-only
    session moved apart cannot borrow a full session's command session.
    """
    from parishkit.stewardship.accounts.automation_models import AutomationLogin

    logins = list(
        AutomationLogin.objects.filter(automation_session_id=row.pk).values_list(
            "portal_session_id", flat=True
        )
    )
    with transaction.atomic(), connection.cursor() as cursor:
        for table, column, keys in (
            ("stewardship_automation_session", "id", [row.pk]),
            ("stewardship_portal_session", "id", logins),
        ):
            cursor.execute(f"ALTER TABLE {table} DISABLE TRIGGER USER")
            cursor.execute(
                f"UPDATE {table} SET authenticated_at=authenticated_at+%s "
                f"WHERE {column}=ANY(%s)",
                [delta, keys],
            )
            cursor.execute(f"ALTER TABLE {table} ENABLE TRIGGER USER")


def _set_expiry(row, value_sql):
    """Set one automation session's deadline with its guards suspended."""
    table = "stewardship_automation_session"
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(f"ALTER TABLE {table} DISABLE TRIGGER USER")
        cursor.execute(
            f"UPDATE {table} SET expires_at={value_sql} WHERE id=%s", [row.pk]
        )
        cursor.execute(f"ALTER TABLE {table} ENABLE TRIGGER USER")


def expire(row):
    """Make the automation session expired (its deadline now)."""
    _set_expiry(row, "now()")


def unexpire(row):
    """Give the automation session a day of life again."""
    _set_expiry(row, "now()+interval '1 day'")


def guard_error(table, actor, session, instant):
    """Insert a bare row as the ordered owner; returns the guard's message.

    The fresh-sign-in check is the first input check of these guards, so a
    later message means it passed.
    """
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(736212,1)")
            cursor.execute("SELECT pg_advisory_xact_lock(736220,1)")
            cursor.execute(
                f"INSERT INTO {table} (id,actor_id,session_id,authenticated_at) "
                "VALUES (%s,%s,%s,%s)",
                [uuid4(), actor, session, instant],
            )
    except DatabaseError as error:
        return str(error).splitlines()[0]
    return None


@pytest.mark.parametrize(
    "table,fresh",
    [
        (
            "stewardship_delivery_control",
            "Delivery control requires fresh Admin authentication",
        ),
        (
            "stewardship_production_withdrawal",
            "Withdrawal requires fresh Admin authentication",
        ),
    ],
)
def test_session_bound_guards_admit_any_live_bound_session(
    auth_service, google, table, fresh
):
    """Hours-old sign-ins on live sessions pass; other sessions are refused.

    The full-scope automation command session and its approving browser
    session pass the session check at any sign-in age. A read-only command
    session, and a command session whose automation session has ended, are
    refused, as 0013 required (ADM-11). Another Administrator, another
    instant, a revoked session and an idle one are refused too.
    """
    _, secret, row = paired(auth_service)
    caller = command(auth_service, secret)
    _, reader, _ = paired(auth_service, scope="read-only")
    read_only = command(auth_service, reader)
    age(STALE)
    caller.portal_session.refresh_from_db()
    read_only.portal_session.refresh_from_db()
    actor = row.principal_id
    browser = PortalSession.objects.get(pk=row.approving_session_id)

    def check(login, instant=None):
        """The guard's answer for this Admin session and its sign-in."""
        return guard_error(table, actor, login.pk, instant or login.authenticated_at)

    for login in (caller.portal_session, browser):
        assert check(login) not in {None, fresh}
    assert check(read_only.portal_session) == fresh
    assert check(browser, browser.authenticated_at - timedelta(seconds=1)) == fresh
    other = guard_error(
        table, uuid4(), caller.portal_session.pk, caller.portal_session.authenticated_at
    )
    assert other == fresh
    # Idle for over an hour, or revoked: refused at any sign-in age.
    with unguarded():
        PortalSession.objects.filter(pk=browser.pk).update(
            last_activity_at=timezone.now() - timedelta(minutes=61)
        )
        PortalSession.objects.filter(pk=caller.portal_session.pk).update(
            revoked_at=timezone.now()
        )
    assert check(browser) == fresh
    assert check(caller.portal_session) == fresh
    # A live command session of an ended automation session is refused.
    _, other_secret, other_row = paired(auth_service)
    other_caller = command(auth_service, other_secret)
    assert check(other_caller.portal_session) not in {None, fresh}
    with unguarded():
        AutomationSession.objects.filter(pk=other_row.pk).update(
            revoked_at=timezone.now(), end_reason="revoked_by_owner"
        )
    assert check(other_caller.portal_session) == fresh


def test_the_family_test_guard_admits_a_command_session_of_any_age(
    family_test,  # noqa: F811
    monkeypatch,
):
    """A chosen-Family test confirmed with an hours-old automation instant.

    An instant that no live session of the Administrator has is refused.
    """
    harness, browser, path, _ = family_test
    _, secret, row = paired(harness.service)
    caller = command(harness.service, secret)
    page = review(browser, path, [1])
    token = page.context["confirm"]["preview"].value()
    age(STALE)
    caller.portal_session.refresh_from_db()
    instant = caller.portal_session.authenticated_at

    def confirm(signed_in):
        """The page's confirmation, recording ``signed_in`` as the sign-in."""
        with monkeypatch.context() as patch:
            patch.setattr(intake, "sign_in_instant", lambda request: signed_in)
            with web_login():
                return post(
                    browser,
                    path,
                    {"action": "confirm", "preview": token, "acknowledge": "on"},
                ).status_code

    # Refused (503: the guard) for an instant no live session holds.
    assert confirm(instant - timedelta(seconds=1)) == 503
    assert not FamilyMailTest.objects.exists()
    assert confirm(instant) == 302
    [ticket] = FamilyMailTest.objects.all()
    assert ticket.requested_by_id == row.principal_id
    assert ticket.reauthenticated_at == instant


def stage_as(principal, instant, target="slack"):
    """The web's sealed intake for ``principal`` with sign-in ``instant``."""
    from parishkit.stewardship.accounts.credential_handoff import PrivateHandoff
    from parishkit.stewardship.accounts.cryptography import Key
    from parishkit.stewardship.accounts.key_files import file_fingerprint
    from parishkit.stewardship.accounts.secret_requests import stage_secret_request

    key = PrivateHandoff(target, Key("handoff", "active", b"h" * 32))
    identifier = uuid4()
    value = b"synthetic-candidate"
    with identity("pk_stewardship_web"):
        stage_secret_request(
            request_id=identifier,
            target=target,
            staging_reference=uuid4(),
            actor_id=principal,
            reauthenticated_at=instant,
            expires_at=timezone.now() + timedelta(minutes=5),
            expected_fingerprint="a" * 64,
            correlation_id=uuid4(),
            required_consumers=("worker",),
            sealed_candidate=key.public().seal(identifier, value),
            candidate_fingerprint=file_fingerprint(value),
        )
    return identifier


def test_secret_request_guards_accept_a_sign_in_of_any_age(
    isolated_roles,  # noqa: F811
    auth_service,
    google,
):
    """Intake and staged-to-testing take an hours-old recorded instant.

    0113 left the request's sign-in instant unchecked; the web records its
    live session's instant.
    """
    _, _, row = paired(auth_service)
    age(STALE)
    instant = AutomationSession.objects.get(pk=row.pk).authenticated_at
    assert timezone.now() - instant > STALE
    staged = stage_as(row.principal_id, instant)
    with identity("pk_stewardship_credential_slack"):
        assert advance(staged, "testing") == 1


def fresh_principal(principal, instant):
    """``stewardship_automation_fresh_principal_v1``'s answer."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT public.stewardship_automation_fresh_principal_v1(%s,%s)",
            [principal, instant],
        )
        return cursor.fetchone()[0]


def test_fresh_principal_is_true_only_for_a_live_full_session(auth_service, google):
    """Never NULL; false for a read-only, revoked or expired session."""
    _, _, row = paired(auth_service)
    _, _, reader = paired(auth_service, scope="read-only")
    assert fresh_principal(row.principal_id, row.authenticated_at) is True
    assert fresh_principal(reader.principal_id, reader.authenticated_at) is (
        reader.authenticated_at == row.authenticated_at
    )
    assert fresh_principal(uuid4(), row.authenticated_at) is False
    assert fresh_principal(row.principal_id, None) is False
    assert (
        fresh_principal(row.principal_id, row.authenticated_at + timedelta(seconds=1))
        is False
    )
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_automation_session DISABLE TRIGGER USER"
        )
        cursor.execute(
            "UPDATE stewardship_automation_session SET expires_at=now() WHERE id=%s",
            [row.pk],
        )
        cursor.execute("ALTER TABLE stewardship_automation_session ENABLE TRIGGER USER")
    assert fresh_principal(row.principal_id, row.authenticated_at) is False


def test_sign_in_instant_admits_a_live_full_session_only(auth_service, google):
    """The automation branch: the session's sign-in instant, until it ends."""
    _, secret, row = paired(auth_service)
    caller = command(auth_service, secret)
    age(STALE)
    caller.portal_session.refresh_from_db()
    with transaction.atomic():
        assert (
            sessions.sign_in_instant(caller) == caller.portal_session.authenticated_at
        )
    # Outside the action's transaction the lock would prove nothing.
    with pytest.raises(StorageInvariantError):
        sessions.sign_in_instant(caller)
    _, reader, _ = paired(auth_service, scope="read-only")
    with pytest.raises(PermissionError), transaction.atomic():
        sessions.sign_in_instant(command(auth_service, reader))
    # Another Administrator's principal never borrows this session.
    admitted = caller.principal
    caller.principal = Principal(uuid4(), admitted.roles)
    with pytest.raises(PermissionError), transaction.atomic():
        sessions.sign_in_instant(caller)
    caller.principal = admitted
    expire(row)
    with pytest.raises(PermissionError), transaction.atomic():
        sessions.sign_in_instant(caller)
    unexpire(row)
    with transaction.atomic():
        assert (
            sessions.sign_in_instant(caller) == caller.portal_session.authenticated_at
        )
    automation.revoke(row.pk, owner(auth_service), reason="revoked_by_owner")
    with pytest.raises(PermissionError), transaction.atomic():
        sessions.sign_in_instant(caller)


def test_the_frozen_files_check_refuses_an_unchanged_guard(auth_service):
    """The DO block passes on 0013's bodies, raises on each pre-0013 one.

    0113 (#1547) replaced every body 0013 installs, so each rolled-back
    transaction first puts 0013's own bodies back.
    """
    check = FROZEN[FROZEN.index("DO $check$") :]

    def install(cursor):
        """0013's own definitions, inside the caller's transaction."""
        for name in CLAUSES:
            cursor.execute(definition(name))

    with transaction.atomic(), connection.cursor() as cursor:
        install(cursor)
        cursor.execute(check)
        transaction.set_rollback(True)
    for name in CLAUSES:
        with (
            pytest.raises(DatabaseError, match="was not replaced|lacks its"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            install(cursor)
            cursor.execute(old_definition(name))
            cursor.execute(check)


def test_automation_is_refused_where_no_workflow_admits_it(auth_service, google):
    """Destructive confirmations and the setup wizard's credentials stay web-only."""
    from parishkit.stewardship.accounts.privileged_actions import admit_admin_action
    from parishkit.stewardship.accounts.setup_credentials import stage_credential
    from parishkit.stewardship.audit.schemas import Action

    _, secret, row = paired(auth_service)
    caller = command(auth_service, secret)
    with pytest.raises(PermissionError), transaction.atomic():
        admit_admin_action(
            caller, actor_id=row.principal_id, action=Action.DESTRUCTIVE_CONFIRMATION
        )
    with pytest.raises(PermissionError):
        stage_credential(
            caller,
            auth_service,
            uuid4(),
            target="slack",
            candidate="value",
            expected_version=1,
        )

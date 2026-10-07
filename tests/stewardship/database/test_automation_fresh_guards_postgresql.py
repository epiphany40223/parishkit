"""Fresh-gated guards accept a live full-scope automation session (ADM-11 PR 5).

Each guard the frozen file 0013 changes is run against its new definition
(installed) and its old one (the same text without the automation clause,
installed for one transaction only): the old refuses an automation command
session whose sign-in is older than five minutes, the new admits it, and the
new still refuses a read-only, revoked or stale browser session. The
definer guards are exercised by an insert whose fresh-sign-in check comes
first, so the error says which clause refused; the Family test guard through
the real chosen-Family test form; the secret request guards through the real
sealed intake and the installer's staged-to-testing step, on disposable
roles. ``require_fresh`` and ``stewardship_automation_fresh_principal_v1``
are covered directly, and the frozen file's DO block is shown to refuse an
unchanged guard.
"""

import re
from contextlib import contextmanager
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
STALE = timedelta(minutes=10)
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


@contextmanager
def old_guard(name):
    """Install the pre-0013 guard for the block, then restore the new one."""
    with connection.cursor() as cursor:
        cursor.execute(old_definition(name))
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute(definition(name))


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
    "table,guard,fresh",
    [
        (
            "stewardship_delivery_control",
            "stewardship_delivery_control_guard_v1",
            "Delivery control requires fresh Admin authentication",
        ),
        (
            "stewardship_production_withdrawal",
            "stewardship_production_withdrawal_guard_v1",
            "Withdrawal requires fresh Admin authentication",
        ),
    ],
)
def test_session_bound_guards_admit_only_a_live_full_command_session(
    auth_service, google, table, guard, fresh
):
    """Old guard refuses the session; new admits it and refuses every other."""
    _, secret, row = paired(auth_service)
    caller = command(auth_service, secret)
    _, reader, _ = paired(auth_service, scope="read-only")
    read_only = command(auth_service, reader)
    age(STALE)
    caller.portal_session.refresh_from_db()
    read_only.portal_session.refresh_from_db()
    actor = row.principal_id
    browser = PortalSession.objects.get(pk=row.approving_session_id)

    def check(login):
        """The guard's answer for this Admin session and its sign-in."""
        return guard_error(table, actor, login.pk, login.authenticated_at)

    assert check(caller.portal_session) not in {None, fresh}
    # A stale browser session and a read-only automation session are refused.
    assert check(browser) == fresh
    assert check(read_only.portal_session) == fresh
    # Another Administrator cannot name this command session.
    other = guard_error(
        table, uuid4(), caller.portal_session.pk, caller.portal_session.authenticated_at
    )
    assert other == fresh
    # An expired automation session no longer stands in.
    expire(row)
    assert check(caller.portal_session) == fresh
    unexpire(row)
    with old_guard(guard):
        assert check(caller.portal_session) == fresh
    automation.revoke(row.pk, owner(auth_service), reason="revoked_by_owner")
    assert check(caller.portal_session) == fresh


def test_the_family_test_guard_admits_a_full_command_session(
    family_test,  # noqa: F811
    monkeypatch,
):
    """A chosen-Family test confirmed with the automation sign-in instant."""
    harness, browser, path, _ = family_test
    _, secret, row = paired(harness.service)
    caller = command(harness.service, secret)
    _, reader, reader_row = paired(harness.service, scope="read-only")
    read_only = command(harness.service, reader)
    page = review(browser, path, [1])
    token = page.context["confirm"]["preview"].value()
    age(STALE)
    shift(reader_row, timedelta(seconds=1))

    def confirm(login):
        """The page's confirmation, recording ``login``'s sign-in instant."""
        login.refresh_from_db()
        with monkeypatch.context() as patch:
            patch.setattr(
                intake, "require_fresh", lambda request: login.authenticated_at
            )
            with web_login():
                return post(
                    browser,
                    path,
                    {"action": "confirm", "preview": token, "acknowledge": "on"},
                ).status_code

    # Refused (503: the guard) for read-only, the stale browser session (its
    # own instant, moved apart: this guard binds the principal and instant,
    # not a session row), and under the old guard.
    assert confirm(read_only.portal_session) == 503
    browser_session = PortalSession.objects.get(pk=row.approving_session_id)
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("ALTER TABLE stewardship_portal_session DISABLE TRIGGER USER")
        cursor.execute(
            "UPDATE stewardship_portal_session "
            "SET authenticated_at=authenticated_at-interval '2 seconds' WHERE id=%s",
            [browser_session.pk],
        )
        cursor.execute("ALTER TABLE stewardship_portal_session ENABLE TRIGGER USER")
    assert confirm(browser_session) == 503
    with old_guard("stewardship_family_mail_test_guard_v1"):
        assert confirm(caller.portal_session) == 503
    assert not FamilyMailTest.objects.exists()
    assert confirm(caller.portal_session) == 302
    [ticket] = FamilyMailTest.objects.all()
    assert ticket.requested_by_id == row.principal_id
    assert ticket.reauthenticated_at == caller.portal_session.authenticated_at


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


def test_secret_request_guards_accept_a_live_full_session(
    isolated_roles,  # noqa: F811
    auth_service,
    google,
):
    """Intake and staged-to-testing admit the automation instant, until it ends."""
    _, secret, row = paired(auth_service)
    _, reader, reader_row = paired(auth_service, scope="read-only")
    age(STALE)
    shift(reader_row, timedelta(seconds=1))
    instant = AutomationSession.objects.get(pk=row.pk).authenticated_at
    principal = row.principal_id
    # The browser's stale instant, or a read-only session's, is refused.
    browser = PortalSession.objects.get(pk=row.approving_session_id)
    for refused in (
        browser.authenticated_at - timedelta(seconds=1),
        AutomationSession.objects.get(pk=reader_row.pk).authenticated_at,
    ):
        with pytest.raises(DatabaseError, match="fresh authentication"):
            stage_as(principal, refused)
    with (
        old_guard("stewardship_sealed_intake_admission_v1"),
        pytest.raises(DatabaseError, match="fresh authentication"),
    ):
        stage_as(principal, instant)
    staged = stage_as(principal, instant)
    # One pending request per target: the second is for another installer.
    second = stage_as(principal, instant, "parishsoft")
    with (
        old_guard("stewardship_secret_state_v2"),
        identity("pk_stewardship_credential_slack"),
        pytest.raises(DatabaseError, match="live sealed request"),
    ):
        advance(staged, "testing")
    with identity("pk_stewardship_credential_slack"):
        assert advance(staged, "testing") == 1
    # Revoked before the installer's test: the step is refused.
    automation.revoke(row.pk, owner(auth_service), reason="revoked_by_owner")
    with (
        identity("pk_stewardship_credential_parishsoft"),
        pytest.raises(DatabaseError, match="live sealed request"),
    ):
        advance(second, "testing")


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


def test_require_fresh_admits_a_live_full_session_only(auth_service, google):
    """The automation branch: the session's sign-in instant, until it ends."""
    _, secret, row = paired(auth_service)
    caller = command(auth_service, secret)
    age(STALE)
    caller.portal_session.refresh_from_db()
    with transaction.atomic():
        assert sessions.require_fresh(caller) == caller.portal_session.authenticated_at
    # Outside the action's transaction the lock would prove nothing.
    with pytest.raises(StorageInvariantError):
        sessions.require_fresh(caller)
    _, reader, _ = paired(auth_service, scope="read-only")
    with pytest.raises(sessions.FreshAuthenticationRequired), transaction.atomic():
        sessions.require_fresh(command(auth_service, reader))
    # Another Administrator's principal never borrows this session.
    admitted = caller.principal
    caller.principal = Principal(uuid4(), admitted.roles)
    with pytest.raises(sessions.FreshAuthenticationRequired), transaction.atomic():
        sessions.require_fresh(caller)
    caller.principal = admitted
    expire(row)
    with pytest.raises(sessions.FreshAuthenticationRequired), transaction.atomic():
        sessions.require_fresh(caller)
    unexpire(row)
    with transaction.atomic():
        assert sessions.require_fresh(caller) == caller.portal_session.authenticated_at
    automation.revoke(row.pk, owner(auth_service), reason="revoked_by_owner")
    with pytest.raises(sessions.FreshAuthenticationRequired), transaction.atomic():
        sessions.require_fresh(caller)


def test_the_frozen_files_check_refuses_an_unchanged_guard(auth_service):
    """The DO block raises while any guard lacks its automation clause."""
    check = FROZEN[FROZEN.index("DO $check$") :]
    with connection.cursor() as cursor:
        cursor.execute(check)
    for name in CLAUSES:
        with (
            pytest.raises(DatabaseError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
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


def test_the_post_cleanup_check_accepts_only_an_automation_caller(
    auth_service, google, monkeypatch
):
    """An instant before cleanup finished passes only through automation."""
    from types import SimpleNamespace

    from parishkit.stewardship.accounts import confirmation_views

    _, secret, row = paired(auth_service)
    caller = command(auth_service, secret)
    completed = timezone.now()
    events = SimpleNamespace(
        filter=lambda **kwargs: SimpleNamespace(
            latest=lambda field: SimpleNamespace(created_at=completed)
        )
    )
    monkeypatch.setattr(
        confirmation_views.ProductionTransitionEvent, "objects", events, raising=False
    )
    before = completed - timedelta(minutes=1)
    monkeypatch.setattr(confirmation_views, "require_fresh", lambda value: before)
    assert confirmation_views._fresh_after_cleanup(caller, uuid4()) is True
    browser = SimpleNamespace(portal_session=None)
    assert confirmation_views._fresh_after_cleanup(browser, uuid4()) is False

"""Verified-address matches and their guards (#2343, frozen migration 0200).

A Google account whose sign-in address is no user's may act as the one user
whose address Google verified for it. Nothing in the portal makes a match
yet (the sign-in steps come in a later change), so these tests write the
rows as the web login would and prove what SQL itself decides: who may
record, confirm again or end a match, when an address is in use, what the
portal user guards allow, the address_matched security event and its two
content compilers, and that the migration's DO block refuses the earlier
definitions.
"""

import json
import re
from contextlib import contextmanager
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import F

from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.policy_models import (
    AddressMatch,
    PolicySecurityEvent,
    PortalUser,
)
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.domain import SystemMode
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.security_content import render_security_alert
from parishkit.stewardship.jobs.security_routing import event_alert

from ..policy_factory import address
from .auth_builders import unguarded
from .campaign_builders import change
from .role_grants import seed_user
from .test_background_grants_postgresql import task_login
from .test_operational_routing_postgresql import routing as routing_fixture
from .test_portal_identity_guards_postgresql import forge
from .test_security_fanout_postgresql import consume, schedule

routing = routing_fixture
pytestmark = pytest.mark.django_db(transaction=True)

SCHEMA = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
FROZEN = (SCHEMA / "migrations/0200_address_match.sql").read_text(encoding="utf-8")
# The file's closing self-check, run on its own against chosen states.
CHECK = FROZEN[FROZEN.index("DO $check$") :]
# The account that signs in with an address that is no user's.
PERSONAL = "person@personal.example"
# A user of the routing fixture's root configuration who is no Administrator,
# so no first-Administrator address: the one these tests match.
STAFF = "staff@example.org"
# The routing fixture's Ministry leader, matched where a Staff user acts too.
LEADER = "minister@example.org"


def web():
    """Statements under the real restricted web login, its session login too."""
    return task_login(ServiceRole.WEB, exact=True, reconnect=True)


def record_id(store, email):
    """The active configuration's record ID of one address."""
    return next(
        record["id"]
        for record in store.active().document()["sections"]["login_rules"]
        if record["values"].get("email") == email
    )


def account(email=PERSONAL):
    """A Google account that signed in with ``email``, recorded as the owner."""
    return seed_user(google_subject=str(uuid4()), email=email)


def verified(portal):
    """Stamp a fresh verification on ``portal``, as a sign-in step does."""
    PortalUser.objects.filter(pk=portal.pk).update(
        verified_at=database_now(), version=F("version") + 1
    )


def new_match(store, portal, email=STAFF, **values):
    """Insert one match for ``portal`` in the caller's transaction."""
    now = database_now()
    if "user_record_id" not in values:
        values["user_record_id"] = record_id(store, email)
    return AddressMatch.objects.create(
        **{
            "portal_user_id": portal.pk,
            "email": email,
            "sign_in_email": portal.sign_in_email,
            "matched_at": now,
            "confirmed_at": now,
        }
        | values
    )


def matched(store, portal=None, email=STAFF):
    """Record a match through the web login, acting as the matched address."""
    portal = portal or account()
    with web(), transaction.atomic():
        verified(portal)
        made = new_match(store, portal, email)
        PortalUser.objects.filter(pk=portal.pk).update(
            email=email, verified_at=database_now(), version=F("version") + 1
        )
    return PortalUser.objects.get(pk=portal.pk), made


def detail(error):
    """The DETAIL a guard refusal carried."""
    return error.value.__cause__.diag.message_detail


def signed_in_days_ago(portal, days):
    """One successful Admin sign-in of ``portal``, ``days`` ago."""
    login = AuditEvent.objects.create(event_type="admin_login", actor_id=portal.pk)
    older(login.pk, days)


def older(event_id, days):
    """Move one append-only audit event back, as only a superuser could."""
    with unguarded(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE stewardship_audit_event SET created_at=created_at-%s WHERE id=%s",
            [timedelta(days=days), event_id],
        )


def confirmed_days_ago(made, days):
    """Move a match's last confirmation back, as the owner may."""
    AddressMatch.objects.filter(pk=made.pk).update(
        confirmed_at=F("confirmed_at") - timedelta(days=days),
        version=F("version") + 1,
    )


def end(made, reason, **values):
    """End a match in the caller's transaction, as its account's sign-in would."""
    AddressMatch.objects.filter(pk=made.pk).update(
        ended_at=database_now(),
        end_reason=reason,
        version=F("version") + 1,
        **values,
    )


def put_back(portal):
    """Make ``portal`` act as its own sign-in address again (no verification)."""
    PortalUser.objects.filter(pk=portal.pk).update(
        email=F("sign_in_email"), version=F("version") + 1
    )


def in_use(email, excluding):
    """What the in-use definer answers, as the web login asks it."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_address_in_use_v1(%s, %s)", [email, excluding]
        )
        return cursor.fetchone()[0]


def automation_session(principal, authenticated_at=None):
    """A live full-scope automation session of ``principal``, seeded directly;
    returns its ID."""
    now = database_now()
    identifier = uuid4()
    with unguarded(), connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO stewardship_automation_session (id, correlation_id, "
            "version, principal_id, approving_session_id, authenticated_at, "
            "expires_at, scope, label, secret_digest, host_digest) VALUES "
            "(%s, %s, 1, %s, %s, %s, %s, 'full', 'test', %s, %s)",
            [
                identifier,
                uuid4(),
                principal.pk,
                uuid4(),
                authenticated_at or now,
                now + timedelta(days=5),
                uuid4().hex * 2,
                uuid4().hex * 2,
            ],
        )
    return identifier


def automation_end_reason(principal):
    """The end reason of ``principal``'s one automation session."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT end_reason FROM stewardship_automation_session "
            "WHERE principal_id=%s",
            [principal.pk],
        )
        (reason,) = cursor.fetchone()
    return reason


def session_aged(session, **changes):
    """Move a forged Admin session's times, as only a superuser could."""
    columns = ", ".join(f"{name}=%s" for name in changes)
    with unguarded(), connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE stewardship_portal_session SET {columns} WHERE id=%s",
            [*changes.values(), session.pk],
        )


def test_a_match_records_its_audit_row_and_security_event(routing):
    """The guard sets the times, audits the match by IDs and records the
    event (whose deferred key names the match written after it), with the
    active Administrators as recipients."""
    store, _, _, _ = routing
    portal, made = matched(store)
    assert portal.email == STAFF and portal.sign_in_email == PERSONAL
    made.refresh_from_db()
    assert made.version == 1 and made.matched_at == made.confirmed_at
    assert made.ended_at is None
    audit = AuditEvent.objects.get(event_type="admin_address_matched")
    assert (audit.actor_id, audit.subject_id) == (portal.pk, made.pk)
    event = PolicySecurityEvent.objects.get(kind="address_matched")
    assert event.activation_id is None and event.address_match_id == made.pk
    assert (event.target, event.rule_record_id) == (STAFF, made.user_record_id)
    assert (event.before_roles, event.after_roles) == ([], ["staff"])
    assert event.account_email == PERSONAL and event.actor_id == portal.pk
    assert event.replaced_account_email is event.replaced_login_at is None
    assert event.recipients == ["admin@example.org", "second@example.org"]
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT condeferrable AND condeferred FROM pg_constraint "
            "WHERE conrelid='stewardship_policy_security_event'::regclass "
            "AND confrelid='stewardship_address_match'::regclass"
        )
        assert cursor.fetchone()[0] is True
    # The account keeps acting as the user through later statements.
    assert AuditEvent.objects.filter(event_type="admin_address_matched").count() == 1


@contextmanager
def refused(error=IntegrityError, message=None):
    """A guard refusal inside its own savepoint; yields the caught error."""
    with pytest.raises(error, match=message) as caught, transaction.atomic():
        yield caught


def test_the_guard_refuses_each_forbidden_insert(routing):
    """Only the web login, for a just-verified enabled account and a
    current non-first-Administrator user, with nothing ended and no actor."""
    store, _, _, _ = routing
    portal = account()
    stale = account("stale@personal.example")
    disabled = account("disabled@personal.example")
    PortalUser.objects.filter(pk=disabled.pk).update(
        disabled=True, version=F("version") + 1
    )
    with web():
        with refused(message="verified in this transaction"):
            new_match(store, stale)
        with refused(message="verified in this transaction"):
            verified(disabled)
            new_match(store, disabled)
        for values in (
            {"sign_in_email": "else@personal.example"},
            {"user_record_id": uuid4()},
            {"ended_at": database_now(), "end_reason": "replaced"},
            # The audit row and event attribute the match; a stored actor,
            # even the account's own, could only be forged.
            {"actor_id": uuid4()},
            {"actor_id": portal.pk},
        ):
            with refused(message="verified in this transaction"):
                verified(portal)
                new_match(store, portal, **values)
        with refused(message="verified in this transaction"):
            verified(portal)
            new_match(store, portal, "nobody@example.org", user_record_id=uuid4())
        # The root configuration made admin@ an Administrator: never matched.
        with refused(message="own Google account") as caught:
            verified(portal)
            new_match(store, portal, "admin@example.org")
        assert detail(caught) == "address_first_administrator"
    with task_login(ServiceRole.WORKER):
        grant("GRANT INSERT ON stewardship_address_match")
        with refused(DatabaseError, "Only the web"), connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO stewardship_address_match (id, correlation_id, "
                "version, portal_user_id, email, user_record_id, "
                "sign_in_email, matched_at, confirmed_at) VALUES (%s, %s, 1, "
                "%s, %s, %s, %s, now(), now())",
                [uuid4(), uuid4(), portal.pk, STAFF, uuid4(), PERSONAL],
            )
    assert not AddressMatch.objects.exists()
    assert not PolicySecurityEvent.objects.filter(kind="address_matched").exists()


def grant(statement):
    """Widen the current test login with one GRANT, made as the owner.

    ``task_login`` gives a login exactly its runtime grants; a test that
    shows a guard refusing what no grant would let through adds one.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user")
        (login,) = cursor.fetchone()
        cursor.execute("RESET SESSION AUTHORIZATION")
        cursor.execute(f'{statement} TO "{login}"')
        cursor.execute(f'SET SESSION AUTHORIZATION "{login}"')


def test_an_address_in_use_is_refused_with_its_code(routing):
    """The address's own account in use (signed in less than 30 days ago,
    or holding a live session) refuses the match with its code."""
    store, _, _, _ = routing
    own = account(STAFF)
    portal = account()
    signed_in_days_ago(own, 29)
    with web():
        with refused(message="in use") as caught:
            verified(portal)
            new_match(store, portal)
        assert detail(caught) == "address_own_account_in_use"
    for login in AuditEvent.objects.filter(actor_id=own.pk):
        older(login.pk, 2)
    forge(own.pk)
    with web(), refused(message="in use"):
        verified(portal)
        new_match(store, portal)
    PortalSession.objects.filter(principal_id=own.pk).update(
        revoked_at=database_now(), version=F("version") + 1
    )
    # 31 days since its sign-in and no live session: the match is made.
    _, made = matched(store, portal)
    assert made.email == STAFF


def test_the_in_use_function_counts_each_account_of_the_address(routing):
    """Accounts of an address are those signing in with it and its standing
    matched account; ``excluding`` NULL leaves none out (IS DISTINCT FROM)."""
    store, _, _, _ = routing
    with web():
        assert not in_use(STAFF, None)
    portal, made = matched(store)
    with web():
        # Just matched: no sign-in recorded yet, no session.
        assert not in_use(STAFF, None)
    signed_in_days_ago(portal, 1)
    with web():
        assert in_use(STAFF, None) and in_use(STAFF, uuid4())
        assert not in_use(STAFF, portal.pk)
        # Its sign-in address is not the matched one.
        assert in_use(PERSONAL, None)
    admin = account("second@example.org")
    with web():
        assert not in_use("second@example.org", None)
    automation_session(admin)
    with web():
        assert in_use("second@example.org", None)
        assert not in_use("second@example.org", admin.pk)
    PortalUser.objects.filter(pk=admin.pk).update(
        disabled=True, version=F("version") + 1
    )
    with web():
        assert not in_use("second@example.org", None)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT has_function_privilege('public', "
            "'stewardship_address_in_use_v1(text,uuid)', 'EXECUTE')"
        )
        assert cursor.fetchone()[0] is False
    assert made.email == STAFF


def test_the_schema_owner_passes_unchecked_and_writes_nothing(routing):
    """Migrations, tests and a repair run as the owner; pytest-django's
    flush truncates the table as the owner, and no TRUNCATE trigger exists."""
    store, _, _, _ = routing
    portal = account()
    with transaction.atomic():
        made = new_match(store, portal, "admin@example.org", user_record_id=uuid4())
    assert not AuditEvent.objects.filter(event_type="admin_address_matched").exists()
    assert not PolicySecurityEvent.objects.filter(kind="address_matched").exists()
    AddressMatch.objects.filter(pk=made.pk).delete()
    matched(store)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal "
            "AND tgrelid='stewardship_address_match'::regclass AND tgtype & 32 <> 0"
        )
        assert cursor.fetchone()[0] == 0
        with transaction.atomic():
            cursor.execute(
                "TRUNCATE stewardship_address_match, "
                "stewardship_policy_security_event CASCADE"
            )
            transaction.set_rollback(True)
    assert AddressMatch.objects.count() == 1


def test_a_recheck_moves_only_the_confirmation_after_a_fresh_sign_in(routing):
    """Only confirmed_at advances, to now; nothing else of a standing match."""
    store, _, _, _ = routing
    portal, made = matched(store)
    confirmed_days_ago(made, 31)
    before = AddressMatch.objects.get(pk=made.pk).confirmed_at
    with web():
        with refused(message="confirmed again only by a sign-in"):
            AddressMatch.objects.filter(pk=made.pk).update(
                confirmed_at=database_now(), version=F("version") + 1
            )
        with transaction.atomic():
            verified(portal)
            AddressMatch.objects.filter(pk=made.pk).update(
                confirmed_at=before, version=F("version") + 1
            )
    after = AddressMatch.objects.get(pk=made.pk).confirmed_at
    assert after > before + timedelta(days=30)
    with web(), refused(DatabaseError):
        AddressMatch.objects.filter(pk=made.pk).update(
            email="else@example.org", version=F("version") + 1
        )
    with web():
        grant("GRANT DELETE ON stewardship_address_match")
        # Raw SQL: Django's own delete stops first at the event's PROTECT.
        with refused(DatabaseError, "kept for attribution"):
            connection.cursor().execute(
                "DELETE FROM stewardship_address_match WHERE id=%s", [made.pk]
            )


def test_each_end_reason_is_derived_from_the_tables(routing):
    """An end follows the account's return to its own address; each reason
    holds only when the tables say so, and an ended match changes no more."""
    store, actor, _, _ = routing
    portal, made = matched(store)
    with web():
        with refused(message="acts as its own address again"):
            end(made, "not_confirmed")
        for reason, message in (
            ("not_confirmed", "unconfirmed only by a sign-in"),
            ("sign_in_address_matches", "no user"),
            ("user_gone", "still there"),
            ("replaced", "in use"),
            ("ended_by_administrator", "live Administrator session"),
        ):
            with refused(DatabaseError, message):
                put_back(portal)
                end(made, reason)
        with refused(DatabaseError, "Only an Administrator end"):
            put_back(portal)
            end(made, "user_gone", ended_by_id=uuid4())
        # A fresh verification makes the check's outcome valid.
        with transaction.atomic():
            put_back(portal)
            verified(portal)
            end(made, "not_confirmed")
        with refused(message="does not change"):
            end(made, "user_gone")
    made.refresh_from_db()
    assert made.end_reason == "not_confirmed" and made.ended_at is not None
    audit = AuditEvent.objects.get(event_type="admin_address_match_ended")
    assert (audit.actor_id, audit.subject_id) == (portal.pk, made.pk)
    # The sign-in address becomes a user's: that end is now admitted.
    portal, again = matched(store, portal)
    leader = address(PERSONAL, ("ministry_leader",))
    patch = [{"operation": "add", "section": "login_rules", **leader}]
    assert change(store, store.active(), actor, patch).state == "applied"
    with web(), transaction.atomic():
        put_back(portal)
        end(again, "sign_in_address_matches")


def test_a_gone_user_is_found_by_its_record_id(routing):
    """Deleting the user and adding the address again as a new record ends
    the match as user_gone, and that end revokes the account's sessions."""
    store, actor, _, _ = routing
    portal, made = matched(store)
    session = forge(portal.pk)
    rule = record_id(store, STAFF)
    for patch in (
        [{"operation": "remove", "section": "login_rules", "id": rule}],
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address(STAFF, ("staff",)),
            }
        ],
    ):
        assert change(store, store.active(), actor, patch).state == "applied"
    assert record_id(store, STAFF) != made.user_record_id
    # Seeded after the user changes, so only the end can stop it.
    automation_session(portal)
    with web(), transaction.atomic():
        put_back(portal)
        end(made, "user_gone")
    session.refresh_from_db()
    assert session.revoked_at is not None
    revoked = AuditEvent.objects.get(event_type="admin_revoked")
    assert (revoked.actor_id, revoked.subject_id) == (portal.pk, session.pk)
    assert automation_end_reason(portal) == "user_removed"
    stopped = AuditEvent.objects.get(event_type="automation_session_ended")
    assert stopped.actor_id is None


def test_a_takeover_needs_an_idle_lapsed_match_and_names_what_it_replaced(routing):
    """The matched account signed in at day 29 (its session since cleaned
    up), so at day 31 another account is refused and so is the takeover;
    idle 30 days, the takeover ends the old account's sessions and the new
    event carries the Replaced Google account row."""
    store, _, _, _ = routing
    old, made = matched(store)
    confirmed_days_ago(made, 31)
    signed_in_days_ago(old, 2)
    newer = account("newer@personal.example")
    with web():
        with refused(message="in use") as caught:
            verified(newer)
            new_match(store, newer)
        assert detail(caught) == "address_own_account_in_use"
        with refused(message="in use") as caught:
            put_back(old)
            end(made, "replaced")
        assert detail(caught) == "address_own_account_in_use"
    for login in AuditEvent.objects.filter(actor_id=old.pk):
        older(login.pk, 29)
    last = AuditEvent.objects.get(actor_id=old.pk, event_type="admin_login")
    # Not live (a Staff user is no Administrator), so not in use, but ended.
    automation_session(old)
    with web(), transaction.atomic():
        put_back(old)
        end(made, "replaced")
        verified(newer)
        taken = new_match(store, newer)
    assert automation_end_reason(old) == "role_lost"
    event = PolicySecurityEvent.objects.get(address_match_id=taken.pk)
    assert event.replaced_account_email == PERSONAL
    assert event.replaced_login_at == last.created_at
    ended = AuditEvent.objects.get(event_type="admin_address_match_ended")
    assert ended.actor_id is None and ended.subject_id == made.pk
    assert AuditEvent.objects.filter(event_type="automation_session_ended").count() == 1


def test_an_administrator_ends_a_match_from_a_live_session(routing):
    """An enabled Administrator other than the account, in a live Admin
    session (0124's conditions: not idle 60 minutes, not expired, a command
    session only while fresh), ends it; the account's sessions end with it.
    The match is of the Ministry leader, so the Staff user's own account can
    try to end it from its own live session."""
    store, _, _, _ = routing
    portal, made = matched(store, email=LEADER)
    matched_session = forge(portal.pk)
    automation_session(portal)
    admin = account("second@example.org")
    staff = account(STAFF)
    live = forge(admin.pk)
    # Activity never precedes the sign-in, so each moves the sign-in back.
    now = database_now()
    idle = forge(admin.pk)
    gone = now - timedelta(minutes=61)
    session_aged(idle, authenticated_at=gone, last_activity_at=gone)
    expired = forge(admin.pk)
    session_aged(
        expired,
        authenticated_at=now - timedelta(hours=2),
        last_activity_at=now - timedelta(minutes=2),
        expires_at=now - timedelta(seconds=1),
    )
    # A command session whose automation session signed in at another
    # instant is not fresh (stewardship_automation_fresh_v1).
    command = forge(admin.pk)
    stale = automation_session(admin, database_now() - timedelta(minutes=1))
    with unguarded(), connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO stewardship_automation_login (portal_session_id, "
            "automation_session_id) VALUES (%s, %s)",
            [command.pk, stale],
        )
    with web():
        for values in (
            {"ended_by_id": admin.pk, "ended_session_id": uuid4()},
            {"ended_by_id": portal.pk, "ended_session_id": matched_session.pk},
            {"ended_by_id": admin.pk, "ended_session_id": matched_session.pk},
            {"ended_by_id": staff.pk, "ended_session_id": forge(staff.pk).pk},
            {"ended_by_id": admin.pk, "ended_session_id": idle.pk},
            {"ended_by_id": admin.pk, "ended_session_id": expired.pk},
            {"ended_by_id": admin.pk, "ended_session_id": command.pk},
        ):
            with refused(DatabaseError, "live Administrator session"):
                put_back(portal)
                end(made, "ended_by_administrator", **values)
        with transaction.atomic():
            put_back(portal)
            end(
                made,
                "ended_by_administrator",
                ended_by_id=admin.pk,
                ended_session_id=live.pk,
            )
    matched_session.refresh_from_db()
    assert matched_session.revoked_at is not None
    live.refresh_from_db()
    assert live.revoked_at is None
    ended = AuditEvent.objects.get(event_type="admin_address_match_ended")
    assert ended.actor_id == admin.pk
    assert automation_end_reason(portal) == "revoked_by_administrator"
    stopped = AuditEvent.objects.get(event_type="automation_session_ended")
    assert stopped.actor_id == admin.pk


def test_the_portal_user_guards_hold_the_acted_as_address_to_a_match(routing):
    """email differs from sign_in_email only with a fresh verification and
    a standing match confirmed less than 30 days ago; it returns to the
    sign-in address at any time."""
    store, _, _, _ = routing
    portal = account()
    with web():
        with refused(message="only through a confirmed match"):
            PortalUser.objects.filter(pk=portal.pk).update(
                email=STAFF, verified_at=database_now(), version=F("version") + 1
            )
        with transaction.atomic():
            verified(portal)
            made = new_match(store, portal)
        with refused(message="fresh verification"):
            PortalUser.objects.filter(pk=portal.pk).update(
                email=STAFF, version=F("version") + 1
            )
    confirmed_days_ago(made, 30)
    with web(), refused(message="only through a confirmed match"):
        PortalUser.objects.filter(pk=portal.pk).update(
            email=STAFF, verified_at=database_now(), version=F("version") + 1
        )
    AddressMatch.objects.filter(pk=made.pk).update(
        confirmed_at=database_now(), version=F("version") + 1
    )
    with web():
        with transaction.atomic():
            PortalUser.objects.filter(pk=portal.pk).update(
                email=STAFF, verified_at=database_now(), version=F("version") + 1
            )
        # Another sign-in address needs the match too: it still names STAFF.
        with refused(message="fresh verification"):
            PortalUser.objects.filter(pk=portal.pk).update(
                sign_in_email="moved@personal.example", version=F("version") + 1
            )
        put_back(portal)
    assert PortalUser.objects.get(pk=portal.pk).email == PERSONAL


def test_a_login_without_match_grants_still_updates_portal_users(routing):
    """The update guard reads the match table only for an acted-as address,
    so a login granted portal users but not matches still updates them."""
    portal = account()
    with task_login(ServiceRole.WORKER), connection.cursor() as cursor:
        # UPDATE, and SELECT for the statement's own reads of the row.
        grant("GRANT SELECT, UPDATE ON stewardship_portal_user")
        cursor.execute(
            "UPDATE stewardship_portal_user SET disabled=true, "
            "version=version+1 WHERE id=%s",
            [portal.pk],
        )
        cursor.execute(
            "SELECT has_table_privilege('stewardship_address_match', 'SELECT')"
        )
        assert cursor.fetchone()[0] is False
    assert PortalUser.objects.get(pk=portal.pk).disabled


def test_both_content_compilers_render_a_match_and_a_takeover(routing):
    """The SQL twin recompiles address_matched byte for byte: a plain match,
    a takeover of an account that never signed in, and a takeover of one
    that last signed in 31 days ago (each form of the Replaced row)."""
    store, _, _, _ = routing
    old, made = matched(store)
    confirmed_days_ago(made, 31)
    newer = account("newer@personal.example")
    with web(), transaction.atomic():
        put_back(old)
        end(made, "replaced")
        verified(newer)
        taken = new_match(store, newer)
    confirmed_days_ago(taken, 31)
    signed_in_days_ago(newer, 31)
    last = AuditEvent.objects.get(actor_id=newer.pk, event_type="admin_login")
    third = account("third@personal.example")
    with web(), transaction.atomic():
        put_back(newer)
        end(taken, "replaced")
        verified(third)
        new_match(store, third)
    events = list(PolicySecurityEvent.objects.filter(kind="address_matched"))
    assert len(events) == 3
    rows = {
        None: None,
        PERSONAL: f"Replaced Google account: {PERSONAL}, never signed in",
        "newer@personal.example": (
            "Replaced Google account: newer@personal.example, last signed in "
        ),
    }
    configuration = store.active().version_id
    for event in events:
        assert (event.replaced_login_at is None) is (
            event.replaced_account_email != "newer@personal.example"
        )
        if event.replaced_login_at is not None:
            assert event.replaced_login_at == last.created_at
        for mode in SystemMode:
            with work_transaction():
                alert = event_alert(event.pk, mode, configuration)
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT stewardship_security_content_v2(%s,%s,%s)",
                        [event.pk, mode.value, configuration],
                    )
                    compiled = cursor.fetchone()[0]
            if isinstance(compiled, str):
                compiled = json.loads(compiled)
            assert compiled == asdict(render_security_alert(alert))
            row = rows[event.replaced_account_email]
            assert (row is None) is ("Replaced Google account" not in compiled["text"])
            assert row is None or row in compiled["text"]
            assert "By:" not in compiled["text"]


def test_a_match_event_reaches_every_recorded_administrator(routing):
    """The existing security fanout delivers address_matched unchanged: the
    cohort is the event's recipients and the recovery recipient leaves a row
    without an activation alone."""
    store, _, _, _ = routing
    matched(store)
    event = PolicySecurityEvent.objects.get(kind="address_matched")
    (identifier,) = schedule()
    assert consume(identifier, store)
    messages = OutboxMessage.objects.filter(purpose="security_event")
    assert sorted(m.render.routed_recipients[0] for m in messages) == event.recipients
    for message in messages.select_related("render"):
        assert message.render.subject == (
            "[TESTING] SECURITY: Google account matched through another address"
        )
        assert f"Account: {PERSONAL}\nUser: {STAFF}\nRole: Staff" in (
            message.render.text
        )


def old_definition(path, name):
    """One function's statement from an earlier file, as CREATE OR REPLACE."""
    text = path.read_text(encoding="utf-8")
    statement = re.search(
        rf"^CREATE (?:OR REPLACE )?FUNCTION public\.{name}\(.*?\$\$;$",
        text,
        re.S | re.M,
    ).group(0)
    return statement.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


OLD_DEFINITIONS = {
    "insert_guard": old_definition(
        SCHEMA / "migrations/0030_portal_user_insert_guard.sql",
        "stewardship_portal_user_insert_v1",
    ),
    "update_guard": old_definition(
        Path(__file__).with_name("pre_address_match_functions.sql"),
        "stewardship_portal_user_mutable_v1",
    ),
    "content": old_definition(
        SCHEMA / "migrations/0043_alert_local_time.sql",
        "stewardship_security_content_v2",
    ),
    "guard_without_actor_check": FROZEN[
        FROZEN.index(
            "CREATE FUNCTION public.stewardship_address_match_guard_v1"
        ) : FROZEN.index(
            "REVOKE ALL ON FUNCTION public.stewardship_address_match_guard_v1"
        )
    ]
    .replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
    .replace(" OR NEW.actor_id IS NOT NULL", "", 1),
    "invoker_guard": (
        "ALTER FUNCTION public.stewardship_address_match_guard_v1() SECURITY INVOKER"
    ),
    "invoker_in_use": (
        "ALTER FUNCTION public.stewardship_address_in_use_v1(text,uuid) "
        "SECURITY INVOKER"
    ),
    "public_in_use": (
        "GRANT EXECUTE ON FUNCTION public.stewardship_address_in_use_v1(text,uuid) "
        "TO PUBLIC"
    ),
    "unpinned_guard": (
        "ALTER FUNCTION public.stewardship_address_match_guard_v1() RESET search_path"
    ),
    "truncate_trigger": (
        "CREATE TRIGGER truncated BEFORE TRUNCATE ON stewardship_address_match "
        "FOR EACH STATEMENT EXECUTE FUNCTION suppress_redundant_updates_trigger()"
    ),
    "nullable_sign_in": (
        "ALTER TABLE stewardship_portal_user ALTER COLUMN sign_in_email DROP NOT NULL"
    ),
    "immediate_key": (
        "ALTER TABLE stewardship_policy_security_event ALTER CONSTRAINT "
        '"stewardship_policy_s_address_match_id_4e8e7c5a_fk_stewardsh" '
        "NOT DEFERRABLE"
    ),
}


def test_the_do_block_passes_on_the_installed_schema():
    """The migration's own check passes on what it installed."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(CHECK)
        transaction.set_rollback(True)


@pytest.mark.parametrize("name", sorted(OLD_DEFINITIONS))
def test_the_do_block_refuses_each_earlier_or_weakened_definition(name):
    """Each replaced function's earlier body, a definer made invoker, an
    unpinned path, PUBLIC EXECUTE, a TRUNCATE trigger, a nullable sign-in
    address or an immediate event key: the check refuses."""
    with (
        pytest.raises(DatabaseError, match="Migration 0200"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(OLD_DEFINITIONS[name])
        cursor.execute(CHECK)

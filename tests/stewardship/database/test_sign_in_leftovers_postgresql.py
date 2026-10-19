"""Migration 0123 removes what #1547's sign-in change left unused (#1550).

0123 (``schema/migrations/0123_sign_in_leftovers.sql``) drops
``stewardship_automation_fresh_principal_v1`` (0013's definer check, which no
guard calls after 0114) and makes an Admin session's sign-in instant
immutable in the portal session guard. Following the in-place SQL rule, the
file runs here whole against the state before it (0013's function and the
guard's earlier body, put back in a rolled-back transaction) with a live
Admin session in place, which it must leave untouched; and its DO block must
refuse each earlier or weakened install. The guard's refusals themselves are
covered by the storage tests.
"""

import re
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.sessions import database_now

from .auth_builders import unguarded

pytestmark = pytest.mark.django_db(transaction=True)

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "src/parishkit/stewardship/schema/migrations"
FROZEN = (MIGRATIONS / "0123_sign_in_leftovers.sql").read_text(encoding="utf-8")
PRINCIPAL = "stewardship_automation_fresh_principal_v1"
GUARD = "stewardship_portal_session_mutable_v1"
# The md5 of the guard's released body (main before 0123), so the rebuilt
# earlier state is the one Production has.
RELEASED_GUARD_MD5 = "be82f0f1e3bbd894c6203d0ee2f311c9"
# The guard's clause that 0123 removed: the instant could advance in place.
ADVANCE = (
    "                -- Step-up reauthentication advances a live session's verified\n"
    "                -- Google instant in place. It never moves backwards, never past\n"
    "                -- this statement's clock, and never changes on a revoked row.\n"
    '                IF NEW."authenticated_at" IS DISTINCT FROM '
    'OLD."authenticated_at"\n'
    '                   AND (OLD."revoked_at" IS NOT NULL\n'
    '                        OR NEW."authenticated_at" < OLD."authenticated_at"\n'
    '                        OR NEW."authenticated_at" > statement_timestamp()) THEN\n'
    "                    RAISE EXCEPTION 'Session authentication may only advance "
    "to a verified past instant'\n"
    "                        USING ERRCODE = '23514';\n"
    "                END IF;\n"
)


def statement(text, name):
    """The first ``CREATE [OR REPLACE] FUNCTION public.name(`` statement, whole."""
    match = re.search(rf"^CREATE (?:OR REPLACE )?FUNCTION public\.{name}\(", text, re.M)
    quote = re.compile(r"\bAS\s+(\$\w*\$)").search(text, match.end())
    end = text.index(quote[1] + ";", quote.end()) + len(quote[1]) + 1
    return text[match.start() : end]


def principal_function():
    """0013's ``CREATE FUNCTION`` of the dropped check, with its PUBLIC revoke."""
    text = (MIGRATIONS / "0013_automation_fresh_guards.sql").read_text(encoding="utf-8")
    start = text.index(f"CREATE FUNCTION public.{PRINCIPAL}(")
    return text[start : text.index("FROM PUBLIC;", start) + len("FROM PUBLIC;")]


def previous_guard():
    """The guard as it was before 0123, from 0123's own copy.

    The released body had no #1550 note, no ``authenticated_at`` in the
    immutable list, and the clause that let the instant advance.
    """
    note = (
        "                -- The verified Google instant is fixed when the session is\n"
        "                -- issued: a new sign-in issues a new session (#1547), so\n"
        "                -- nothing advances it in place any more (#1550).\n"
    )
    clause = ' OR NEW."authenticated_at" IS DISTINCT FROM OLD."authenticated_at"'
    activity = "                -- #306 M2"
    body = statement(FROZEN, GUARD)
    for part in (note, clause, activity):
        assert body.count(part) == 1, part
    return (
        body.replace(note, "").replace(clause, "").replace(activity, ADVANCE + activity)
    )


def live_session():
    """One live Admin session row, as a signed-in Administrator has."""
    store = SessionStore()
    store["principal_id"] = str(uuid4())
    store.save()
    now = database_now()
    with unguarded():
        return PortalSession.objects.create(
            session_id=store.session_key,
            principal_id=uuid4(),
            authenticated_at=now,
            last_activity_at=now,
            expires_at=now + timedelta(hours=1),
        )


def test_the_function_and_its_grants_are_gone():
    """No function of that name, and nothing calls it."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n "
            "ON n.oid=p.pronamespace WHERE n.nspname='public' "
            "AND (p.proname=%s OR p.prosrc LIKE %s)",
            [PRINCIPAL, f"%{PRINCIPAL[:-3]}%"],
        )
        assert cursor.fetchone()[0] == 0


def test_the_file_runs_on_the_earlier_state_and_keeps_every_session():
    """On 0013's function and the earlier guard, 0123 installs and changes no row.

    A live Admin session keeps its sign-in instant, version and liveness.
    The file has no UPDATE or DELETE at all; the Family session, link and
    code tables are hashed too (empty here) as a guard against one added.
    """
    session = live_session()
    # Every Admin session and every Family session, link and code row, whole.
    rows = " UNION ALL ".join(
        f"SELECT '{table}', md5(coalesce(string_agg(t::text, ',' ORDER BY t::text),''))"
        f" FROM {table} t"
        for table in (
            "stewardship_portal_session",
            "stewardship_family_session",
            "stewardship_family_token",
            "stewardship_family_code_mac",
        )
    )
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(principal_function())
        cursor.execute(previous_guard())
        cursor.execute(
            "SELECT md5(prosrc) FROM pg_proc WHERE oid=%s::regprocedure",
            [f"public.{GUARD}()"],
        )
        assert cursor.fetchone()[0] == RELEASED_GUARD_MD5
        cursor.execute(rows)
        before = cursor.fetchall()
        cursor.execute(FROZEN)
        cursor.execute(rows)
        assert cursor.fetchall() == before
        cursor.execute(
            "SELECT authenticated_at,version,revoked_at FROM stewardship_portal_session"
            " WHERE id=%s",
            [session.pk],
        )
        assert cursor.fetchone() == (session.authenticated_at, session.version, None)
        transaction.set_rollback(True)


def test_migration_check_refuses_each_earlier_or_weakened_install():
    """0123's check passes as installed and refuses each regression."""
    capture = FROZEN[FROZEN.index("SELECT set_config(") : FROZEN.index("true);") + 6]
    check = FROZEN[FROZEN.index("DO $check$") :]
    guard = statement(FROZEN, GUARD)
    changes = [
        # The dropped check installed again, or a body still calling it.
        (principal_function(), "is still installed"),
        (
            # PL/pgSQL, as the guards are: its calls resolve only when run.
            "CREATE FUNCTION public.pk_s1550_probe() RETURNS boolean "
            f"LANGUAGE plpgsql AS $$ BEGIN RETURN public.{PRINCIPAL}(NULL,NULL); "
            "END $$",
            "still calls",
        ),
        # The guard's earlier body, and one that lets the instant change.
        (previous_guard(), "not installed as declared"),
        (
            guard.replace(
                ' OR NEW."authenticated_at" IS DISTINCT FROM OLD."authenticated_at"',
                "",
            ),
            "not installed as declared",
        ),
        # Its rights, configuration or grants changed, or its trigger off.
        (
            f"ALTER FUNCTION public.{GUARD}() SECURITY DEFINER",
            "rights, configuration or grants",
        ),
        (
            f"ALTER FUNCTION public.{GUARD}() SET search_path TO public",
            "rights, configuration or grants",
        ),
        (
            "CREATE ROLE pk_s1550_probe NOLOGIN; GRANT EXECUTE ON FUNCTION "
            f"public.{GUARD}() TO pk_s1550_probe",
            "rights, configuration or grants",
        ),
        (
            "ALTER TABLE public.stewardship_portal_session "
            "DISABLE TRIGGER stewardship_portal_session_mutable_guard_v1",
            "no longer has its trigger",
        ),
        # What 0114 kept is still required.
        (
            "ALTER FUNCTION public.stewardship_automation_instant_ended_v1"
            "(uuid,timestamptz) SECURITY INVOKER",
            "0114 kept is missing",
        ),
        (
            "GRANT EXECUTE ON FUNCTION public.stewardship_automation_instant_ended_v1"
            "(uuid,timestamptz) TO PUBLIC",
            "0114 kept is missing",
        ),
        (
            "ALTER FUNCTION public.stewardship_automation_fresh_v1(uuid,uuid) "
            "RENAME TO pk_s1550_probe",
            "0114 kept is missing",
        ),
    ]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(capture)
        cursor.execute(check)
        for change, message in changes:
            # The change is named on failure, so a passing regression is found.
            with (
                pytest.raises(DatabaseError, match=f"Migration 0123.*{message}"),
                transaction.atomic(),
            ):
                cursor.execute(change)
                cursor.execute(check)
                pytest.fail(f"0123's check admitted: {change[:120]}")
        cursor.execute(check)
        transaction.set_rollback(True)

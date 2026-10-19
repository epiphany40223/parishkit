"""Migration 0125 binds a secret request to its automation session (#1763).

0125 (``schema/migrations/0125_secret_request_session.sql``) adds
``stewardship_secret_request.automation_session_id``, the definer check
``stewardship_automation_request_live_v1``, replaces the sealed intake check,
the secret request state guard and the request's immutability guard so that
only a request naming a session gets the automation check, and drops 0114's
``stewardship_automation_instant_ended_v1``. Following the in-place SQL rule,
the file runs here whole against the state before it (each earlier body, the
dropped check and no column, put back in a rolled-back transaction) with a
staged request in place, which it must leave untouched, and runs again; its
DO block must refuse each earlier or weakened install. What the guards admit
and refuse is covered in ``test_automation_fresh_guards_postgresql``.
"""

import re
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from parishkit.stewardship.accounts.secret_requests import stage_secret_request

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
FROZEN = (MIGRATIONS / "migrations/0125_secret_request_session.sql").read_text(
    encoding="utf-8"
)
LIVE = "stewardship_automation_request_live_v1"
LIVE_SIGNATURE = f"public.{LIVE}(uuid,uuid,timestamptz)"
ENDED = "stewardship_automation_instant_ended_v1"
INTAKE = "stewardship_sealed_intake_admission_v1"
STATE = "stewardship_secret_state_v2"
MUTABLE = "stewardship_secret_request_mutable_v1"
# The clause 0125 adds to the immutability guard.
BOUND = ' OR NEW."automation_session_id" IS DISTINCT FROM OLD."automation_session_id"'
# Each replaced body's md5 before 0125 (0124's intake check, 0114's state
# guard, the baseline's immutability guard), so the rebuilt earlier state is
# the one an upgraded database has.
EARLIER_MD5 = {
    INTAKE: "ec8e4b98eeefb9fe437cee968865d00d",
    STATE: "eb1a61f9d22c3b36162efb6ca9416e08",
    MUTABLE: "c5c3348fb3d5b4b2f82892c969af8eef",
}


def statement(text, name):
    """The first ``CREATE [OR REPLACE] FUNCTION public.name(`` statement, whole."""
    match = re.search(rf"^CREATE (?:OR REPLACE )?FUNCTION public\.{name}\(", text, re.M)
    quote = re.compile(r"\bAS\s+(\$\w*\$)").search(text, match.end())
    end = text.index(quote[1] + ";", quote.end()) + len(quote[1]) + 1
    return text[match.start() : end]


def frozen(number):
    """One earlier frozen file's text, by its four-digit prefix."""
    [path] = (MIGRATIONS / "migrations").glob(f"{number}_*.sql")
    return path.read_text(encoding="utf-8")


def earlier_state():
    """Statements that put the schema back as it was before 0125.

    0114's ended-instant check (with its PUBLIC revoke), 0124's intake
    check, 0114's state guard, the immutability guard without the new
    clause, and no column, nor the new check.
    """
    ended = frozen("0114")
    start = ended.index(f"CREATE FUNCTION public.{ENDED}(")
    mutable = statement(FROZEN, MUTABLE)
    assert mutable.count(BOUND) == 1
    return [
        ended[start : ended.index("FROM PUBLIC;", start) + len("FROM PUBLIC;")],
        statement(frozen("0124"), INTAKE),
        statement(frozen("0114"), STATE),
        mutable.replace(BOUND, ""),
        f"DROP FUNCTION {LIVE_SIGNATURE}",
        "ALTER TABLE public.stewardship_secret_request DROP COLUMN "
        "automation_session_id",
    ]


def md5s(cursor):
    """Each replaced function's installed body md5."""
    cursor.execute(
        "SELECT proname, md5(prosrc) FROM pg_proc WHERE proname = ANY(%s)",
        [list(EARLIER_MD5)],
    )
    return dict(cursor.fetchall())


def staged_request():
    """One staged browser request, as a key replacement in flight has."""
    identifier = uuid4()
    stage_secret_request(
        request_id=identifier,
        target="parishsoft",
        staging_reference=uuid4(),
        actor_id=uuid4(),
        reauthenticated_at=timezone.now() - timedelta(minutes=1),
        expires_at=timezone.now() + timedelta(minutes=10),
        expected_fingerprint="a" * 64,
        correlation_id=uuid4(),
    )
    return identifier


def test_the_installed_schema_is_as_declared():
    """The column, the new check's rights and no PUBLIC EXECUTE; no ended check."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT format_type(atttypid, atttypmod), attnotnull, atthasdef "
            "FROM pg_attribute WHERE attrelid='public.stewardship_secret_request'"
            "::regclass AND attname='automation_session_id' AND NOT attisdropped"
        )
        assert cursor.fetchone() == ("uuid", False, False)
        cursor.execute(
            "SELECT prosecdef, has_function_privilege('public', oid, 'EXECUTE') "
            "FROM pg_proc WHERE oid=%s::regprocedure",
            [LIVE_SIGNATURE],
        )
        assert cursor.fetchone() == (True, False)
        cursor.execute(
            "SELECT count(*) FROM pg_proc WHERE proname=%s OR prosrc LIKE %s",
            [ENDED, f"%{ENDED}%"],
        )
        assert cursor.fetchone()[0] == 0


def test_the_file_runs_on_the_earlier_state_and_again():
    """On the state before it, 0125 installs, changes no row and can run again.

    A staged request keeps every value, with no named session. The file has
    no UPDATE or DELETE; Admin sessions and the Family session, link and
    code tables are hashed too, as a guard against one added. Before running
    again, as the users swap's tests do, the new check is dropped (it is a
    plain CREATE); the column, the replaced bodies and the dropped check
    tolerate a second run.
    """
    staged_request()
    rows = " UNION ALL ".join(
        f"SELECT '{table}', md5(coalesce(string_agg(to_jsonb(t)::text, ','"
        f" ORDER BY t::text),'')) FROM {table} t"
        for table in (
            "stewardship_secret_request",
            "stewardship_portal_session",
            "stewardship_family_session",
            "stewardship_family_token",
            "stewardship_family_code_mac",
        )
    )
    with transaction.atomic(), connection.cursor() as cursor:
        for change in earlier_state():
            cursor.execute(change)
        assert md5s(cursor) == EARLIER_MD5
        cursor.execute(rows)
        before = cursor.fetchall()
        cursor.execute(FROZEN)
        cursor.execute(
            "SELECT automation_session_id FROM public.stewardship_secret_request"
        )
        assert cursor.fetchall() == [(None,)]
        # The new column is the only change to the request row: drop it from
        # the comparison by hashing without it.
        cursor.execute(
            rows.replace("to_jsonb(t)", "(to_jsonb(t)-'automation_session_id')")
        )
        assert cursor.fetchall() == before
        cursor.execute(f"DROP FUNCTION {LIVE_SIGNATURE}")
        cursor.execute(FROZEN)
        transaction.set_rollback(True)


def test_migration_check_refuses_each_earlier_or_weakened_install():
    """0125's check passes as installed and refuses each regression."""
    capture = FROZEN[
        FROZEN.index("SELECT set_config(") : FROZEN.index("\n\nALTER TABLE public.")
    ]
    assert capture.count("SELECT set_config(") == 2
    check = FROZEN[FROZEN.index("DO $check$") :]
    declared = "is not installed as declared"
    ended = earlier_state()[0]
    intake = statement(FROZEN, INTAKE)
    state = statement(FROZEN, STATE)
    live = statement(FROZEN, LIVE).replace(
        "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"
    )
    changes = [
        # Each earlier body.
        *(
            (body, f"{name} {declared}")
            for name, body in zip(
                (INTAKE, STATE, MUTABLE), earlier_state()[1:4], strict=True
            )
        ),
        # Key clauses undone: the intake and testing checks admitting an
        # ended session, and a named session that may change.
        (intake.replace("IF NOT live THEN", "IF false THEN"), f"{INTAKE} {declared}"),
        (
            intake.replace("            ELSE\n                live := false;\n", ""),
            f"{INTAKE} {declared}",
        ),
        (state.replace("           OR NOT live\n", ""), f"{STATE} {declared}"),
        (
            state.replace("            ELSE\n                live := false;\n", ""),
            f"{STATE} {declared}",
        ),
        # The new check always true, without its live test, as its caller's
        # rights, open to PUBLIC, or with another search_path.
        (
            live.replace("    SELECT EXISTS", "    SELECT true OR EXISTS", 1),
            f"{LIVE} {declared}",
        ),
        (
            live.replace(
                "\n              AND public.stewardship_automation_live_v1(a.id)", ""
            ),
            f"{LIVE} {declared}",
        ),
        (f"ALTER FUNCTION {LIVE_SIGNATURE} SECURITY INVOKER", f"{LIVE} {declared}"),
        (f"GRANT EXECUTE ON FUNCTION {LIVE_SIGNATURE} TO PUBLIC", f"{LIVE} {declared}"),
        (
            "CREATE ROLE pk_s1763_live NOLOGIN; GRANT EXECUTE ON FUNCTION "
            f"{LIVE_SIGNATURE} TO pk_s1763_live",
            f"{LIVE} {declared}",
        ),
        (
            f"ALTER FUNCTION {LIVE_SIGNATURE} SET search_path TO public",
            f"{LIVE} {declared}",
        ),
        # The column as another type, required, or with a default.
        (
            "ALTER TABLE public.stewardship_secret_request ALTER COLUMN "
            "automation_session_id TYPE text",
            "automation_session_id is not installed",
        ),
        (
            "ALTER TABLE public.stewardship_secret_request ALTER COLUMN "
            "automation_session_id SET DEFAULT gen_random_uuid()",
            "automation_session_id is not installed",
        ),
        # The ended check back, or a function still calling it.
        (ended, "is still installed"),
        (
            "CREATE FUNCTION public.pk_s1763_probe() RETURNS boolean "
            f"LANGUAGE plpgsql AS $$ BEGIN RETURN public.{ENDED}(NULL,NULL); END $$",
            "still calls",
        ),
        # A replaced guard with definer rights, another search_path or a new
        # grant, or one of its triggers disabled.
        (
            f"ALTER FUNCTION public.{STATE}() SECURITY DEFINER",
            "rights, configuration or grants",
        ),
        (
            f"ALTER FUNCTION public.{INTAKE}() SET search_path TO public",
            "rights, configuration or grants",
        ),
        (
            "CREATE ROLE pk_s1763_probe NOLOGIN; GRANT EXECUTE ON FUNCTION "
            f"public.{MUTABLE}() TO pk_s1763_probe",
            "rights, configuration or grants",
        ),
        (
            "ALTER TABLE public.stewardship_sealed_credential_staging "
            f"DISABLE TRIGGER {INTAKE}",
            "no longer has its trigger",
        ),
        (
            "ALTER TABLE public.stewardship_secret_request "
            "DISABLE TRIGGER stewardship_secret_request_mutable_guard_v1",
            "no longer has its trigger",
        ),
    ]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(capture)
        cursor.execute(check)
        for change, message in changes:
            # The change is named on failure, so a passing regression is found.
            with (
                pytest.raises(DatabaseError, match=f"Migration 0125.*{message}"),
                transaction.atomic(),
            ):
                cursor.execute(change)
                cursor.execute(check)
                pytest.fail(f"0125's check admitted: {change[:120]}")
        cursor.execute(check)
        transaction.set_rollback(True)

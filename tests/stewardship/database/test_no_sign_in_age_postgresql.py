"""Migration 0114's DO block refuses every earlier or weakened body (#1547).

0114 (``schema/migrations/0114_no_sign_in_age.sql``) drops the sign-in age
from every guard that checked it. Following the in-place SQL rule, its check
runs here against each function's previous body (the latest earlier frozen
file, or the baseline) and against weakened installs, inside a rolled-back
transaction, and must refuse each: each body is pinned exactly (its md5),
so dropping any one of a guard's key clauses (the live, bound session and a
command session's automation session) is refused too. The new definer check
``stewardship_automation_instant_ended_v1`` must keep definer rights, its
body and no PUBLIC EXECUTE. What each guard now admits is covered by that
guard's own tests.
"""

import re
from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

pytestmark = pytest.mark.django_db(transaction=True)

SCHEMA = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
MIGRATIONS = SCHEMA / "migrations"
FROZEN = MIGRATIONS / "0114_no_sign_in_age.sql"


def statement(text, name):
    """One whole ``CREATE [OR REPLACE] FUNCTION`` statement, as a replacement."""
    found = None
    for match in re.finditer(
        rf"^CREATE (?:OR REPLACE )?FUNCTION public\.{name}\(", text, re.M
    ):
        quote = re.compile(r"\bAS\s+(\$\w*\$)").search(text, match.end())
        end = text.index(quote[1] + ";", quote.end()) + len(quote[1]) + 1
        found = text[match.start() : end]
    if found is None:
        return None
    return re.sub(r"^CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", found)


# The definer check 0114 adds (CREATE OR REPLACE only so the file can run
# again); it replaces nothing.
ENDED = "stewardship_automation_instant_ended_v1"
ENDED_REFUSED = f"{ENDED} is not installed"
# Key clauses of the replaced guards, each a whole predicate. Each one found
# in a body (not as part of a longer name) is made always true in turn, a
# weakened install the check must refuse.
KEY_CLAUSES = (
    "login.revoked_at IS NULL",
    "s.revoked_at IS NULL",
    "revoked_at IS NULL",
    "login.expires_at>clock_timestamp()",
    "login.expires_at>stamp",
    "expires_at>stamp",
    "login.last_activity_at>clock_timestamp()-interval '60 minutes'",
    "s.last_activity_at>statement_timestamp()-interval '60 minutes'",
    "login.last_activity_at>stamp-interval '60 minutes'",
    "last_activity_at>stamp-interval '60 minutes'",
    "login.principal_id=NEW.actor_id",
    "login.principal_id=actor",
    "login.principal_id=attempt.owner_id",
    "principal_id=NEW.requested_by_id",
    "login.authenticated_at=NEW.authenticated_at",
    "login.authenticated_at=signed_in",
    "authenticated_at=NEW.reauthenticated_at",
    "s.authenticated_at=NEW.authenticated_at",
    "command.portal_session_id=login.id",
    "command.portal_session_id=own.id",
    "public.stewardship_automation_fresh_v1(login.id,NEW.actor_id)",
    "public.stewardship_automation_fresh_v1(login.id,NEW.cleared_actor_id)",
    "public.stewardship_automation_fresh_v1(login.id,actor)",
    "public.stewardship_automation_fresh_v1(login.id,attempt.owner_id)",
    "public.stewardship_automation_fresh_v1(own.id,NEW.requested_by_id)",
    "event.action='complete'",
)


def replaced_names():
    """The functions 0114 replaces, in file order."""
    return [
        name
        for name in re.findall(
            r"^CREATE OR REPLACE FUNCTION public\.(\w+)\(",
            FROZEN.read_text(encoding="utf-8"),
            re.M,
        )
        if name != ENDED
    ]


def weakened(text):
    """Each replaced body with one key clause made always true."""
    for name in replaced_names():
        body = statement(text, name)
        for clause in KEY_CLAUSES:
            whole = re.compile(rf"(?<![\w.]){re.escape(clause)}")
            if whole.search(body):
                weaker = whole.sub(lambda m: f"({m[0]} OR true)", body, count=1)
                yield weaker, f"{name} is not installed"
    # The command-session rule of every session-bound guard, at once: a
    # command session admitted without its automation session.
    yield (
        statement(text, "stewardship_delivery_control_guard_v1").replace(
            "AND (NOT EXISTS (SELECT 1 FROM public.stewardship_automation_login",
            "AND (EXISTS (SELECT 1 FROM public.stewardship_automation_login",
        ),
        "stewardship_delivery_control_guard_v1 is not installed",
    )
    # The secret request guards with the ended-automation check undone.
    for name in (
        "stewardship_sealed_intake_admission_v1",
        "stewardship_secret_state_v2",
    ):
        body = statement(text, name)
        assert body.count(f"public.{ENDED}(") == 1, name
        yield (
            re.sub(rf"coalesce\(public\.{ENDED}\([^)]*\),false\)", "false", body),
            f"{name} is not installed",
        )


def previous_body(name):
    """The function's body before 0114: its latest earlier frozen copy."""
    body = None
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.name >= FROZEN.name:
            break
        body = statement(path.read_text(encoding="utf-8"), name) or body
    assert body and len(body) > 300, name
    return body


def test_migration_replaces_each_sign_in_age_guard():
    """0114 names the thirteen guards that checked a sign-in's age."""
    assert sorted(replaced_names()) == sorted(
        [
            "stewardship_automation_session_guard_v1",
            "stewardship_backup_request_guard_v1",
            "stewardship_debug_off_switch_guard_v1",
            "stewardship_delivery_control_guard_v1",
            "stewardship_family_mail_test_guard_v1",
            "stewardship_production_confirmation_guard_v1",
            "stewardship_production_withdrawal_guard_v1",
            "stewardship_publication_confirmation_guard_v1",
            "stewardship_restore_decision_admitted_v1",
            "stewardship_sealed_intake_admission_v1",
            "stewardship_secret_state_v2",
            "stewardship_setup_secret_guard_v1",
            "stewardship_source_drop_acceptance_guard_v1",
        ]
    )


def test_installed_guards_have_no_sign_in_age():
    """No installed function still checks a sign-in's age, nor accepts an
    instant because an automation session holds it (the principal helper);
    command sessions still need their live automation session."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT p.proname FROM pg_proc p"
            " JOIN pg_namespace n ON n.oid=p.pronamespace"
            " WHERE n.nspname='public'"
            " AND p.proname NOT LIKE 'stewardship_automation_fresh%%'"
            " AND (p.prosrc LIKE '%%stewardship_automation_fresh_principal%%'"
            " OR p.prosrc ~ %s)",
            [r"authenticated_at\s*(BETWEEN|>=?)\s*[a-z_()]*\s*-\s*interval"],
        )
        assert cursor.fetchall() == []


def test_migration_check_refuses_each_earlier_or_weakened_body():
    """0114's check passes as installed and refuses each regression."""
    text = FROZEN.read_text(encoding="utf-8")
    capture = text[text.index("SELECT set_config(") : text.index("true);") + 6]
    check = text[text.index("DO $check$") : text.index("$check$;") + len("$check$;")]
    changes = [
        (previous_body(name), f"{name} is not installed") for name in replaced_names()
    ]
    changes += list(weakened(text))
    signature = f"public.{ENDED}(uuid,timestamptz)"
    changes += [
        # The ended check as its caller's rights, open to PUBLIC, or always
        # false.
        (f"ALTER FUNCTION {signature} SECURITY INVOKER", ENDED_REFUSED),
        (
            f"GRANT EXECUTE ON FUNCTION {signature} TO PUBLIC",
            ENDED_REFUSED,
        ),
        (
            statement(text, ENDED).replace(
                "    SELECT EXISTS", "    SELECT false AND EXISTS", 1
            ),
            ENDED_REFUSED,
        ),
    ]
    changes += [
        # A definer that runs as its caller, and an invoker made a definer.
        (
            "ALTER FUNCTION public.stewardship_delivery_control_guard_v1() "
            "SECURITY INVOKER",
            "rights, configuration or grants",
        ),
        (
            "ALTER FUNCTION public.stewardship_secret_state_v2() SECURITY DEFINER",
            "rights, configuration or grants",
        ),
        (
            "ALTER FUNCTION public.stewardship_restore_decision_admitted_v1"
            "(uuid,uuid,timestamptz) SET search_path TO public",
            "rights, configuration or grants",
        ),
        (
            "CREATE ROLE pk_s1547_probe NOLOGIN; GRANT EXECUTE ON FUNCTION "
            "public.stewardship_secret_state_v2() TO pk_s1547_probe",
            "rights, configuration or grants",
        ),
        (
            "ALTER TABLE public.stewardship_delivery_control "
            "DISABLE TRIGGER delivery_control_guard",
            "no longer has its trigger",
        ),
    ]
    with transaction.atomic(), connection.cursor() as cursor:
        # 0124 (#1762) has since reworded some of these bodies' refusals, so
        # 0114's own bodies go back first (rolled back below).
        for name in replaced_names():
            cursor.execute(statement(text, name))
        cursor.execute(capture)
        cursor.execute(check)
        for change, message in changes:
            # The change is named on failure, so a passing regression is found.
            with (
                pytest.raises(DatabaseError, match=f"Migration 0114.*{message}"),
                transaction.atomic(),
            ):
                cursor.execute(change)
                cursor.execute(check)
                pytest.fail(f"0114's check admitted: {change[:120]}")
        cursor.execute(check)
        transaction.set_rollback(True)

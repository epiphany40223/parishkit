"""Migration 0113's DO block refuses every earlier or weakened body (#1547).

0113 (``schema/migrations/0113_no_sign_in_age.sql``) drops the sign-in age
from every guard that checked it. Following the in-place SQL rule, its check
runs here against each function's previous body (the latest earlier frozen
file, or the baseline) and against weakened installs, inside a rolled-back
transaction, and must refuse each. What each guard now admits is covered by
that guard's own tests.
"""

import re
from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

pytestmark = pytest.mark.django_db(transaction=True)

SCHEMA = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
MIGRATIONS = SCHEMA / "migrations"
FROZEN = MIGRATIONS / "0113_no_sign_in_age.sql"


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


def replaced_names():
    """The functions 0113 replaces, in file order."""
    return re.findall(
        r"^CREATE OR REPLACE FUNCTION public\.(\w+)\(",
        FROZEN.read_text(encoding="utf-8"),
        re.M,
    )


def previous_body(name):
    """The function's body before 0113: its latest earlier frozen copy."""
    body = None
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.name >= FROZEN.name:
            break
        body = statement(path.read_text(encoding="utf-8"), name) or body
    assert body and len(body) > 300, name
    return body


def test_migration_replaces_each_sign_in_age_guard():
    """0113 names the thirteen guards that checked a sign-in's age."""
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
    """0113's check passes as installed and refuses each regression."""
    text = FROZEN.read_text(encoding="utf-8")
    capture = text[text.index("SELECT set_config(") : text.index("true);") + 6]
    check = text[text.index("DO $check$") : text.index("$check$;") + len("$check$;")]
    changes = [
        (previous_body(name), f"{name} is not installed") for name in replaced_names()
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
        cursor.execute(capture)
        cursor.execute(check)
        for change, message in changes:
            # The change is named on failure, so a passing regression is found.
            with (
                pytest.raises(DatabaseError, match=f"Migration 0113.*{message}"),
                transaction.atomic(),
            ):
                cursor.execute(change)
                cursor.execute(check)
                pytest.fail(f"0113's check admitted: {change[:120]}")
        cursor.execute(check)
        transaction.set_rollback(True)

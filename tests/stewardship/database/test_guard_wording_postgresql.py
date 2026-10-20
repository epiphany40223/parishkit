"""Migration 0124 rewords the guards' sign-in refusals (#1762).

0124 (``schema/migrations/0124_guard_wording.sql``) replaces twelve guard
bodies, each its latest earlier copy with only words changed: ten refusals
that still said a write "requires fresh ... authentication" now name the
live session they check (no guard has checked a sign-in's age since #1547),
and four comments that described a sign-in age or a step-up sign-in are
reworded. Following the in-place SQL rule, the file runs here whole against
the earlier bodies (put back in a rolled-back transaction), and its DO block
must refuse each earlier or weakened install. What each guard admits and
refuses is covered by that guard's own tests, which match the new words.
"""

import hashlib
import re
from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.system_health import SIGNED_OUT_REFUSAL

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "src/parishkit/stewardship/schema/migrations"
FROZEN_PATH = MIGRATIONS / "0124_guard_wording.sql"
FROZEN = FROZEN_PATH.read_text(encoding="utf-8")

# Each refusal's words before 0124, and after.
REWORDED = {
    "A backup request requires fresh Administrator authentication": (
        "A backup request requires a live Administrator session"
    ),
    "The debug-off switch requires fresh Administrator authentication": (
        "The debug-off switch requires a live Administrator session"
    ),
    "Allowing debug logging again requires fresh Administrator authentication": (
        "Allowing debug logging again requires a live Administrator session"
    ),
    "Delivery control requires fresh Admin authentication": (
        "Delivery control requires a live Admin session"
    ),
    "Production confirmation requires fresh post-cleanup Admin authentication": (
        "Production confirmation requires completed cleanup and a live Admin session"
    ),
    "Withdrawal requires fresh Admin authentication": (
        "Withdrawal requires a live Admin session"
    ),
    "Publishing to ParishSoft requires fresh Administrator authentication": (
        "Publishing to ParishSoft requires a live Administrator session"
    ),
    "Sealed intake requires fresh authentication": (
        "Sealed intake requires a live automation session"
    ),
    "Accepting a large ParishSoft change requires fresh Administrator authentication": (
        "Accepting a large ParishSoft change requires a live Administrator session"
    ),
    "A restore decision requires fresh Administrator authentication": (
        "A restore decision requires a live Administrator session"
    ),
}
# Each reworded comment's words before 0124, and after.
COMMENTS = {
    "    -- Settling a held email is an Administrator's decision made within five\n"
    "    -- minutes of a Google sign-in (migration 0017, #537).\n": (
        "    -- Settling a held email is the decision of an Administrator whose\n"
        "    -- session is live (migration 0017, #537; any sign-in age, #1547).\n"
    ),
    "        -- A freshly signed-in Administrator releases the site after review.\n": (
        "        -- An Administrator whose session is live releases the site after "
        "review.\n"
    ),
    "        -- The installer copies the login's instant, but a concurrent step-up\n"
    "        -- may advance that session before this check; never a later claim.\n": (
        "        -- The installer copies the login's instant, which never changes in\n"
        "        -- place (#1550); never a later claim.\n"
    ),
    "        -- was sealed (#1402): a later step-up in the same session overwrites\n"
    "        -- authenticated_at, so the age is recorded now. A separate "
    "statement,\n": (
        "        -- was sealed (#1402), recorded as audit evidence when it is staged\n"
        "        -- (the instant never changes in place, #1550). A separate "
        "statement,\n"
    ),
}
# The one replaced body that had no earlier frozen copy (the baseline's
# setup credential installer check): the md5 of its released body, so the
# rebuilt earlier state is the one Production has.
RELEASED_INSTALL_BINDING_MD5 = "8702829cb5c752393dc136831c8ab140"
# The guards that keep definer rights; the rest run as their caller.
DEFINERS = {
    "stewardship_delivery_control_guard_v1",
    "stewardship_production_confirmation_guard_v1",
    "stewardship_production_withdrawal_guard_v1",
}


def statement(text, name):
    """The last ``CREATE [OR REPLACE] FUNCTION public.name(`` statement, as a
    replacement, or None when the text has none."""
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


def body(replacement):
    """The text between a statement's dollar quotes, as ``pg_proc.prosrc``."""
    start = replacement.index("AS $$") + len("AS $$")
    return replacement[start : replacement.rindex("$$;")]


def replaced_names():
    """The functions 0124 replaces, in file order."""
    return re.findall(r"^CREATE OR REPLACE FUNCTION public\.(\w+)\(", FROZEN, re.M)


def earlier(replacement):
    """A 0124 statement with its old words put back."""
    for before, after in (*REWORDED.items(), *COMMENTS.items()):
        replacement = replacement.replace(after, before)
    return replacement


def previous_copy(name):
    """The function's latest frozen copy before 0124, or None."""
    found = None
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.name >= FROZEN_PATH.name:
            break
        found = statement(path.read_text(encoding="utf-8"), name) or found
    return found


def test_migration_rewords_these_twelve_guards():
    """0124 names the guards with the old words, each once."""
    assert sorted(replaced_names()) == sorted(
        [
            "stewardship_backup_request_guard_v1",
            "stewardship_debug_off_switch_guard_v1",
            "stewardship_delivery_control_guard_v1",
            "stewardship_production_confirmation_guard_v1",
            "stewardship_production_withdrawal_guard_v1",
            "stewardship_publication_confirmation_guard_v1",
            "stewardship_restore_resolution_v1",
            "stewardship_runtime_transition_v1",
            "stewardship_sealed_intake_admission_v1",
            "stewardship_setup_install_binding_v1",
            "stewardship_setup_secret_audit_v1",
            "stewardship_source_drop_acceptance_guard_v1",
        ]
    )


def test_each_body_changes_only_its_words():
    """With the old words put back, each body is its latest earlier copy.

    So no rule, clause or error code changes, and every old refusal and
    comment is reworded in the bodies.
    """
    bodies = "".join(statement(FROZEN, name) for name in replaced_names())
    for before, after in (*REWORDED.items(), *COMMENTS.items()):
        assert after in bodies, after
        assert before not in FROZEN, before
    for name in replaced_names():
        restored = earlier(statement(FROZEN, name))
        assert restored != statement(FROZEN, name), name
        previous = previous_copy(name)
        if previous is None:
            assert name == "stewardship_setup_install_binding_v1"
            digest = hashlib.md5(body(restored).encode()).hexdigest()
            assert digest == RELEASED_INSTALL_BINDING_MD5
        else:
            assert restored == previous, name


def test_system_health_matches_the_new_words():
    """The page tells a signed-out Administrator so for each of its actions."""
    for before, words in REWORDED.items():
        if before.startswith(("A backup request", "The debug-off", "Allowing debug")):
            assert SIGNED_OUT_REFUSAL in words
            assert f"'{words}'" in FROZEN, words


@pytest.mark.django_db(transaction=True)
def test_installed_guards_carry_the_new_words():
    """Every new refusal is installed, and no function keeps the old words."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT string_agg(p.prosrc, ' ') FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'"
        )
        installed = cursor.fetchone()[0]
    for words in REWORDED.values():
        assert f"'{words}'" in installed, words
    assert not re.search(r"requires fresh [^']*authentication", installed)
    assert "step-up" not in installed


@pytest.mark.django_db(transaction=True)
def test_the_file_runs_on_the_earlier_bodies_and_again():
    """On the earlier bodies the whole file installs; it can run again."""
    with transaction.atomic(), connection.cursor() as cursor:
        for name in replaced_names():
            cursor.execute(earlier(statement(FROZEN, name)))
        cursor.execute(
            "SELECT count(*) FROM pg_proc WHERE prosrc LIKE %s",
            ["%requires fresh Administrator authentication%"],
        )
        assert cursor.fetchone()[0] == 6
        cursor.execute(FROZEN)
        cursor.execute(FROZEN)
        cursor.execute(
            "SELECT proname, prosecdef FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='public' AND proname = ANY(%s)",
            [replaced_names()],
        )
        assert {name for name, definer in cursor.fetchall() if definer} == DEFINERS
        transaction.set_rollback(True)


@pytest.mark.django_db(transaction=True)
def test_migration_check_refuses_each_earlier_or_weakened_install():
    """0124's check passes as installed and refuses each regression."""
    # Both captures: attributes, then enabled triggers.
    capture = FROZEN[
        FROZEN.index("SELECT set_config(") : FROZEN.index("\n\n-- stewardship_backup")
    ]
    assert capture.count("SELECT set_config(") == 2
    check = FROZEN[
        FROZEN.index("DO $check$") : FROZEN.index("$check$;") + len("$check$;")
    ]
    declared = "is not installed as declared"
    changes = [
        (earlier(statement(FROZEN, name)), f"{name} {declared}")
        for name in replaced_names()
    ]
    backup = statement(FROZEN, "stewardship_backup_request_guard_v1")
    changes += [
        # A key clause made always true.
        (
            backup.replace(
                "login.revoked_at IS NULL", "(login.revoked_at IS NULL OR true)", 1
            ),
            f"stewardship_backup_request_guard_v1 {declared}",
        ),
        # A definer that runs as its caller, an invoker made a definer, and
        # another search_path.
        (
            "ALTER FUNCTION public.stewardship_delivery_control_guard_v1() "
            "SECURITY INVOKER",
            f"stewardship_delivery_control_guard_v1 {declared}",
        ),
        (
            "ALTER FUNCTION public.stewardship_backup_request_guard_v1() "
            "SECURITY DEFINER",
            f"stewardship_backup_request_guard_v1 {declared}",
        ),
        (
            "ALTER FUNCTION public.stewardship_restore_resolution_v1() "
            "SET search_path TO public",
            f"stewardship_restore_resolution_v1 {declared}",
        ),
        # A new grant.
        (
            "CREATE ROLE pk_s1762_probe NOLOGIN; GRANT EXECUTE ON FUNCTION "
            "public.stewardship_sealed_intake_admission_v1() TO pk_s1762_probe",
            "rights, configuration or grants",
        ),
        # Another owner, whose rights a definer would run with.
        (
            "CREATE ROLE pk_s1762_owner NOLOGIN; ALTER FUNCTION "
            "public.stewardship_delivery_control_guard_v1() OWNER TO pk_s1762_owner",
            "rights, configuration or grants",
        ),
        # A disabled trigger.
        (
            "ALTER TABLE public.stewardship_delivery_control "
            "DISABLE TRIGGER delivery_control_guard",
            "no longer has its trigger",
        ),
        # One of the sealed intake check's two triggers disabled.
        (
            "ALTER TABLE public.stewardship_secret_request "
            "DISABLE TRIGGER stewardship_sealed_intake_admission_v1",
            "no longer has its trigger",
        ),
        # Another function that still raises the old words.
        (
            "CREATE FUNCTION public.pk_s1762_probe() RETURNS void LANGUAGE plpgsql "
            "AS $$ BEGIN RAISE EXCEPTION 'X requires fresh Admin authentication'; "
            "END $$",
            "still asks for fresh authentication",
        ),
    ]
    with transaction.atomic(), connection.cursor() as cursor:
        # 0125 (#1763) has since replaced the sealed intake check, and report
        # emails' 0191 (#1786) the delivery control guard, so 0124's own
        # bodies go back first (rolled back below); CREATE OR REPLACE drops
        # the guard's SECURITY DEFINER, which 0124 sets again too.
        cursor.execute(statement(FROZEN, "stewardship_sealed_intake_admission_v1"))
        cursor.execute(statement(FROZEN, "stewardship_delivery_control_guard_v1"))
        cursor.execute(
            "ALTER FUNCTION public.stewardship_delivery_control_guard_v1() "
            "SECURITY DEFINER"
        )
        cursor.execute(capture)
        cursor.execute(check)
        for change, message in changes:
            # The change is named on failure, so a passing regression is found.
            with (
                pytest.raises(DatabaseError, match=f"Migration 0124.*{message}"),
                transaction.atomic(),
            ):
                cursor.execute(change)
                cursor.execute(check)
                pytest.fail(f"0124's check admitted: {change[:120]}")
        cursor.execute(check)
        transaction.set_rollback(True)

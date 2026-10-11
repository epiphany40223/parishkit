"""Sign-in reads only users once the swap migration ran (#952 slice 4).

Migration 0052 replaces the Admin session admission, export authorization,
Ministry scope, role-derived leader scope and policy activation so that none
reads hosted-domain rules. Its DO block refuses an unconverted configuration,
so the offline upgrade converts first (``upgrade-users``) on the schema
``migrate --users-upgrade-base`` leaves, and Admin-access recovery must work
in every state of that upgrade (D2). The test database is a fresh install,
so the swap is already applied; a pending swap is made for the length of one
test by removing its ``django_migrations`` row and reinstalling the real
pre-swap bodies of the functions it replaces (``swap_pending``).
"""

import json
import re
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.config import ConfigError
from parishkit.stewardship import operator_commands, runtime_database
from parishkit.stewardship.accounts import users_upgrade as upgrade
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.users_schema import SCHEMA
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.runtime_database import USERS_SWAP

from .campaign_builders import initialized
from .test_background_grants_postgresql import task_login
from .test_portal_identity_guards_postgresql import forge, refused
from .test_users_upgrade_postgresql import (
    ADMINS,
    _offline,
    active_users,
    arguments,
    identity,
    rules,
)

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
SWAP = (MIGRATIONS / "migrations/0052_users_swap.sql").read_text(encoding="utf-8")
# The file's closing self-check, run on its own against chosen states.
CHECK = SWAP[SWAP.index("DO $check$") :]


def scalar(sql, params=()):
    """One value from the test's own (superuser) connection."""
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()[0]


def authorized(portal, admin_only=False):
    """What export authorization answers for one portal user."""
    return scalar(
        "SELECT stewardship_export_authorized_v1(%s, %s)", [portal.pk, admin_only]
    )


def ministry_scope(portal):
    """The Ministry report scope SQL derives for one portal user, or None."""
    return scalar("SELECT stewardship_ministry_scope_v1(%s)", [portal.pk])


def check_refuses(message):
    """Run the DO block in a savepoint, expecting it to refuse with ``message``."""
    with (
        pytest.raises(DatabaseError, match=message),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(CHECK)


def check_passes():
    """Run the DO block in a savepoint that is always rolled back."""
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(CHECK)
        transaction.set_rollback(True)


def test_sign_in_reads_only_users_and_every_administrator_keeps_access(tmp_path):
    """Every Administrator, and the domain colleague, keeps access through the swap.

    Before the conversion and during it (converted, swap still pending) the
    pre-swap SQL runs, as `migrate --users-upgrade-base` leaves it: the
    hosted-domain colleague who signed in is admitted through the domain
    rule, then as the Staff user the conversion makes. After the swap the
    new SQL admits that user too. Both Administrators are Administrators in
    every state. The new bodies alone, on an unconverted configuration (a
    state the swap's check never commits), grant nothing from a domain rule.
    """
    store, _, _ = initialized(tmp_path, rules())
    portals = {email: identity(email) for email in (*ADMINS, "signed@example.org")}
    colleague = portals["signed@example.org"]

    def admitted(*, staff_scope):
        """Admin session, export and Ministry scope for every portal user."""
        for email, portal in portals.items():
            assert forge(portal.pk).principal_id == portal.pk, email
            assert authorized(portal) is True, email
            assert authorized(portal, admin_only=True) is (email in ADMINS), email
        assert json.loads(ministry_scope(colleague)) == {
            "capability": "ministry_report",
            "operational": staff_scope,
            "ministries": [],
        }

    # The new bodies: the domain rule grants nothing in SQL.
    with refused():
        forge(colleague.pk)
    assert authorized(colleague) is False
    assert ministry_scope(colleague) is None
    with swap_pending():
        assert all(reads_domain_rules(name) for name in REPLACED)
        # Before the conversion, on the pre-swap SQL: the domain rule
        # (Ministry leader and Staff) admits the colleague.
        admitted(staff_scope=True)
        assert upgrade.upgrade_users(store, **arguments()).state == "applied"
        assert active_users(store)["signed@example.org"] == ("signed", "staff")
        # Converted, swap still pending: the same SQL reads the users.
        admitted(staff_scope=True)
    # After the swap: the new SQL admits every user the same way.
    assert not any(reads_domain_rules(name) for name in REPLACED)
    admitted(staff_scope=True)


def test_the_swap_refuses_an_unconverted_configuration(tmp_path):
    """The DO block passes on a fresh install, in setup and once converted."""
    # A fresh install, before bootstrap: no configuration to convert.
    assert not SystemConfiguration.objects.exists()
    check_passes()
    store, _, _ = initialized(tmp_path, rules())
    check_refuses("still holds sign-in rules; run upgrade-users first")
    runtime = SystemConfiguration.objects.get()
    # A deployment still in initial setup is exempt exactly as startup
    # exempts it; nothing past setup passes as one.
    for state, passes in (
        (dict(mode="testing", restore_review_required=False), True),
        (dict(mode="production", restore_review_required=False), False),
        (dict(mode="testing", restore_review_required=True), False),
    ):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL session_replication_role = replica")
                cursor.execute(
                    "UPDATE stewardship_configuration_version "
                    "SET validation_schema='bootstrap-policy-v1' WHERE id=%s",
                    [runtime.active_configuration_id],
                )
                cursor.execute(
                    "UPDATE stewardship_system_configuration SET mode=%s, "
                    "restore_review_required=%s, current_campaign_id=NULL",
                    [state["mode"], state["restore_review_required"]],
                )
                cursor.execute("SET LOCAL session_replication_role = origin")
            if passes:
                check_passes()
            else:
                check_refuses("still holds sign-in rules")
            transaction.set_rollback(True)
    assert upgrade.upgrade_users(store, **arguments()).state == "applied"
    assert SystemConfiguration.objects.get().active_configuration.validation_schema == (
        SCHEMA
    )
    check_passes()


# The three replaced functions whose earlier bodies are in no frozen
# migration, frozen as a test fixture from the baseline before the swap.
PRE_SWAP = Path(__file__).with_name("pre_users_swap_functions.sql")
REPLACED = (
    "stewardship_portal_session_admission_v1",
    "stewardship_export_authorized_v1",
    "stewardship_ministry_scope_v1",
    "stewardship_ministry_leader_scope_v1",
    "stewardship_policy_activation_v1",
)


def old_definitions():
    """Each replaced function's real definition before the swap, by name.

    The Ministry scope and the leader scope (SECURITY DEFINER in its own
    text) come from migration 0042, the latest frozen file before 0052 that
    defines them. The session guard, export authorization and policy
    activation come from the frozen fixture copied from the baseline before
    0052 replaced them; nothing between created or replaced any of the five.
    """
    sources = (
        MIGRATIONS / "migrations/0042_ministry_leaders_from_roles.sql"
    ).read_text(encoding="utf-8") + PRE_SWAP.read_text(encoding="utf-8")
    definitions = {}
    for name in REPLACED:
        match = re.search(
            rf"^CREATE (?:OR REPLACE )?FUNCTION public\.{name}\(.*?\$\$;$",
            sources,
            re.S | re.M,
        )
        definitions[name] = match.group(0).replace(
            "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
        )
    return definitions


@pytest.mark.parametrize("name", REPLACED)
def test_the_swap_check_refuses_each_old_definition(tmp_path, name):
    """With any one function still reading hosted domains, the swap refuses.

    The leader scope is also refused once it stops being SECURITY DEFINER,
    which a CREATE OR REPLACE without the attribute would do.
    """
    store, _, _ = initialized(tmp_path, rules())
    assert upgrade.upgrade_users(store, **arguments()).state == "applied"
    check_passes()
    old = old_definitions()[name]
    assert "domain_rule" in old
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(old)
        check_refuses("a replaced function is not installed as declared")
        transaction.set_rollback(True)
    if name == "stewardship_ministry_leader_scope_v1":
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "ALTER FUNCTION public.stewardship_ministry_leader_scope_v1(uuid) "
                    "SECURITY INVOKER"
                )
            check_refuses("a replaced function is not installed as declared")
            transaction.set_rollback(True)
    check_passes()


@contextmanager
def swap_pending():
    """Put the database in the state `migrate --users-upgrade-base` leaves.

    The swap's ``django_migrations`` row is removed and the five functions
    it replaces get their real earlier bodies back (``old_definitions``),
    so sign-in, export authorization and recovery run on the SQL a
    production database has between the base migration and the full one.
    Afterwards the swap's own statements (everything before its closing
    check) and its row are put back: a transactional test's flush touches
    neither function bodies nor that table, so later tests would otherwise
    see this state.
    """
    app, name = USERS_SWAP
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM django_migrations WHERE app=%s AND name=%s RETURNING applied",
            [app, name],
        )
        (applied,) = cursor.fetchone()
        for definition in old_definitions().values():
            cursor.execute(definition)
    try:
        yield
    finally:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            cursor.execute(SWAP[: SWAP.index("DO $check$")])
            cursor.execute(
                "INSERT INTO django_migrations (app, name, applied) "
                "VALUES (%s, %s, %s)",
                [app, name, applied],
            )


def reads_domain_rules(name):
    """Whether the installed body of one replaced function names domain rules."""
    return "stewardship_domain_rule" in scalar(
        "SELECT prosrc FROM pg_proc WHERE oid=%s::regproc", [f"public.{name}"]
    )


def test_only_the_swap_closure_may_be_pending_for_recovery():
    """The relaxed check admits exactly the swap pending, nothing else."""
    runtime_database.require_current_schema()
    with swap_pending():
        with pytest.raises(ConfigError, match="do not match"):
            runtime_database.require_current_schema()
        runtime_database.require_current_schema(users_swap_pending=True)
    # Any other pending migration still refuses, relaxed or not: a leaf of
    # another app is outside the swap's closure.
    from django.db.migrations.loader import MigrationLoader

    other = next(
        key
        for key in sorted(MigrationLoader(connection).graph.leaf_nodes())
        if key[0] != USERS_SWAP[0]
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM django_migrations WHERE app=%s AND name=%s RETURNING applied",
            list(other),
        )
        (applied,) = cursor.fetchone()
    try:
        with pytest.raises(ConfigError, match="do not match"):
            runtime_database.require_current_schema(users_swap_pending=True)
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO django_migrations (app, name, applied) "
                "VALUES (%s, %s, %s)",
                [*other, applied],
            )


def offline_commands(monkeypatch, store):
    """Run the operator commands in this process, admitting the real schema state.

    Process admission, the interlock and the capacity and TEMP checks are
    stubbed (covered elsewhere); require_current_schema is not, so the
    commands' own choice of what may be pending is what is tested.
    """
    configuration = SimpleNamespace(
        service_role=ServiceRole.ADMIN_RECOVERY, paths={"authority": store.root}
    )
    monkeypatch.setattr(
        operator_commands,
        "admit_offline_service",
        lambda configuration: ServiceRole.ADMIN_RECOVERY,
    )
    monkeypatch.setattr(
        operator_commands, "configure_operator_database", lambda configuration: None
    )
    monkeypatch.setattr(operator_commands, "StartupLease", lambda *a, **k: _offline())
    monkeypatch.setattr(
        operator_commands, "RuntimeLayout", lambda _: SimpleNamespace(interlock=None)
    )
    for name in (
        "require_no_temporary_authority",
        "require_capacity",
        "require_role_capacity",
    ):
        monkeypatch.setattr(runtime_database, name, lambda *a, **k: None)
    return configuration


def recover(configuration, deployment, email):
    """Recover Administrator access for ``email`` through the real command."""
    return operator_commands.recover_admin_command(
        configuration,
        deployment_id=deployment,
        operation_id=str(uuid4()),
        target_email=email,
        confirmed_email=email,
        operator_name="Test operator",
        reason="Lost administration access",
    )


def test_recovery_works_before_during_and_after_the_conversion(tmp_path, monkeypatch):
    """D2: recovery and the upgrade run with the swap pending, and after it.

    With only the swap pending and the pre-swap SQL installed, as the
    upgrade's base migration leaves the database, recover-admin adds an
    Administrator rule, which opens an Admin session at once; upgrade-users
    then converts with that Administrator confirmed too; recovery still runs
    on the converted configuration before the swap, and every Administrator
    so far opens an Admin session through the pre-swap guard. Once the swap
    is applied recovery still runs, and every Administrator recovered on the
    way opens an Admin session through the swapped guard.
    """
    store, _, _ = initialized(tmp_path, rules())
    deployment = str(SystemConfiguration.objects.get().pk)
    configuration = offline_commands(monkeypatch, store)

    portals = {}

    def sessions(*emails):
        """Each address is an Administrator user who can open an Admin session."""
        users = active_users(store)
        for email in emails:
            assert users[email][1] == "administrator", email
            if email not in portals:
                portals[email] = identity(email, signed_in=False)
            assert forge(portals[email].pk).principal_id == portals[email].pk, email

    with swap_pending():
        with task_login(ServiceRole.ADMIN_RECOVERY):
            # Before the conversion, with only the swap pending.
            preview = operator_commands.preview_recovery_command(
                configuration,
                deployment_id=deployment,
                target_email="early@example.org",
            )
            assert preview["recovery_preview"]
            assert recover(configuration, deployment, "early@example.org")[
                "recovery_applied"
            ]
        # The just-recovered Administrator signs in on the pre-swap SQL.
        early = portals["early@example.org"] = identity(
            "early@example.org", signed_in=False
        )
        assert forge(early.pk).principal_id == early.pk
        with task_login(ServiceRole.ADMIN_RECOVERY):
            result = operator_commands.upgrade_users_command(
                configuration,
                deployment_id=deployment,
                preview=False,
                operation_id=str(uuid4()),
                operator_name="Test operator",
                reason="Users revamp",
                confirmed_administrators=",".join(
                    sorted([*ADMINS, "early@example.org"])
                ),
            )
            assert result["users_upgrade_applied"] is True
            # Converted, swap still pending: recovery writes a users form.
            assert recover(configuration, deployment, "middle@example.org")[
                "recovery_applied"
            ]
        # The pre-swap SQL admits every Administrator of the users form.
        sessions(*ADMINS, "early@example.org", "middle@example.org")
    # The swap applied (the full migration ran): recovery still runs.
    with task_login(ServiceRole.ADMIN_RECOVERY):
        assert recover(configuration, deployment, "late@example.org")[
            "recovery_applied"
        ]
        # A converted configuration is left alone by a repeat.
        assert operator_commands.upgrade_users_command(
            configuration,
            deployment_id=deployment,
            preview=False,
            operation_id=str(uuid4()),
            operator_name="Test operator",
            reason="Users revamp",
            confirmed_administrators="admin@example.org",
        ) == {"users_upgrade_applied": False, "already_users": True}
    sessions(*ADMINS, "early@example.org", "middle@example.org", "late@example.org")

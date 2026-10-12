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
import sys
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from psycopg import sql as psycopg_sql

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


# Objects a later closure migration's frozen file creates with plain CREATE,
# which a full migration would otherwise fail to create again: #970's 0056
# and #995's 0057 follow 0055 (#993), so they are in the swap's closure too.
# Each is dropped while the swap is pending (with the trigger that uses it)
# and, unless the test's full migration made it again, installed again
# afterwards from its migration's frozen file.
CLOSURE_CREATED = {
    "public.stewardship_limitations_report_v1(uuid,jsonb)": (
        "stewardship_reports",
        "0013_retire_talents",
    ),
    "public.stewardship_admin_session_key_v1(text)": (
        "stewardship_accounts",
        "0017_admin_session_key_digests",
    ),
    "public.stewardship_portal_session_key_digest_v1()": (
        "stewardship_accounts",
        "0017_admin_session_key_digests",
    ),
}


@contextmanager
def swap_pending():
    """Put the database in the state `migrate --users-upgrade-base` leaves.

    The ``django_migrations`` rows of the swap and of every migration that
    depends on it are removed and the five functions
    it replaces get their real earlier bodies back (``old_definitions``),
    so sign-in, export authorization and recovery run on the SQL a
    production database has between the base migration and the full one.
    Afterwards the swap's own statements (everything before its closing
    check) and its row are put back: a transactional test's flush touches
    neither function bodies nor that table, so later tests would otherwise
    see this state. Each ``CLOSURE_CREATED`` object is dropped meanwhile.
    """
    from django.db.migrations.loader import MigrationLoader

    # The swap and every later migration that depends on it are pending
    # then (a later one, such as 0053's Name lookup, keeps its objects:
    # nothing here reads them).
    loader = MigrationLoader(connection)
    closure = sorted(runtime_database.users_swap_closure(loader.graph))
    assert set(CLOSURE_CREATED.values()) <= set(closure)
    removed = []
    with transaction.atomic(), connection.cursor() as cursor:
        for app, name in closure:
            cursor.execute(
                "DELETE FROM django_migrations WHERE app=%s AND name=%s "
                "RETURNING applied",
                [app, name],
            )
            removed.extend((app, name, applied) for (applied,) in cursor.fetchall())
        assert USERS_SWAP in {(app, name) for app, name, _ in removed}
        for definition in old_definitions().values():
            cursor.execute(definition)
        for function in CLOSURE_CREATED:
            cursor.execute(f"DROP FUNCTION {function} CASCADE")
    try:
        yield
    finally:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            cursor.execute(SWAP[: SWAP.index("DO $check$")])
            for function, key in CLOSURE_CREATED.items():
                cursor.execute("SELECT to_regprocedure(%s)", [function])
                if cursor.fetchone()[0] is None:
                    module = sys.modules[loader.disk_migrations[key].__module__]
                    cursor.execute(Path(module.FROZEN_SQL).read_text(encoding="utf-8"))
            for app, name, applied in removed:
                # A test that ran the full migration recorded the row again.
                cursor.execute(
                    "INSERT INTO django_migrations (app, name, applied) "
                    "SELECT %s, %s, %s WHERE NOT EXISTS (SELECT 1 FROM "
                    "django_migrations WHERE app=%s AND name=%s)",
                    [app, name, applied, app, name],
                )


@contextmanager
def closure_functions_absent():
    """Drop each routine the swap's closure creates, as the base lacks them.

    ``swap_pending`` keeps them (nothing there reads them), but
    database-grants on the users upgrade's base must not name them (#952).
    Afterwards each is installed again from its migration's frozen file,
    unless a full migration in the test already did.
    """
    from django.db.migrations.loader import MigrationLoader

    from parishkit.stewardship.runtime_grants import CLOSURE_FUNCTIONS

    migrations = MigrationLoader(connection).disk_migrations
    with connection.cursor() as cursor:
        for function in CLOSURE_FUNCTIONS:
            cursor.execute(f"DROP FUNCTION public.{function}")
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            for function, key in CLOSURE_FUNCTIONS.items():
                cursor.execute("SELECT to_regprocedure(%s)", [f"public.{function}"])
                if cursor.fetchone()[0] is None:
                    module = sys.modules[migrations[key].__module__]
                    cursor.execute(Path(module.FROZEN_SQL).read_text(encoding="utf-8"))


@contextmanager
def provisioned_logins(monkeypatch):
    """Every runtime login, empty, and the real database-grants to fill them.

    Yields a function that runs ``provision_grants`` on this test's own
    connection as the operator would. Its operator, marker and role checks
    are stubbed (the Docker provisioning test covers them); the grants
    themselves, the writer-guard check and the existing-grant checks run
    for real. The logins are dropped afterwards.
    """
    from parishkit.stewardship import database_provisioning as provisioning
    from parishkit.stewardship.runtime_identities import database_identities

    logins = [
        (login, role)
        for _, login, role, _ in database_identities()
        if role is not ServiceRole.MIGRATION
    ]
    with connection.cursor() as cursor:
        for login, role in logins:
            name = psycopg_sql.Identifier(login)
            cursor.execute(
                psycopg_sql.SQL("CREATE ROLE {} LOGIN NOINHERIT").format(name)
            )
            cursor.execute(
                psycopg_sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(name)
            )
            if role is ServiceRole.BACKUP_WORKER:
                cursor.execute(
                    psycopg_sql.SQL("GRANT pg_read_all_data TO {}").format(name)
                )
    for name in ("_password", "_admit_operator", "role_limit"):
        monkeypatch.setattr(provisioning, name, lambda *a, **k: None)
    monkeypatch.setattr(provisioning, "_check_role", lambda *a, **k: True)
    connection.ensure_connection()
    monkeypatch.setattr(
        provisioning, "_connection", lambda *a: nullcontext(connection.connection)
    )
    configuration = SimpleNamespace(postgres=SimpleNamespace(password_file=None))

    def grants():
        """Run database-grants as the operator; its result."""
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
        return provisioning.provision_grants(configuration, uuid4())

    try:
        yield grants
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            for login, _ in logins:
                name = psycopg_sql.Identifier(login)
                cursor.execute(psycopg_sql.SQL("DROP OWNED BY {}").format(name))
                cursor.execute(psycopg_sql.SQL("DROP ROLE {}").format(name))


def test_database_grants_wait_for_the_swap_closures_functions(tmp_path, monkeypatch):
    """The users upgrade's order: base grants, conversion, migration, grants.

    On the base (the swap's closure pending, the routines it adds absent)
    database-grants succeeds without them; the conversion runs as the
    admin-recovery login with exactly the grants it gave; the full
    migration then installs the swap and the Name lookup, and the grants
    again give web the lookup (#952).
    """
    from django.core.management import call_command

    from parishkit.stewardship.runtime_grants import (
        CLOSURE_FUNCTIONS,
        MEMBER_NAMES_FOR_FUNCTION,
    )

    lookup = f"public.{MEMBER_NAMES_FOR_FUNCTION}"
    store, _, _ = initialized(tmp_path, rules())
    deployment = str(SystemConfiguration.objects.get().pk)
    configuration = offline_commands(monkeypatch, store)
    with (
        provisioned_logins(monkeypatch) as grants,
        swap_pending(),
        closure_functions_absent(),
    ):
        assert scalar("SELECT to_regprocedure(%s)", [lookup]) is None
        assert grants() == {"database_grants_provisioned": True}
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION AUTHORIZATION pk_stewardship_admin_recovery")
        result = operator_commands.upgrade_users_command(
            configuration,
            deployment_id=deployment,
            preview=False,
            operation_id=str(uuid4()),
            operator_name="Test operator",
            reason="Users revamp",
            confirmed_administrators=",".join(ADMINS),
        )
        assert result["users_upgrade_applied"] is True
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
        call_command("migrate", interactive=False, verbosity=0)
        runtime_database.require_current_schema()
        with connection.cursor() as cursor:
            cursor.execute("SELECT app, name FROM django_migrations")
            assert set(CLOSURE_FUNCTIONS.values()) <= set(cursor.fetchall())
        assert grants() == {"database_grants_provisioned": True}
        assert scalar(
            "SELECT has_function_privilege('pk_stewardship_web', %s, 'EXECUTE')",
            [lookup],
        )


def catalog(cursor):
    """Every routine and relation in ``public``, by oid, with how to name it.

    Routines (functions and procedures, every overload) are named by their
    full ``regprocedure`` signature; relations (tables, views, sequences
    and the rest) by kind and name.
    """
    cursor.execute(
        "SELECT oid, oid::regprocedure::text FROM pg_proc "
        "WHERE pronamespace='public'::regnamespace"
    )
    routines = dict(cursor.fetchall())
    cursor.execute(
        "SELECT oid, relkind::text || ' ' || relname FROM pg_class "
        "WHERE relnamespace='public'::regnamespace"
    )
    return routines, dict(cursor.fetchall())


def test_closure_functions_list_every_granted_routine_the_swap_closure_adds(
    tmp_path,
):
    """No grant names an object only the users swap's closure creates, unlisted.

    A scratch database is migrated as ``migrate --users-upgrade-base`` would
    (every app to its target before the swap), then through each closure
    migration in order, and the catalog is diffed after each step, so
    whatever SQL spelling a migration uses (quoting, an unqualified name,
    IF NOT EXISTS, an overload, a view or a procedure) the objects it adds
    are what PostgreSQL reports. A granted routine among them must be in
    CLOSURE_FUNCTIONS with the migration that adds it, so database-grants
    waits for it on the base (#952); a granted table or view has no such
    wait, so none may be among them. Registry names are resolved by the
    database too, so type spellings need not match.
    """
    import psycopg
    from django.db.migrations.loader import MigrationLoader

    from parishkit.stewardship.operator_commands import users_upgrade_targets
    from parishkit.stewardship.runtime_grants import (
        CLOSURE_FUNCTIONS,
        MEMBER_NAMES_FOR_FUNCTION,
        runtime_functions,
        runtime_grants,
    )
    from parishkit.stewardship.runtime_identities import database_identities

    from .test_upgrade_parity_postgresql import (
        ROOT,
        SETTINGS,
        database_parameters,
        drop_database,
        migrate,
    )

    loader = MigrationLoader(None, ignore_no_migrations=True)
    closure = runtime_database.users_swap_closure(loader.graph)
    # The closure in an order that applies each migration after its parents.
    steps = []
    for leaf in sorted(loader.graph.leaf_nodes()):
        steps += [
            key
            for key in loader.graph.forwards_plan(leaf)
            if key in closure and key not in steps
        ]
    assert USERS_SWAP in steps and set(CLOSURE_FUNCTIONS.values()) <= closure
    settings_directory = tmp_path / "settings"
    settings_directory.mkdir()
    (settings_directory / "upgrade_parity_settings.py").write_text(SETTINGS)
    source = ROOT / "src"
    scratch = f"{connection.settings_dict['NAME']}_closure"
    with psycopg.connect(
        **database_parameters(connection.settings_dict["NAME"]), autocommit=True
    ) as maintenance:
        drop_database(maintenance, scratch)
        maintenance.execute(f'CREATE DATABASE "{scratch}"')
        try:
            for target in users_upgrade_targets(loader):
                migrate(source, settings_directory, scratch, "base", target)
            added_routines, added_relations = {}, {}
            with psycopg.connect(**database_parameters(scratch)) as database:
                routines, relations = catalog(database.cursor())
                database.rollback()
                for key in steps:
                    migrate(source, settings_directory, scratch, "closure", key)
                    after_routines, after_relations = catalog(database.cursor())
                    database.rollback()
                    for oid in after_routines.keys() - routines.keys():
                        added_routines[oid] = (after_routines[oid], key)
                    for oid in after_relations.keys() - relations.keys():
                        added_relations[oid] = (after_relations[oid], key)
                    routines, relations = after_routines, after_relations

                def resolve(kind, name):
                    """The oid the registry's name resolves to, or None."""
                    cursor = database.cursor()
                    cursor.execute(f"SELECT {kind}(%s)::oid", [f"public.{name}"])
                    return cursor.fetchone()[0]

                lookup = resolve("to_regprocedure", MEMBER_NAMES_FOR_FUNCTION)
                assert lookup in added_routines
                for _, _, role, target in database_identities():
                    if role is ServiceRole.MIGRATION:
                        continue
                    for function in runtime_functions(role, target=target):
                        oid = resolve("to_regprocedure", function)
                        assert oid is not None, function
                        key = added_routines.get(oid, (None, None))[1]
                        assert CLOSURE_FUNCTIONS.get(function) == key, function
                    tables, columns = runtime_grants(role, target=target)
                    for table in tables.keys() | columns.keys():
                        oid = resolve("to_regclass", table)
                        assert oid not in added_relations, added_relations[oid]
        finally:
            drop_database(maintenance, scratch)


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


def backup_command(monkeypatch, run_backup):
    """Run `backup` in this process with ``run_backup`` standing in for the set.

    Process admission, the interlock, the identity check and the off-site
    copy are stubbed (covered elsewhere); the schema check is the real one.
    """
    from contextlib import nullcontext

    from parishkit.stewardship import (
        backup,
        backup_boundaries,
        backup_commands,
        runtime_paths,
        startup_interlock,
    )

    monkeypatch.setattr(backup_commands, "configure_logging", lambda: None)
    monkeypatch.setattr(backup_commands, "load_deployment", lambda path: None)
    monkeypatch.setattr(backup_commands, "_admit_backup_identity", lambda: None)
    monkeypatch.setattr(backup_commands, "_copy_offsite", lambda c: {"state": "x"})
    monkeypatch.setattr(backup_boundaries, "admit_backup_service", lambda c: None)
    monkeypatch.setattr(
        operator_commands, "configure_operator_database", lambda c: None
    )
    monkeypatch.setattr(
        runtime_paths, "RuntimeLayout", lambda c: SimpleNamespace(interlock=None)
    )
    monkeypatch.setattr(
        startup_interlock, "StartupLease", lambda path, offline: nullcontext()
    )
    monkeypatch.setattr(backup, "run_backup", run_backup)


def test_the_backup_admits_a_database_with_only_the_swap_pending(
    tmp_path, monkeypatch, capsys
):
    """A repeated deploy's step-1 backup runs between the two migrations (#952).

    The schema check is the real one: with only the swap pending the backup
    records its set, whose manifest lists every applied migration but the
    swap, so it restores exactly that state.
    """
    from parishkit.stewardship.cli import main

    from .test_backup_postgresql import FACTS, backup_login

    initialized(tmp_path, rules())
    listed = []

    def run_backup(configuration, *, record, migrations, recipient):
        """Record one run and keep the migration list the manifest would carry."""
        listed.extend(migrations)
        record(started_at=scalar("SELECT statement_timestamp()"), **FACTS)
        return {
            "database": {"plaintext_bytes": 1},
            "files": {"plaintext_bytes": 1},
            "recipient_fingerprint": FACTS["recipient_fingerprint"],
        }

    backup_command(monkeypatch, run_backup)
    with swap_pending(), backup_login():
        assert main(["backup", "--config", "unused"]) == 0
    assert json.loads(capsys.readouterr().out)["backup_recorded"] is True
    assert listed and USERS_SWAP not in set(map(tuple, listed))


@contextmanager
def restored_copy():
    """A restore of the test database as it is now, as a second database.

    pg_dump and pg_restore are not on the test runner (and the CI runner's
    cannot read a PostgreSQL 18 dump; test_restore_compare_postgresql.py),
    so the server copies the database instead (CREATE DATABASE ... TEMPLATE):
    every table row, function body and grant comes back as a restore brings
    them back. While the context is open the test's connection uses the
    copy; afterwards it is back on the test database and the copy is gone.
    """
    import psycopg

    settings = connection.settings_dict
    original = settings["NAME"]
    copy = f"{original}_restored"
    server = {
        "host": settings["HOST"],
        "port": settings["PORT"],
        "user": settings["USER"],
        "password": settings["PASSWORD"],
        "dbname": "postgres",
        "autocommit": True,
    }
    # A template copy needs no other session on its source.
    connection.close()
    with psycopg.connect(**server) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{copy}"')
        admin.execute(f'CREATE DATABASE "{copy}" TEMPLATE "{original}"')
    settings["NAME"] = copy
    try:
        yield
    finally:
        connection.close()
        settings["NAME"] = original
        with psycopg.connect(**server) as admin:
            admin.execute(f'DROP DATABASE "{copy}" WITH (FORCE)')


def test_a_swap_pending_backup_restores_into_the_same_state(
    tmp_path, monkeypatch, capsys
):
    """Back up with only the swap pending, restore, and recover Admin access.

    The set's manifest lists what the database had applied; restore-check,
    against this image's real migration files, calls it swap_pending (exit
    4), not a mismatch. The restored database is in the same state: only the
    swap pending (the services' check refuses it, the recovery commands'
    admits it) and the pre-swap function bodies installed. recover-admin
    runs there, and the recovered Administrator opens an Admin session
    through the restored pre-swap guard.
    """
    from parishkit.stewardship import restore_check
    from parishkit.stewardship.cli import main

    from .test_backup_postgresql import FACTS, backup_login

    store, _, _ = initialized(tmp_path, rules())
    deployment = str(SystemConfiguration.objects.get().pk)
    manifest = tmp_path / "manifest.json"

    def run_backup(configuration, *, record, migrations, recipient):
        """Write the set's manifest as the backup does, and record the run."""
        manifest.write_text(
            json.dumps(
                {
                    "version": 1,
                    "application_version": FACTS["application_version"],
                    "image": "ghcr.io/example/stewardship@sha256:" + "c" * 64,
                    "migrations": sorted([app, name] for app, name in migrations),
                }
            )
        )
        record(started_at=scalar("SELECT statement_timestamp()"), **FACTS)
        return {
            "database": {"plaintext_bytes": 1},
            "files": {"plaintext_bytes": 1},
            "recipient_fingerprint": FACTS["recipient_fingerprint"],
        }

    backup_command(monkeypatch, run_backup)
    with swap_pending():
        with backup_login():
            assert main(["backup", "--config", "unused"]) == 0
        assert json.loads(capsys.readouterr().out)["backup_recorded"] is True
        # The restore: a copy of the database the set was taken from.
        with restored_copy():
            code, report = restore_check.check(manifest)
            assert (code, report["result"]) == (
                restore_check.SWAP_PENDING,
                "swap_pending",
            )
            assert report["not_in_backup"] == sorted(
                map(list, restore_check.image_swap_closure())
            )
            assert report["unknown_to_image"] == []
            # The same pending state as before the backup.
            with pytest.raises(ConfigError, match="do not match"):
                runtime_database.require_current_schema()
            runtime_database.require_current_schema(users_swap_pending=True)
            assert (
                scalar(
                    "SELECT count(*) FROM django_migrations WHERE app=%s AND name=%s",
                    list(USERS_SWAP),
                )
                == 0
            )
            assert all(reads_domain_rules(name) for name in REPLACED)
            # Admin-access recovery works on the restored database.
            configuration = offline_commands(monkeypatch, store)
            with task_login(ServiceRole.ADMIN_RECOVERY):
                assert recover(configuration, deployment, "restored@example.org")[
                    "recovery_applied"
                ]
            portal = identity("restored@example.org", signed_in=False)
            assert forge(portal.pk).principal_id == portal.pk
    # The test database itself is back with the swap applied.
    runtime_database.require_current_schema()


@contextmanager
def base_partly_applied():
    """Swap pending and one migration outside its closure pending too.

    The state a `migrate --users-upgrade-base` that stopped partway (a
    failure, a time limit) leaves: the newest migration of another app is
    not yet recorded. Its row is put back afterwards.
    """
    from django.db.migrations.loader import MigrationLoader

    other = next(
        key
        for key in sorted(MigrationLoader(connection).graph.leaf_nodes())
        if key[0] != USERS_SWAP[0]
    )
    with swap_pending():
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM django_migrations WHERE app=%s AND name=%s "
                "RETURNING applied",
                list(other),
            )
            (applied,) = cursor.fetchone()
        try:
            yield
        finally:
            with connection.cursor() as cursor:
                cursor.execute("RESET SESSION AUTHORIZATION")
                cursor.execute(
                    "INSERT INTO django_migrations (app, name, applied) "
                    "VALUES (%s, %s, %s)",
                    [*other, applied],
                )


def test_a_resumed_users_upgrade_reuses_the_earlier_runs_backup(
    tmp_path, monkeypatch, capsys
):
    """A base migration that stopped partway: the repeat reuses the backup.

    With migrations outside the swap pending, `backup` refuses as before;
    `backup --users-upgrade-resume` reports the newest recorded backup (the
    earlier run's step 1, within migrate's 24-hour window) instead of
    taking one, with that set's own off-site state. Without such a backup it
    still refuses; with only the swap pending it backs up as usual.
    """
    from datetime import timedelta

    from parishkit.stewardship.cli import main
    from parishkit.stewardship.jobs.backup_models import BackupUpload

    from .test_backup_postgresql import FACTS, backup_login, recorded

    initialized(tmp_path, rules())
    taken = []

    def run_backup(configuration, *, record, migrations, recipient):
        """Record one fresh run; the reuse path never reaches this."""
        taken.append(sorted(migrations))
        record(started_at=scalar("SELECT statement_timestamp()"), **FACTS)
        return {
            "database": {"plaintext_bytes": 1},
            "files": {"plaintext_bytes": 1},
            "recipient_fingerprint": FACTS["recipient_fingerprint"],
        }

    def backup(*flags):
        """Run the command as the backup login; its exit code and JSON."""
        with backup_login():
            code = main(["backup", "--config", "unused", *flags])
        out = capsys.readouterr().out
        return code, json.loads(out) if out else None

    backup_command(monkeypatch, run_backup)
    resume = "--users-upgrade-resume"
    with base_partly_applied():
        # No recent backup: nothing to reuse, so it refuses either way.
        recorded(age=timedelta(hours=25), manifest_digest="d" * 64)
        assert backup(resume) == (2, None)
        # The earlier run's step-1 backup, copied off-site.
        recorded(manifest_digest="e" * 64)
        assert backup() == (2, None)
        code, report = backup(resume)
        assert code == 0
        assert report["backup_recorded"] is False
        assert report["backup_reused"] is True
        assert report["reused_backup"]["manifest_digest"] == "e" * 64
        assert report["pending_base_migrations"] == 1
        # No off-site destination in this configuration and no copy row.
        assert report["offsite"] == {"state": "not_configured"}
        with transaction.atomic():
            BackupUpload.objects.create(
                state="uploaded",
                set_name="20261010T000000Z",
                manifest_digest="e" * 64,
                folder_id="f" * 20,
            )
        assert backup(resume)[1]["offsite"] == {"state": "uploaded"}
        assert taken == []
    with swap_pending():
        # Only the swap pending: a fresh backup, flag or not.
        code, report = backup(resume)
        assert (code, report["backup_recorded"]) == (0, True)
        assert "backup_reused" not in report
    assert len(taken) == 1

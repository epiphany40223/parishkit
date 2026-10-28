"""Operator provisioning on an owned empty PostgreSQL, never a shared schema."""

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.key_files import write_private
from parishkit.stewardship.database_provisioning import (
    isolation_drift,
    migration_exemption,
    provision_grants,
    provision_roles,
    role_limit,
)
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.deployment_documents import deployment_document
from parishkit.stewardship.inspect_login import (
    CLEANUP,
    CREDENTIAL_NAME,
    EXCLUDED_COLUMNS,
    EXCLUDED_TABLES,
    INSPECT_LOGIN,
    REVIEWED_COLUMNS,
    REVIEWED_VIEWS,
    expected_selects,
)
from parishkit.stewardship.runtime_identities import database_identities
from parishkit.stewardship.runtime_paths import RuntimeLayout
from parishkit.stewardship.runtime_topology import POSTGRES_IMAGE

from .bootstrap_factory import bootstrap_fixture
from .test_runtime_topology import configuration_at

pytestmark = pytest.mark.skipif(
    os.environ.get("PARISHKIT_RUN_RUNTIME_TESTS") != "1",
    reason="Requires explicitly opted-in disposable Docker runtime validation",
)
PASSWORD = "disposable-test-only"
INSPECT_WRAPPER = (
    Path(__file__).resolve().parents[2] / "tools/stewardship-ops/pk-inspect"
)


def docker(*arguments):
    """Bound operations against only this fixture's known container name."""
    result = subprocess.run(
        ["docker", *arguments], text=True, capture_output=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def empty_operator_database(operator_container):
    """The disposable database's operator configuration alone."""
    return operator_container[1]


@pytest.fixture
def operator_container(tmp_path):
    """Stock PostgreSQL proves arbitrary non-root UID with no capability grants.

    Yields the container's name, for tests that reach its socket by
    ``docker exec``, and the operator configuration.
    """
    name = "parishkit-phase1c-provision-" + uuid4().hex
    docker(
        "run",
        "--detach",
        "--rm",
        "--name",
        name,
        "--label",
        "parishkit.disposable=phase1c-provision",
        "--user",
        "10001:10001",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,mode=1777",
        "--tmpfs",
        "/var/lib/postgresql:rw,nosuid,nodev,uid=10001,gid=10001,mode=0700",
        "--tmpfs",
        "/var/run/postgresql:rw,nosuid,nodev,uid=10001,gid=10001,mode=0700",
        "--env",
        "POSTGRES_USER=pk_stewardship_operator",
        "--env",
        "POSTGRES_DB=stewardship_provision_tests",
        "--env",
        "POSTGRES_PASSWORD=" + PASSWORD,
        "--publish",
        "127.0.0.1::5432",
        POSTGRES_IMAGE,
    )
    try:
        port = int(docker("port", name, "5432/tcp").rsplit(":", 1)[1])
        configuration = configuration_at(tmp_path)
        layout = RuntimeLayout(configuration)
        password_directory = layout.database_password("operator").parent
        password_directory.mkdir(parents=True, mode=0o700)
        for role in ["operator", *(item[0] for item in database_identities())]:
            write_private(layout.database_password(role), PASSWORD.encode())
        configuration = replace(
            configuration,
            service_role=ServiceRole.DATABASE_PROVISION,
            postgres=replace(
                configuration.postgres,
                host="127.0.0.1",
                port=port,
                name="stewardship_provision_tests",
                user="pk_stewardship_operator",
                password_file=layout.database_password("operator"),
            ),
        )
        deadline = time.monotonic() + 45
        while True:
            try:
                with connect(configuration):
                    break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    pytest.fail("Disposable PostgreSQL did not become ready")
                time.sleep(0.2)
        yield name, configuration
    finally:
        # This exact UUID container owns tmpfs only, never host persistent data.
        docker("stop", "--time", "10", name)


def connect(configuration, user="pk_stewardship_operator"):
    """Each assertion authenticates through SCRAM, not SET ROLE impersonation."""
    db = configuration.postgres
    return psycopg.connect(
        host=db.host,
        port=db.port,
        dbname=db.name,
        user=user,
        password=PASSWORD,
        connect_timeout=2,
    )


def test_initial_database_roles_retry_and_refuse_takeover(empty_operator_database):
    """Credentials match on retry, limits are real, and unrelated identity is fatal."""
    configuration = empty_operator_database
    deployment = uuid4()
    assert provision_roles(configuration, deployment)["database_roles_provisioned"]
    assert provision_roles(configuration, deployment)["database_roles_provisioned"]
    with pytest.raises(ConfigError):
        provision_roles(configuration, uuid4())
    for _, login, role, _ in database_identities():
        with connect(configuration, login) as database, database.cursor() as cursor:
            cursor.execute(
                "SELECT current_user,session_user,rolsuper,rolbypassrls,rolcreatedb,"
                "rolcreaterole,rolinherit,rolconnlimit,"
                "has_database_privilege(current_user,current_database(),'TEMP'),"
                "has_schema_privilege(current_user,'public','CREATE') "
                "FROM pg_roles WHERE rolname=current_user"
            )
            assert cursor.fetchone() == (
                login,
                login,
                False,
                # Only the backup login bypasses row-level security: pg_dump
                # runs with row security off, which a policy-bound login
                # cannot do.
                role is ServiceRole.BACKUP_WORKER,
                False,
                False,
                False,
                role_limit(configuration, role),
                False,
                role is ServiceRole.MIGRATION,
            )
    path = RuntimeLayout(configuration).database_password("web")
    write_private(path, b"different-password")
    with pytest.raises(psycopg.OperationalError):
        provision_roles(configuration, deployment)
    with connect(configuration, "pk_stewardship_web"):
        pass  # A mismatching file did not alter the existing database password.


def test_provisioning_and_migrate_refuse_isolation_drift(empty_operator_database):
    """Provisioning retry refuses a lent or foreign-reaching login (#389).

    The migration login owns the schema after provisioning and is still
    admitted, so its ownership exemption holds; a member of it, or
    foreign-data USAGE, is refused. Migrate's identity query renders the same
    boolean, evaluated here as the real migration login.
    """
    configuration = empty_operator_database
    deployment = uuid4()
    provision_roles(configuration, deployment)
    drift = (
        "SELECT "
        f"{isolation_drift(migration_exemption(ServiceRole.MIGRATION))} "
        "FROM pg_roles r WHERE rolname=current_user"
    )
    cases = [
        (
            "CREATE ROLE pk_drift_member; "
            "GRANT pk_stewardship_migration TO pk_drift_member",
            "DROP ROLE pk_drift_member",
            True,
        ),
        (
            "CREATE FOREIGN DATA WRAPPER pk_drift_fdw; "
            "GRANT USAGE ON FOREIGN DATA WRAPPER pk_drift_fdw "
            "TO pk_stewardship_migration",
            "DROP FOREIGN DATA WRAPPER pk_drift_fdw CASCADE",
            True,
        ),
        (
            "CREATE ROLE pk_drift_web_member; "
            "GRANT pk_stewardship_web TO pk_drift_web_member",
            "DROP ROLE pk_drift_web_member",
            False,
        ),
    ]
    for apply, undo, migration in cases:
        with connect(configuration) as database:
            database.execute(apply)
        with pytest.raises(ConfigError, match="differs from initial provisioning"):
            provision_roles(configuration, deployment)
        with connect(configuration, "pk_stewardship_migration") as database:
            assert database.execute(drift).fetchone() == (migration,)
        with connect(configuration) as database:
            database.execute(undo)
        assert provision_roles(configuration, deployment)["database_roles_provisioned"]
        with connect(configuration, "pk_stewardship_migration") as database:
            assert database.execute(drift).fetchone() == (False,)


def test_migration_owner_and_narrow_runtime_grants(empty_operator_database, tmp_path):
    """Migrate as the isolated owner, then provision real runtime identities."""
    configuration = empty_operator_database
    bootstrap, identity = bootstrap_fixture(tmp_path)
    deployment = identity.deployment_id
    provision_roles(configuration, deployment)
    layout = RuntimeLayout(configuration)
    migration = replace(
        configuration,
        service_role=ServiceRole.MIGRATION,
        postgres=replace(
            configuration.postgres,
            user="pk_stewardship_migration",
            password_file=layout.database_password("migration"),
        ),
    )
    config_file = tmp_path / "migration.json"
    config_file.write_text(json.dumps(deployment_document(migration)))
    script = tmp_path / "migrate_fixture.py"
    script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "from parishkit.stewardship.deployment import load_deployment\n"
        "from parishkit.stewardship import operator_commands as commands\n"
        "configuration = load_deployment(Path(sys.argv[1]), environ={})\n"
        "commands.configure_operator_database(configuration)\n"
        "from django.core.management import call_command\n"
        "call_command('migrate', interactive=False, verbosity=0)\n"
    )
    result = subprocess.run(
        [sys.executable, str(script), str(config_file)],
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    assert provision_grants(configuration, deployment)["database_grants_provisioned"]
    assert provision_grants(configuration, deployment)["database_grants_provisioned"]
    for _, login, role, _ in database_identities():
        if role is ServiceRole.MIGRATION:
            continue
        with connect(configuration, login) as database, database.cursor() as cursor:
            if role is ServiceRole.BACKUP_UPLOADER:
                # The off-site uploader (#235) reads only what binds a set to
                # its run row, and nothing else of the schema.
                cursor.execute(
                    "SELECT count(manifest_digest) FROM stewardship_backup_run"
                )
                assert cursor.fetchone() == (0,)
                cursor.execute(
                    "SELECT has_function_privilege(current_user,"
                    "'public.stewardship_bootstrap_empty_database()','EXECUTE'),"
                    "has_function_privilege(current_user,"
                    "'public.stewardship_backup_destination_v1()','EXECUTE')"
                )
                assert cursor.fetchone() == (False, True)
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    cursor.execute("SELECT COUNT(*) FROM django_migrations")
                continue
            if role == "download":
                cursor.execute("SELECT COUNT(*) FROM stewardship_campaign")
                assert cursor.fetchone() == (0,)
            else:
                cursor.execute("SELECT COUNT(*) FROM django_migrations")
                assert cursor.fetchone()[0] > 0
            cursor.execute(
                "SELECT has_function_privilege(current_user,"
                "'public.stewardship_bootstrap_empty_database()','EXECUTE')"
            )
            assert cursor.fetchone() == (False,)
            if role is ServiceRole.CREDENTIAL_INSTALLER:
                cursor.execute(
                    "SELECT id,validation_schema FROM stewardship_configuration_version"
                )
                assert cursor.fetchall() == []
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    cursor.execute("SELECT * FROM stewardship_configuration_version")
            if role is ServiceRole.BACKUP_WORKER:
                # The dump reads every table through its inherited membership
                # and may write only its own record.
                cursor.execute("SELECT COUNT(*) FROM stewardship_campaign")
                assert cursor.fetchone() == (0,)
                # pg_dump's precondition: with row security off, a table under
                # a forced policy is readable only by a bypassing login.
                cursor.execute("SET row_security = off")
                cursor.execute("SELECT COUNT(*) FROM stewardship_secret_request")
                assert cursor.fetchone() == (0,)
                cursor.execute("RESET row_security")
                cursor.execute(
                    "SELECT pg_has_role(current_user,'pg_read_all_data','USAGE'),"
                    "has_table_privilege(current_user,"
                    "'stewardship_backup_run','INSERT'),"
                    "has_table_privilege(current_user,'stewardship_campaign','INSERT')"
                )
                assert cursor.fetchone() == (True, True, False)
    from parishkit.stewardship.bootstrap import provision_initial_files

    bootstrap = replace(
        bootstrap,
        postgres=replace(
            configuration.postgres,
            user="pk_stewardship_bootstrap",
            password_file=layout.database_password("bootstrap"),
        ),
    )
    provision_initial_files(bootstrap, identity)
    bootstrap_file = tmp_path / "bootstrap.json"
    bootstrap_file.write_text(json.dumps(deployment_document(bootstrap)))
    script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "from uuid import UUID\n"
        "from parishkit.stewardship.deployment import load_deployment\n"
        "from parishkit.stewardship import operator_commands as commands\n"
        "configuration = load_deployment(Path(sys.argv[1]), environ={})\n"
        "commands.configure_operator_database(configuration)\n"
        "from parishkit.stewardship.runtime_database import admit_offline_database\n"
        "admit_offline_database(configuration)\n"
        "from parishkit.stewardship.bootstrap import (\n"
        "    BootstrapIdentity, materialize_initial_files)\n"
        "identity = BootstrapIdentity(UUID(sys.argv[2]), 'admin@example.org')\n"
        "materialize_initial_files(configuration, identity)\n"
    )
    # Deliberately seed foreign credential history in this UUID-owned disposable
    # database. Only fixture setup disables triggers; bootstrap itself runs with
    # all normal guards and a non-superuser SECURITY DEFINER owner.
    foreign_request = uuid4()
    with connect(configuration) as database, database.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role=replica")
        cursor.execute(
            "INSERT INTO stewardship_secret_request "
            "(id,version,correlation_id,target,staging_reference,requested_by_id,"
            "reauthenticated_at,expires_at,required_consumers) VALUES "
            "(%s,1,%s,'metrics',%s,%s,clock_timestamp()-interval '1 second',"
            "clock_timestamp()+interval '1 hour','[\"web\"]')",
            [foreign_request, uuid4(), uuid4(), uuid4()],
        )
    with connect(configuration, "pk_stewardship_migration") as database:
        assert database.execute(
            "SELECT count(*) FROM stewardship_secret_request"
        ).fetchone() == (1,)
    refused = subprocess.run(
        [sys.executable, str(script), str(bootstrap_file), str(deployment)],
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert refused.returncode != 0
    assert "empty application database" in refused.stderr
    with connect(configuration) as database, database.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role=replica")
        cursor.execute(
            "DELETE FROM stewardship_secret_request WHERE id=%s", [foreign_request]
        )
    result = subprocess.run(
        [sys.executable, str(script), str(bootstrap_file), str(deployment)],
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    # Initial SQL provisioning cannot become a configured upgrade bypass.
    with pytest.raises(ConfigError, match="upgrade admission"):
        provision_grants(configuration, deployment)
    web = replace(
        bootstrap,
        service_role=ServiceRole.WEB,
        postgres=replace(
            bootstrap.postgres,
            user="pk_stewardship_web",
            password_file=layout.database_password("web"),
        ),
    )
    web_file = tmp_path / "web.json"
    web_file.write_text(json.dumps(deployment_document(web)))
    script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "from parishkit.stewardship.deployment import load_deployment\n"
        "from parishkit.stewardship import operator_commands as commands\n"
        "configuration = load_deployment(Path(sys.argv[1]), environ={})\n"
        "commands.configure_operator_database(configuration)\n"
        "from parishkit.stewardship.runtime_grants import admit_runtime_database\n"
        "admit_runtime_database(configuration)\n"
        "from parishkit.stewardship.accounts.authority import AuthorityStore\n"
        "from parishkit.stewardship.accounts.configuration_schema import (\n"
        "    validate_sections)\n"
        "from parishkit.stewardship.accounts.configuration_installation import (\n"
        "    coherent_configuration)\n"
        "store = AuthorityStore(configuration.paths['authority'], validate_sections)\n"
        "assert coherent_configuration(store).mode == 'testing'\n"
    )
    result = subprocess.run(
        [sys.executable, str(script), str(web_file)],
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    recovery_admission(bootstrap, layout, tmp_path, configuration)


def recovery_admission(bootstrap, layout, tmp_path, configuration):
    """The full offline admission of admin-recovery, as its commands run it.

    Its provisioned portal session grants are column grants only (#389 L6),
    which ``admit_offline_database`` admits; the whole-table grant an
    earlier release gave it is refused until migration 0027 revokes it.
    """
    recovery = replace(
        bootstrap,
        service_role=ServiceRole.ADMIN_RECOVERY,
        postgres=replace(
            bootstrap.postgres,
            user="pk_stewardship_admin_recovery",
            password_file=layout.database_password("admin-recovery"),
        ),
    )
    recovery_file = tmp_path / "admin-recovery.json"
    recovery_file.write_text(json.dumps(deployment_document(recovery)))
    script = tmp_path / "admit_recovery.py"
    script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "from parishkit.stewardship.deployment import load_deployment\n"
        "from parishkit.stewardship import operator_commands as commands\n"
        "configuration = load_deployment(Path(sys.argv[1]), environ={})\n"
        "commands.configure_operator_database(configuration)\n"
        "from parishkit.stewardship.runtime_database import admit_offline_database\n"
        "admit_offline_database(configuration)\n"
    )

    def admit():
        """Run the admission in a fresh process, as the operator command does."""
        return subprocess.run(
            [sys.executable, str(script), str(recovery_file)],
            text=True,
            capture_output=True,
            timeout=60,
        )

    result = admit()
    assert result.returncode == 0, result.stderr
    grant = (
        "SELECT, UPDATE ON stewardship_portal_session {} pk_stewardship_admin_recovery"
    )
    with connect(configuration) as database, database.cursor() as cursor:
        cursor.execute("GRANT " + grant.format("TO"))
    try:
        refused = admit()
        # Refused by the column admission, not by some unrelated failure.
        assert refused.returncode != 0
        assert "column grants are excessive" in refused.stderr, refused.stderr
    finally:
        with connect(configuration) as database, database.cursor() as cursor:
            cursor.execute("REVOKE " + grant.format("FROM"))
    assert admit().returncode == 0


def upgrade_noop(configuration, deployment, tmp_path):
    """Render the check in a fresh process, as the upgrade does, then run it.

    The operator session is read-only, as the upgrade scripts run it; any
    query error counts as "not a no-op", which is how they treat it.
    """
    config_file = tmp_path / "upgrade-check.json"
    config_file.write_text(json.dumps(deployment_document(configuration)))
    script = tmp_path / "upgrade_check.py"
    script.write_text(
        "import sys\n"
        "from parishkit.stewardship.cli import main\n"
        "sys.exit(main(sys.argv[1:]))\n"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "upgrade-check",
            "--config",
            str(config_file),
            "--confirm-deployment",
            str(deployment),
        ],
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    with connect(configuration) as database, database.cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        try:
            cursor.execute(result.stdout)
        except psycopg.Error:
            return False
        # psql prints the text answer as the scripts compare it; anything
        # but t and f is a cause sentence (#2291), returned as it is.
        value = cursor.fetchone()[0]
        return {"t": True, "f": False}.get(value, value)


def test_upgrade_check_proves_migration_and_grants_are_no_ops(
    empty_operator_database, tmp_path
):
    """True only when migrate and database-grants would both change nothing."""
    configuration = empty_operator_database
    deployment = uuid4()
    provision_roles(configuration, deployment)
    # Before migration the tables it names do not exist yet.
    assert upgrade_noop(configuration, deployment, tmp_path) is False
    layout = RuntimeLayout(configuration)
    migration = replace(
        configuration,
        service_role=ServiceRole.MIGRATION,
        postgres=replace(
            configuration.postgres,
            user="pk_stewardship_migration",
            password_file=layout.database_password("migration"),
        ),
    )
    config_file = tmp_path / "migration.json"
    config_file.write_text(json.dumps(deployment_document(migration)))
    script = tmp_path / "migrate_fixture.py"
    script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "from parishkit.stewardship.deployment import load_deployment\n"
        "from parishkit.stewardship import operator_commands as commands\n"
        "configuration = load_deployment(Path(sys.argv[1]), environ={})\n"
        "commands.configure_operator_database(configuration)\n"
        "from django.core.management import call_command\n"
        "call_command('migrate', interactive=False, verbosity=0)\n"
    )
    result = subprocess.run(
        [sys.executable, str(script), str(config_file)],
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    with connect(configuration) as database:
        database.execute(
            # What the migrate command, not bare Django, also establishes.
            "UPDATE stewardship_download_policy SET capacity=%s,"
            "version=version+1 WHERE id=1 AND capacity<>%s",
            [configuration.runtime_budget.download_capacity] * 2,
        )
    # Migrated, but no runtime grant is installed yet.
    assert upgrade_noop(configuration, deployment, tmp_path) is False
    provision_grants(configuration, deployment)
    assert upgrade_noop(configuration, deployment, tmp_path) is True
    # Another deployment's marker is never proof.
    assert upgrade_noop(configuration, uuid4(), tmp_path) is False
    # Each change that migrate or database-grants would make, or refuse, is
    # detected, and undoing it restores the proof.
    with connect(configuration) as database:
        guard = database.execute(
            "SELECT pg_get_functiondef("
            "'public.stewardship_operational_log_writer_v1()'::regprocedure)"
        ).fetchone()[0]
    changes = [
        (
            # Another body under the same name and trigger: an older guard.
            "CREATE OR REPLACE FUNCTION "
            "public.stewardship_operational_log_writer_v1() RETURNS trigger "
            "LANGUAGE plpgsql AS $$BEGIN RETURN NEW; END$$",
            guard,
        ),
        (
            "CREATE ROLE pk_upgrade_check_group; "
            "GRANT pk_upgrade_check_group TO pk_stewardship_web",
            "DROP ROLE pk_upgrade_check_group",
        ),
        # The isolation drift every runtime login's own admission refuses
        # (#389 L7), so the upgrade stops before its services would.
        (
            "CREATE ROLE pk_upgrade_check_member; "
            "GRANT pk_stewardship_web TO pk_upgrade_check_member",
            "DROP ROLE pk_upgrade_check_member",
        ),
        (
            # The schema owner is exempt from the ownership check only.
            "CREATE ROLE pk_upgrade_check_owner_member; "
            "GRANT pk_stewardship_migration TO pk_upgrade_check_owner_member",
            "DROP ROLE pk_upgrade_check_owner_member",
        ),
        (
            "CREATE FUNCTION public.upgrade_check_owned() RETURNS integer "
            "LANGUAGE sql AS 'SELECT 1'; "
            "ALTER FUNCTION public.upgrade_check_owned() "
            "OWNER TO pk_stewardship_backup_worker",
            "DROP FUNCTION public.upgrade_check_owned()",
        ),
        (
            "CREATE FOREIGN DATA WRAPPER upgrade_check_fdw; "
            "GRANT USAGE ON FOREIGN DATA WRAPPER upgrade_check_fdw "
            "TO pk_stewardship_download",
            "DROP FOREIGN DATA WRAPPER upgrade_check_fdw CASCADE",
        ),
        # Parameter privileges (#845), for any login including the schema
        # owner, and through PUBLIC.
        (
            "GRANT SET ON PARAMETER session_replication_role "
            "TO pk_stewardship_migration",
            "REVOKE SET ON PARAMETER session_replication_role "
            "FROM pk_stewardship_migration",
        ),
        (
            "GRANT ALTER SYSTEM ON PARAMETER log_statement TO PUBLIC",
            "REVOKE ALTER SYSTEM ON PARAMETER log_statement FROM PUBLIC",
        ),
        # The backup login's excess authority, as its backup admission
        # refuses it (#389 L7).
        (
            "GRANT CREATE ON SCHEMA public TO pk_stewardship_backup_worker",
            "REVOKE CREATE ON SCHEMA public FROM pk_stewardship_backup_worker",
        ),
        # A look-alike operator, which an unqualified operator in a definer
        # function could resolve to, and the CREATE that would let a runtime
        # login add one (#2166).
        (
            "CREATE FUNCTION public.upgrade_check_has(jsonb, varchar) "
            "RETURNS boolean LANGUAGE sql AS 'SELECT true'; "
            "CREATE OPERATOR public.? (LEFTARG=jsonb, RIGHTARG=varchar, "
            "FUNCTION=public.upgrade_check_has)",
            "DROP OPERATOR public.?(jsonb, varchar); "
            "DROP FUNCTION public.upgrade_check_has(jsonb, varchar)",
        ),
        (
            "GRANT CREATE ON SCHEMA public TO pk_stewardship_web",
            "REVOKE CREATE ON SCHEMA public FROM pk_stewardship_web",
        ),
        (
            "GRANT CREATE ON DATABASE stewardship_provision_tests "
            "TO pk_stewardship_worker",
            "REVOKE CREATE ON DATABASE stewardship_provision_tests "
            "FROM pk_stewardship_worker",
        ),
        (
            "CREATE SEQUENCE public.upgrade_check_sequence; "
            "GRANT USAGE ON SEQUENCE public.upgrade_check_sequence "
            "TO pk_stewardship_backup_worker",
            "DROP SEQUENCE public.upgrade_check_sequence",
        ),
        (
            "ALTER ROLE pk_stewardship_web BYPASSRLS",
            "ALTER ROLE pk_stewardship_web NOBYPASSRLS",
        ),
        (
            "CREATE SCHEMA upgrade_check_extra; "
            "CREATE TABLE upgrade_check_extra.extra (id integer); "
            "GRANT SELECT ON upgrade_check_extra.extra TO pk_stewardship_worker",
            "DROP SCHEMA upgrade_check_extra CASCADE",
        ),
        (
            "REVOKE INSERT ON stewardship_backup_run FROM pk_stewardship_backup_worker",
            "GRANT INSERT ON stewardship_backup_run TO pk_stewardship_backup_worker",
        ),
        (
            "REVOKE SELECT ON stewardship_campaign FROM pk_stewardship_web",
            "GRANT SELECT ON stewardship_campaign TO pk_stewardship_web",
        ),
        (
            "GRANT DELETE ON stewardship_campaign TO pk_stewardship_web",
            "REVOKE DELETE ON stewardship_campaign FROM pk_stewardship_web",
        ),
        (
            "GRANT UPDATE (id) ON stewardship_campaign TO pk_stewardship_download",
            "REVOKE UPDATE (id) ON stewardship_campaign FROM pk_stewardship_download",
        ),
        (
            "GRANT UPDATE ON stewardship_campaign TO pk_stewardship_backup_worker",
            "REVOKE UPDATE ON stewardship_campaign FROM pk_stewardship_backup_worker",
        ),
        (
            "ALTER ROLE pk_stewardship_worker CONNECTION LIMIT 99",
            "ALTER ROLE pk_stewardship_worker CONNECTION LIMIT "
            + str(role_limit(configuration, ServiceRole.WORKER)),
        ),
        (
            "UPDATE stewardship_download_policy "
            "SET capacity=capacity+1,version=version+1 WHERE id=1",
            "UPDATE stewardship_download_policy "
            "SET capacity=capacity-1,version=version+1 WHERE id=1",
        ),
        (
            "ALTER TABLE stewardship_operational_log DISABLE TRIGGER "
            "stewardship_operational_log_writer_v1",
            "ALTER TABLE stewardship_operational_log ENABLE TRIGGER "
            "stewardship_operational_log_writer_v1",
        ),
        (
            "INSERT INTO django_migrations (app,name,applied) "
            "VALUES ('accounts','9999_future',now())",
            "DELETE FROM django_migrations WHERE name='9999_future'",
        ),
        # The inspection login (#2148): a write, an excluded column, a
        # missing SELECT, a lifted read-only default, a password, a SET ROLE
        # path and another membership each make database-grants necessary
        # (or refuse it).
        (
            "GRANT INSERT ON stewardship_campaign TO pk_stewardship_inspect",
            "REVOKE INSERT ON stewardship_campaign FROM pk_stewardship_inspect",
        ),
        (
            "GRANT SELECT (session_id) ON stewardship_portal_session "
            "TO pk_stewardship_inspect",
            "REVOKE SELECT (session_id) ON stewardship_portal_session "
            "FROM pk_stewardship_inspect",
        ),
        (
            "REVOKE SELECT ON stewardship_campaign FROM pk_stewardship_inspect",
            "GRANT SELECT ON stewardship_campaign TO pk_stewardship_inspect",
        ),
        (
            "ALTER ROLE pk_stewardship_inspect SET default_transaction_read_only=off",
            "ALTER ROLE pk_stewardship_inspect SET default_transaction_read_only=on",
        ),
        (
            "ALTER ROLE pk_stewardship_inspect PASSWORD 'disposable'",
            "ALTER ROLE pk_stewardship_inspect PASSWORD NULL",
        ),
        (
            "GRANT pg_read_all_stats TO pk_stewardship_inspect WITH SET TRUE",
            "GRANT pg_read_all_stats TO pk_stewardship_inspect WITH SET FALSE",
        ),
        (
            "GRANT pg_signal_backend TO pk_stewardship_inspect",
            "REVOKE pg_signal_backend FROM pk_stewardship_inspect",
        ),
        (
            "REVOKE CONNECT ON DATABASE stewardship_provision_tests "
            "FROM pk_stewardship_inspect",
            "GRANT CONNECT ON DATABASE stewardship_provision_tests "
            "TO pk_stewardship_inspect",
        ),
    ]
    for change, undo in changes:
        with connect(configuration) as database:
            database.execute(change)
        assert upgrade_noop(configuration, deployment, tmp_path) is False, change
        with connect(configuration) as database:
            database.execute(undo)
        assert upgrade_noop(configuration, deployment, tmp_path) is True, undo
    with connect(configuration) as database:
        row = database.execute(
            "DELETE FROM django_migrations WHERE id=(SELECT max(id) "
            "FROM django_migrations) RETURNING app,name"
        ).fetchone()
    assert row is not None
    assert upgrade_noop(configuration, deployment, tmp_path) is False


def migrate(configuration, tmp_path):
    """Migrate as the isolated schema owner in a fresh process, as migrate runs."""
    layout = RuntimeLayout(configuration)
    migration = replace(
        configuration,
        service_role=ServiceRole.MIGRATION,
        postgres=replace(
            configuration.postgres,
            user="pk_stewardship_migration",
            password_file=layout.database_password("migration"),
        ),
    )
    config_file = tmp_path / "migration.json"
    config_file.write_text(json.dumps(deployment_document(migration)))
    script = tmp_path / "migrate_fixture.py"
    script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "from parishkit.stewardship.deployment import load_deployment\n"
        "from parishkit.stewardship import operator_commands as commands\n"
        "configuration = load_deployment(Path(sys.argv[1]), environ={})\n"
        "commands.configure_operator_database(configuration)\n"
        "from django.core.management import call_command\n"
        "call_command('migrate', interactive=False, verbosity=0)\n"
    )
    result = subprocess.run(
        [sys.executable, str(script), str(config_file)],
        text=True,
        capture_output=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr


def inspect(container, *arguments):
    """Run the real pk-inspect wrapper against the disposable container's socket."""
    return subprocess.run(
        [str(INSPECT_WRAPPER), "-At", "-v", "ON_ERROR_STOP=1", *arguments],
        env={**os.environ, "PK_INSPECT_CONTAINER": container},
        stdin=subprocess.DEVNULL,
        text=True,
        capture_output=True,
        timeout=60,
    )


def answer(container, *statements):
    """The output of ``statements`` (each its own ``-c``); they must succeed."""
    arguments = [part for statement in statements for part in ("-c", statement)]
    result = inspect(container, *arguments)
    assert result.returncode == 0, (statements, result.stderr)
    return result.stdout.strip()


def refused(container, message, *statements):
    """The last of ``statements`` (each its own transaction) fails with ``message``.

    psql exits 1 when a ``-c`` statement fails under ON_ERROR_STOP.
    """
    arguments = [part for statement in statements for part in ("-c", statement)]
    result = inspect(container, *arguments)
    assert result.returncode == 1, (statements, result.stdout, result.stderr)
    assert message in result.stderr, (statements, result.stderr)


def expected(configuration):
    """The ``(relname, attname)`` SELECT grants the catalog rule gives now."""
    with connect(configuration) as database:
        return set(database.execute(expected_selects()).fetchall())


def test_inspection_login_reads_but_never_writes(operator_container, tmp_path):
    """The SELECT-only login (#2148), through its real host wrapper.

    It exists after database-roles and holds its grants after
    database-grants; it cannot sign in over TCP (no password); it reads
    tables, views and statistics; every write and DDL is refused by the
    read-only default and, with that lifted, by the grants; it cannot
    change identity; credential tables, columns and views over them are
    refused; and database-grants creates it on a deployment that lacks it
    and refuses one that drifted.
    """
    container, configuration = operator_container
    deployment = uuid4()
    provision_roles(configuration, deployment)
    migrate(configuration, tmp_path)
    provision_grants(configuration, deployment)
    with pytest.raises(psycopg.OperationalError):
        connect(configuration, INSPECT_LOGIN).close()
    assert answer(container, "SELECT current_user") == INSPECT_LOGIN
    assert answer(container, "SHOW default_transaction_read_only") == "on"
    assert answer(container, "SELECT count(*)>0 FROM django_migrations") == "t"
    assert answer(container, "SELECT count(*) FROM stewardship_campaign") == "0"
    assert answer(container, "SELECT count(id) FROM stewardship_portal_session") == "0"
    assert (
        answer(
            container, "SELECT pg_has_role(current_user,'pg_read_all_stats','USAGE')"
        )
        == "t"
    )
    rows = expected(configuration)
    with connect(configuration) as database:
        views = {
            name
            for (name,) in database.execute(
                "SELECT relname FROM pg_class WHERE relkind='v' "
                "AND relnamespace='public'::regnamespace"
            )
        }
    readable_views = sorted({name for name, _ in rows} & views)
    assert readable_views
    answer(container, f"SELECT count(*) FROM {readable_views[0]}")

    # Writes and DDL: refused by the read-only default, then by the grants.
    denied = "permission denied for table"
    writes = {
        "INSERT INTO stewardship_campaign DEFAULT VALUES": denied,
        "UPDATE stewardship_campaign SET version=version": denied,
        "DELETE FROM stewardship_campaign": denied,
        "TRUNCATE stewardship_campaign": denied,
        "CREATE TABLE public.inspect_probe (i integer)": "permission denied for schema",
        "CREATE TEMP TABLE inspect_probe (i integer)": "permission denied to create",
        "DROP TABLE stewardship_campaign": "must be owner of table",
        "ALTER TABLE stewardship_campaign ADD COLUMN probe integer": (
            "must be owner of table"
        ),
    }
    for statement, message in writes.items():
        refused(container, "read-only transaction", statement)
        refused(
            container, message, "SET default_transaction_read_only = off", statement
        )
    refused(
        container,
        "permission denied for table",
        "START TRANSACTION READ WRITE; DELETE FROM stewardship_campaign",
    )
    for statement in (
        "SET ROLE pk_stewardship_web",
        "SET ROLE pk_stewardship_operator",
        "SET ROLE pg_read_all_stats",
    ):
        refused(container, "permission denied to set role", statement)
    refused(
        container,
        "permission denied to set session authorization",
        "SET SESSION AUTHORIZATION pk_stewardship_operator",
    )

    # Credential material: excluded tables, row-security tables and columns.
    with connect(configuration) as database:
        secured = {
            name
            for (name,) in database.execute(
                "SELECT relname FROM pg_class WHERE relrowsecurity "
                "AND relnamespace='public'::regnamespace"
            )
        }
    assert "stewardship_sealed_credential_staging" in secured
    for table in sorted(EXCLUDED_TABLES | secured):
        refused(container, "permission denied", f"SELECT 1 FROM {table} LIMIT 1")
    for table, columns in sorted(EXCLUDED_COLUMNS.items()):
        for column in sorted(columns):
            refused(container, "permission denied", f"SELECT {column} FROM {table}")
            assert (table, column) not in rows

    # A view reads with its owner's privileges, so one over an excluded
    # column, a whole row of such a table or a hidden table gets no grant;
    # one over readable columns does.
    # A whole-row reference beside a named column leaves only the column's
    # dependency, so any unreviewed view over such a table is hidden; so is
    # an inheritance child of one.
    probes = {
        "inspect_probe_secret": "SELECT session_id FROM stewardship_portal_session",
        "inspect_probe_row": "SELECT p FROM stewardship_portal_session p",
        "inspect_probe_mixed": "SELECT p.id, p FROM stewardship_portal_session p",
        "inspect_probe_json": "SELECT t.id,to_jsonb(t) FROM stewardship_family_token t",
        "inspect_probe_column": "SELECT id FROM stewardship_portal_session",
        "inspect_probe_hidden": "SELECT count(*) FROM stewardship_secret_request",
        "inspect_probe_nested": "SELECT * FROM inspect_probe_column",
        "inspect_probe_open": "SELECT id, version FROM stewardship_campaign",
    }
    with connect(configuration) as database:
        for name, query in probes.items():
            database.execute(f"CREATE VIEW public.{name} AS {query}")
            database.execute(
                f"ALTER VIEW public.{name} OWNER TO pk_stewardship_migration"
            )
        database.execute(
            "CREATE TABLE public.inspect_probe_child () "
            "INHERITS (stewardship_portal_session)"
        )
    try:
        names = {name for name, _ in expected(configuration)}
        assert names & {*probes, "inspect_probe_child"} == {"inspect_probe_open"}
        # A granted view replaced to read a secret keeps its grant, so
        # database-grants revokes it (that only narrows what is read).
        provision_grants(configuration, deployment)
        assert answer(container, "SELECT count(*) FROM inspect_probe_open") == "0"
        with connect(configuration) as database:
            database.execute(
                "CREATE OR REPLACE VIEW public.inspect_probe_open AS "
                "SELECT c.id, c.version, s.session_id FROM stewardship_campaign c "
                "CROSS JOIN stewardship_portal_session s"
            )
        # Until then the kept grant shows the secret column.
        answer(container, "SELECT session_id FROM inspect_probe_open")
        provision_grants(configuration, deployment)
        refused(container, "permission denied", "SELECT id FROM inspect_probe_open")
    finally:
        with connect(configuration) as database:
            database.execute("DROP TABLE public.inspect_probe_child")
            for name in reversed(list(probes)):
                database.execute(f"DROP VIEW public.{name}")

    # Repeatable; a read the rule does not give is revoked.
    provision_grants(configuration, deployment)
    provision_roles(configuration, deployment)
    with connect(configuration) as database:
        database.execute(
            "GRANT SELECT (session_id) ON stewardship_portal_session "
            "TO pk_stewardship_inspect"
        )
        database.execute("GRANT SELECT ON django_session TO pk_stewardship_inspect")
    provision_grants(configuration, deployment)
    refused(
        container,
        "permission denied",
        "SELECT session_id FROM stewardship_portal_session",
    )
    refused(container, "permission denied", "SELECT 1 FROM django_session")
    assert answer(container, "SELECT count(id) FROM stewardship_portal_session") == "0"

    # A large object the login creates once its read-only default is lifted
    # (PUBLIC may run lo_create) is an owned object: the isolation drift
    # every login is refused for, so the next database-grants and upgrade
    # check stop, and both name what it owns and the clean-up (#2291).
    assert upgrade_noop(configuration, deployment, tmp_path) is True
    for _ in range(2):
        assert (
            answer(
                container,
                "SET default_transaction_read_only = off",
                "SELECT lo_create(0) > 0",
            ).splitlines()[-1]
            == "t"
        )
    cause = (
        "The inspection login owns objects (large objects in database "
        "stewardship_provision_tests: 2). As the operator, in each named "
        f"database, run: {CLEANUP}; then repeat this step (deployment "
        "runbook, Clean-up when the inspection login owns objects)."
    )
    with pytest.raises(ConfigError) as refusal:
        provision_grants(configuration, deployment)
    assert str(refusal.value) == cause
    with pytest.raises(ConfigError) as refusal:
        provision_roles(configuration, deployment)
    assert str(refusal.value) == cause
    assert upgrade_noop(configuration, deployment, tmp_path) == cause
    # The documented clean-up, run as the operator, restores both.
    with connect(configuration) as database:
        database.execute(CLEANUP)
    assert upgrade_noop(configuration, deployment, tmp_path) is True
    provision_grants(configuration, deployment)

    # Any other kind it owns names no clean-up; the login cannot create one
    # itself, so the operator makes it the owner here.
    with connect(configuration) as database:
        database.execute(
            "CREATE TABLE public.inspect_probe_owned (i integer); "
            "ALTER TABLE public.inspect_probe_owned OWNER TO pk_stewardship_inspect"
        )
    try:
        cause = upgrade_noop(configuration, deployment, tmp_path)
        assert cause.startswith(
            "The inspection login owns objects (objects in pg_class in database "
            "stewardship_provision_tests: 1). Only large objects have a "
            "documented clean-up: investigate"
        ), cause
        assert "lo_unlink" not in cause
    finally:
        with connect(configuration) as database:
            database.execute("DROP TABLE public.inspect_probe_owned")

    # A session can change the login's own role (password, settings): the
    # generic refusal points to recreating it, which the runbook's steps do.
    answer(
        container,
        "SET default_transaction_read_only = off",
        "ALTER ROLE pk_stewardship_inspect RESET default_transaction_read_only",
    )
    # The wrapper still starts every session read-only (PGOPTIONS
    # overrides role settings), even with the role's default set off.
    answer(
        container,
        "SET default_transaction_read_only = off",
        "ALTER ROLE pk_stewardship_inspect SET default_transaction_read_only = off",
    )
    assert answer(container, "SHOW default_transaction_read_only") == "on"
    refused(container, "read-only transaction", "DELETE FROM stewardship_campaign")
    with pytest.raises(ConfigError, match="Recreating the inspection login"):
        provision_grants(configuration, deployment)
    assert upgrade_noop(configuration, deployment, tmp_path) is False
    with connect(configuration) as database:
        database.execute("DROP OWNED BY pk_stewardship_inspect")
        database.execute("DROP ROLE pk_stewardship_inspect")
    provision_grants(configuration, deployment)
    assert answer(container, "SHOW default_transaction_read_only") == "on"
    assert upgrade_noop(configuration, deployment, tmp_path) is True

    # Other drift is refused rather than repaired.
    for change, undo, command in (
        (
            "GRANT INSERT ON stewardship_campaign TO pk_stewardship_inspect",
            "REVOKE INSERT ON stewardship_campaign FROM pk_stewardship_inspect",
            provision_grants,
        ),
        (
            "REVOKE CONNECT ON DATABASE stewardship_provision_tests "
            "FROM pk_stewardship_inspect",
            "GRANT CONNECT ON DATABASE stewardship_provision_tests "
            "TO pk_stewardship_inspect",
            provision_grants,
        ),
        (
            "ALTER ROLE pk_stewardship_inspect CONNECTION LIMIT 9",
            "ALTER ROLE pk_stewardship_inspect CONNECTION LIMIT 3",
            provision_roles,
        ),
        (
            "ALTER ROLE pk_stewardship_inspect PASSWORD 'disposable'",
            "ALTER ROLE pk_stewardship_inspect PASSWORD NULL",
            provision_grants,
        ),
    ):
        with connect(configuration) as database:
            database.execute(change)
        with pytest.raises(ConfigError, match="inspection login"):
            command(configuration, deployment)
        with connect(configuration) as database:
            database.execute(undo)
        command(configuration, deployment)

    # An existing deployment without the login gets it from database-grants.
    with connect(configuration) as database:
        database.execute("DROP OWNED BY pk_stewardship_inspect")
        database.execute("DROP ROLE pk_stewardship_inspect")
    provision_grants(configuration, deployment)
    assert answer(container, "SELECT count(*)>0 FROM django_migrations") == "t"
    refused(
        container,
        "permission denied",
        "SELECT ciphertext FROM stewardship_family_token",
    )


def test_credential_looking_columns_are_classified(operator_container, tmp_path):
    """No column that looks like credential material is readable unreviewed.

    Every migrated column whose name matches CREDENTIAL_NAME is excluded,
    under row-level security or in an excluded table, or reviewed as not a
    credential; and every excluded name still exists, so a rename cannot
    quietly make a credential readable.
    """
    _, configuration = operator_container
    provision_roles(configuration, uuid4())
    migrate(configuration, tmp_path)
    with connect(configuration) as database:
        columns = database.execute(
            "SELECT c.relname::text,a.attname::text,c.relrowsecurity "
            "FROM pg_class c JOIN pg_attribute a ON a.attrelid=c.oid "
            "WHERE c.relnamespace='public'::regnamespace "
            "AND c.relkind IN('r','p','v','m') AND a.attnum>0 AND NOT a.attisdropped"
        ).fetchall()
        rows = set(database.execute(expected_selects()).fetchall())
    present = {(table, column) for table, column, _ in columns}
    unclassified = sorted(
        (table, column)
        for table, column, secured in columns
        if re.search(CREDENTIAL_NAME, column, re.IGNORECASE)
        and not secured
        and table not in EXCLUDED_TABLES
        and column not in EXCLUDED_COLUMNS.get(table, ())
        and (table, column) not in REVIEWED_COLUMNS
        # A view showing such a column is hidden by the rule, or reviewed.
        and ((table, None) in rows or (table, column) in rows)
    )
    assert unclassified == []
    assert {table for table, _ in present} >= EXCLUDED_TABLES
    assert {
        (table, column) for table, names in EXCLUDED_COLUMNS.items() for column in names
    } <= present
    assert present >= REVIEWED_COLUMNS
    assert {table for table, _ in present} >= REVIEWED_VIEWS
    # Today's schema has no partitions or inheritance children.
    with connect(configuration) as database:
        assert database.execute("SELECT count(*) FROM pg_inherits").fetchone() == (0,)


UPLOADER = "pk_stewardship_backup_uploader"


def migrated(configuration, deployment, tmp_path):
    """Roles, then this release's migrations, as the upgrade's step 4 runs them."""
    provision_roles(configuration, deployment)
    layout = RuntimeLayout(configuration)
    migration = replace(
        configuration,
        service_role=ServiceRole.MIGRATION,
        postgres=replace(
            configuration.postgres,
            user="pk_stewardship_migration",
            password_file=layout.database_password("migration"),
        ),
    )
    config_file = tmp_path / "migration.json"
    config_file.write_text(json.dumps(deployment_document(migration)))
    script = tmp_path / "migrate_fixture.py"
    script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "from parishkit.stewardship.deployment import load_deployment\n"
        "from parishkit.stewardship import operator_commands as commands\n"
        "configuration = load_deployment(Path(sys.argv[1]), environ={})\n"
        "commands.configure_operator_database(configuration)\n"
        "from django.core.management import call_command\n"
        "call_command('migrate', interactive=False, verbosity=0)\n"
    )
    result = subprocess.run(
        [sys.executable, str(script), str(config_file)],
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    with connect(configuration) as database:
        database.execute(
            "UPDATE stewardship_download_policy SET capacity=%s,"
            "version=version+1 WHERE id=1 AND capacity<>%s",
            [configuration.runtime_budget.download_capacity] * 2,
        )


def stored_matches(configuration, password):
    """Whether the cluster's stored verifier for the uploader is ``password``'s."""
    from parishkit.stewardship.uploader_password import password_matches

    with connect(configuration) as database:
        row = database.execute(
            "SELECT rolpassword FROM pg_authid WHERE rolname=%s", [UPLOADER]
        ).fetchone()
    return row is not None and password_matches(password.encode(), row[0])


def raw_upgrade_answer(configuration, deployment, tmp_path):
    """The upgrade check's own answer, with no error swallowed."""
    from parishkit.stewardship.upgrade_check import upgrade_noop_query

    with connect(configuration) as database, database.cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute(upgrade_noop_query(configuration, deployment))
        return cursor.fetchone()[0]


def test_the_uploader_login_is_created_checked_and_recreated(
    empty_operator_database, tmp_path
):
    """The off-site uploader's login on real PostgreSQL (#235).

    database-roles creates it from its file; a missing login answers the
    upgrade check's f, not an error, and database-grants creates it before
    its per-identity loop; an existing login whose file no longer matches
    the stored SCRAM verifier refuses before any grant.
    """
    configuration = empty_operator_database
    deployment = uuid4()
    migrated(configuration, deployment, tmp_path)
    layout = RuntimeLayout(configuration)
    assert stored_matches(configuration, PASSWORD)
    provision_grants(configuration, deployment)
    assert raw_upgrade_answer(configuration, deployment, tmp_path) == "t"
    # Roles again: the existing uploader is checked by its verifier, not by
    # connecting as it, and the same file passes.
    provision_roles(configuration, deployment)
    # A lost or replaced file: refused before any grant.
    path = layout.database_password("backup-uploader")
    path.unlink()
    write_private(path, b"another-password-entirely")
    with connect(configuration) as database:
        database.execute("REVOKE SELECT ON stewardship_backup_upload FROM " + UPLOADER)
    with pytest.raises(ConfigError, match="lost-file recovery"):
        provision_grants(configuration, deployment)
    with pytest.raises(ConfigError, match="lost-file recovery"):
        provision_roles(configuration, deployment)
    with connect(configuration) as database:
        held = database.execute(
            "SELECT has_table_privilege(%s,'stewardship_backup_upload','SELECT')",
            [UPLOADER],
        ).fetchone()
    assert held == (False,)  # nothing was granted by the refused run
    # The recovery: drop the login, then grants create it from the file.
    with connect(configuration) as database:
        database.execute(f"DROP OWNED BY {UPLOADER}")
        database.execute(f"DROP ROLE {UPLOADER}")
    assert raw_upgrade_answer(configuration, deployment, tmp_path) == "f"
    provision_grants(configuration, deployment)
    assert stored_matches(configuration, "another-password-entirely")
    assert not stored_matches(configuration, PASSWORD)
    assert raw_upgrade_answer(configuration, deployment, tmp_path) == "t"
    with connect(configuration) as database:
        row = database.execute(
            "SELECT rolsuper,rolbypassrls,rolinherit,rolcanlogin,rolconnlimit,"
            "shobj_description(oid,'pg_authid') FROM pg_roles WHERE rolname=%s",
            [UPLOADER],
        ).fetchone()
    assert row == (
        False,
        False,
        False,
        True,
        configuration.runtime_budget.operator_connections,
        f"parishkit-stewardship:{deployment}",
    )


def test_grants_after_a_restore_need_the_restored_sets_marker(
    empty_operator_database, tmp_path
):
    """--after-restore replaces the 24-hour rule only for the loaded set (#235).

    A configured database whose newest backup row is days old, as a
    restored set's is (it never holds its own row): plain grants refuse;
    with the set's marker and no later row they pass; another set's marker
    or a later row refuses.
    """
    from datetime import UTC, datetime, timedelta

    from parishkit.stewardship.backup import RecentBackupRequired

    configuration = empty_operator_database
    deployment = uuid4()
    migrated(configuration, deployment, tmp_path)
    now = datetime.now(UTC).replace(microsecond=0)
    earlier = now - timedelta(days=3)
    set_start = now - timedelta(days=2)
    set_name = set_start.strftime("%Y%m%dT%H%M%SZ")
    with connect(configuration) as database:
        # Fixture rows only; the guards that bind them are not under test.
        database.execute("SET session_replication_role = replica")
        database.execute(
            "INSERT INTO stewardship_system_configuration (id, correlation_id, "
            "version, mode, testing_recipient, restore_review_required) "
            "VALUES (%s,%s,1,'testing','testing@example.org',false)",
            [uuid4(), uuid4()],
        )
        database.execute(
            "INSERT INTO stewardship_backup_run (id, started_at, completed_at, "
            "database_bytes, files_bytes, manifest_digest, recipient_fingerprint, "
            "application_version) VALUES (%s,%s,%s,1,1,%s,%s,'1.0.0')",
            [uuid4(), earlier, earlier, "a" * 64, "b" * 16],
        )
    with pytest.raises(RecentBackupRequired):
        provision_grants(configuration, deployment)
    with pytest.raises(ConfigError, match="--after-restore"):
        provision_grants(configuration, deployment, after_restore=set_name)
    marker = f"pk-restore:{set_name}:" + "c" * 64
    with connect(configuration) as database:
        database.execute(f"COMMENT ON SCHEMA public IS '{marker}'")
    result = provision_grants(configuration, deployment, after_restore=set_name)
    assert result == {"database_grants_provisioned": True, "after_restore": set_name}
    other = (set_start - timedelta(hours=1)).strftime("%Y%m%dT%H%M%SZ")
    with pytest.raises(ConfigError, match="--after-restore"):
        provision_grants(configuration, deployment, after_restore=other)
    with pytest.raises(RecentBackupRequired):
        provision_grants(configuration, deployment)
    with connect(configuration) as database:
        database.execute("SET session_replication_role = replica")
        database.execute(
            "INSERT INTO stewardship_backup_run (id, started_at, completed_at, "
            "database_bytes, files_bytes, manifest_digest, recipient_fingerprint, "
            "application_version) VALUES (%s,%s,%s,1,1,%s,%s,'1.0.0')",
            [uuid4(), set_start, set_start, "d" * 64, "b" * 16],
        )
    with pytest.raises(ConfigError, match="a backup ran on this database"):
        provision_grants(configuration, deployment, after_restore=set_name)

"""Operator provisioning on an owned empty PostgreSQL, never a shared schema."""

import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.key_files import write_private
from parishkit.stewardship.database_provisioning import (
    provision_grants,
    provision_roles,
    role_limit,
)
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.deployment_documents import deployment_document
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


def docker(*arguments):
    """Bound operations against only this fixture's known container name."""
    result = subprocess.run(
        ["docker", *arguments], text=True, capture_output=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def empty_operator_database(tmp_path):
    """Stock PostgreSQL proves arbitrary non-root UID with no capability grants."""
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
        yield configuration
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
        return cursor.fetchone()[0]


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
        # The backup login's excess authority, as its backup admission
        # refuses it (#389 L7).
        (
            "GRANT CREATE ON SCHEMA public TO pk_stewardship_backup_worker",
            "REVOKE CREATE ON SCHEMA public FROM pk_stewardship_backup_worker",
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

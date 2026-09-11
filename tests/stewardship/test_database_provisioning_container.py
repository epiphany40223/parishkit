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
                False,
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

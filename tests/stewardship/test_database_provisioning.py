"""Fast operator refusal/SQL-shape tests complement real isolated PostgreSQL runs."""

from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import database_provisioning as provisioning
from parishkit.stewardship.accounts.key_files import write_private
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.runtime_identities import database_identities

from .test_runtime_topology import configuration_at


class Cursor:
    """Retain generated SQL and provide only the reviewed metadata result sequence."""

    def __init__(self, rows=()):
        self.rows, self.statements = iter(rows), []

    def execute(self, statement, parameters=None):
        """Psycopg composables are rendered without a connection or private inputs."""
        self.statements.append(
            statement if isinstance(statement, str) else statement.as_string()
        )

    def fetchone(self):
        """Unexpected metadata reads fail instead of returning permissive defaults."""
        return next(self.rows)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Database:
    """Mock SQL generation here; Docker tests verify real transaction behavior."""

    def __init__(self, cursor):
        self.statement_cursor = cursor
        self.pgconn = SimpleNamespace(encrypt_password=lambda *args: b"SCRAM-SYNTHETIC")

    def cursor(self):
        return self.statement_cursor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.mark.parametrize(
    "value", [b"valid-password", b"bad space", b"bad\t", b"\xff", b""]
)
def test_operator_password_validation_is_bounded_and_private(tmp_path, value):
    """Whitespace and non-ASCII input cannot become SQL connection metadata."""
    path = tmp_path / "password"
    if value and value != b"\xff":
        write_private(path, value)
    else:
        path.write_bytes(value)
        path.chmod(0o600)
    if value == b"valid-password":
        assert provisioning._password(path) == value
    else:
        with pytest.raises((ConfigError, ValueError)):
            provisioning._password(path)


@pytest.mark.parametrize("existing", [None, "matching", "foreign"])
def test_existing_role_must_match_every_restricted_attribute(existing):
    """An existing role is never adopted merely because its name is expected."""
    row = (
        None
        if existing is None
        else (
            "marker" if existing == "matching" else "foreign",
            False,
            False,
            False,
            False,
            False,
            False,
            True,
            16,
            False,
        )
    )
    cursor = Cursor([row])
    if existing == "foreign":
        with pytest.raises(ConfigError):
            provisioning._check_role(cursor, "web", "marker", 16)
    else:
        assert provisioning._check_role(cursor, "web", "marker", 16) is (
            existing is not None
        )


@pytest.mark.parametrize(
    "identity, marker, populated, configured, allowed",
    [
        ("operator", None, False, False, True),
        ("operator", "matching", False, False, True),
        ("other", None, False, False, False),
        ("operator", "foreign", False, False, False),
        ("operator", None, True, False, False),
        ("operator", "matching", False, True, False),
    ],
)
def test_initial_operator_database_admission_refuses_ownership_or_data_mismatch(
    tmp_path, identity, marker, populated, configured, allowed
):
    """Only an empty or exactly bound unconfigured database can be provisioned."""
    configuration = configuration_at(tmp_path)
    rows = [
        (
            "pk_stewardship_operator" if identity == "operator" else "other",
            "pk_stewardship_operator",
            True,
            configuration.postgres.name,
        ),
        (marker,),
    ]
    if marker is None:
        rows.append((populated,))
    rows.append(("stewardship_system_configuration" if configured else None,))
    if configured:
        rows.append((True,))
    cursor = Cursor(rows)
    if allowed:
        provisioning._admit_operator(cursor, configuration, "matching", initial=True)
    else:
        with pytest.raises(ConfigError):
            provisioning._admit_operator(
                cursor, configuration, "matching", initial=True
            )


@pytest.mark.parametrize("existing", [False, True])
def test_role_creation_preflights_all_identities_and_never_emits_plaintext(
    tmp_path, monkeypatch, existing
):
    """Retry verifies actual logins; first creation emits only a SCRAM verifier."""
    configuration = configuration_at(tmp_path)
    cursor, admissions, connections = Cursor(), [], []
    monkeypatch.setattr(provisioning, "_password", lambda path: b"private-password")
    monkeypatch.setattr(provisioning, "_admit_operator", lambda *args, **kwargs: None)

    def role_check(*args):
        """No mutation may precede completion of every role preflight."""
        assert not cursor.statements
        admissions.append(args[1])
        return existing

    def connect(config, user, password):
        """Record operator and matching-retry login verification separately."""
        connections.append(user)
        return Database(cursor)

    monkeypatch.setattr(provisioning, "_check_role", role_check)
    monkeypatch.setattr(provisioning, "_connection", connect)
    assert provisioning.provision_roles(configuration, uuid4())[
        "database_roles_provisioned"
    ]
    assert len(admissions) == len(database_identities())
    sql = "\n".join(cursor.statements)
    assert "private-password" not in sql
    assert ("CREATE ROLE" in sql) is (not existing)
    assert ("SCRAM-SYNTHETIC" in sql) is (not existing)
    assert "ALTER PASSWORD" not in sql
    assert len(connections) == 1 + (len(admissions) if existing else 0)


def test_grant_provisioning_uses_only_explicit_table_and_column_registry(
    tmp_path, monkeypatch
):
    """Schema ownership is not translated into runtime ALL or wildcard grants."""
    configuration = configuration_at(tmp_path)
    cursor = Cursor()
    monkeypatch.setattr(provisioning, "_password", lambda path: b"private-password")
    monkeypatch.setattr(provisioning, "_admit_operator", lambda *args, **kwargs: None)
    monkeypatch.setattr(provisioning, "_check_role", lambda *args: True)
    monkeypatch.setattr(provisioning, "_connection", lambda *args: Database(cursor))
    assert provisioning.provision_grants(configuration, uuid4())[
        "database_grants_provisioned"
    ]
    assert cursor.statements
    assert all(statement.startswith("GRANT ") for statement in cursor.statements)
    assert all(
        "ALL" not in statement and "private-password" not in statement
        for statement in cursor.statements
    )
    monkeypatch.setattr(provisioning, "_check_role", lambda *args: False)
    with pytest.raises(ConfigError, match="before grants"):
        provisioning.provision_grants(configuration, uuid4())


def test_operator_connection_uses_separate_password_argument(tmp_path, monkeypatch):
    """Never interpolate a credential into a connection URL or SQL statement."""
    configuration = configuration_at(tmp_path)
    captured = []
    monkeypatch.setattr(
        provisioning.psycopg, "connect", lambda **kwargs: captured.append(kwargs)
    )
    provisioning._connection(configuration, "operator", b"private-password")
    assert captured[0]["password"] == "private-password"
    assert captured[0]["dbname"] == configuration.postgres.name
    for operation in (provisioning.provision_roles, provisioning.provision_grants):
        with pytest.raises(ConfigError, match="UUID"):
            operation(configuration, "invalid")
    configuration = replace(configuration, service_role=ServiceRole.MIGRATION)
    assert (
        provisioning.role_limit(configuration, ServiceRole.MIGRATION)
        == configuration.runtime_budget.operator_connections
    )

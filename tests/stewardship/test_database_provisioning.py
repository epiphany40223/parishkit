"""Fast operator refusal/SQL-shape tests complement real isolated PostgreSQL runs."""

from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import database_provisioning as provisioning
from parishkit.stewardship.accounts.key_files import write_private
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.runtime_grants import (
    CLOSURE_COLUMNS,
    CLOSURE_FUNCTIONS,
    CLOSURE_REVOKES,
    CLOSURE_TABLES,
    DISPATCH_TOKEN_FUNCTION,
    LEADERS_WITHOUT_USER_FUNCTION,
    MEMBER_NAMES_FOR_FUNCTION,
    login_name,
    pending_revokes,
)
from parishkit.stewardship.runtime_identities import database_identities

from .test_runtime_topology import configuration_at

# Every migration the users swap's closure registries wait for, as applied.
CLOSURE_APPLIED = {
    *CLOSURE_FUNCTIONS.values(),
    *CLOSURE_TABLES.values(),
    *CLOSURE_COLUMNS.values(),
}
# Migrations that create both a granted routine and a granted table (#806's
# 0077, #530's 0078): their table waits with their routines.
BOTH = set(CLOSURE_FUNCTIONS.values()) & set(CLOSURE_TABLES.values())
# The login that writes each closure table: web (the large-change
# acceptances, 0080, since System health's button, ADM-13 PR 5b), except the
# address follow-ups (0077), which mail dispatch updates.
TABLE_WRITERS = {
    "stewardship_outbox_address_retry": "pk_stewardship_mail_dispatch",
}


def names_closure_object(statement, migrations):
    """Whether a grant names a routine or table one of ``migrations`` creates."""
    return any(
        f in statement for f, key in CLOSURE_FUNCTIONS.items() if key in migrations
    ) or any(
        f'"{table}"' in statement
        for table, key in CLOSURE_TABLES.items()
        if key in migrations
    )


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


@pytest.mark.parametrize(
    "existing",
    [None, "matching", "foreign", "member", "reader", "reader-admin", "drift"],
)
def test_existing_role_must_match_every_restricted_attribute(existing):
    """An existing role is never adopted merely because its name is expected.

    Ordinary logins belong to no role; the backup login belongs to exactly
    pg_read_all_data with inheritance and without admin option. A role that
    shows isolation drift is refused whatever else matches.
    """
    membership = {
        "member": "some_role:true:false",
        "reader": provisioning.READER_MEMBERSHIP,
        "reader-admin": "pg_read_all_data:true:true",
    }.get(existing)
    row = (
        None
        if existing is None
        else (
            "foreign" if existing == "foreign" else "marker",
            False,
            # The reader rows bypass row-level security, as pg_dump needs.
            existing in {"reader", "reader-admin"},
            False,
            False,
            False,
            False,
            True,
            16,
            membership,
            existing == "drift",
        )
    )
    for reader in (False, True):
        cursor = Cursor([row])
        # A plain login matches without membership; the backup login only with
        # exactly its reader membership; everything else is foreign.
        accepted = existing is None or existing == ("reader" if reader else "matching")
        if accepted:
            assert provisioning._check_role(
                cursor, "web", "marker", 16, reader=reader
            ) is (existing is not None)
        else:
            with pytest.raises(ConfigError):
                provisioning._check_role(cursor, "web", "marker", 16, reader=reader)


def test_isolation_drift_waives_only_the_named_checks():
    """Provisioning and migrate check every drift; migration skips ownership only.

    The rendered boolean ORs each ``ISOLATION_DRIFT`` check that is not
    exempt, and only the migration role is exempt from anything.
    """
    drift = provisioning.ISOLATION_DRIFT
    every = provisioning.isolation_drift()
    owner = provisioning.isolation_drift(
        provisioning.migration_exemption(ServiceRole.MIGRATION)
    )
    for name, check in drift.items():
        assert check in every
        assert (check in owner) is (name != provisioning.OWNERSHIP)
    for role in ServiceRole:
        expected = {provisioning.OWNERSHIP} if role is ServiceRole.MIGRATION else set()
        assert provisioning.migration_exemption(role) == expected


def test_check_role_renders_the_drift_column_with_its_exemption():
    """The role query carries the drift column, minus the exempt checks."""
    owner = provisioning.ISOLATION_DRIFT[provisioning.OWNERSHIP]
    for exempt in (frozenset(), frozenset({provisioning.OWNERSHIP})):
        cursor = Cursor([None])
        provisioning._check_role(cursor, "web", "marker", 16, exempt=exempt)
        query = cursor.statements[0]
        assert provisioning.isolation_drift(exempt) in query
        assert (owner in query) is not bool(exempt)


@pytest.mark.parametrize(
    "identity, marker, populated, configured, backup, allowed",
    [
        ("operator", None, False, False, None, True),
        ("operator", "matching", False, False, None, True),
        ("other", None, False, False, None, False),
        ("operator", "foreign", False, False, None, False),
        ("operator", None, True, False, None, False),
        # A configured database changes only behind a recent recorded backup:
        # no backup table, a stale backup and a recent one.
        ("operator", "matching", False, True, "absent", False),
        ("operator", "matching", False, True, "stale", False),
        ("operator", "matching", False, True, "recent", True),
    ],
)
def test_initial_operator_database_admission_refuses_ownership_or_data_mismatch(
    tmp_path, identity, marker, populated, configured, backup, allowed
):
    """Only an empty, exactly bound or recently backed-up database is admitted."""
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
        rows.append(("stewardship_backup_run" if backup != "absent" else None,))
        if backup != "absent":
            rows.append((backup == "recent",))
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

    def role_check(*args, reader=False, exempt=frozenset()):
        """No mutation may precede completion of every role preflight."""
        assert not cursor.statements
        assert reader is (args[1] == "pk_stewardship_backup_worker")
        # Only the schema owner is waived, and only from the ownership check.
        migration = args[1] == "pk_stewardship_migration"
        assert exempt == ({provisioning.OWNERSHIP} if migration else set())
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


def grant_statements(tmp_path, monkeypatch, applied):
    """Run provision_grants on a fake cursor; its configuration and GRANTs.

    ``applied`` is the ``(app, name)`` rows the cursor answers for
    ``django_migrations``.
    """
    configuration = configuration_at(tmp_path)
    cursor = Cursor()
    cursor.fetchall = lambda: list(applied)
    monkeypatch.setattr(provisioning, "_password", lambda path: b"private-password")
    monkeypatch.setattr(provisioning, "_admit_operator", lambda *args, **kwargs: None)
    monkeypatch.setattr(provisioning, "_check_role", lambda *args, **kwargs: True)
    monkeypatch.setattr(provisioning, "_connection", lambda *args: Database(cursor))
    monkeypatch.setattr(provisioning, "_admit_existing_grants", lambda *args: None)
    monkeypatch.setattr(provisioning, "_admit_reader", lambda *args: None)
    monkeypatch.setattr(provisioning, "_admit_writer_guard", lambda *args: None)
    assert provisioning.provision_grants(configuration, uuid4())[
        "database_grants_provisioned"
    ]
    assert cursor.statements[0] == "SELECT app,name FROM public.django_migrations"
    return configuration, cursor.statements[1:]


def test_grant_provisioning_uses_only_explicit_table_and_column_registry(
    tmp_path, monkeypatch
):
    """Schema ownership is not translated into runtime ALL or wildcard grants."""
    configuration, statements = grant_statements(tmp_path, monkeypatch, CLOSURE_APPLIED)
    assert statements
    assert all(statement.startswith("GRANT ") for statement in statements)
    assert all(
        "ALL" not in statement and "private-password" not in statement
        for statement in statements
    )
    monkeypatch.setattr(provisioning, "_check_role", lambda *args, **kwargs: False)
    with pytest.raises(ConfigError, match="before grants"):
        provisioning.provision_grants(configuration, uuid4())


@pytest.mark.parametrize("applied", [False, True])
def test_grants_wait_for_the_users_swap_closures_functions(
    tmp_path, monkeypatch, applied
):
    """On the users upgrade's base, a routine the swap's closure adds waits.

    Its creating migration is pending there (#952), so naming it would fail;
    once the migration is recorded, the same grants include it.
    """
    _, statements = grant_statements(
        tmp_path, monkeypatch, CLOSURE_FUNCTIONS.values() if applied else ()
    )
    for function in (
        MEMBER_NAMES_FOR_FUNCTION,
        LEADERS_WITHOUT_USER_FUNCTION,
        DISPATCH_TOKEN_FUNCTION,
    ):
        named = [s for s in statements if function in s]
        assert len(named) == (1 if applied else 0), function
    # Everything else is granted either way. A routine may go to several
    # logins (#1034's recorder to two), so the statements that wait are
    # those naming one, not one per routine.
    full = grant_statements(tmp_path, monkeypatch, CLOSURE_FUNCTIONS.values())[1]
    closure = [
        s
        for s in full
        if any(f in s for f in CLOSURE_FUNCTIONS) or names_closure_object(s, BOTH)
    ]
    assert len(closure) >= len(CLOSURE_FUNCTIONS)
    assert len(statements) == len(full) - (0 if applied else len(closure))


def test_grants_wait_for_the_users_swap_closures_tables(tmp_path, monkeypatch):
    """On the users upgrade's base, a table the swap's closure adds waits.

    The base lacks the table (its migration is pending, #1018's archives),
    so naming it would fail; with the migration recorded, web gets its table
    and column grants, and nothing else changes.
    """
    others = {*CLOSURE_FUNCTIONS.values(), *CLOSURE_COLUMNS.values()} - BOTH
    _, waiting = grant_statements(tmp_path, monkeypatch, others)
    _, granted = grant_statements(tmp_path, monkeypatch, CLOSURE_APPLIED)
    for table in CLOSURE_TABLES:
        writer = TABLE_WRITERS.get(table, "pk_stewardship_web")
        assert not [s for s in waiting if f'"{table}"' in s], table
        assert [s for s in granted if f'"{table}" TO "{writer}"' in s], table
    assert set(waiting) < set(granted)
    assert all(
        names_closure_object(s, set(CLOSURE_TABLES.values()))
        for s in set(granted) - set(waiting)
    )


def test_grants_wait_for_the_users_swap_closures_columns(tmp_path, monkeypatch):
    """On the users upgrade's base, a column the swap's closure adds waits.

    The base has the table but not the column (its migration is pending,
    #1018's digest comparison), so naming it would fail; the table's other
    column grants still go ahead, and with the migration recorded the same
    grant names the column too.
    """
    others = {*CLOSURE_FUNCTIONS.values(), *CLOSURE_TABLES.values()}
    _, waiting = grant_statements(tmp_path, monkeypatch, others)
    _, granted = grant_statements(tmp_path, monkeypatch, CLOSURE_APPLIED)
    assert len(waiting) == len(granted)
    for table, column in CLOSURE_COLUMNS:
        assert not [s for s in waiting if f'"{column}"' in s and f'"{table}"' in s]
        assert [s for s in waiting if f'"{table}" TO "pk_stewardship_web"' in s]
        assert [
            s
            for s in granted
            if f'"{column}"' in s and f'"{table}" TO "pk_stewardship_web"' in s
        ]


@pytest.mark.parametrize("applied", [False, True])
def test_grants_tolerate_a_closure_revoke_only_until_it_applies(
    tmp_path, monkeypatch, applied
):
    """Mail dispatch's old ciphertext reads are admitted only on the base.

    On the users upgrade's base, migration 0073 (#389 L1) has not revoked
    them yet, so database-grants admits exactly those columns as held; once
    0073 is recorded they are excess again. Either way no GRANT names them.
    """
    admitted = {}
    applied_rows = set(CLOSURE_FUNCTIONS.values()) if applied else set()
    monkeypatch.setattr(
        provisioning,
        "_admit_existing_grants",
        lambda cursor, login, tables, columns: admitted.__setitem__(login, columns),
    )
    configuration = configuration_at(tmp_path)
    cursor = Cursor()
    cursor.fetchall = lambda: list(applied_rows)
    monkeypatch.setattr(provisioning, "_password", lambda path: b"private-password")
    monkeypatch.setattr(provisioning, "_admit_operator", lambda *args, **kwargs: None)
    monkeypatch.setattr(provisioning, "_check_role", lambda *args, **kwargs: True)
    monkeypatch.setattr(provisioning, "_connection", lambda *args: Database(cursor))
    monkeypatch.setattr(provisioning, "_admit_reader", lambda *args: None)
    monkeypatch.setattr(provisioning, "_admit_writer_guard", lambda *args: None)
    provisioning.provision_grants(configuration, uuid4())
    mail = admitted[login_name(ServiceRole.MAIL_DISPATCH)]
    assert ("ciphertext" in mail["stewardship_family_token"]["SELECT"]) is not applied
    assert (
        "token_ciphertext" in mail["stewardship_rehearsal_credential"]["SELECT"]
    ) is not applied
    for login, columns in admitted.items():
        if login != login_name(ServiceRole.MAIL_DISPATCH):
            token = columns.get("stewardship_family_token", {})
            assert "ciphertext" not in token.get("SELECT", set()), login
    mail_grants = [
        statement
        for statement in cursor.statements
        if statement.endswith(f'"{login_name(ServiceRole.MAIL_DISPATCH)}"')
    ]
    # Mail dispatch still reads the token metadata, so the check below sees
    # its token GRANT and would see a ciphertext column in it.
    assert any(
        "stewardship_family_token" in statement and '"digest"' in statement
        for statement in mail_grants
    )
    for table, column in (
        ("stewardship_family_token", '"ciphertext"'),
        ("stewardship_rehearsal_credential", '"token_ciphertext"'),
    ):
        assert not any(
            table in statement and column in statement for statement in mail_grants
        )


def test_pending_revokes_name_only_their_role_and_pending_migration():
    """Tolerance follows the revoking migration's row, for its role only."""
    (migration,) = CLOSURE_REVOKES
    assert pending_revokes(ServiceRole.MAIL_DISPATCH, set()) == {
        "stewardship_family_token": {"SELECT": {"ciphertext"}},
        "stewardship_rehearsal_credential": {"SELECT": {"token_ciphertext"}},
    }
    assert pending_revokes(ServiceRole.MAIL_DISPATCH, {migration}) == {}
    assert pending_revokes(ServiceRole.WORKER, set()) == {}
    # The revoking migration is the one that adds the replacement definer.
    assert CLOSURE_FUNCTIONS[DISPATCH_TOKEN_FUNCTION] == migration


@pytest.mark.parametrize("installed", ["current", "older", "missing"])
def test_grants_wait_for_the_operational_log_writer_guard(installed):
    """Timeout-only log writers get INSERT only once this release's guard exists."""
    row = {
        "current": (provisioning._writer_guard_digest(),),
        "older": ("0" * 32,),
        "missing": None,
    }[installed]
    cursor = Cursor([row])
    if installed == "current":
        provisioning._admit_writer_guard(cursor)
    else:
        with pytest.raises(ConfigError, match="writer guard"):
            provisioning._admit_writer_guard(cursor)
    assert "stewardship_operational_log_writer_v1" in cursor.statements[0]


@pytest.mark.parametrize(
    "membership, privileges, columns, accepted",
    [
        ((True,), [], [], True),
        ((False,), [], [], False),
        ((True,), [("public", "stewardship_backup_run", "INSERT")], [], True),
        ((True,), [("public", "stewardship_campaign", "INSERT")], [], False),
        ((True,), [("public", "stewardship_backup_run", "UPDATE")], [], False),
        ((True,), [("other", "stewardship_backup_run", "INSERT")], [], False),
        # A column-level write beyond the registry is refused the same way.
        ((True,), [], [("public", "stewardship_campaign", "name", "UPDATE")], False),
        ((True,), [], [("public", "stewardship_backup_run", "id", "INSERT")], True),
        # Its column grants admit exactly those column writes (request mode).
        (
            (True,),
            [],
            [("public", "stewardship_backup_request", "state", "UPDATE")],
            True,
        ),
        (
            (True,),
            [],
            [("public", "stewardship_backup_request", "actor_id", "UPDATE")],
            False,
        ),
        (
            (True,),
            [],
            [("public", "stewardship_backup_request", "state", "INSERT")],
            False,
        ),
    ],
)
def test_reader_admission_requires_membership_and_only_its_writes(
    membership, privileges, columns, accepted
):
    """The read-all member may hold no write beyond its own record."""
    cursor = Cursor([membership])
    sweeps = iter([privileges, columns])
    cursor.fetchall = lambda: next(sweeps)
    tables = {"stewardship_backup_run": {"SELECT", "INSERT"}}
    granted = {"stewardship_backup_request": {"UPDATE": {"state", "held_at"}}}
    login = "pk_stewardship_backup_worker"
    if accepted:
        provisioning._admit_reader(cursor, login, tables, granted)
    else:
        with pytest.raises(ConfigError):
            provisioning._admit_reader(cursor, login, tables, granted)


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


@pytest.mark.parametrize(
    "rows",
    [
        [[("public", "table", "SELECT")], []],
        [[("public", "table", "UPDATE")], []],
        [[], [("public", "metadata", "private", "SELECT")]],
        [[], [("public", "metadata", "id", "SELECT")]],
    ],
)
def test_grant_provisioning_refuses_excess_table_and_column_authority(rows):
    """A narrowed registry cannot leave old broad grants silently accepted."""
    cursor = Cursor()
    results = iter(rows)
    cursor.fetchall = lambda: next(results)
    if rows in (
        [[("public", "table", "SELECT")], []],
        [[], [("public", "metadata", "id", "SELECT")]],
    ):
        provisioning._admit_existing_grants(
            cursor, "web", {"table": {"SELECT"}}, {"metadata": {"SELECT": {"id"}}}
        )
    else:
        with pytest.raises(ConfigError, match="exceed"):
            provisioning._admit_existing_grants(
                cursor, "web", {"table": {"SELECT"}}, {"metadata": {"SELECT": {"id"}}}
            )

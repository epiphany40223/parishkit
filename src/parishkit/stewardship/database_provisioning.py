"""Explicit operator-only initial SQL provisioning, never online authority repair.

The dedicated profile receives individual database password files but no provider
credentials, keyrings, YAML write target, application data or Docker socket.
Database/cluster identity is bound before mutations. Existing unrelated roles
and populated application databases are refused rather than adopted or reset.
"""

from uuid import UUID

import psycopg
from psycopg import sql

from parishkit.config import ConfigError

from .accounts.key_files import read_private
from .deployment import ServiceRole
from .runtime_identities import database_identities
from .runtime_paths import RuntimeLayout


def _password(path):
    """Keep raw credentials solely in memory and individual private files."""
    value = read_private(path).removesuffix(b"\n")
    if not value or any(byte <= 32 or byte >= 127 for byte in value):
        raise ConfigError("A database provisioning password file is invalid.")
    return value


def role_limit(configuration, role):
    """Bound each role so background/stream work cannot spend interactive reserves."""
    budget = configuration.runtime_budget
    if role == "download":
        return (
            budget.web_processes
            * budget.replicas
            * budget.rollout_overlap
            * budget.download_pool_per_process
        )
    if role is ServiceRole.WEB:
        return (
            budget.web_processes
            * budget.replicas
            * budget.rollout_overlap
            * (budget.web_threads + 2)  # Two bounded health-observation threads.
        )
    if role in {ServiceRole.CONFIG_INSTALLER, ServiceRole.CREDENTIAL_INSTALLER}:
        return configuration.runtime_budget.rollout_overlap
    return configuration.runtime_budget.operator_connections


def _admit_operator(cursor, configuration, marker, *, initial):
    """A role name alone cannot authorize takeover of a database or cluster roles."""
    cursor.execute(
        "SELECT current_user,session_user,rolsuper,current_database() "
        "FROM pg_roles WHERE rolname=current_user"
    )
    if cursor.fetchone() != (
        "pk_stewardship_operator",
        "pk_stewardship_operator",
        True,
        configuration.postgres.name,
    ):
        raise ConfigError("Database provisioning requires its operator login.")
    cursor.execute(
        "SELECT shobj_description(oid,'pg_database') FROM pg_database "
        "WHERE datname=current_database()"
    )
    existing = cursor.fetchone()[0]
    if existing != marker and not (initial and existing is None):
        raise ConfigError("Database belongs to another provisioning identity.")
    if existing is None:
        cursor.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n "
            "ON n.oid=c.relnamespace WHERE n.nspname !~ '^pg_' "
            "AND n.nspname<>'information_schema')"
        )
        if cursor.fetchone()[0]:
            raise ConfigError("Only an empty database may receive initial ownership.")
    cursor.execute("SELECT to_regclass('public.stewardship_system_configuration')")
    if cursor.fetchone()[0] is not None:
        cursor.execute(
            "SELECT EXISTS(SELECT 1 FROM public.stewardship_system_configuration)"
        )
        if cursor.fetchone()[0]:
            raise ConfigError("Configured SQL changes require upgrade admission.")


def _check_role(cursor, name, marker, limit):
    """Idempotent retry never adopts, repairs or silently changes an existing role."""
    cursor.execute(
        "SELECT shobj_description(oid,'pg_authid'),rolsuper,rolbypassrls,rolcreatedb,"
        "rolcreaterole,rolreplication,rolinherit,rolcanlogin,rolconnlimit,"
        "EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r.oid) "
        "FROM pg_roles r WHERE rolname=%s",
        (name,),
    )
    row = cursor.fetchone()
    if row is not None and row != (
        marker,
        False,
        False,
        False,
        False,
        False,
        False,
        True,
        limit,
        False,
    ):
        raise ConfigError("Existing database role differs from initial provisioning.")
    return row is not None


def _connection(configuration, name, password):
    """Never construct a password-bearing URL or return libpq error messages."""
    db = configuration.postgres
    return psycopg.connect(
        host=db.host,
        port=db.port,
        dbname=db.name,
        user=name,
        password=password.decode("ascii"),
        connect_timeout=db.connect_timeout,
    )


def provision_roles(configuration, deployment_id):
    """Create all foundation roles atomically before migrations, with matching retry."""
    if not isinstance(deployment_id, UUID):
        raise ConfigError("Database provisioning requires a deployment UUID.")
    marker = "parishkit-stewardship:" + str(deployment_id)
    layout = RuntimeLayout(configuration)
    identities = database_identities()
    passwords = {
        name: _password(layout.database_password(name)) for name, _, _, _ in identities
    }
    operator = _password(configuration.postgres.password_file)
    with (
        _connection(configuration, "pk_stewardship_operator", operator) as database,
        database.cursor() as cursor,
    ):
        _admit_operator(cursor, configuration, marker, initial=True)
        # Preflight every identity before the first role/password mutation.
        existing = {
            name: _check_role(cursor, login, marker, role_limit(configuration, role))
            for name, login, role, _ in identities
        }
        for name, login, _, _ in identities:
            if existing[name]:
                with _connection(configuration, login, passwords[name]):
                    pass  # Verify matching supplied password; never ALTER PASSWORD.
        cursor.execute(
            sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                sql.Identifier(configuration.postgres.name), sql.Literal(marker)
            )
        )
        cursor.execute(
            sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(configuration.postgres.name)
            )
        )
        cursor.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        for name, login, role, _ in identities:
            identifier = sql.Identifier(login)
            if not existing[name]:
                # SCRAM is generated locally: plaintext never becomes SQL text.
                verifier = database.pgconn.encrypt_password(
                    passwords[name], login.encode("ascii"), b"scram-sha-256"
                ).decode("ascii")
                cursor.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS "
                        "NOCREATEDB NOCREATEROLE NOREPLICATION "
                        "CONNECTION LIMIT {} PASSWORD {}"
                    ).format(
                        identifier,
                        sql.Literal(role_limit(configuration, role)),
                        sql.Literal(verifier),
                    )
                )
                cursor.execute(
                    sql.SQL("COMMENT ON ROLE {} IS {}").format(
                        identifier, sql.Literal(marker)
                    )
                )
            cursor.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(configuration.postgres.name), identifier
                )
            )
            cursor.execute(
                sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(identifier)
            )
            cursor.execute(
                sql.SQL("ALTER ROLE {} SET search_path = {}").format(
                    identifier,
                    sql.SQL(
                        "public, pg_catalog"
                        if role is ServiceRole.MIGRATION
                        else "pg_catalog, public"
                    ),
                )
            )
        # Migration owns only the application schema, not the database/roles.
        cursor.execute("ALTER SCHEMA public OWNER TO pk_stewardship_migration")
    return {"database_roles_provisioned": True}


def provision_grants(configuration, deployment_id):
    """Grant only the explicit foundation registry after its migrations exist."""
    from .runtime_grants import runtime_grants

    if not isinstance(deployment_id, UUID):
        raise ConfigError("Database provisioning requires a deployment UUID.")
    marker = "parishkit-stewardship:" + str(deployment_id)
    with (
        _connection(
            configuration,
            "pk_stewardship_operator",
            _password(configuration.postgres.password_file),
        ) as database,
        database.cursor() as cursor,
    ):
        _admit_operator(cursor, configuration, marker, initial=False)
        for _, login, role, target in database_identities():
            if not _check_role(cursor, login, marker, role_limit(configuration, role)):
                raise ConfigError("Database roles must be provisioned before grants.")
            if role is ServiceRole.MIGRATION:
                continue
            tables, columns = runtime_grants(role, target=target)
            _admit_existing_grants(cursor, login, tables, columns)
            for table, privileges in tables.items():
                cursor.execute(
                    sql.SQL("GRANT {} ON public.{} TO {}").format(
                        sql.SQL(", ").join(
                            sql.SQL(value) for value in sorted(privileges)
                        ),
                        sql.Identifier(table),
                        sql.Identifier(login),
                    )
                )
            for table, privileges in columns.items():
                for privilege, names in privileges.items():
                    cursor.execute(
                        sql.SQL("GRANT {} ({}) ON public.{} TO {}").format(
                            sql.SQL(privilege),
                            sql.SQL(", ").join(
                                sql.Identifier(name) for name in sorted(names)
                            ),
                            sql.Identifier(table),
                            sql.Identifier(login),
                        )
                    )
    return {"database_grants_provisioned": True}


def _admit_existing_grants(cursor, login, tables, columns):
    """Refuse superseded grants rather than silently succeeding with excess access.

    Initial provisioning is additive, not an authority-repair command. An operator
    must investigate unexpected grants; no broad REVOKE mutates unrelated state.
    """
    cursor.execute(
        "SELECT n.nspname,c.relname,p FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE',"
        "'TRUNCATE','REFERENCES','TRIGGER']) p "
        "WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema' "
        "AND c.relkind IN('r','p','v','m','f') "
        "AND has_table_privilege(%s,c.oid,p)",
        [login],
    )
    for schema, table, privilege in cursor.fetchall():
        if schema != "public" or privilege not in tables.get(table, set()):
            raise ConfigError("Existing SQL grants exceed initial provisioning intent.")
    cursor.execute(
        "SELECT n.nspname,c.relname,a.attname,p FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_attribute a ON a.attrelid=c.oid "
        "CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','REFERENCES']) p "
        "WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema' "
        "AND c.relkind IN('r','p','v','m','f') AND a.attnum>0 AND NOT a.attisdropped "
        "AND has_column_privilege(%s,c.oid,a.attnum,p)",
        [login],
    )
    for schema, table, column, privilege in cursor.fetchall():
        if schema != "public" or (
            privilege not in tables.get(table, set())
            and column not in columns.get(table, {}).get(privilege, set())
        ):
            raise ConfigError(
                "Existing SQL column grants exceed initial provisioning intent."
            )

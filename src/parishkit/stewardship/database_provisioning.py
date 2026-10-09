"""Explicit operator-only initial SQL provisioning, never online authority repair.

The dedicated profile receives individual database password files but no provider
credentials, keyrings, YAML write target, application data or Docker socket.
Database/cluster identity is bound before mutations. Existing unrelated roles
and populated application databases are refused rather than adopted or reset.
"""

import hashlib
import re
from pathlib import Path
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
            * (budget.web_threads + 3)  # Readiness, metrics and periodic auth health.
        )
    if role in {ServiceRole.CONFIG_INSTALLER, ServiceRole.CREDENTIAL_INSTALLER}:
        return configuration.runtime_budget.rollout_overlap
    if role in {ServiceRole.WORKER, ServiceRole.MAIL_DISPATCH}:
        # The worker container runs two consumer processes (#336), and so
        # does mail dispatch (two Family mail consumers, one per batched
        # helper). Each may hold a task connection, a lease-renewal
        # connection and a private timeout-log connection (audit.timeouts),
        # so three per process. Compose never overlaps two containers of
        # either service, so the overlap factor's slots serve the second
        # process (runtime_process.split_source and split_mail).
        return budget.rollout_overlap * 3
    if role is ServiceRole.SCHEDULER:
        # The scheduler pins exactly one singleton session.
        return budget.rollout_overlap
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
            from .backup import require_recent_backup

            # A configured deployment changes only behind a recent verified
            # backup: the v1 reduction of the deferred upgrade admission.
            require_recent_backup(cursor)


def _admit_reader(cursor, login, tables):
    """The backup login reads everything by membership and writes only its row.

    `pg_read_all_data` makes every table readable, so the ordinary grant
    comparison cannot apply; instead the membership must be present and no
    privilege other than SELECT may exist beyond the registry.
    """
    cursor.execute("SELECT pg_has_role(%s,'pg_read_all_data','MEMBER')", [login])
    if cursor.fetchone() != (True,):
        raise ConfigError("The backup login lacks its read-all membership.")
    cursor.execute(
        "SELECT n.nspname,c.relname,p FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "CROSS JOIN unnest(ARRAY['INSERT','UPDATE','DELETE',"
        "'TRUNCATE','REFERENCES','TRIGGER']) p "
        "WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema' "
        "AND c.relkind IN('r','p','v','m','f') "
        "AND has_table_privilege(%s,c.oid,p)",
        [login],
    )
    for schema, table, privilege in cursor.fetchall():
        if schema != "public" or privilege not in tables.get(table, set()):
            raise ConfigError("Existing SQL grants exceed initial provisioning intent.")
    # Column-level writes are refused the same way; column reads come with
    # the membership and need no listing.
    cursor.execute(
        "SELECT n.nspname,c.relname,a.attname,p FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_attribute a ON a.attrelid=c.oid "
        "CROSS JOIN unnest(ARRAY['INSERT','UPDATE','REFERENCES']) p "
        "WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema' "
        "AND c.relkind IN('r','p','v','m','f') AND a.attnum>0 AND NOT a.attisdropped "
        "AND has_column_privilege(%s,c.oid,a.attnum,p)",
        [login],
    )
    for schema, table, _, privilege in cursor.fetchall():
        if schema != "public" or privilege not in tables.get(table, set()):
            raise ConfigError("Existing SQL grants exceed initial provisioning intent.")


# The backup login's only membership, as pg_auth_members reports it.
READER_MEMBERSHIP = "pg_read_all_data:true:false"

# Authority a runtime login could gain or lend outside its declared grants,
# after cluster drift (#389 L7): each a boolean over the login's ``pg_roles``
# row ``r``, true when the drift is present, keyed by what a refusal names.
# Provisioning creates each login as the superuser operator, so it is never a
# role's member (PostgreSQL 16 gives a CREATEROLE creator one) and owns
# nothing. Every login's own admission refuses these
# (``credential_database._identity``, ``backup_commands``), and the upgrade
# check refuses them too, so an upgrade stops before its services would.
# Provisioning (``_check_role``) and migrate's identity query refuse them
# through ``isolation_drift``, so the migration login, which has no runtime
# admission of its own, is checked before it runs a migration.
# The migration login owns the schema, so only the ownership check
# (``OWNERSHIP``) is waived for it; a member of the schema owner would hold
# every table, policy and guard, so reverse membership still applies.
# - Reverse membership: another role that is a member of the login inherits
#   or can SET ROLE to its grants.
# - Ownership: an owner holds every privilege on what it owns and can grant
#   it. pg_shdepend's owner rows cover every kind of object (functions,
#   types, schemas, sequences, servers, default privileges, databases),
#   not only the relations the grant check sees. There is deliberately no
#   dbid filter: login names are cluster-wide, so ownership in another
#   database of the cluster, or of a shared object (dbid 0), is drift too.
#   Do not narrow it to current_database().
# - Foreign data: USAGE on a wrapper or server lets the login reach outside
#   the database. Wrappers and servers belong to one database, and a login
#   may connect only to this one, so this database is the one to check.
OWNERSHIP = "the login owns an object"
ISOLATION_DRIFT = {
    "another role is a member of the login": (
        "EXISTS(SELECT 1 FROM pg_auth_members WHERE roleid=r.oid)"
    ),
    OWNERSHIP: (
        "EXISTS(SELECT 1 FROM pg_shdepend WHERE refclassid='pg_authid'::regclass "
        "AND refobjid=r.oid AND deptype='o')"
    ),
    "the login has foreign-data USAGE": (
        "(EXISTS(SELECT 1 FROM pg_foreign_data_wrapper w "
        "WHERE has_foreign_data_wrapper_privilege(r.oid,w.oid,'USAGE')) "
        "OR EXISTS(SELECT 1 FROM pg_foreign_server s "
        "WHERE has_server_privilege(r.oid,s.oid,'USAGE')))"
    ),
}


def isolation_drift(exempt=frozenset()):
    """One SQL boolean over a ``pg_roles r`` row, true when any drift is present.

    It ORs every ``ISOLATION_DRIFT`` check whose name is not in ``exempt``.
    The migration login passes ``{OWNERSHIP}``; every other login none.
    """
    checks = (check for name, check in ISOLATION_DRIFT.items() if name not in exempt)
    return f"({' OR '.join(checks)})"


# A routine's signature as admission names it: ``name(argument types)``.
SIGNATURE = "p.proname||'('||oidvectortypes(p.proargtypes)||')'"
# The sequence privileges the backup login must not hold. Its
# pg_read_all_data membership gives SELECT on every sequence by design.
BACKUP_SEQUENCES = "USAGE,UPDATE"


def excess_authority_checks(role, functions, sequences):
    """Authority no declared grant gives a login, each a boolean true when present.

    Keyed by what a refusal names. All three arguments are SQL expressions:
    ``role`` is the login (``current_user`` at admission, a quoted name in
    the upgrade check), ``functions`` a text array of the public definer
    signatures it may EXECUTE, ``sequences`` the sequence privileges it must
    not hold. Admission passes ``%s`` bind placeholders for the last two.
    One definition serves admission and the upgrade check, so they cannot
    disagree.
    """
    return {
        "EXECUTE on a definer routine": (
            "EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n "
            "ON n.oid=p.pronamespace WHERE n.nspname !~ '^pg_' "
            "AND n.nspname<>'information_schema' AND p.prosecdef "
            f"AND NOT (n.nspname='public' AND {SIGNATURE}=ANY({functions})) "
            f"AND has_function_privilege({role},p.oid,'EXECUTE'))"
        ),
        # The CASE keeps has_sequence_privilege off every non-sequence:
        # PostgreSQL may evaluate the WHERE terms in any order, and the
        # function raises on, for example, a TOAST table.
        "a sequence privilege": (
            "EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n "
            "ON n.oid=c.relnamespace WHERE n.nspname !~ '^pg_' "
            "AND n.nspname<>'information_schema' AND c.relkind='S' "
            "AND CASE WHEN c.relkind='S' "
            f"THEN has_sequence_privilege({role},c.oid,{sequences}) END)"
        ),
        "CREATE on a schema": (
            "EXISTS(SELECT 1 FROM pg_namespace n WHERE n.nspname !~ '^pg_' "
            "AND n.nspname<>'information_schema' "
            f"AND has_schema_privilege({role},n.oid,'CREATE'))"
        ),
        "CREATE on the database": (
            f"has_database_privilege({role},current_database(),'CREATE')"
        ),
    }


def _check_role(cursor, name, marker, limit, *, reader=False, exempt=frozenset()):
    """Idempotent retry never adopts, repairs or silently changes an existing role.

    No foundation login is a member of any role or bypasses row-level
    security, except the backup login, whose one membership is exactly
    pg_read_all_data with inheritance and without admin option and which
    bypasses row-level security for pg_dump; anything else is a foreign role.
    An existing role must also show no ``ISOLATION_DRIFT`` outside
    ``exempt`` (the migration login's ``OWNERSHIP``), as the upgrade check
    requires.
    """
    cursor.execute(
        "SELECT shobj_description(oid,'pg_authid'),rolsuper,rolbypassrls,rolcreatedb,"
        "rolcreaterole,rolreplication,rolinherit,rolcanlogin,rolconnlimit,"
        "(SELECT string_agg(m.rolname||':'||am.inherit_option::text||':'"
        "||am.admin_option::text,',' ORDER BY m.rolname) FROM pg_auth_members am "
        "JOIN pg_roles m ON m.oid=am.roleid WHERE am.member=r.oid),"
        f"{isolation_drift(exempt)} "
        "FROM pg_roles r WHERE rolname=%s",
        (name,),
    )
    row = cursor.fetchone()
    if row is not None and row != (
        marker,
        False,
        reader,
        False,
        False,
        False,
        False,
        True,
        limit,
        READER_MEMBERSHIP if reader else None,
        False,
    ):
        raise ConfigError("Existing database role differs from initial provisioning.")
    return row is not None


def migration_exemption(role):
    """The ``ISOLATION_DRIFT`` names waived for ``role``: ownership for migration."""
    return frozenset({OWNERSHIP}) if role is ServiceRole.MIGRATION else frozenset()


def _connection(configuration, name, password):
    """Never construct a password-bearing URL or return libpq error messages."""
    from .runtime_database import require_internal_database

    require_internal_database(configuration)
    db = configuration.postgres
    return psycopg.connect(
        host=db.host,
        port=db.port,
        dbname=db.name,
        user=name,
        password=password.decode("ascii"),
        connect_timeout=db.connect_timeout,
        sslmode="disable",
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
            name: _check_role(
                cursor,
                login,
                marker,
                role_limit(configuration, role),
                reader=role is ServiceRole.BACKUP_WORKER,
                exempt=migration_exemption(role),
            )
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
                # pg_dump runs with row security off, which PostgreSQL refuses
                # for a login subject to a forced policy; the backup login
                # therefore bypasses row-level security, and nothing else does.
                cursor.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER {} "
                        "NOCREATEDB NOCREATEROLE NOREPLICATION "
                        "CONNECTION LIMIT {} PASSWORD {}"
                    ).format(
                        identifier,
                        sql.SQL(
                            "BYPASSRLS"
                            if role is ServiceRole.BACKUP_WORKER
                            else "NOBYPASSRLS"
                        ),
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
            if role is ServiceRole.BACKUP_WORKER:
                # pg_dump needs every table; the login is NOINHERIT, so the
                # membership itself must carry inheritance (PostgreSQL 16+).
                cursor.execute(
                    sql.SQL("GRANT pg_read_all_data TO {} WITH INHERIT TRUE").format(
                        identifier
                    )
                )
        # Migration owns only the application schema, not the database/roles.
        cursor.execute("ALTER SCHEMA public OWNER TO pk_stewardship_migration")
    return {"database_roles_provisioned": True}


def provision_grants(configuration, deployment_id):
    """Grant only the explicit foundation registry after its migrations exist."""
    from .runtime_grants import runtime_functions, runtime_grants

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
        _admit_writer_guard(cursor)
        for _, login, role, target in database_identities():
            if not _check_role(
                cursor,
                login,
                marker,
                role_limit(configuration, role),
                reader=role is ServiceRole.BACKUP_WORKER,
                exempt=migration_exemption(role),
            ):
                raise ConfigError("Database roles must be provisioned before grants.")
            if role is ServiceRole.MIGRATION:
                continue
            tables, columns = runtime_grants(role, target=target)
            if role is ServiceRole.BACKUP_WORKER:
                _admit_reader(cursor, login, tables)
            else:
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
            # Signatures are fixed registry constants, never caller input.
            for function in sorted(runtime_functions(role, target=target)):
                cursor.execute(
                    sql.SQL("GRANT EXECUTE ON FUNCTION public.{} TO {}").format(
                        sql.SQL(function), sql.Identifier(login)
                    )
                )
    return {"database_grants_provisioned": True}


def _writer_guard_digest():
    """The md5 of the writer guard's body as this release's schema defines it."""
    text = (Path(__file__).with_name("schema") / "functions.sql").read_text()
    body = re.search(
        r"^CREATE FUNCTION public\.stewardship_operational_log_writer_v1\(\)"
        r".*?AS \$\$(.*?)\$\$;$",
        text,
        re.S | re.M,
    )
    if body is None:
        raise ConfigError("The schema's operational log writer guard is missing.")
    return hashlib.md5(body.group(1).encode()).hexdigest()


def _admit_writer_guard(cursor):
    """Refuse grants until this release's operational log writer guard is installed.

    The mail-dispatch and backup logins may insert into the operational log
    only because stewardship_operational_log_writer_v1 limits them to timeout
    entries (#293); without it their INSERT grant could forge any event. The
    trigger must call exactly this release's function body, so an older,
    weaker guard cannot pass either.
    """
    cursor.execute(
        "SELECT md5(p.prosrc) FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid "
        "WHERE t.tgname='stewardship_operational_log_writer_v1' "
        "AND t.tgrelid='public.stewardship_operational_log'::regclass "
        "AND t.tgenabled='O' AND t.tgtype=7 "
        "AND t.tgfoid='public.stewardship_operational_log_writer_v1()'::regprocedure"
    )
    row = cursor.fetchone()
    if row is None or row[0] != _writer_guard_digest():
        raise ConfigError(
            "Install the schema's operational log writer guard before grants."
        )


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

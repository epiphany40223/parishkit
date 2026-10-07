"""Real PostgreSQL identities admit target-specific credential queue operations."""

from time import monotonic

from django.db import DEFAULT_DB_ALIAS, connection, connections
from django.db.backends.signals import connection_created

from parishkit.config import ConfigError

from .secret_models import SECRET_TARGETS

# How long one connection's full grant admission stays trusted (#639). The
# identity check still runs on every queue operation; only the catalog-wide
# grant inspection (admit_grants and the metadata column check) is repeated
# at most this often on a connection that has already passed it. A new
# connection, including every reconnect, always runs the full check first.
# Grants change only at a deploy or upgrade, which restarts every installer,
# so the window applies only to a grant change made by hand on a live
# database; see the operations spec's "Installer idle polling" section. The
# default is chosen; the Administrator's decision on it is open on #639.
GRANT_RECHECK_SECONDS = 300

INSTALLER_GRANTS = {
    "stewardship_provider_context": {"SELECT"},
    "stewardship_public_credential_handoff": {"SELECT", "INSERT"},
    "django_migrations": {"SELECT"},
    "stewardship_secret_request": {"SELECT", "UPDATE"},
    "stewardship_secret_checkpoint": {"SELECT", "INSERT"},
    "stewardship_sealed_credential_staging": {"SELECT", "UPDATE"},
    "stewardship_credential_consumer_ack": {"SELECT"},
    "stewardship_audit_event": {"INSERT"},
    "stewardship_system_configuration": {"SELECT"},
    "stewardship_parish": {"SELECT"},
    "stewardship_configuration_version": {"SELECT"},
}
INSTALLER_METADATA = {
    "stewardship_system_configuration": {"active_configuration_id"},
    "stewardship_parish": {"id", "configuration_id"},
    "stewardship_configuration_version": {"id", "validation_schema"},
}


def installer_permissions(target):
    """Return independent closed maps including only this target's setup owner."""
    from .setup_exchange_grants import extend_installer_permissions

    tables = {table: set(names) for table, names in INSTALLER_GRANTS.items()}
    metadata = {table: set(names) for table, names in INSTALLER_METADATA.items()}
    extend_installer_permissions(target, tables, metadata)
    if target == "google_workspace":
        from .setup_mail_grants import extend_workspace_permissions

        extend_workspace_permissions(tables, metadata)
    elif target == "slack":
        from .setup_notification_grants import extend_slack_permissions

        extend_slack_permissions(tables, metadata)
    from .setup_install_grants import extend_initial_permissions

    extend_initial_permissions(target, tables, metadata)
    return tables, metadata


def _identity(expected, *, database=None):
    """Reject superusers, SET ROLE impersonation and any inherited role authority.

    A login that has been disabled (NOLOGIN) or has passed its VALID UNTIL is
    refused too. PostgreSQL checks NOLOGIN only when a session starts, and
    VALID UNTIL only when it authenticates a password, while a service keeps
    its connection open between passes (#639); this check is what makes the
    service's own code stop on a kept session once its login is withdrawn.
    A compromised process can skip it, so containment still needs the
    backend ended (see the operations spec's installer idle polling).

    Returns the login's role OID, so a caller can tell a recreated role of the
    same name from the one it already admitted.
    """
    database = connection if database is None else database
    if database.vendor != "postgresql":
        raise ConfigError("Credential services require PostgreSQL isolation.")
    with database.cursor() as cursor:
        cursor.execute(
            "SELECT current_user, session_user, rolsuper, rolbypassrls, rolcreatedb, "
            "rolcreaterole, rolreplication, rolinherit, "
            "EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r.oid), "
            "has_schema_privilege(current_user,'public','CREATE'), "
            "NOT rolcanlogin, coalesce(rolvaliduntil<=clock_timestamp(),false), "
            "r.oid FROM pg_roles r WHERE rolname=current_user"
        )
        row = cursor.fetchone()
    if row is None or row[:2] != (expected, expected) or any(row[2:-1]):
        raise ConfigError("Credential service database identity is not isolated.")
    return row[-1]


def _forget_admission(sender, connection, **kwargs):
    """A newly opened connection must pass the full grant admission again."""
    connection.stewardship_installer_admission = None


# Every new connection, including a reconnect after an error or a deliberate
# close, clears that connection's admission record (#639).
connection_created.connect(
    _forget_admission, dispatch_uid="stewardship-installer-admission"
)


def admit_installer_database(target):
    """Check the actual login on every queue operation, and its grants regularly.

    RLS supplies target scoping; deployment provisioning owns these narrow grants.
    The online installer cannot be a table owner, read campaign answers, inspect
    audit payloads, or bypass the target-scoped staging store's row policies.

    The identity check (one pg_roles row) runs on every call. The full grant
    inspection reads the whole catalog, so it runs when this connection has
    not passed it yet (startup and every reconnect), when the login's role
    differs from the one admitted, and otherwise at most every
    ``GRANT_RECHECK_SECONDS`` (#639). PostgreSQL still enforces every grant,
    and every revocation, on each statement; the inspection only catches
    authority beyond the closed list.
    """
    if type(target) is not str or target not in SECRET_TARGETS:
        raise ConfigError("Unknown credential target.")
    role = _identity("pk_stewardship_credential_" + target)
    database = connections[DEFAULT_DB_ALIAS]
    admitted = getattr(database, "stewardship_installer_admission", None)
    if (
        admitted is not None
        and admitted[0] == (target, role)
        and monotonic() - admitted[1] < GRANT_RECHECK_SECONDS
    ):
        return
    # A refusal below must leave no record that a later call could trust.
    database.stewardship_installer_admission = None
    _admit_installer_grants(target)
    database.stewardship_installer_admission = ((target, role), monotonic())


def _admit_installer_grants(target):
    """Refuse any table, routine or metadata column grant beyond the target's list."""
    from parishkit.stewardship.deployment import ServiceRole
    from parishkit.stewardship.jobs.service_status_grants import (
        add_service_status_writer_grants,
    )
    from parishkit.stewardship.runtime_grants import admit_columns, runtime_functions

    tables, metadata = installer_permissions(target)
    # The installer's own service status record (ADM-13): insert, refresh
    # and an id read, admitted beside the target's queue grants.
    status_tables, status_columns = {}, {}
    add_service_status_writer_grants(status_tables, status_columns)
    allowed = {table: set(names) for table, names in tables.items()}
    for grants in (status_tables, status_columns):
        for table, privileges in grants.items():
            allowed.setdefault(table, set()).update(privileges)
    # The definer routines this installer may EXECUTE: the automation
    # sign-in check for the targets an automation session may replace.
    admit_grants(
        allowed,
        functions=runtime_functions(ServiceRole.CREDENTIAL_INSTALLER, target=target),
    )
    admit_columns(connection, status_tables, status_columns)
    with connection.cursor() as cursor:
        # Attribution is column-scoped: no full YAML or public draft payloads.
        # Target-specific candidate context is separately scoped by its RLS;
        # setup's public draft checks need only generated equality evidence.
        cursor.execute(
            "SELECT c.relname,a.attname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "JOIN pg_attribute a ON a.attrelid=c.oid "
            "WHERE n.nspname='public' AND c.relname=ANY(%s) "
            "AND a.attnum>0 AND NOT a.attisdropped "
            "AND has_column_privilege(current_user,c.oid,a.attnum,'SELECT')",
            [list(metadata)],
        )
        permitted = {
            (table, column) for table, columns in metadata.items() for column in columns
        }
        if set(cursor.fetchall()) - permitted:
            raise ConfigError("Credential installer metadata grants are excessive.")


def admit_grants(allowed, *, database=None, functions=frozenset()):
    """Inspect all application schemas, including indirect definer authority.

    System routines and ordinary SECURITY INVOKER helpers do not add authority:
    their table access is checked as this same restricted login. Definer routines,
    sequence privileges, schema creation and relations outside public are denied,
    except the public definer ``functions`` named by signature, which this
    login must then hold EXECUTE on (web's Family login, #306 M3).
    """
    database = connection if database is None else database
    with database.cursor() as cursor:
        cursor.execute(
            "SELECT n.nspname,c.relname,p,c.relowner=(SELECT oid FROM pg_roles "
            "WHERE rolname=current_user) FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE',"
            "'TRUNCATE','REFERENCES','TRIGGER']) p "
            "WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema' "
            "AND c.relkind IN('r','p','v','m','f') "
            "AND (has_table_privilege(current_user,c.oid,p) OR "
            "CASE WHEN p IN('SELECT','INSERT','UPDATE','REFERENCES') "
            "THEN has_any_column_privilege(current_user,c.oid,p) ELSE false END)"
        )
        for schema, table, privilege, owner in cursor.fetchall():
            if (
                schema != "public"
                or owner
                or privilege not in allowed.get(table, set())
            ):
                raise ConfigError("Installer database grants are excessive.")
        signature = "p.proname||'('||oidvectortypes(p.proargtypes)||')'"
        cursor.execute(
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n "
            "ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prosecdef "
            f"AND {signature}=ANY(%s) "
            "AND has_function_privilege(current_user,p.oid,'EXECUTE')",
            [sorted(functions)],
        )
        if cursor.fetchone()[0] != len(functions):
            raise ConfigError("Required database function grants are missing.")
        cursor.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n "
            "ON n.oid=p.pronamespace WHERE n.nspname !~ '^pg_' "
            "AND n.nspname<>'information_schema' AND p.prosecdef "
            f"AND NOT (n.nspname='public' AND {signature}=ANY(%s)) "
            "AND has_function_privilege(current_user,p.oid,'EXECUTE')) OR "
            "EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n "
            "ON n.oid=c.relnamespace WHERE n.nspname !~ '^pg_' "
            "AND n.nspname<>'information_schema' AND c.relkind='S' "
            "AND has_sequence_privilege(current_user,c.oid,'USAGE,SELECT,UPDATE')) OR "
            "EXISTS(SELECT 1 FROM pg_namespace n WHERE n.nspname !~ '^pg_' "
            "AND n.nspname<>'information_schema' "
            "AND has_schema_privilege(current_user,n.oid,'CREATE')) OR "
            "has_database_privilege(current_user,current_database(),'CREATE')",
            [sorted(functions)],
        )
        if cursor.fetchone()[0]:
            raise ConfigError("Installer database grants are excessive.")


def admit_consumer_database(consumer):
    """A consumer attests as its own authenticated login, never as an installer."""
    from parishkit.stewardship.service_boundaries import ALLOWED_SECRETS

    if type(consumer) is not str or consumer not in {
        role.value for role in ALLOWED_SECRETS
    }:
        raise ConfigError("Unknown credential consumer.")
    _identity("pk_stewardship_" + consumer.replace("-", "_"))
    if consumer == "web":
        admit_web_staging_grants()


def admit_web_staging_grants():
    """Require ciphertext exclusion at web startup and consumer acknowledgement.

    OPS-02/OPS-04 must call this with the actual web login during startup, beside
    their complete runtime grant/mount admission. RLS alone is not column privacy.
    """
    _identity("pk_stewardship_web")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT has_column_privilege(current_user,"
            "'public.stewardship_sealed_credential_staging','ciphertext','SELECT')"
            " OR has_column_privilege(current_user,"
            "'public.stewardship_setup_sealed_credential','ciphertext','SELECT')"
        )
        if cursor.fetchone()[0]:
            raise ConfigError("Web staging ciphertext access is forbidden.")

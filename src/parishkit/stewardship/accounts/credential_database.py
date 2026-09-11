"""Real PostgreSQL identities admit target-specific credential queue operations."""

from django.db import connection

from parishkit.config import ConfigError

from .secret_models import SECRET_TARGETS

INSTALLER_GRANTS = {
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


def _identity(expected, *, database=None):
    """Reject superusers, SET ROLE impersonation and any inherited role authority."""
    database = connection if database is None else database
    if database.vendor != "postgresql":
        raise ConfigError("Credential services require PostgreSQL isolation.")
    with database.cursor() as cursor:
        cursor.execute(
            "SELECT current_user, session_user, rolsuper, rolbypassrls, rolcreatedb, "
            "rolcreaterole, rolreplication, rolinherit, "
            "EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r.oid), "
            "has_schema_privilege(current_user,'public','CREATE') "
            "FROM pg_roles r WHERE rolname=current_user"
        )
        row = cursor.fetchone()
    if row is None or row[:2] != (expected, expected) or any(row[2:]):
        raise ConfigError("Credential service database identity is not isolated.")


def admit_installer_database(target):
    """Check actual login and grants on every queue operation, including reconnects.

    RLS supplies target scoping; deployment provisioning owns these narrow grants.
    The online installer cannot be a table owner, read campaign answers, inspect
    audit payloads, or bypass the target-scoped staging store's row policies.
    """
    if type(target) is not str or target not in SECRET_TARGETS:
        raise ConfigError("Unknown credential target.")
    _identity("pk_stewardship_credential_" + target)
    admit_grants(INSTALLER_GRANTS)
    with connection.cursor() as cursor:
        # Metadata attribution is deliberately column-scoped. No full YAML,
        # testing recipient, configuration content or provider settings are needed.
        cursor.execute(
            "SELECT c.relname,a.attname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "JOIN pg_attribute a ON a.attrelid=c.oid "
            "WHERE n.nspname='public' AND c.relname=ANY(%s) "
            "AND a.attnum>0 AND NOT a.attisdropped "
            "AND has_column_privilege(current_user,c.oid,a.attnum,'SELECT')",
            [list(INSTALLER_METADATA)],
        )
        permitted = {
            (table, column)
            for table, columns in INSTALLER_METADATA.items()
            for column in columns
        }
        if set(cursor.fetchall()) - permitted:
            raise ConfigError("Credential installer metadata grants are excessive.")


def admit_grants(allowed, *, database=None):
    """Inspect all application schemas, including indirect definer authority.

    System routines and ordinary SECURITY INVOKER helpers do not add authority:
    their table access is checked as this same restricted login. Definer routines,
    sequence privileges, schema creation and relations outside public are denied.
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
        cursor.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n "
            "ON n.oid=p.pronamespace WHERE n.nspname !~ '^pg_' "
            "AND n.nspname<>'information_schema' AND p.prosecdef "
            "AND has_function_privilege(current_user,p.oid,'EXECUTE')) OR "
            "EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n "
            "ON n.oid=c.relnamespace WHERE n.nspname !~ '^pg_' "
            "AND n.nspname<>'information_schema' AND c.relkind='S' "
            "AND has_sequence_privilege(current_user,c.oid,'USAGE,SELECT,UPDATE')) OR "
            "EXISTS(SELECT 1 FROM pg_namespace n WHERE n.nspname !~ '^pg_' "
            "AND n.nspname<>'information_schema' "
            "AND has_schema_privilege(current_user,n.oid,'CREATE')) OR "
            "has_database_privilege(current_user,current_database(),'CREATE')"
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
        )
        if cursor.fetchone()[0]:
            raise ConfigError("Web staging ciphertext access is forbidden.")
